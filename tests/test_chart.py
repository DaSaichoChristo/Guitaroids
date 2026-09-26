"""Tests for the chart model.

The timing arithmetic here is the crux of the whole app (DESIGN.md §1.5, §1.6), so
it is tested against a synthetic in-memory ``Song``. No ``.gp5`` file, no audio
device, no display -- which is the point of keeping ``chart.py`` in L1.

Reference values used throughout: 4/4 at 120bpm means one quarter note is 960 ticks
= 0.5s, and one measure of 4/4 is 3840 ticks = 2.0s.
"""

from __future__ import annotations

from pathlib import Path

import guitarpro
import pytest

from guitaroids.model.chart import (
    TICKS_PER_BEAT,
    Chart,
    ChartError,
    chart_from_gp5,
    chart_from_song,
    classify_track,
    playable_tracks,
    suggest_track,
    TrackKind,
)

TICK = TICKS_PER_BEAT  # 960
MEASURE_4_4 = 4 * TICK  # 3840


def headers(count: int, *, numerator: int = 4, denominator: int = 4):
    """`count` measure headers, each a bar long, with ascending start ticks."""
    out = []
    tick = 0
    for number in range(1, count + 1):
        header = guitarpro.MeasureHeader(number=number, start=tick)
        header.timeSignature.numerator = numerator
        header.timeSignature.denominator.value = denominator
        out.append(header)
        tick += numerator * (TICK * 4 // denominator)
    return out


def make_song(
    *,
    tempo: int = 120,
    measure_count: int = 2,
    numerator: int = 4,
    denominator: int = 4,
    notes_per_measure: list[list[tuple[int, int]]] | None = None,
    header_kwargs: list[dict] | None = None,
    string_count: int = 6,
    track_number: int = 1,
    percussion: bool = False,
    clef_transpose: int | None = None,
) -> guitarpro.Song:
    """Build a minimal but structurally real Song.

    `notes_per_measure` is a list (one entry per measure) of (tick, string) pairs,
    where tick is **relative to the start of that measure**.
    """
    heads = headers(measure_count, numerator=numerator, denominator=denominator)
    for header, kwargs in zip(heads, header_kwargs or []):
        for key, value in kwargs.items():
            setattr(header, key, value)

    song = guitarpro.Song(measureHeaders=heads, tempo=tempo, title="Test", artist="Nobody")

    track = guitarpro.Track(song, number=track_number, name=f"Track {track_number}")
    track.isPercussionTrack = percussion
    track.clefTranspose = clef_transpose
    track.strings = [
        guitarpro.GuitarString(n, 64 - (n - 1) * 5) for n in range(1, string_count + 1)
    ]
    song.tracks = [track]
    track.measures = [
        guitarpro.Measure(track, header) for header in heads
    ]

    plan = notes_per_measure or [[] for _ in range(measure_count)]
    for measure, entries in zip(track.measures, plan):
        voice = measure.voices[0]
        for relative_tick, string in entries:
            # `Beat.start` is an ABSOLUTE tick in the score, so a beat in measure 2
            # must be offset by that measure's start. Taking the entry as
            # measure-relative here keeps the fixtures readable.
            beat = guitarpro.Beat(voice, start=measure.header.start + relative_tick)
            note = guitarpro.Note(beat, value=3, string=string)
            note.type = guitarpro.NoteType.normal
            beat.notes.append(note)
            voice.beats.append(beat)

    return song


# --- timing arithmetic --------------------------------------------------------


def test_quarter_note_is_half_a_second_at_120bpm() -> None:
    song = make_song(notes_per_measure=[[(0, 1), (TICK, 1), (2 * TICK, 1)]])
    chart = chart_from_song(song, Path("x.gp5"))
    assert [round(n.time, 6) for n in chart.notes] == [0.0, 0.5, 1.0]


def test_tempo_halving_doubles_note_times() -> None:
    plan = [[(0, 1), (TICK, 1)]]
    fast = chart_from_song(make_song(tempo=240, notes_per_measure=plan), Path("a.gp5"))
    slow = chart_from_song(make_song(tempo=120, notes_per_measure=plan), Path("b.gp5"))
    assert slow.notes[0].time == pytest.approx(0.0)
    assert slow.notes[1].time == pytest.approx(2 * fast.notes[1].time)


def test_bar_lines_land_on_measure_boundaries() -> None:
    song = make_song(measure_count=3, notes_per_measure=[[(0, 1)], [], []])
    chart = chart_from_song(song, Path("x.gp5"))
    assert [b.time for b in chart.bar_lines] == [0.0, 2.0, 4.0]


def test_notes_land_after_the_previous_measures() -> None:
    """A note in measure 2 must be offset by measure 1's length."""
    song = make_song(measure_count=2, notes_per_measure=[[], [(0, 1)]])
    chart = chart_from_song(song, Path("x.gp5"))
    assert chart.notes[0].time == pytest.approx(2.0)


def test_time_signature_three_four_shortens_measures() -> None:
    """3/4 at 120bpm is 1.5s per bar, so bar 2 starts at 1.5s not 2.0s."""
    song = make_song(measure_count=2, numerator=3, denominator=4, notes_per_measure=[[(0, 1)], []])
    chart = chart_from_song(song, Path("x.gp5"))
    assert [b.time for b in chart.bar_lines] == [0.0, 1.5]


def test_notes_are_sorted_by_time() -> None:
    song = make_song(
        measure_count=2,
        notes_per_measure=[[(2 * TICK, 1)], [(0, 1)]],
    )
    chart = chart_from_song(song, Path("x.gp5"))
    times = [n.time for n in chart.notes]
    assert times == sorted(times)


# --- lane mapping -------------------------------------------------------------


def test_lane_is_string_minus_one() -> None:
    # Each string at its own tick, otherwise this is one 6-note chord and gets
    # collapsed to a single lane. Use collapse=False to keep them all.
    song = make_song(notes_per_measure=[[(i * TICK, i + 1) for i in range(6)]])
    chart = chart_from_song(song, Path("x.gp5"), collapse=False)
    assert sorted({n.lane for n in chart.notes}) == [0, 1, 2, 3, 4, 5]
    assert sorted({n.string for n in chart.notes}) == [1, 2, 3, 4, 5, 6]


def test_fret_is_carried_but_lane_is_what_counts() -> None:
    song = make_song(notes_per_measure=[[(0, 6)]])
    chart = chart_from_song(song, Path("x.gp5"))
    note = chart.notes[0]
    assert (note.string, note.lane, note.fret) == (6, 5, 3)


def test_out_of_range_strings_are_dropped() -> None:
    song = make_song(notes_per_measure=[[(0, 0), (0, 7), (0, 1)]])
    chart = chart_from_song(song, Path("x.gp5"))
    assert [n.string for n in chart.notes] == [1]


def test_rest_notes_are_skipped() -> None:
    song = make_song(measure_count=1)
    measure = song.tracks[0].measures[0]
    voice = measure.voices[0]
    beat = guitarpro.Beat(voice, start=0)
    rest = guitarpro.Note(beat, value=0, string=1)
    rest.type = guitarpro.NoteType.rest
    beat.notes.append(rest)
    voice.beats.append(beat)

    normal = guitarpro.Beat(voice, start=TICK)
    real = guitarpro.Note(normal, value=1, string=1)
    real.type = guitarpro.NoteType.normal
    normal.notes.append(real)
    voice.beats.append(normal)

    chart = chart_from_song(song, Path("x.gp5"))
    assert chart.note_count == 1


# --- repeats ------------------------------------------------------------------


def test_repeat_doubles_the_section_notes() -> None:
    """|: a :| with a note in the repeated measure yields it twice."""
    song = make_song(
        measure_count=2,
        notes_per_measure=[[(0, 1)], []],
        header_kwargs=[{"isRepeatOpen": True, "repeatClose": 1}, {}],
    )
    chart = chart_from_song(song, Path("x.gp5"))
    assert chart.note_count == 2, "one written note, played twice"
    # The second pass is pushed forward by the repeated bar's own length (2.0s at
    # 4/4, 120bpm) -- a repeat is a real replay, not a doubled hit.
    assert [n.time for n in chart.notes] == pytest.approx([0.0, 2.0])
    assert chart.uses_repeats is True


def test_repeated_section_times_are_offset() -> None:
    """The second pass must be pushed forward by the section's length."""
    song = make_song(
        measure_count=2,
        notes_per_measure=[[(0, 1)], []],
        header_kwargs=[{"isRepeatOpen": True, "repeatClose": 1}, {}],
    )
    chart = chart_from_song(song, Path("x.gp5"))
    first, second = chart.notes
    assert second.time - first.time == pytest.approx(2.0), "one 4/4 bar at 120bpm"


def test_bar_lines_follow_the_unrolled_sequence() -> None:
    song = make_song(
        measure_count=2,
        notes_per_measure=[[(0, 1)], []],
        header_kwargs=[{"isRepeatOpen": True, "repeatClose": 1}, {}],
    )
    chart = chart_from_song(song, Path("x.gp5"))
    # 2 written measures, first repeated -> 3 bars on the highway.
    assert len(chart.bar_lines) == 3
    assert [b.time for b in chart.bar_lines] == pytest.approx([0.0, 2.0, 4.0])


def test_repeat_free_chart_has_no_warning() -> None:
    chart = chart_from_song(make_song(notes_per_measure=[[(0, 1)]]), Path("x.gp5"))
    assert chart.uses_repeats is False
    assert chart.warnings == ()

# --- track classification and selection ----------------------------------------


def build_track(song, number, name, *, instrument, strings=6, percussion=False, clef=None):
    track = guitarpro.Track(song, number=number, name=name)
    track.channel.instrument = instrument
    track.isPercussionTrack = percussion
    track.clefTranspose = clef
    tunings = {6: [(1, 64), (2, 59), (3, 55), (4, 50), (5, 45), (6, 40)],
               4: [(1, 43), (2, 38), (3, 33), (4, 28)]}
    track.strings = [guitarpro.GuitarString(n, v) for n, v in tunings[strings]]
    return track


@pytest.mark.parametrize(
    "instrument,expected",
    [
        (0, TrackKind.OTHER),    # Lead/piano
        (24, TrackKind.GUITAR),  # Acoustic Guitar (nylon)
        (25, TrackKind.GUITAR),  # Acoustic Guitar (steel)
        (29, TrackKind.GUITAR),  # Electric Guitar (clean)
        (31, TrackKind.GUITAR),  # Electric Guitar (harmonics)
        (32, TrackKind.BASS),
        (35, TrackKind.BASS),    # Electric Bass (finger)
        (87, TrackKind.OTHER),   # a vocal line in the real tab
    ],
)
def test_classify_track_by_gm_program(instrument: int, expected: TrackKind) -> None:
    song = guitarpro.Song(measureHeaders=headers(1), tempo=120)
    track = build_track(song, 1, "T", instrument=instrument)
    assert classify_track(track) is expected


def test_percussion_flag_wins_over_gm_program() -> None:
    song = guitarpro.Song(measureHeaders=headers(1), tempo=120)
    track = build_track(song, 1, "Drums", instrument=25, percussion=True)
    assert classify_track(track) is TrackKind.DRUMS


def test_clef_12_identifies_bass_even_with_a_guitar_program() -> None:
    """Hand-written tabs often have the wrong GM program; clef is independent."""
    song = guitarpro.Song(measureHeaders=headers(1), tempo=120)
    track = build_track(song, 1, "Bass", instrument=25, strings=4, clef=12)
    assert classify_track(track) is TrackKind.BASS


def test_only_guitar_tracks_are_playable() -> None:
    song = guitarpro.Song(measureHeaders=headers(1), tempo=120)
    song.tracks = [
        build_track(song, 1, "Vocals", instrument=87),
        build_track(song, 2, "Lead", instrument=25),
        build_track(song, 3, "Bass", instrument=35, strings=4),
        build_track(song, 4, "Drums", instrument=0, percussion=True),
    ]
    assert [t.name for t in playable_tracks(song)] == ["Lead"]


def test_trackkind_selectable() -> None:
    assert TrackKind.GUITAR.selectable
    assert not TrackKind.BASS.selectable
    assert not TrackKind.DRUMS.selectable
    assert not TrackKind.OTHER.selectable


# --- rejections ---------------------------------------------------------------


def test_empty_chart_is_rejected() -> None:
    with pytest.raises(ChartError, match="no playable notes|no guitar track"):
        chart_from_song(make_song(measure_count=2), Path("x.gp5"))


def test_missing_file_is_a_chart_error() -> None:
    with pytest.raises(ChartError, match="no such file"):
        chart_from_gp5("definitely-not-here.gp5")


def test_corrupt_file_is_a_chart_error_not_a_crash() -> None:
    """A junk file must surface as ChartError, never as GPException."""
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".gp5", delete=False) as handle:
        handle.write(b"this is definitely not a Guitar Pro file")
        name = handle.name

    with pytest.raises(ChartError):
        chart_from_gp5(name)


def test_tempo_change_is_rejected() -> None:
    song = make_song(measure_count=2, notes_per_measure=[[(0, 1)], []])
    beat = guitarpro.Beat(song.tracks[0].measures[1].voices[0], start=0)
    change = guitarpro.MixTableChange()
    change.tempo = 90
    beat.effect.mixTableChange = change
    song.tracks[0].measures[1].voices[0].beats.append(beat)

    with pytest.raises(ChartError, match="changes tempo"):
        chart_from_song(song, Path("x.gp5"))


def test_mix_table_change_without_tempo_is_fine() -> None:
    """A volume change is not a tempo change and must not be rejected."""
    song = make_song(measure_count=2, notes_per_measure=[[(0, 1)], []])
    beat = guitarpro.Beat(song.tracks[0].measures[1].voices[0], start=0)
    change = guitarpro.MixTableChange()
    change.tempo = song.tempo  # same tempo: harmless
    beat.effect.mixTableChange = change
    song.tracks[0].measures[1].voices[0].beats.append(beat)

    chart = chart_from_song(song, Path("x.gp5"))
    assert chart.tempo == 120


# --- derived properties -------------------------------------------------------


def test_duration_and_density() -> None:
    song = make_song(measure_count=2, notes_per_measure=[[(0, 1)], [(0, 1)]])
    chart = chart_from_song(song, Path("x.gp5"))
    assert chart.duration == pytest.approx(2.0)
    assert chart.note_count == 2
    assert chart.notes_per_second == pytest.approx(1.0)


def test_notes_in_lane_filters() -> None:
    song = make_song(measure_count=1, notes_per_measure=[[(0, 1), (TICK, 4)]])
    chart = chart_from_song(song, Path("x.gp5"))
    assert len(chart.notes_in_lane(0)) == 1
    assert len(chart.notes_in_lane(3)) == 1
    assert len(chart.notes_in_lane(5)) == 0


def test_chart_is_immutable() -> None:
    chart = chart_from_song(make_song(notes_per_measure=[[(0, 1)]]), Path("x.gp5"))
    with pytest.raises(Exception):
        chart.tempo = 200  # type: ignore[misc]


def test_empty_duration_is_zero_not_an_error() -> None:
    chart = Chart(
        path=Path("x.gp5"),
        title="t",
        artist="a",
        tempo=120,
        time_signature=(4, 4),
        track_name="Track 1",
        notes=(),
    )
    assert chart.duration == 0.0
    assert chart.notes_per_second == 0.0
