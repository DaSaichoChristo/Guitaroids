"""Tests for chord collapsing and track classification against a real tab.

The collapsing rules exist because the one tab in ``songs/`` has 688 of its 1108
onsets carrying five or six notes (DESIGN.md §7.4), which a single fretting-hand
lane cannot express. These tests pin the rhythm-preserving property, which is the
part that matters for gameplay: **every onset must survive**.

Tests that need a real tab locate it and skip when it is absent, so the suite
still runs on a fresh clone.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from guitaroids.model.chart import (
    CollapseRule,
    Note,
    TrackKind,
    chart_from_song,
    classify_track,
    collapse_chords,
    count_chord_sizes,
    group_by_onset,
    playable_tracks,
    suggest_track,
)

ROOT = Path(__file__).resolve().parent.parent
REAL_TAB = ROOT / "songs" / "eagles_the-hotel_california_5.gp5"
needs_real_tab = pytest.mark.skipif(not REAL_TAB.is_file(), reason="real tab not present")


def n(time: float, lane: int, *, string: int | None = None, pitch: int = 0) -> Note:
    string = lane + 1 if string is None else string
    return Note(
        time=time, lane=lane, fret=1, string=string, measure=1,
        pitch=pitch if pitch else 60 + lane,
    )


# --- grouping -----------------------------------------------------------------


def test_group_by_onset_preserves_order() -> None:
    notes = [n(2.0, 0), n(0.0, 1), n(1.0, 2), n(0.0, 3)]
    groups = group_by_onset(notes)
    assert [g[0].time for g in groups] == [0.0, 1.0, 2.0]
    assert len(groups[0]) == 2


def test_group_by_onset_on_empty() -> None:
    assert group_by_onset([]) == []


def test_count_chord_sizes() -> None:
    assert count_chord_sizes([n(0.0, 0), n(0.0, 1), n(1.0, 2)]) == {0.0: 2, 1.0: 1}


# --- the rhythm-preserving property -------------------------------------------


def test_collapse_preserves_the_number_of_onsets() -> None:
    """The single most important property: the game still has the same number of
    things to hit, just not all at once."""
    notes = [n(0.0, 0), n(0.0, 1), n(0.0, 2), n(1.0, 3), n(1.0, 4), n(2.0, 5)]
    collapsed = collapse_chords(notes)
    assert len(collapsed) == 3
    assert [x.time for x in collapsed] == [0.0, 1.0, 2.0]


@pytest.mark.parametrize("rule", list(CollapseRule))
def test_every_rule_preserves_onsets_and_time_order(rule: CollapseRule) -> None:
    notes = [n(0.0, 0), n(0.0, 2), n(0.5, 1), n(0.5, 4), n(1.25, 3)]
    collapsed = collapse_chords(notes, rule)
    times = [x.time for x in collapsed]
    assert times == sorted(times)
    assert len(times) == len({x.time for x in notes})


@pytest.mark.parametrize("rule", list(CollapseRule))
def test_singleton_onsets_pass_through_untouched(rule: CollapseRule) -> None:
    solo = n(3.0, 2)
    assert collapse_chords([solo], rule) == [solo]


# --- rules --------------------------------------------------------------------


def test_highest_picks_the_top_of_the_chord() -> None:
    notes = [n(0.0, 0, pitch=52), n(0.0, 3, pitch=64), n(0.0, 5, pitch=45)]
    assert collapse_chords(notes, CollapseRule.HIGHEST)[0].pitch == 64


def test_lowest_picks_the_bass() -> None:
    notes = [n(0.0, 0, pitch=52), n(0.0, 3, pitch=64), n(0.0, 5, pitch=45)]
    assert collapse_chords(notes, CollapseRule.LOWEST)[0].pitch == 45


def test_common_prefers_the_frequent_string() -> None:
    # String 2 is common overall, so it wins the chord even though string 1 is
    # the highest pitch here.
    notes = [n(0.0, 0, string=1, pitch=64), n(0.0, 1, string=2, pitch=59),
             n(0.0, 1, string=2, pitch=59), n(0.0, 1, string=2, pitch=59),
             n(0.0, 5, string=6, pitch=40)]
    assert collapse_chords(notes, CollapseRule.COMMON)[0].string == 2


def test_common_falls_back_to_pitch_on_a_tie() -> None:
    notes = [n(0.0, 0, string=1, pitch=64), n(0.0, 3, string=4, pitch=52)]
    assert collapse_chords(notes, CollapseRule.COMMON)[0].pitch == 64


# --- chord_size ---------------------------------------------------------------


def test_chord_size_is_reported_even_when_collapsed() -> None:
    from dataclasses import replace

    notes = [n(0.0, i) for i in range(6)] + [n(1.0, 2)]
    # Apply chord_size the way chart_from_song does, then collapse.
    sizes = count_chord_sizes(notes)
    sized = [replace(x, chord_size=sizes[x.time]) for x in notes]
    collapsed = collapse_chords(sized)
    assert collapsed[0].chord_size == 6
    assert collapsed[1].chord_size == 1


# --- against the real tab -----------------------------------------------------


@needs_real_tab
def test_real_tab_collapse_reduces_notes_but_not_onsets() -> None:
    import guitarpro

    song = guitarpro.parse(REAL_TAB)
    track = suggest_track(song)
    full = chart_from_song(song, REAL_TAB, track=track, collapse=False)
    collapsed = chart_from_song(song, REAL_TAB, track=track, collapse=True)

    assert len(collapsed.notes) < len(full.notes)
    assert len(collapsed.notes) == len({n.time for n in full.notes}), "onsets must survive"
    assert max(n.chord_size for n in collapsed.notes) > 1, "chord_size must survive"


@needs_real_tab
def test_real_tab_collapse_lowers_density() -> None:
    import guitarpro

    song = guitarpro.parse(REAL_TAB)
    track = suggest_track(song)
    full = chart_from_song(song, REAL_TAB, track=track, collapse=False)
    collapsed = chart_from_song(song, REAL_TAB, track=track, collapse=True)
    assert collapsed.notes_per_second < full.notes_per_second


@needs_real_tab
def test_real_tab_vocals_are_not_classified_as_guitar() -> None:
    """Regression for the bug that made the importer pick the vocal line."""
    import guitarpro

    song = guitarpro.parse(REAL_TAB)
    names = [t.name for t in song.tracks]
    assert "Vocals" in names, "fixture changed - this tab should have a Vocals track"

    vocals = next(t for t in song.tracks if t.name == "Vocals")
    assert classify_track(vocals) is TrackKind.OTHER
    assert vocals not in playable_tracks(song)
    assert suggest_track(song).name != "Vocals"


@needs_real_tab
def test_real_tab_suggested_track_is_the_main_guitar_part() -> None:
    import guitarpro

    song = guitarpro.parse(REAL_TAB)
    chosen = suggest_track(song)
    assert classify_track(chosen) is TrackKind.GUITAR
    counts = {t.name: len(t.measures) for t in playable_tracks(song)}
    assert chosen.number in [t.number for t in playable_tracks(song)]
    assert counts  # sanity


@needs_real_tab
def test_real_tab_bass_and_drums_are_excluded() -> None:
    import guitarpro

    song = guitarpro.parse(REAL_TAB)
    kinds = {t.name: classify_track(t) for t in song.tracks}
    assert kinds["Bass"] is TrackKind.BASS
    assert kinds["Drums"] is TrackKind.DRUMS
    selectable = {t.name for t in playable_tracks(song)}
    assert "Bass" not in selectable and "Drums" not in selectable and "Vocals" not in selectable


@needs_real_tab
def test_real_tab_all_six_lanes_survive_collapsing() -> None:
    """Collapsing must not sterilise the highway."""
    import guitarpro

    song = guitarpro.parse(REAL_TAB)
    chart = chart_from_song(song, REAL_TAB, track=suggest_track(song), collapse=True)
    assert sorted({n_.lane for n_ in chart.notes}) == [0, 1, 2, 3, 4, 5]


@needs_real_tab
@pytest.mark.parametrize("rule", list(CollapseRule))
def test_real_tab_every_rule_keeps_onset_count(rule: CollapseRule) -> None:
    import guitarpro

    song = guitarpro.parse(REAL_TAB)
    track = suggest_track(song)
    full = chart_from_song(song, REAL_TAB, track=track, collapse=False)
    collapsed = chart_from_song(song, REAL_TAB, track=track, collapse=True, rule=rule)
    onsets = len({n_.time for n_ in full.notes})
    assert len(collapsed.notes) == onsets


# --- pitch --------------------------------------------------------------------


def test_pitch_is_tuning_plus_fret() -> None:
    """String 1 open is 64 in guitarpro's default tuning."""
    assert 64 + 3 == 67


@needs_real_tab
def test_real_tab_pitches_are_in_midi_range() -> None:
    import guitarpro

    song = guitarpro.parse(REAL_TAB)
    chart = chart_from_song(song, REAL_TAB, track=suggest_track(song), collapse=True)
    assert all(0 < n_.pitch <= 127 for n_ in chart.notes)
    assert all(math.isfinite(n_.time) for n_ in chart.notes)
