"""Tests for judging and game state.

The windows are the thing worth pinning, and specifically at their **boundaries**:
±35ms and ±80ms are the difference between Perfect and Good, and an off-by-one in
either direction is invisible in normal play and wrong on every frame.

The note times come from ``songbuild.make_chart`` at tempo 60, where one beat is
exactly one second, so a note asked for at 2.0 is at 2.0 and a press can be placed
0.035s from it with no floating-point guesswork about what the tempo did.
"""

from __future__ import annotations

import pytest
from songbuild import make_chart

from guitaroids.session.judge import (
    GOOD_SECONDS,
    MISS_SECONDS,
    PERFECT_SECONDS,
    GameState,
    Judgement,
    Verdict,
    verdict_for,
)

#: Comfortably inside each window, so a test failure means the logic changed rather
#: than that the arithmetic rounded.
CLEAR_PERFECT = 0.010
CLEAR_GOOD = 0.050
CLEAR_MISS = 0.100


@pytest.fixture()
def state() -> GameState:
    """One note per lane, all at t=2.0.

    One lane per note so a press in one lane cannot be satisfied by another's note,
    which is the case most likely to hide a bug in the lane search. ``collapse=False``
    because six notes on one onset would otherwise be merged into a single note by
    the chord rule -- which silently reduces the fixture to one lane and makes the
    lane tests pass for the wrong reason.
    """
    return GameState(make_chart([(2.0, lane, lane) for lane in range(6)], collapse=False))


# --- the windows --------------------------------------------------------------


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (0.0, Verdict.PERFECT),
        (CLEAR_PERFECT, Verdict.PERFECT),
        (-CLEAR_PERFECT, Verdict.PERFECT),
        (CLEAR_GOOD, Verdict.GOOD),
        (-CLEAR_GOOD, Verdict.GOOD),
        (CLEAR_MISS, Verdict.MISS),
        (-CLEAR_MISS, Verdict.MISS),
    ],
)
def test_verdict_for_a_delta(delta: float, expected: Verdict) -> None:
    assert verdict_for(delta) is expected


@pytest.mark.parametrize("boundary", [PERFECT_SECONDS, GOOD_SECONDS])
def test_the_hit_windows_are_inclusive_at_their_edges(boundary: float) -> None:
    """Exactly on a window edge counts as inside it, not outside.

    A note hit at exactly +35ms is Perfect, because the window is "+/-35ms". Getting
    this wrong shifts every judgement at the edges, which no human notices and a
    test does immediately.
    """
    inside = verdict_for(boundary) if boundary == PERFECT_SECONDS else Verdict.GOOD
    assert verdict_for(boundary) is inside
    assert verdict_for(-boundary) is inside


def test_the_miss_boundary_is_where_a_note_expires_not_a_hit_window() -> None:
    """140ms is the end of a note's life, not a third hit window.

    ``verdict_for`` has no window out there by design: a press that far off is a
    stray, and the note is missed by expiry in :meth:`GameState.update`.
    """
    assert verdict_for(MISS_SECONDS) is Verdict.MISS
    assert verdict_for(MISS_SECONDS + 0.001) is Verdict.MISS


def test_just_outside_perfect_is_good() -> None:
    assert verdict_for(PERFECT_SECONDS + 0.001) is Verdict.GOOD


def test_just_outside_good_is_a_miss() -> None:
    assert verdict_for(GOOD_SECONDS + 0.001) is Verdict.MISS


def test_the_windows_are_ordered() -> None:
    assert PERFECT_SECONDS < GOOD_SECONDS < MISS_SECONDS


# --- pressing -----------------------------------------------------------------


def test_a_dead_on_press_is_perfect(state: GameState) -> None:
    judgement = state.press(3, 2.0)
    assert judgement is not None
    assert judgement.verdict is Verdict.PERFECT
    assert judgement.lane == 3
    assert judgement.note_time == pytest.approx(2.0)
    assert judgement.delta_seconds == pytest.approx(0.0)


def test_the_delta_is_reported_signed(state: GameState) -> None:
    """Negative is early. The screen shows it, so the sign has to be right."""
    early = state.press(0, 2.0 - CLEAR_GOOD)
    assert early.delta_seconds == pytest.approx(-CLEAR_GOOD)
    late = GameState(make_chart([(2.0, 0, 0)])).press(0, 2.0 + CLEAR_GOOD)
    assert late.delta_seconds == pytest.approx(CLEAR_GOOD)


@pytest.mark.parametrize("lane", range(6))
def test_every_lane_can_be_pressed(state: GameState, lane: int) -> None:
    assert state.press(lane, 2.0).verdict is Verdict.PERFECT


def test_a_lane_with_no_note_is_a_stray(state: GameState) -> None:
    """Far from any note in that lane, and a stray is never penalised."""
    state.press(0, 2.0)
    stray = state.press(5, 30.0)
    assert stray.verdict is Verdict.STRAY
    assert stray.is_penalised is False
    assert state.misses == 0


def test_a_stray_is_counted_separately(state: GameState) -> None:
    """Tracked, not penalised -- so a player can see they are faking."""
    for _ in range(3):
        state.press(5, 30.0)
    assert state.strays == 3
    assert state.misses == 0
    assert state.counts()[Verdict.STRAY] == 3


def test_a_stray_does_not_consume_a_note(state: GameState) -> None:
    """Pressing nothing must not stop the real note being hit afterwards."""
    state.press(5, 30.0)
    assert state.press(0, 2.0).verdict is Verdict.PERFECT
    assert state.resolved == 1


def test_a_note_is_only_judged_once(state: GameState) -> None:
    state.press(2, 2.0)
    second = state.press(2, 2.0)
    assert second.verdict is Verdict.STRAY, "the note was already resolved"
    assert state.resolved == 1
    assert state.counts()[Verdict.PERFECT] == 1


def test_the_nearest_note_in_the_lane_wins() -> None:
    """Two notes close together: the press must land on its own, not the neighbour."""
    state = GameState(make_chart([(2.00, 0, 0), (2.05, 0, 1)], tempo=60))
    judgement = state.press(0, 2.048)
    assert judgement.note_time == pytest.approx(2.05)
    assert judgement.delta_seconds == pytest.approx(-0.002)


def test_a_press_outside_the_miss_window_is_a_stray(state: GameState) -> None:
    """Beyond 140ms it is not a mistimed hit, it is a press with nothing under it."""
    assert state.press(0, 2.0 + MISS_SECONDS + 0.05).verdict is Verdict.STRAY
    assert state.misses == 0


def test_a_press_inside_the_miss_window_resolves_the_note_as_a_miss(
    state: GameState,
) -> None:
    """The band between GOOD and MISS is a *miss*, not a stray plus an expired note.

    Counting one mistimed hit as both would report two failures for a single event.
    """
    judgement = state.press(0, 2.0 + CLEAR_MISS)
    assert judgement.verdict is Verdict.MISS
    assert state.misses == 1
    assert state.strays == 0, "a mistimed hit is not also a stray"
    assert state.resolved == 1


def test_a_lane_out_of_range_is_rejected(state: GameState) -> None:
    for lane in (-1, 6, 99):
        with pytest.raises(ValueError, match="lane"):
            state.press(lane, 2.0)


# --- expiry -------------------------------------------------------------------


def test_a_note_past_its_miss_window_is_missed(state: GameState) -> None:
    """All six notes share t=2.0, so they all expire together."""
    missed = state.update(2.0 + MISS_SECONDS + 0.01)
    assert [j.verdict for j in missed] == [Verdict.MISS] * 6
    assert state.misses == 6
    assert state.resolved == 6


def test_a_note_inside_its_window_is_not_missed(state: GameState) -> None:
    assert state.update(2.0 + MISS_SECONDS - 0.01) == []
    assert state.misses == 0


def test_a_hit_note_does_not_also_expire(state: GameState) -> None:
    state.press(1, 2.0)
    remaining = state.update(10.0)
    assert len(remaining) == 5, "the other five expire; the hit one must not"
    assert state.misses == 5
    assert state.resolved == 6


def test_expiry_reports_each_note_once(state: GameState) -> None:
    state.update(10.0)
    assert state.update(20.0) == []
    assert state.misses == 6, "all six notes, counted once each"


def test_expiry_keeps_the_note_it_ran_out(state: GameState) -> None:
    judgement = state.update(2.5)[0]
    assert judgement.note_time == pytest.approx(2.0)
    assert judgement.delta_seconds == pytest.approx(0.5)


# --- tallying -----------------------------------------------------------------


def test_accuracy_is_hits_over_notes_judged_so_far(state: GameState) -> None:
    """A live readout, so a missed note must drag it down.

    Regression: this was ``resolved / note_count``, and since ``resolved`` counts
    misses too, a run where every note was missed reported an accuracy of 1.0.
    """
    state.press(0, 2.0)
    state.press(1, 2.0)
    assert state.accuracy == pytest.approx(1.0), "2 of 2 judged, both hit"
    state.update(2.0 + MISS_SECONDS + 0.01)
    assert state.accuracy == pytest.approx(2 / 6), "4 of 6 judged, all 4 missed"


def test_song_accuracy_is_hits_over_the_whole_chart(state: GameState) -> None:
    """The results-screen figure. Distinct from the live one, on purpose."""
    state.press(0, 2.0)
    assert state.song_accuracy == pytest.approx(1 / 6)
    state.update(2.0 + MISS_SECONDS + 0.01)
    assert state.song_accuracy == pytest.approx(1 / 6)


def test_strays_neither_hit_nor_dilute_the_denominator(state: GameState) -> None:
    """Faking is visible in ``strays`` and changes neither accuracy figure."""
    for _ in range(5):
        state.press(4, 50.0)
    state.press(0, 2.0)
    assert state.resolved == 1, "a stray must not resolve a note"
    assert state.accuracy == pytest.approx(1.0), "1 judged, 1 hit"
    assert state.song_accuracy == pytest.approx(1 / 6)


def test_accuracy_of_an_empty_chart_is_zero() -> None:
    """A chart with no notes is built by stripping one, not by asking for zero.

    ``chart_from_song`` refuses an empty song -- it raises rather than producing a
    chart nothing can play -- so the empty case is a real chart with its notes
    removed.
    """
    from dataclasses import replace

    empty = replace(make_chart([(1.0, 0, 0)]), notes=())
    assert GameState(empty).accuracy == 0.0


def test_counts_start_at_zero(state: GameState) -> None:
    tally = state.counts()
    assert all(count == 0 for count in tally.values())
    assert set(tally) == set(Verdict)


def test_outstanding_tracks_the_remainder(state: GameState) -> None:
    assert state.outstanding == 6
    state.press(0, 2.0)
    assert state.outstanding == 5
    state.update(2.5)
    assert state.outstanding == 0


# --- verdicts -----------------------------------------------------------------


def test_perfect_and_good_are_hits() -> None:
    assert Judgement(Verdict.PERFECT, 0, 0.0).is_hit
    assert Judgement(Verdict.GOOD, 0, 0.0).is_hit
    assert not Judgement(Verdict.MISS, 0, 0.0).is_hit


def test_only_a_miss_is_penalised() -> None:
    for verdict in Verdict:
        penalty = Judgement(verdict, 0, 0.0).is_penalised
        assert penalty is (verdict is Verdict.MISS), verdict


# --- purity -------------------------------------------------------------------


def test_judging_needs_no_qt() -> None:
    """The screen drives this, and the widget is driven by it. Neither is required."""
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import guitaroids.session.judge, sys;"
            "bad=[n for n in ('PySide6','cv2') if n in sys.modules];"
            "print(','.join(bad) or 'clean')",
        ],
        capture_output=True,
        text=True,
        cwd=str(root),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "clean"


# --- chords (§21) -------------------------------------------------------------
#
# Full chords are the shipped default now, so "a chord is playable" is no longer a
# curiosity: it is the main path. `GameState.press` resolves one lane at a time and
# each note is independent, so a six-note chord should be six simultaneous presses
# and six PERFECTs. This is the test that says so, because the alternative -- a
# chord silently costing five misses -- is not a thing anyone would notice by
# playing until they had already judged themselves bad at their own instrument.


@pytest.fixture()
def chord_state() -> GameState:
    """One six-note chord at t=2.0, on all six strings."""
    return GameState(make_chart([(2.0, lane, 5) for lane in range(6)], collapse=False))


def test_a_six_note_chord_is_six_simultaneous_presses(chord_state: GameState) -> None:
    for lane in range(6):
        judgement = chord_state.press(lane, 2.0)
        assert judgement is not None, f"lane {lane} had nothing to hit"
        assert judgement.verdict is Verdict.PERFECT


def test_a_chord_played_completely_leaves_nothing_outstanding(chord_state: GameState) -> None:
    for lane in range(6):
        chord_state.press(lane, 2.0)
    chord_state.update(2.5)  # well past MISS_SECONDS
    assert chord_state.outstanding == 0
    assert chord_state.misses == 0, "a chord you played must not age into a miss"


def test_the_notes_of_one_chord_are_independent(chord_state: GameState) -> None:
    """Hitting three of the six strands costs exactly three misses, not six.

    Worth pinning because the alternative -- one press resolving the whole onset --
    would make a chord *easier* than a single note, and nothing else in the judge
    would notice.
    """
    for lane in range(3):
        chord_state.press(lane, 2.0)
    chord_state.update(2.5)
    assert chord_state.misses == 3
    assert chord_state.accuracy == pytest.approx(0.5)


def test_a_chord_still_counts_as_one_onset_for_the_tally(chord_state: GameState) -> None:
    """`outstanding` is notes, but the *song* is one rhythmic event.

    Not asserted as a behaviour change -- it is a note about what the numbers in
    the HUD now mean. With full chords the note count is three to four times what
    it was, so an accuracy figure is no longer comparable with a collapsed run of
    the same song, and there is no leaderboard to be inconsistent with yet.
    """
    assert chord_state.outstanding == 6
    assert GameState(
        make_chart([(2.0, lane, 5) for lane in range(6)], collapse=True)
    ).outstanding == 1
