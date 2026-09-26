"""Builders for synthetic Guitar Pro songs, shared by the test modules.

Two shapes, and the difference matters:

- :func:`write_tab` writes a real ``.gp5`` to disk. Needed by anything that goes
  through :func:`guitaroids.songlib.scan_library` or the background loader, since
  those discover files rather than being handed one.
- :func:`make_song` and :func:`make_entry` build a parsed ``Song`` in memory. Much
  faster, and needed by anything that takes a ``Library`` directly -- notably the
  widget tests, which must not scan a real directory.

Every builder here goes through the same production functions
(``suggest_track``, ``chart_from_song``, ``describe_tracks``) that
``songlib.load_tab`` uses, so a synthetic entry cannot drift from a real one.
"""

from __future__ import annotations

from pathlib import Path

import guitarpro

from guitaroids.songlib import SongEntry, Status, describe_tracks

#: Guitar Pro's ticks-per-quarter-note. Everything in the timing maths assumes it.
TICK = 960

#: A General MIDI programme in the guitar range (24-31), so ``suggest_track``
#: classifies it as a guitar rather than falling through to the first track.
GUITAR_PROGRAM = 25


def _headers(measures: int = 2) -> list[guitarpro.MeasureHeader]:
    headers = []
    for number in range(1, measures + 1):
        header = guitarpro.MeasureHeader(number=number, start=(number - 1) * 4 * TICK)
        header.timeSignature.numerator = 4
        header.timeSignature.denominator.value = 4
        headers.append(header)
    return headers


def _attach_notes(track: guitarpro.Track, notes: int, chord_size: int = 1) -> None:
    """Put ``notes`` onsets on the track, ``chord_size`` strings at each onset.

    ``chord_size`` > 1 stacks notes from the low string up, which is what a real
    tab looks like and what §21 made the default shape. Most fixtures want 1: a
    chord is several notes sharing one onset, and a fixture that wanted one note
    per lane would otherwise find them merged.
    """
    for index in range(notes):
        measure = track.measures[index % len(track.measures)]
        voice = measure.voices[0]
        beat = guitarpro.Beat(voice, start=measure.header.start + index * TICK)
        for string in range(1, chord_size + 1):
            note = guitarpro.Note(beat, value=0, string=string)
            note.type = guitarpro.NoteType.normal
            beat.notes.append(note)
        voice.beats.append(beat)


def make_song(
    *,
    title: str = "Synthetic",
    artist: str = "Nobody",
    tempo: int = 120,
    tracks: int = 1,
    notes: int = 4,
    chord_size: int = 1,
    program: int = GUITAR_PROGRAM,
) -> guitarpro.Song:
    """A structurally real Song, built in memory. No file is touched."""
    headers = _headers()
    song = guitarpro.Song(
        measureHeaders=headers, tempo=tempo, title=title, artist=artist
    )
    song.tracks = []
    for number in range(1, tracks + 1):
        track = guitarpro.Track(song, number=number, name=f"Guitar {number}")
        track.channel.instrument = program
        track.measures = [guitarpro.Measure(track, header) for header in headers]
        _attach_notes(track, notes, chord_size)
        song.tracks.append(track)
    return song


def make_entry(
    song: guitarpro.Song | None = None,
    path: Path | str = Path("songs/synthetic.gp5"),
    *,
    status: Status = Status.OK,
    collapse: bool = False,
) -> SongEntry:
    """Wrap a Song the way ``songlib.load_tab`` would, without reading a file."""
    from guitaroids.model.chart import chart_from_song, suggest_track

    song = song if song is not None else make_song()
    path = Path(path)
    suggested = suggest_track(song)
    chart = chart_from_song(song, path, track=suggested, collapse=collapse)
    return SongEntry(
        tab_path=path,
        status=status,
        chart=chart,
        tracks=describe_tracks(song, suggested),
        collapse=collapse,
        _song=song,
    )


def write_tab(
    path: Path,
    *,
    notes: int = 4,
    tempo: int = 120,
    tracks: int = 1,
    chord_size: int = 1,
) -> Path:
    """Write a minimal but real ``.gp5``. Returns the path."""
    song = make_song(
        title=path.stem, tempo=tempo, tracks=tracks, notes=notes, chord_size=chord_size
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    guitarpro.write(song, path)
    return path


def broken_tab(path: Path) -> Path:
    """A file with a .gp5 extension and nothing valid inside it.

    Lands on ``UNSUPPORTED_VERSION`` rather than ``PARSE_ERROR``: PyGuitarPro
    cannot read a version header out of arbitrary bytes, and that is what its
    message says. Unplayable either way, which is what callers care about.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"definitely not a guitar pro file")
    return path


def make_chart(
    notes: list[tuple[float, int, int]] | None = None,
    *,
    tempo: int = 60,
    beats: int = 32,
    title: str = "Synthetic",
    collapse: bool = True,
    path: Path | str = Path("songs/synthetic.gp5"),
) -> "Chart":
    """A chart holding exactly the given ``(seconds, lane, fret)`` notes.

    ``tempo=60`` makes one beat exactly one second and one quarter note 960 ticks, so
    a note asked for at ``2.5`` lands at ``2.5`` -- which is the property tests of the
    highway geometry and the judge windows both need. guitarpro is tick-based, so
    exact seconds are not available at an arbitrary tempo.

    ``lane`` is 0-5 and maps to string ``lane + 1``; guitarpro has no notion of a
    lane, so the string is derived from it.

    Pass ``collapse=False`` to keep several notes that share an onset -- chord
    collapse merges them into one, so a six-lane fixture has to opt out.
    """
    import guitarpro

    from guitaroids.model.chart import chart_from_song, suggest_track

    requested = notes if notes is not None else [(0.0, 0, 0)]
    measures = max(1, -(-beats // 4))
    headers = []
    for number in range(1, measures + 1):
        header = guitarpro.MeasureHeader(number=number, start=(number - 1) * 4 * TICK)
        header.timeSignature.numerator = 4
        header.timeSignature.denominator.value = 4
        headers.append(header)

    song = guitarpro.Song(measureHeaders=headers, tempo=tempo, title=title)
    track = guitarpro.Track(song, number=1, name="Guitar")
    track.channel.instrument = GUITAR_PROGRAM
    track.measures = [guitarpro.Measure(track, header) for header in headers]
    song.tracks = [track]

    for seconds, lane, fret in requested:
        tick = int(round(seconds * 960 / (60 / tempo)))
        measure_index = min(len(track.measures) - 1, tick // (4 * TICK))
        measure = track.measures[measure_index]
        voice = measure.voices[0]
        beat = guitarpro.Beat(voice, start=tick)
        note = guitarpro.Note(beat, value=fret, string=lane + 1)
        note.type = guitarpro.NoteType.normal
        beat.notes.append(note)
        voice.beats.append(beat)

    return chart_from_song(song, path, track=suggest_track(song), collapse=collapse)
