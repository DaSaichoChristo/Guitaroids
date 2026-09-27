"""Song library: discover, pair, validate.

Walks a directory of tabs and reports what is playable and what is not. The whole
point is that **one bad file must not stop the library loading** -- every candidate
becomes a ``SongEntry`` with a status, and the song-select screen shows problems in
a collapsed section instead of a stack trace (DESIGN.md §6.6).

Pairing convention (DESIGN.md §3.4)::

    songs/<slug>.gp5
    songs/<slug>.{mp3,ogg,wav,flac,m4a}   <- same basename, optional

A tab with no audio is a supported state, not an error: it plays as metronome-only.

This module is L2 (see DESIGN.md §1.3) -- it does filesystem I/O and delegates
parsing to L1, but knows nothing about Qt or devices.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from .model.chart import (
    Chart,
    ChartError,
    CollapseRule,
    TrackKind,
    classify_track,
    count_notes,
    changes_tempo,
    playable_tracks,
    suggest_track,
)

TAB_EXTENSIONS = (".gp3", ".gp4", ".gp5")
AUDIO_EXTENSIONS = (".ogg", ".mp3", ".wav", ".flac", ".m4a")


class Status(Enum):
    """Outcome of validating one tab."""

    OK = "ok"
    """Playable with backing audio."""

    NO_AUDIO = "no_audio"
    """Playable as metronome-only. Not a problem -- just a badge."""

    UNSUPPORTED_VERSION = "unsupported_version"
    """.gpx and other formats PyGuitarPro 0.11 cannot read (DESIGN.md §6.3)."""

    PARSE_ERROR = "parse_error"
    """Corrupt or truncated file."""

    TEMPO_CHANGE = "tempo_change"
    """Tab changes tempo; rejected per DESIGN.md §1.6."""

    NOT_GUITAR = "not_guitar"
    """No 6-string guitar track -- bass, or a non-guitar instrument."""

    EMPTY = "empty"
    """Parsed, but no playable notes."""

    @property
    def is_playable(self) -> bool:
        return self in (Status.OK, Status.NO_AUDIO)


@dataclass(frozen=True, slots=True)
class TrackInfo:
    """One selectable guitar track within a tab.

    The user picks the track (DESIGN.md §7.3); this is what the song-select list
    renders. Classification is by General MIDI programme, because guitarpro gives
    every track 6 strings by default and string count cannot tell a guitar part
    from a vocal line.
    """

    number: int
    name: str
    instrument: int
    kind: TrackKind
    string_count: int
    note_count: int
    is_suggested: bool = False

    @property
    def label(self) -> str:
        marker = " (default)" if self.is_suggested else ""
        return f"{self.name} - {self.note_count} notes{marker}"


@dataclass(frozen=True, slots=True)
class SongEntry:
    """One tab, plus whatever we could work out about it."""

    tab_path: Path
    status: Status
    detail: str = ""
    chart: Chart | None = None
    audio_path: Path | None = None
    tracks: tuple[TrackInfo, ...] = ()
    collapse: bool = False
    rule: CollapseRule = CollapseRule.HIGHEST
    _song: object | None = field(default=None, repr=False, compare=False)

    @property
    def slug(self) -> str:
        return self.tab_path.stem

    def chart_for(
        self, track_number: int, *, collapse: bool | None = None
    ) -> Chart | None:
        """Build the chart for a specific track, or ``None`` if unavailable.

        ``collapse`` overrides the value the entry was scanned with, so a
        preference change takes effect on the next attempt **without a rescan**
        (§21). Omit it and the entry's own setting is used, which is what the
        song list wants: it is describing the scan, not an attempt.

        Falls back to the entry's own chart if the song was not retained (a
        tab that failed to parse has no tracks anyway). Note the fallback cannot
        honour ``collapse`` -- there is no song left to rebuild from -- so it is
        only correct when the entry was scanned the same way.
        """
        wanted = self.collapse if collapse is None else bool(collapse)
        if self._song is None:
            # No song, so nothing can be rebuilt: the cached chart is the only
            # answer available, and it is only the right one if it was built the
            # way we now want. Returning None rather than a chart of the wrong
            # shape is the honest failure -- a silently collapsed tab is exactly
            # the bug this argument was added to fix (§21).
            if wanted != self.collapse:
                return None
            return self.chart if self.chart and self.chart.track_number == track_number else None
        try:
            track = next(
                (t for t in playable_tracks(self._song) if t.number == track_number), None
            )
            if track is None:
                return None
            from .model.chart import chart_from_song

            return chart_from_song(
                self._song, self.tab_path, track=track, collapse=wanted, rule=self.rule
            )
        except ChartError:
            return None

    @property
    def default_track(self) -> TrackInfo | None:
        for track in self.tracks:
            if track.is_suggested:
                return track
        return self.tracks[0] if self.tracks else None

    @property
    def title(self) -> str:
        if self.chart is not None and self.chart.title:
            return self.chart.title
        return self.tab_path.stem

    @property
    def artist(self) -> str:
        return self.chart.artist if self.chart is not None else ""

    @property
    def tempo(self) -> int:
        return self.chart.tempo if self.chart is not None else 0

    @property
    def note_count(self) -> int:
        return self.chart.note_count if self.chart is not None else 0

    @property
    def duration(self) -> float:
        return self.chart.duration if self.chart is not None else 0.0

    @property
    def notes_per_second(self) -> float:
        """Every note, chord included. This is what the song list displays."""
        return self.chart.notes_per_second if self.chart is not None else 0.0

    @property
    def onsets_per_second(self) -> float:
        """One per rhythmic event, chord included. This is what difficulty bands on."""
        return self.chart.onsets_per_second if self.chart is not None else 0.0

    @property
    def difficulty(self) -> str:
        """Coarse band from **onset** density. See :func:`difficulty_band`."""
        return difficulty_band(self.onsets_per_second)


#: Difficulty band boundaries, in **onsets** per second.
#:
#: **These were wrong, and every tab in the library said "Medium".** The first set was
#: 2.0 / 4.5 / 7.0, which put the whole library in one 2.5-wide band: measured onsets
#: per second are 2.02, 2.91, 3.37 and 3.90 for Sweet Child O' Mine, Hotel California,
#: Sweet Child O' Mine (Live) and Afterlife. Four genuinely different rock tabs, one
#: label, and nothing on screen to say why.
#:
#: That is a real band being 2.5 wide, not four songs being the same. A beginner riff
#: and a modern metal track are not the same difficulty and the numbers say so.
#:
#: **Judgement, not measurement.** Four tabs is a thin sample, and the boundaries above
#: are the numbers that separate them with room left over -- `Expert` is deliberately
#: unclaimed, because nothing in the library is that fast. A larger library may want
#: these moved; `test_the_bands_separate_the_real_library` is what will notice.
EASY_BELOW = 2.5
MEDIUM_BELOW = 3.5
HARD_BELOW = 5.0

#: Shown when there is no rate to band on -- an unparsed tab, or an empty chart.
UNKNOWN_DIFFICULTY = "?"


def difficulty_band(onsets_per_second: float) -> str:
    """Coarse band from **onset** density. A pure function of one number.

    Onsets rather than notes, and the reason is §21: a chord is one thing to hit, so
    banding on note count would make the setting quadruple the density of every song
    and push the whole library into "Expert" without the songs having got any harder.
    The *displayed* density stays note-based, because "10.77 nps" is the truth about
    how much is written down -- which is why the song select also shows the onset
    rate, or the band looks inexplicable next to the number the player can see.
    """
    if onsets_per_second <= 0:
        return UNKNOWN_DIFFICULTY
    if onsets_per_second < EASY_BELOW:
        return "Easy"
    if onsets_per_second < MEDIUM_BELOW:
        return "Medium"
    if onsets_per_second < HARD_BELOW:
        return "Hard"
    return "Expert"


def classify_error(exc: ChartError) -> tuple[Status, str]:
    """Map a ChartError message onto a Status.

    Matching on message text is fragile. It is here only because PyGuitarPro raises a
    single ``GPException`` type for every version problem, and the alternatives --
    sniffing magic bytes or duplicating its version table -- are worse. If these
    substrings drift, ``tests/test_songlib.py`` pins the current behaviour.
    """
    text = str(exc).lower()
    if "unsupported" in text:
        return Status.UNSUPPORTED_VERSION, str(exc)
    if "tempo" in text:
        return Status.TEMPO_CHANGE, str(exc)
    if "no playable notes" in text:
        return Status.EMPTY, str(exc)
    if "string" in text or "percussion" in text:
        return Status.NOT_GUITAR, str(exc)
    return Status.PARSE_ERROR, str(exc)


def find_audio(song_dir: Path, slug: str) -> Path | None:
    """Locate backing audio sharing the tab's basename, by extension priority."""
    for extension in AUDIO_EXTENSIONS:
        candidate = song_dir / f"{slug}{extension}"
        if candidate.is_file():
            return candidate
    return None


def describe_tracks(song, suggested) -> tuple[TrackInfo, ...]:
    """Build the selectable-track list for a parsed song.

    Only ``TrackKind.GUITAR`` tracks with at least one playable note are offered:
    bass has 4 strings and drums are unpitched, so neither maps onto six string
    lanes, and an empty track would be a dead end in the UI (DESIGN.md §7.3).

    A track that **changes tempo** is dropped for the same reason: it cannot be
    charted at all (§1.6), so listing it means listing something the game will
    refuse. Found by a real .gp4 whose track 3 drops to 96 against the song's 127
    while tracks 4 and 5 are constant -- the tab is playable, one of its tracks is
    not, and it was being offered.
    """
    suggested_number = suggested.number if suggested is not None else None
    infos = []
    for track in playable_tracks(song):
        if changes_tempo(song, track):
            continue
        count = count_notes(track)
        if count == 0:
            continue
        infos.append(
            TrackInfo(
                number=int(track.number),
                name=track.name or f"Track {track.number}",
                instrument=int(track.channel.instrument),
                kind=classify_track(track),
                string_count=len(track.strings),
                note_count=count,
                is_suggested=(track.number == suggested_number),
            )
        )
    return tuple(infos)


def load_tab(
    tab_path: Path,
    *,
    collapse: bool = False,
    rule: CollapseRule = CollapseRule.HIGHEST,
) -> SongEntry:
    """Parse and validate one tab. Never raises for bad input.

    A tab can expose several guitar tracks; the suggested one is charted eagerly
    so the song list can show its note count and difficulty without re-parsing,
    and the others are built on demand via :meth:`SongEntry.chart_for`.
    """
    import guitarpro

    try:
        song = guitarpro.parse(tab_path)
    except guitarpro.GPException as exc:
        status, detail = classify_error(ChartError(f"unsupported or corrupt Guitar Pro file: {exc}"))
        return SongEntry(tab_path=tab_path, status=status, detail=detail)
    except Exception as exc:  # noqa: BLE001 - a bad file must never propagate
        return SongEntry(
            tab_path=tab_path,
            status=Status.PARSE_ERROR,
            detail=f"unexpected error: {exc}",
        )

    suggested = None
    try:
        suggested = suggest_track(song)
    except ChartError as exc:
        # A tab with no guitar track is a real, explainable outcome -- but only if
        # it genuinely has none. A tab full of guitar tracks that all parse to
        # nothing is an empty-chart problem instead.
        if playable_tracks(song):
            return SongEntry(
                tab_path=tab_path,
                status=Status.EMPTY,
                detail=str(exc),
                _song=song,
            )
        return SongEntry(tab_path=tab_path, status=Status.NOT_GUITAR, detail=str(exc), _song=song)

    from .model.chart import chart_from_song

    try:
        chart = chart_from_song(song, tab_path, track=suggested, collapse=collapse, rule=rule)
    except ChartError as exc:
        status, detail = classify_error(exc)
        return SongEntry(
            tab_path=tab_path,
            status=status,
            detail=detail,
            tracks=describe_tracks(song, suggested),
            _song=song,
        )

    audio = find_audio(tab_path.parent, tab_path.stem)
    return SongEntry(
        tab_path=tab_path,
        status=Status.OK if audio else Status.NO_AUDIO,
        chart=chart,
        audio_path=audio,
        tracks=describe_tracks(song, suggested),
        collapse=collapse,
        rule=rule,
        _song=song,
    )


@dataclass(frozen=True, slots=True)
class Library:
    """The result of scanning a directory."""

    root: Path
    entries: tuple[SongEntry, ...] = field(default_factory=tuple)

    @property
    def playable(self) -> tuple[SongEntry, ...]:
        return tuple(e for e in self.entries if e.status.is_playable)

    @property
    def problems(self) -> tuple[SongEntry, ...]:
        return tuple(e for e in self.entries if not e.status.is_playable)

    def get(self, slug: str) -> SongEntry | None:
        for entry in self.entries:
            if entry.slug == slug:
                return entry
        return None

    def by_status(self, status: Status) -> tuple[SongEntry, ...]:
        return tuple(e for e in self.entries if e.status is status)


def find_tabs(root: str | Path) -> tuple[Path, ...]:
    """Every tab under ``root``, recursively, in a stable order.

    Exists so the background loader (:mod:`guitaroids.ui.library_loader`) can
    discover files without reimplementing the glob. A loader that filtered
    differently would rescan a different set than :func:`scan_library`, and the
    bug would only show up on a library containing a nested directory or an
    oddly cased extension.

    A missing directory yields an empty tuple, matching :func:`scan_library`'s
    empty-library behaviour, so first run is an empty state and not a crash.
    """
    root = Path(root)
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in TAB_EXTENSIONS
        )
    )


def scan_library(
    root: str | Path,
    *,
    collapse: bool = False,
    rule: CollapseRule = CollapseRule.HIGHEST,
) -> Library:
    """Scan ``root`` for tabs and build a :class:`Library`.

    A missing directory yields an empty library rather than raising, so the song
    select screen can show an empty state instead of crashing on first run.
    """
    root = Path(root)
    entries = [load_tab(path, collapse=collapse, rule=rule) for path in find_tabs(root)]
    return Library(root=root, entries=tuple(entries))


def format_duration(seconds: float) -> str:
    """Seconds as m:ss."""
    if seconds <= 0:
        return "0:00"
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes}:{remainder:02d}"
