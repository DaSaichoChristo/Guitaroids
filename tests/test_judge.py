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


# --- press_pitch: the microphone's way in (§24.2, §29.4) ----------------------
#
# A detected fundamental does not identify a lane. Measured on the real library, 13 of
# 24 distinct pitches are reachable on more than one string and MIDI 49 is on three, so
# matching a detected pitch to a single lane would be wrong about half the time in two
# of the three tabs -- and never wrong in the third, which is how it would have shipped.


def test_a_detected_pitch_hits_the_note_the_song_asked_for() -> None:
    chart = make_chart([(2.0, 0, 0)], tempo=60, collapse=False)
    state = GameState(chart)
    pitch = chart.notes[0].pitch
    judgement = state.press_pitch(pitch, 2.0)
    assert judgement is not None
    assert judgement.lane == 0, "the chart's lane, not a guess at the string"
    assert judgement.pitch == pitch
    assert judgement.verdict is Verdict.PERFECT


def test_a_pitch_on_three_lanes_resolves_to_the_nearest_note() -> None:
    """The real case: one pitch, three strings, and pitch alone cannot say which.

    The library's tuning is dropped, which is how MIDI 49 lands on three lanes there.
    This builds the same shape in the standard tuning, where MIDI 50 is reachable as
    open D (lane 3), A-string 5th fret (lane 4) and low-E 10th fret (lane 5) -- so the
    ambiguity is a property of the instrument, not of one odd tab.
    """
    chart = make_chart(
        [(1.0, 3, 0), (2.0, 4, 5), (3.0, 5, 10)], tempo=60, collapse=False
    )
    pitches = {n.pitch for n in chart.notes}
    assert len(pitches) == 1, f"expected one shared pitch, got {pitches}"
    pitch = pitches.pop()

    state = GameState(chart)
    assert state.press_pitch(pitch, 1.0).lane == 3
    assert state.press_pitch(pitch, 2.0).lane == 4
    assert state.press_pitch(pitch, 3.0).lane == 5
    assert state.misses == 0


def test_measured_overlap_on_the_real_library_is_not_a_rare_edge_case() -> None:
    """Why the pitch index exists, counted rather than asserted in prose.

    A filter bank or a lane guess would be right about half the pitches in two of the
    three tabs -- and perfectly right in the third, so it would have shipped.
    """
    from pathlib import Path

    from guitaroids.songlib import scan_library

    library = scan_library(Path("songs"), collapse=False)
    shared = total = 0
    for entry in library.entries:
        chart = entry.chart_for(entry.tracks[0].number, collapse=False)
        if chart is None:
            continue
        lanes: dict[int, set[int]] = {}
        for note in chart.notes:
            lanes.setdefault(note.pitch, set()).add(note.lane)
        total += len(lanes)
        shared += sum(1 for value in lanes.values() if len(value) > 1)
    if total == 0:
        pytest.skip("no tabs in songs/")
    assert total > 10, f"only {total} distinct pitches; the library looks different"
    assert shared / total > 0.25, (
        f"only {shared} of {total} pitches are ambiguous, so §24.2's argument is "
        "weaker than recorded"
    )


def test_the_two_ways_in_cannot_judge_the_same_note_twice() -> None:
    """They share `by_note`, so a note hit by pitch is closed to a keypress on its lane."""
    chart = make_chart([(2.0, 0, 0)], tempo=60, collapse=False)
    state = GameState(chart)
    first = state.press_pitch(chart.notes[0].pitch, 2.0)
    assert first is not None
    second = state.press(0, 2.0)
    assert second is not None and second.verdict is Verdict.STRAY, "already judged"
    assert state.resolved == 1
    assert state.misses == 0, "one note, not a miss and a hit"


def test_a_pitch_the_song_never_uses_is_a_stray() -> None:
    chart = make_chart([(2.0, 0, 0)], tempo=60, collapse=False)
    state = GameState(chart)
    stray = state.press_pitch(chart.notes[0].pitch + 6, 2.0)
    assert stray is not None and stray.verdict is Verdict.STRAY
    assert state.strays == 1


def test_a_held_note_is_not_counted_as_a_stray_for_every_window() -> None:
    """The subtlety that makes a microphone usable.

    At the 512-sample hop a 400ms note is detected about 35 times. Counting each
    re-detection would add 35 to the tally for one note held down, and the count would
    say nothing about how the player played. So a re-detection is silent.
    """
    chart = make_chart([(2.0, 0, 0)], tempo=60, collapse=False)
    state = GameState(chart)
    pitch = chart.notes[0].pitch

    first = state.press_pitch(pitch, 2.0)
    assert first is not None
    assert state.strays == 0

    # 35 further detections across the note's length, as the estimator would deliver.
    for step in range(1, 36):
        again = state.press_pitch(pitch, 2.0 + step * 0.0116)
        assert again is None, f"re-detection {step} produced {again}"

    assert state.strays == 0
    assert state.resolved == 1
    assert state.misses == 0


def test_a_detection_outside_the_window_is_silent_too() -> None:
    """Before the window, the note has not been missed -- it has not started."""
    chart = make_chart([(2.0, 0, 0)], tempo=60, collapse=False)
    state = GameState(chart)
    assert state.press_pitch(chart.notes[0].pitch, 1.0) is None
    assert state.strays == 0


def test_the_miss_window_still_expires_a_note_nobody_played() -> None:
    """`update` is unchanged and still per-lane, and every note is in exactly one lane."""
    chart = make_chart([(2.0, 0, 0), (3.0, 1, 1)], tempo=60, collapse=False)
    state = GameState(chart)
    fresh = state.update(2.5)
    assert [j.lane for j in fresh] == [0]
    assert fresh[0].verdict is Verdict.MISS
    assert state.misses == 1


def test_a_chart_with_no_pitches_cannot_be_matched_by_pitch() -> None:
    """No tuning means no pitch index, and that is a state to be able to report."""
    from dataclasses import replace

    chart = make_chart([(2.0, 0, 0)], tempo=60, collapse=False)
    stripped = replace(chart, notes=tuple(replace(n, pitch=0) for n in chart.notes))
    state = GameState(stripped)
    assert state.unpitched == 1
    assert state.press_pitch(64, 2.0) is not None, "the only thing left is a stray"
    assert state.strays == 1
    assert state.unpitched == 1, "and it stays reported, because a chart the microphone "
    "can never hit is worth knowing about"


def test_every_note_in_the_real_library_carries_a_pitch() -> None:
    """Measured, not assumed: the pitch index is only worth building if this is true.

    9764 notes over three tabs. If the tuning were ever unavailable the pitch-keyed
    index would silently match nothing, and this is the assertion that says so.
    """
    from pathlib import Path

    from guitaroids.songlib import scan_library

    library = scan_library(Path("songs"), collapse=False)
    if not library.entries:
        pytest.skip("no tabs in songs/")
    total = unpitched = 0
    for entry in library.entries:
        chart = entry.chart_for(entry.tracks[0].number, collapse=False)
        if chart is None:
            continue
        total += len(chart.notes)
        unpitched += sum(1 for note in chart.notes if note.pitch <= 0)
    assert total > 1000, f"only {total} notes checked; the library looks different"
    assert unpitched == 0, f"{unpitched} of {total} notes have no pitch"


# --- timing_delta: measuring an offset the windows cannot see (§39) -----------


@pytest.fixture()
def repeated() -> GameState:
    """Two notes of the *same* pitch, two seconds apart, on the same string.

    Needed because a pitch-keyed search is only interesting where one pitch has more
    than one note, and the shared `state` fixture has one note per lane.
    """
    return GameState(make_chart([(2.0, 0, 0), (4.0, 0, 0)], collapse=False))


def test_timing_delta_measures_a_press_far_outside_the_window(state: GameState) -> None:
    """The whole reason this exists.

    ``press_pitch`` is silent outside the MISS window by design -- a press 300ms late
    resolves nothing, because the note it belonged to has already expired. So a readout
    fed only by judgements is blind at exactly the offsets a player most needs to be
    told about. This asks the question the judgement cannot.
    """
    note = state.chart.notes[0]
    assert state.press_pitch(note.pitch, note.time + 0.300) is None, (
        "precondition: nothing is resolved this far out"
    )
    assert state.timing_delta(note.pitch, note.time + 0.300) == pytest.approx(0.300)


def test_timing_delta_is_negative_when_early(state: GameState) -> None:
    """Signed like `Judgement.delta_seconds`, so the two can be pooled."""
    note = state.chart.notes[0]
    assert state.timing_delta(note.pitch, note.time - 0.250) == pytest.approx(-0.250)


def test_timing_delta_finds_the_nearest_of_several_notes_of_one_pitch(
    repeated: GameState,
) -> None:
    """Two notes of one pitch a second apart, pressed between them: the nearer wins.

    A player between two of the same note is reporting on the one they were reaching
    for, not the one that happens to come first in the chart.
    """
    pitch = repeated.chart.notes[0].pitch
    midpoint = 3.0
    assert repeated.timing_delta(pitch, midpoint) == pytest.approx(midpoint - 2.0)


def test_timing_delta_ignores_notes_already_resolved(repeated: GameState) -> None:
    """A held note is detected over and over, and must not drag the answer backwards.

    Without this, re-detecting the note just hit would report arrival at the *next* one
    and the readout would walk away from the truth the longer a note rang.
    """
    pitch = repeated.chart.notes[0].pitch
    repeated.press_pitch(pitch, 2.0)
    # The 2.0 note is judged and gone, so the only candidate left is the one at 4.0
    # -- which puts this position 2.0s *before* it. Had the judged note still been
    # offered, this would have read +0.0 and looked like perfect timing.
    assert repeated.timing_delta(pitch, 2.0) == pytest.approx(-2.0)


def test_timing_delta_is_none_for_a_pitch_the_song_never_uses(state: GameState) -> None:
    """A pitch with no notes is not a timing observation, it is a stray."""
    used = {note.pitch for note in state.chart.notes}
    assert state.timing_delta(max(used) + 40, 1.0) is None


def test_timing_delta_is_none_once_every_note_of_that_pitch_is_gone(
    repeated: GameState,
) -> None:
    """Nothing left to be late for, so no answer rather than a wrong one."""
    pitch = repeated.chart.notes[0].pitch
    for note in repeated.chart.notes:
        repeated.press_pitch(pitch, note.time)
    assert repeated.timing_delta(pitch, 5.0) is None


def test_an_expiry_is_not_marked_as_pressed(state: GameState) -> None:
    """§39. The flag the timing readout depends on to not count an unplayed note."""
    state.update(2.0 + MISS_SECONDS + 0.001)
    assert state.judgements, "precondition: something expired"
    assert all(not j.pressed for j in state.judgements)
    assert all(j.verdict is Verdict.MISS for j in state.judgements)


def test_a_press_is_marked_as_pressed(state: GameState) -> None:
    note = state.chart.notes[0]
    state.press_pitch(note.pitch, note.time + CLEAR_MISS)
    assert state.judgements[-1].pressed is True
