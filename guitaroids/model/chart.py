"""Chart model: the pure data the game plays.

L1 in the layering from DESIGN.md §1.3 -- no Qt, no OpenCV, no sounddevice, no
device I/O. Everything here is deterministic: same input, same output, no globals,
no clocks. That is what makes the timing chain testable with no hardware.

The one judgement call is where guitarpro's exceptions stop. Parsing lives in
``Chart.from_gp5`` and converts a malformed file into a ``ChartError`` rather than
letting a ``GPException`` escape, so the library loader can report one bad file
without taking the app down.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

import guitarpro

#: ``Duration.quarterTime`` -- ticks per quarter note.
TICKS_PER_BEAT = 960

#: A standard 6-string guitar. Lanes are 0-5 (DESIGN.md §1.6).
EXPECTED_STRINGS = 6

#: General MIDI programme numbers, per the GM level-1 sound set.
#:
#: ``channel.instrument`` is the real discriminator between tracks -- guitarpro
#: gives *every* track 6 strings by default, so string count does not distinguish
#: a guitar part from a vocal line (DESIGN.md §7.3).
GM_ACOUSTIC_GUITAR = 24
GM_ELECTRIC_GUITAR = 31
GM_BASS = 32
GM_ELECTRIC_BASS = 39


class ChartError(Exception):
    """A tab could not be turned into a playable chart."""


class ChartWarning(Exception):
    """A tab parsed, but something about it needs the player's attention."""


class TrackKind(Enum):
    """What a track appears to be, from its General MIDI programme."""

    GUITAR = "guitar"
    BASS = "bass"
    DRUMS = "drums"
    OTHER = "other"

    @property
    def selectable(self) -> bool:
        """Whether this track can be played on a 6-lane guitar highway.

        Bass has 4 strings and drums are unpitched, so neither maps onto six
        string lanes.
        """
        return self is TrackKind.GUITAR


class CollapseRule(Enum):
    """Which note survives when a chord is collapsed to a single onset.

    Real tabs are chord-heavy: the one tab in ``songs/`` has 688 of 1108 onsets
    carrying five or six notes (DESIGN.md §7.4). A fretting hand's x-position
    selects one lane, so the chord has to be reduced to something playable.
    """

    HIGHEST = "highest"
    """The highest-pitched note. Most melodic, and what beginner charts do."""

    LOWEST = "lowest"
    """The lowest-pitched note, i.e. the bass of the chord."""

    COMMON = "common"
    """The most frequently used string across the chart, falling back to HIGHEST."""


# --- chord collapsing ---------------------------------------------------------


def group_by_onset(notes: list[Note]) -> list[list[Note]]:
    """Group notes that sound at the same instant, in time order.

    Times come from a single computation per beat, so notes within a chord share
    an identical float and can be grouped by exact equality.
    """
    onsets: dict[float, list[Note]] = defaultdict(list)
    for note in notes:
        onsets[note.time].append(note)
    return [onsets[t] for t in sorted(onsets)]


def count_chord_sizes(notes: list[Note]) -> dict[float, int]:
    """How many notes share each onset."""
    return {onset[0].time: len(onset) for onset in group_by_onset(notes)}


def collapse_chords(notes: list[Note], rule: CollapseRule = CollapseRule.HIGHEST) -> list[Note]:
    """Reduce each onset to a single note, per ``rule``.

    The rhythm is preserved exactly -- every onset survives, so the game still
    has the same number of things to hit. Only the pitch choice changes, which is
    the part a single fretting-hand lane cannot express anyway.

    The kept note keeps the true ``chord_size``, so the UI can still show that an
    onset was a six-note chord.
    """
    string_frequency = Counter(n.string for n in notes)
    kept: list[Note] = []

    for onset in group_by_onset(notes):
        if len(onset) == 1:
            kept.append(onset[0])
            continue

        if rule is CollapseRule.HIGHEST:
            chosen = max(onset, key=lambda n: n.pitch)
        elif rule is CollapseRule.LOWEST:
            chosen = min(onset, key=lambda n: n.pitch)
        elif rule is CollapseRule.COMMON:
            # Most-used string across the whole chart wins; pitch breaks ties.
            chosen = max(onset, key=lambda n: (string_frequency[n.string], n.pitch))
        else:  # pragma: no cover - exhaustive
            raise ChartError(f"unknown collapse rule {rule!r}")

        kept.append(chosen)

    return kept


@dataclass(frozen=True, slots=True)
class Note:
    """One playable note."""

    time: float
    """Seconds from the start of the audio."""

    lane: int
    """0-5, derived from the tab's string number minus 1."""

    fret: int
    """Fret number. Carried for display; ``lane`` is what gets judged."""

    string: int
    """Original 1-based string number, for debugging and the fret display."""

    measure: int
    """1-based measure number in the *written* score, before repeat unrolling."""

    pitch: int = 0
    """MIDI note number, ``tuning[string - 1] + fret``. Used by the collapse
    rules and by MIDI export. 0 when the tuning was unavailable."""

    chord_size: int = 1
    """How many notes shared this onset in the tab. 1 means it was not a chord.

    Always populated, even when collapsing is off, so the UI can label a chord
    and the per-song toggle can render full chords for keyboard play.
    """

    duration_beats: float = 0.0
    """Length in beats, for sustained notes. Unused in v1."""


@dataclass(frozen=True, slots=True)
class BarLine:
    """A measure boundary on the highway."""

    time: float


@dataclass(frozen=True, slots=True)
class Chart:
    """A playable song, derived from one ``.gp5`` file."""

    path: Path
    title: str
    artist: str
    tempo: int
    time_signature: tuple[int, int]
    track_name: str
    notes: tuple[Note, ...]
    track_number: int = 0
    """1-based track number within the tab, so a song select can match a chart
    back to the :class:`~guitaroids.songlib.TrackInfo` it was built from."""
    bar_lines: tuple[BarLine, ...] = ()
    uses_repeats: bool = False
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def duration(self) -> float:
        """Time of the last note, or 0.0 for an empty chart."""
        return self.notes[-1].time if self.notes else 0.0

    @property
    def note_count(self) -> int:
        return len(self.notes)

    @property
    def notes_per_second(self) -> float:
        """Naive density, for a rough difficulty read in the song list."""
        if not self.notes or self.duration <= 0:
            return 0.0
        return len(self.notes) / self.duration

    def notes_in_lane(self, lane: int) -> tuple[Note, ...]:
        return tuple(n for n in self.notes if n.lane == lane)


# --- track classification -----------------------------------------------------


def classify_track(track) -> TrackKind:
    """Classify a track from its General MIDI programme.

    ``channel.instrument`` is the only signal that reliably separates a guitar
    part from a vocal line in a real tab. Verified on the 9 tracks of
    ``songs/eagles_the-hotel_california_5.gp5`` (DESIGN.md §7.3)::

        #1 Vocals                GM 87  -> OTHER
        #3 12-stg Guitar (1)     GM 25  -> GUITAR
        #4 Acoustic Guitar (2)   GM 24  -> GUITAR
        #8 Bass                  GM 35  -> BASS
        #9 Drums                 percussion flag -> DRUMS

    ``clefTranspose == 12`` identifies bass independently of the programme number,
    which matters for tabs written by hand where the programme is often wrong.
    """
    if track.isPercussionTrack:
        return TrackKind.DRUMS
    if track.clefTranspose == 12:
        return TrackKind.BASS
    instrument = int(track.channel.instrument)
    if GM_ACOUSTIC_GUITAR <= instrument <= GM_ELECTRIC_GUITAR:
        return TrackKind.GUITAR
    if GM_BASS <= instrument <= GM_ELECTRIC_BASS:
        return TrackKind.BASS
    return TrackKind.OTHER


def playable_tracks(song) -> list:
    """Every track that can be played on a 6-lane highway.

    Bass and drums are excluded, not because they are unplayable but because they
    do not map onto six string lanes.
    """
    return [t for t in song.tracks if classify_track(t).selectable]


def count_notes(track) -> int:
    """Playable notes in a track. Cheap enough to call for every track."""
    total = 0
    for measure in track.measures:
        for voice in measure.voices:
            for beat in voice.beats:
                for note in beat.notes:
                    if note.type == guitarpro.NoteType.normal and 1 <= note.string <= EXPECTED_STRINGS:
                        total += 1
    return total


def suggest_track(song) -> object:
    """Pick the best default guitar track: the densest one.

    Density is used only as a tiebreak among tracks already classified as GUITAR,
    so it can no longer promote a vocal line -- which is exactly the bug that
    made the original heuristic choose "Vocals" (DESIGN.md §7.3).

    Raises:
        ChartError: if the tab has no usable guitar track.
    """
    candidates = [t for t in playable_tracks(song) if count_notes(t) > 0]
    if not candidates:
        raise ChartError("no guitar track with playable notes in this tab")
    return max(candidates, key=lambda t: (count_notes(t), -t.number))


# --- tempo --------------------------------------------------------------------


def _mix_tempo(effect) -> int | None:
    """The BPM this beat's effect sets, or ``None`` if it does not set one.

    Two shapes, both real, both from PyGuitarPro 0.11 reading two different files:

    - some effect types wrap a mix table as ``effect.mixTableChange``;
    - others **are** the mix table, with ``effect.tempo`` directly on them.

    And ``tempo`` is not a number in either case -- it is a ``MixTableItem``, a
    value/duration/allTracks triple -- and is ``None`` when the change only touches
    volume or reverb.

    Every version of this function that assumed one shape raised on a real tab. It
    raised ``TypeError`` (``int()`` of a ``MixTableItem``) and ``AttributeError``
    (no ``mixTableChange``), and because this runs inside the library scan, either
    one took out **every** song rather than the one that was malformed.
    """
    change = getattr(effect, "mixTableChange", None)
    if change is None:
        change = effect  # the effect *is* the mix table
    tempo = getattr(change, "tempo", None)
    if tempo is None:
        return None
    try:
        return int(getattr(tempo, "value", tempo))
    except (TypeError, ValueError):
        return None


def changes_tempo(song, track) -> bool:
    """Public: does this track change tempo? Song select filters on it.

    A tempo-changing track cannot be charted, so offering it would be offering a
    dead end -- song select would list it and the game would come up empty.
    """
    return _has_tempo_change(song, track)


def _has_tempo_change(song, track) -> bool:
    """True if any beat carries a mix table change that alters the tempo.

    DESIGN.md §1.6 / §6.4: constant tempo only. A tab that changes tempo is
    rejected rather than silently misplayed against a single global tempo.
    """
    try:
        song_tempo = int(song.tempo)
    except (TypeError, ValueError):
        return False
    if song_tempo <= 0:
        # Nothing to compare against. A tab with no usable tempo is played at full
        # speed elsewhere (``Game.rate_for``), so rejecting its tracks here would
        # make it unplayable in a second, unrelated way.
        return False

    for measure in track.measures:
        for voice in measure.voices:
            for beat in voice.beats:
                if beat.effect is None:
                    continue
                changed = _mix_tempo(beat.effect)
                if changed is not None and changed != song_tempo:
                    return True
    return False


# --- construction -------------------------------------------------------------


def chart_from_song(
    song,
    path: Path,
    track=None,
    *,
    collapse: bool = True,
    rule: CollapseRule = CollapseRule.HIGHEST,
) -> Chart:
    """Build a :class:`Chart` from an already-parsed guitarpro ``Song``.

    Args:
        track: which track to chart. Defaults to :func:`suggest_track`.
        collapse: reduce each chord to a single note. See :class:`CollapseRule`.
            Defaults to ``True`` because a fretting hand selects one lane and
            cannot hold a six-fret barre (DESIGN.md §7.4).
        rule: which note survives a collapse.
    """
    from .repeats import has_repeats, unroll_repeats

    track = track if track is not None else suggest_track(song)

    warnings: list[str] = []
    if _has_tempo_change(song, track):
        raise ChartError(
            "this tab changes tempo partway through; only constant-tempo tabs are "
            "supported (DESIGN.md §1.6)"
        )

    headers = song.measureHeaders
    order = unroll_repeats(headers)
    uses_repeats = has_repeats(headers)
    if uses_repeats:
        warnings.append(f"tab uses repeat barlines ({len(order)} measures after unrolling)")

    seconds_per_tick = 60.0 / (song.tempo * TICKS_PER_BEAT)

    # Open-string tuning, indexed by string number. A 12-string part is stored as
    # 6 strings by guitarpro, so this is standard tuning either way.
    tuning = [0] + [int(s.value) for s in track.strings]

    notes: list[Note] = []
    bar_lines: list[BarLine] = []
    offset = 0

    for measure_index in order:
        header = headers[measure_index]
        # Repeated measures reuse their written ticks, so the unrolled position is
        # the running offset plus the measure-local tick. `length` follows the time
        # signature, so signature changes are handled for free.
        bar_lines.append(BarLine(time=offset * seconds_per_tick))

        measure = track.measures[measure_index]
        for voice in measure.voices:
            for beat in voice.beats:
                if beat.start is None:
                    continue
                local_tick = beat.start - header.start
                if local_tick < 0:
                    # `Beat.start` is absolute; a beat landing before its own measure
                    # start means a malformed file. Playing it would put the note
                    # before its bar line, so drop it and say so rather than
                    # silently misplacing it.
                    warnings.append(
                        f"measure {measure_index + 1}: beat at tick {beat.start} precedes "
                        f"the measure start {header.start}; skipped"
                    )
                    continue
                beat_time = (offset + local_tick) * seconds_per_tick

                for note in beat.notes:
                    if note.type != guitarpro.NoteType.normal:
                        continue
                    string = int(note.string)
                    if not 1 <= string <= EXPECTED_STRINGS:
                        continue
                    fret = int(note.value)
                    notes.append(
                        Note(
                            time=beat_time,
                            lane=string - 1,
                            fret=fret,
                            string=string,
                            measure=measure_index + 1,
                            pitch=tuning[string] + fret,
                        )
                    )

        offset += header.length

    notes.sort(key=lambda n: (n.time, n.pitch))

    if not notes:
        raise ChartError("no playable notes found in the selected track")

    chord_sizes = count_chord_sizes(notes)
    notes = [replace(n, chord_size=chord_sizes[n.time]) for n in notes]

    if collapse:
        before = len(notes)
        notes = collapse_chords(notes, rule)
        if before != len(notes):
            warnings.append(
                f"chords collapsed to one note per onset ({rule.value}): "
                f"{before} notes -> {len(notes)}"
            )
    else:
        chorded = sum(1 for n in notes if n.chord_size > 1)
        if chorded:
            warnings.append(f"{chorded} notes belong to chords (full chords kept)")

    signature = (headers[0].timeSignature.numerator, headers[0].timeSignature.denominator.value)

    return Chart(
        path=path,
        title=song.title or path.stem,
        artist=song.artist,
        tempo=int(song.tempo),
        time_signature=signature,
        track_name=track.name or f"Track {track.number}",
        notes=tuple(notes),
        track_number=int(track.number),
        bar_lines=tuple(bar_lines),
        uses_repeats=uses_repeats,
        warnings=tuple(warnings),
    )


def chart_from_gp5(path: str | Path) -> Chart:
    """Parse a ``.gp5`` file into a :class:`Chart`.

    Raises:
        ChartError: for anything that makes the file unplayable, including
            unsupported Guitar Pro versions and tempo changes.
    """
    path = Path(path)
    if not path.is_file():
        raise ChartError(f"no such file: {path}")

    try:
        song = guitarpro.parse(path)
    except guitarpro.GPException as exc:
        # Covers .gpx and other unsupported versions -- see DESIGN.md §6.3.
        raise ChartError(f"unsupported or corrupt Guitar Pro file: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - any parse failure is a ChartError
        raise ChartError(f"could not parse {path.name}: {exc}") from exc

    try:
        return chart_from_song(song, path)
    except ChartError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ChartError(f"could not build a chart from {path.name}: {exc}") from exc
