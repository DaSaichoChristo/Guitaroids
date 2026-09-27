"""User settings: what the preferences screen edits.

Pure by design -- no Qt, no sound device, no audio input. That keeps it testable in
milliseconds with no display, which matters because it is the first thing three
screens depend on.

Three rules, all of them about not surprising the user:

1. **A missing file is normal**, not an error. First run writes nothing.
2. **A corrupt file falls back to defaults** rather than crashing the app. Someone
   hand-editing JSON, or a half-written file from a crash, should still get a
   working app.
3. **Out-of-range values are clamped, unknown keys ignored.** A settings file
   written by a newer version must not break an older one.

Writes are atomic (temp file plus rename), so an interrupted save cannot leave a
truncated file that rule 2 then has to rescue.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from pathlib import Path

#: Bumped when a change is not backwards compatible. Older files are migrated or
#: ignored, never fatal.
#:
#: 2 -- ``collapse_chords`` defaults to False (full chords, §21). Files written at
#: version 1 have the key, and almost all of them have the *old default* in it, so
#: the key is dropped on load and the new default applies. A file that says
#: ``false`` already agrees with the new default, so nothing is lost by dropping it.
#:
#: 3 -- ``camera_device`` is gone and ``input_mode: "camera"`` means the microphone
#: (§25). The key is dropped on load; ``InputMode.parse`` maps the old value. Without
#: this, a file that said ``"camera"`` would fall back to KEYBOARD -- silently
#: handing a player who had deliberately chosen non-keyboard input the one input
#: they did not want, with nothing to say why.
#:
#: 4 -- ``input_mode`` and ``InputMode`` are GONE (§32). The keyboard is not a mode
#: any more, so there is one input and nothing to choose between. The key is dropped
#: on load, and ``"keyboard"`` -- the old default, and the value in every existing
#: file -- needs no mapping, because reading any file now means the microphone.
#:
#: 5 -- ``song_best_accuracy`` is new (§34). Nothing is dropped, so a v4 file gains
#: the key with an empty dict and every older file loads unchanged.
#:
#: 6 -- ``hidden_songs`` is new (§46). Also additive: an empty list, so every older
#: file loads with nothing hidden, which is the correct default -- a song is visible
#: until the player says otherwise.
SETTINGS_VERSION = 6

#: Files below this version had their ``collapse_chords`` key dropped on load, so
#: that the flip in version 2 reaches an existing installation.
_MIGRATE_COLLAPSE_FROM = 2

#: ``camera_device`` is dropped from files older than this.
_MIGRATE_CAMERA_DEVICE_FROM = 3

#: ``input_mode`` is dropped from files older than this. See version 4.
_MIGRATE_INPUT_MODE_FROM = 4

#: Practice-tempo bounds. The floor is where the beat line stops being readable as a
#: moving thing; the ceiling is far above any real tab, and a song's *own* written
#: tempo is the real ceiling (see :meth:`Settings.set_bpm_for`), clamped at use.
MIN_BPM = 20.0
MAX_BPM = 400.0


def _clamp_float(value: object, low: float, high: float, default: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(low, min(high, number))


def _clamp_int(value: object, low: int, high: int, default: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _parse_number(value: object) -> float | None:
    """Strict numeric parse: ``None`` if it is not a real number.

    Distinct from :func:`_clamp_float`, which substitutes a default. Per-song
    offsets need the difference, because 0.0 is a meaningful value ("aligned") and
    quietly rewriting garbage to 0.0 would claim alignment the user never set.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number  # NaN

@dataclass
class Settings:
    """Everything the preferences screen can change.

    Not a per-song offset: that belongs on the PlayRequest, because it describes
    one attempt at one song. ``song_offsets_ms`` exists only to remember it for
    next time, keyed by tab slug.
    """

    master_volume: float = 0.8
    """0.0-1.0, the whole mix."""

    click_volume: float = 0.5
    """0.0-1.0, the metronome, relative to the master volume."""

    """Keyboard by default, because the keyboard path must always work (§4.1).

    A demo on an unfamiliar laptop may have no audio input at all, which is the same
    failure as having no camera and gets the same answer.
    """

    input_latency_ms: float = 0.0
    """The player's measured input lag, subtracted before judging (§4.1).

    Named and ranged for whichever device is in use, because §25 changed the device:
    a webcam was 30-100ms of unfixable buffering, and a microphone block is 23ms and
    chosen by us. The range is asymmetric on purpose -- +2s allows a Bluetooth
    interface being genuinely bad, and -500ms allows for the correction being
    measured in the other direction.
    """

    audio_device: str | None = None
    """``None`` means the system default. Matched as a substring of device names."""

    input_device: str | None = None
    """Which audio *input* to listen on, as a substring of a device name.

    Replaces ``camera_device``, which was an integer index into a camera enumeration
    that never existed. Removed rather than left as a dead integer (§21.2's failure
    mode: a setting that is saved, clamped, serialised and read by nothing).

    A string rather than an index because a machine with a guitar interface, a laptop
    microphone and a monitor loopback has three plausible answers, and "the second
    one" is not a setting anybody can check.
    """

    count_in_bars: int = 1
    """0-2 bars of clicks before the music. Many tracks open with their own."""

    collapse_chords: bool = False
    """Keep every note in a chord, or reduce each onset to one.

    **False by default since §21.** The tab in ``songs/`` loses 2991 of its 4099
    notes when chords are collapsed, and the survivor is the highest note of each
    chord -- so 68% of what is left sits on the high E string and the play view
    stops resembling the tab the player learned the song from. That was the wrong
    default for a game whose whole claim is to represent the song.

    A chord is genuinely playable: ``GameState.press`` resolves each lane
    independently, so a six-note chord is six simultaneous presses and six
    PERFECTs. The cost is difficulty -- a full six-note chord is six keys at once,
    which a keyboard player will not manage, and this is why the preference stays
    rather than being removed. Collapsing is one click away.
    """

    song_offsets_ms: dict[str, float] = field(default_factory=dict)
    """Per-tab audio alignment, keyed by slug, so a manual tweak sticks."""

    song_best_accuracy: dict[str, float] = field(default_factory=dict)
    """Best accuracy per tab, keyed by slug. 0.0-1.0.

    There are no points (§34.1), so this is the one number in a run worth comparing
    between attempts, and it is what the results screen means by "best".

    Keyed by slug like its two neighbours above, which means the best is across *all*
    tracks in a tab. A per-track key would be more precise and is not what the
    existing per-song data does, so consistency won; a tab whose tracks are wildly
    different is the case that notices.
    """

    song_bpm: dict[str, float] = field(default_factory=dict)
    """Per-tab practice tempo, keyed by slug.

    Absolute BPM rather than a percentage, because a percentage is a *rate* and a
    rate is song-relative: "80%" of a 76 BPM tab and of a 50 BPM tab are different
    tempi, so one global number could not mean "play it slower" for a whole library.
    A per-song BPM can, and it is also the value an audio transport needs directly.

    Absent -- and an explicit 0 -- both mean the tab's own written tempo, so an
    untouched song is unaffected *and* a stored 0 does not pin a tab whose tempo is
    later edited. Set from song select, before the song starts (§19.1).
    """

    hidden_songs: list[str] = field(default_factory=list)
    """Slugs the player has taken out of the song list, in the order they were hidden.

    **This hides; it does not delete.** A tab in ``songs/`` is the player's own work
    and the folder is gitignored precisely because it is not the repository's to
    manage, so a button that unlinks someone's tab file is a button that can lose it.
    The file stays exactly where it was and the entry comes back from the Hidden
    toggle.

    Keyed by slug like its three neighbours, with the same consequence: hiding is per
    *file*, not per track, so hiding a tab hides all of its tracks. That is the same
    unit the offsets, tempi and bests are keyed on, and a per-track key would be more
    precise at the cost of disagreeing with them.

    A **list rather than a set** because a settings file that can be hand-edited reads
    better as an ordered list, and because the order is the order the player hid them,
    which is what the Hidden panel shows. Membership tests are over a list of a dozen
    slugs, not a hot path.
    """

    version: int = SETTINGS_VERSION

    # --- serialisation ----------------------------------------------------

    def to_dict(self) -> dict:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, raw: object) -> "Settings":
        """Build from a parsed mapping, tolerating anything.

        Unknown keys are dropped by only reading known fields; wrong types fall
        back per-field rather than rejecting the whole file, so one bad value does
        not discard the other twenty.
        """
        defaults = cls()
        if not isinstance(raw, dict):
            return defaults

        # Before the per-field loop, because the loop is what would otherwise
        # resurrect the old default: a version-1 file has the key present with
        # True in it, and that is the value this migration exists to discard.
        file_version = raw.get("version")
        if not isinstance(file_version, int) or file_version < _MIGRATE_COLLAPSE_FROM:
            raw = {k: v for k, v in raw.items() if k != "collapse_chords"}
        if not isinstance(file_version, int) or file_version < _MIGRATE_CAMERA_DEVICE_FROM:
            raw = {k: v for k, v in raw.items() if k != "camera_device"}
        if not isinstance(file_version, int) or file_version < _MIGRATE_INPUT_MODE_FROM:
            # Every existing file says "keyboard", and that is no longer a choice, so
            # the key is dropped rather than mapped. Keeping it would leave a field
            # nothing reads -- the exact failure this whole change removes.
            raw = {k: v for k, v in raw.items() if k != "input_mode"}

        values: dict = {}
        for spec in fields(cls):
            name = spec.name
            if name not in raw:
                continue
            given = raw[name]

            if name in ("master_volume", "click_volume"):
                values[name] = _clamp_float(given, 0.0, 1.0, getattr(defaults, name))
            elif name == "input_latency_ms":
                values[name] = _clamp_float(given, -500.0, 2000.0, 0.0)
            elif name == "input_device":
                if given is None or isinstance(given, str):
                    values[name] = given or None
                else:
                    values[name] = None
            elif name == "count_in_bars":
                values[name] = _clamp_int(given, 0, 2, defaults.count_in_bars)
            elif name == "collapse_chords":
                values[name] = bool(given) if isinstance(given, bool) else defaults.collapse_chords
            elif name == "audio_device":
                if given is None or isinstance(given, str):
                    values[name] = given or None
                else:
                    values[name] = None
            elif name == "song_offsets_ms":
                offsets: dict[str, float] = {}
                if isinstance(given, dict):
                    for slug, offset in given.items():
                        if not isinstance(slug, str):
                            continue
                        number = _parse_number(offset)
                        if number is None:
                            continue  # drop, do not invent a 0.0 alignment
                        offsets[slug] = max(-5000.0, min(5000.0, number))
                values[name] = offsets
            elif name == "song_bpm":
                tempos: dict[str, float] = {}
                if isinstance(given, dict):
                    for slug, bpm in given.items():
                        if not isinstance(slug, str):
                            continue
                        number = _parse_number(bpm)
                        if number is None or number <= 0.0:
                            # 0 and below are the "as written" sentinel or plain
                            # nonsense; neither is a tempo to remember, and an
                            # absent key already means "as written".
                            continue
                        tempos[slug] = max(MIN_BPM, min(MAX_BPM, number))
                values[name] = tempos
            elif name == "song_best_accuracy":
                bests: dict[str, float] = {}
                if isinstance(given, dict):
                    for slug, value in given.items():
                        if not isinstance(slug, str):
                            continue
                        number = _parse_number(value)
                        if number is None:
                            continue
                        # Clamped rather than discarded: a stored 1.4 is nonsense, but
                        # it is obviously meant to be the best run, and dropping it
                        # would report that song as never having been played.
                        bests[slug] = max(0.0, min(1.0, number))
                values[name] = bests
            elif name == "hidden_songs":
                # A list of slugs, tolerantly: a hand-edited file may hold anything,
                # and the one thing that must not happen is an entry that is not a
                # string quietly becoming a slug nothing will ever match.
                hidden: list[str] = []
                if isinstance(given, list):
                    for slug in given:
                        if isinstance(slug, str) and slug:
                            hidden.append(slug)
                # De-duplicated while keeping order, so a hand-edited file with the
                # same slug twice does not make the Hidden panel list it twice.
                values[name] = list(dict.fromkeys(hidden))
            elif name == "version":
                # A file older than this build has been *migrated* on the way in,
                # so it now reports itself as current. Without that, a
                # version-1 file keeps saying 1 forever and every migration added
                # later re-runs on every single load. A file from a *newer* build
                # keeps its own number, so downgrading does not re-run old
                # migrations over data it has already been past.
                stored = _clamp_int(given, 0, 999, SETTINGS_VERSION)
                values[name] = max(stored, SETTINGS_VERSION)

        return cls(**values)

    # --- disk -------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str) -> "Settings":
        """Read settings, never raising.

        A missing file, unreadable file, invalid JSON, or a JSON document that is
        not an object all yield defaults.
        """
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return cls()
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return cls()
        return cls.from_dict(raw)

    def save(self, path: Path | str) -> None:
        """Write settings atomically: temp file in the same directory, then rename.

        A crash mid-write leaves the old file intact rather than a truncated one.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True)

        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=".settings-",
            suffix=".tmp",
            delete=False,
        )
        tmp = Path(handle.name)
        try:
            with handle:
                handle.write(payload + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            tmp.replace(path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    # --- per-song helpers --------------------------------------------------

    def offset_for(self, slug: str) -> float:
        """The remembered offset for one tab, in milliseconds."""
        return self.song_offsets_ms.get(slug, 0.0)

    def set_offset_for(self, slug: str, offset_ms: float) -> None:
        self.song_offsets_ms[slug] = _clamp_float(offset_ms, -5000.0, 5000.0, 0.0)

    def bpm_for(self, slug: str, default: float = 0.0) -> float:
        """The remembered practice tempo for one tab, in BPM.

        ``default`` is the caller's fallback -- the tab's own written tempo -- and is
        returned when nothing is stored. Returning the caller's default rather than a
        constant is what keeps "no stored value" from being a lie about tempo.

        A stored **0 means the written tempo**, deliberately not a floor value: the
        control in song select writes 0 when it is at the written tempo, so a tab
        whose tempo is later edited in Guitar Pro is not held at the old one.
        """
        stored = self.song_bpm.get(slug, 0.0)
        return default if stored <= 0.0 else stored

    def set_bpm_for(self, slug: str, bpm: float) -> None:
        """Remember a practice tempo.

        Zero or less is stored as 0, the "as written" sentinel, rather than being
        pulled up to ``MIN_BPM``: a caller that means "no practice tempo" must not
        get a 20 BPM song, and the clamp is for *positive* nonsense (1 BPM, 9999).
        """
        self.song_bpm[slug] = 0.0 if bpm <= 0.0 else _clamp_float(bpm, MIN_BPM, MAX_BPM, MIN_BPM)

    # --- hiding a song from the list (§46) ---------------------------------------

    def is_hidden(self, slug: str) -> bool:
        """Whether the player has taken this tab out of the song list."""
        return bool(slug) and slug in self.hidden_songs

    def hide_song(self, slug: str) -> bool:
        """Hide a tab from the list. Returns whether anything changed.

        Idempotent, so the caller does not have to check first -- and an empty slug is
        refused, because a nameless entry in a per-song store is a bug waiting to be
        read. Hiding twice is not an error, it is a no-op that does not also re-save.
        """
        if not slug or self.is_hidden(slug):
            return False
        self.hidden_songs.append(slug)
        return True

    def show_song(self, slug: str) -> bool:
        """Put a hidden tab back. Returns whether anything changed."""
        if not slug or not self.is_hidden(slug):
            return False
        self.hidden_songs.remove(slug)
        return True

    def best_accuracy_for(self, slug: str, default: float = 0.0) -> float:
        """The best accuracy recorded for one tab, or ``default`` if it has never been.

        The caller's default rather than a constant, like :meth:`bpm_for`: "no run
        recorded" is not the same claim as "a run that scored nothing", and a screen
        that prints 0% for both would tell a player who has never played the song that
        their first attempt was their worst.
        """
        return self.song_best_accuracy.get(slug, default)

    def record_accuracy_for(self, slug: str, accuracy: float) -> tuple[bool, float | None]:
        """Record a run's accuracy, keeping the best.

        Returns ``(is_new_best, previous_best)``, and both halves are needed together:
        the results screen has to say "new best" *and* name what it beat, and asking
        twice means reading the store between the two calls, when it already holds this
        run. ``previous_best`` is ``None`` when this was the song's first recorded run.

        **The first run for a song is always a new best**, including one that scored
        nothing. The test is against a *missing* key rather than against 0.0, because
        the two differ exactly here: a first attempt at 0% has nothing to beat, so it
        establishes the bar. Comparing against 0.0 instead made the first run report
        "not a best" while still storing it, which is a contradiction a screen would
        have to explain.

        Strictly greater after that, so replaying an identical run does not announce a
        new best -- a screen that congratulated you every time you retried the same
        attempt would be teaching the player to ignore it.

        An empty slug records nothing, because there is no song to attach it to and a
        per-song store with one nameless entry is a bug waiting to be read.
        """
        if not slug:
            return False, None
        value = _clamp_float(accuracy, 0.0, 1.0, 0.0)
        previous = self.song_best_accuracy.get(slug)
        if previous is None or value > previous:
            self.song_best_accuracy[slug] = value
            return True, previous
        return False, previous
