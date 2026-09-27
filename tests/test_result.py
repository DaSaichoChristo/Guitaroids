"""Tests for the finished-attempt record and the per-song best.

Pure: no Qt, no device, no clock. A `Result` is built from a `GameState` and nothing
else, so every case here is arithmetic on a tally -- which is the point. The screen that
renders one is in `test_results.py`, and the game screen that publishes one in
`test_game_screen.py`; this file covers the number itself.

**The distinction this file exists to protect:** `GameState.accuracy` is hits over
notes *judged so far*, for a live HUD. `Result.accuracy` is hits over the *whole
song*. They are equal only once every note has been judged, and a change that made
one delegate to the other would silently break the HUD -- at three seconds in, a
correct player would read near-zero accuracy.
"""

from __future__ import annotations

import pytest
from songbuild import make_chart

from guitaroids.session.judge import GameState, Verdict
from guitaroids.session.result import Result
from guitaroids.settings import Settings


def _state(notes: int = 8) -> GameState:
    """A chart of single notes, one per second, on six lanes."""
    return GameState(make_chart([(float(i), i % 6, 0) for i in range(notes)], collapse=False))


def _finish(state: GameState, lane: int, when: float) -> None:
    state.press(lane, when)


# --- building it ---------------------------------------------------------------


def test_a_full_combo_is_a_full_combo() -> None:
    state = _state(4)
    for index in range(4):
        _finish(state, index % 6, float(index))
    result = Result.from_state(state, title="Synthetic")
    assert result.perfect == 4
    assert result.miss == 0
    assert result.notes_hit == 4
    assert result.accuracy == pytest.approx(1.0)
    assert result.is_full_combo is True
    assert result.completed is True


def test_a_miss_is_not_a_full_combo_however_well_the_rest_went() -> None:
    state = _state(4)
    for index in range(3):  # three of four; the fourth is never played
        _finish(state, index % 6, float(index))
    state.update(10.0)  # expire the one nobody hit
    result = Result.from_state(state, title="Synthetic")
    assert result.miss == 1
    assert result.accuracy == pytest.approx(0.75)
    assert result.is_full_combo is False


def test_a_good_note_still_counts_as_a_hit() -> None:
    """Good is inside the window, so it is a note played -- just not well timed."""
    state = _state(1)
    _finish(state, 0, 0.0 + 0.060)  # past PERFECT_SECONDS, inside GOOD_SECONDS
    result = Result.from_state(state, title="Synthetic")
    assert result.perfect == 0
    assert result.good == 1
    assert result.notes_hit == 1
    assert result.is_full_combo is True, "a full combo is about playing every note"


def test_strays_are_counted_but_are_not_notes() -> None:
    """A stray is the app hearing something the chart never asked for.

    In neither side of the ratio, so a microphone that mis-hears a string cannot make
    accuracy look worse -- or better -- than the playing.
    """
    state = _state(2)
    _finish(state, 0, 0.0)
    _finish(state, 1, 1.0)
    state.press_pitch(127, 1.5)  # a pitch the chart does not contain
    result = Result.from_state(state, title="Synthetic")
    assert result.stray == 1
    assert result.note_count == 2
    assert result.notes_hit == 2
    assert result.accuracy == pytest.approx(1.0), "a stray is not a missed note"


# --- the two accuracies are not the same number ---------------------------------


def test_the_live_accuracy_and_the_final_one_differ_mid_song() -> None:
    """The reason `Result` computes its own rather than reading `GameState`'s.

    At one note in, the live readout is 100% -- everything that has come up was hit.
    Over the whole song it is 1/8. A results screen printing 100% for a run that hit
    one note of eight would be the bug this test exists to prevent.
    """
    state = _state(8)
    _finish(state, 0, 0.0)
    assert state.accuracy == pytest.approx(1.0)
    assert Result.from_state(state, title="Synthetic").accuracy == pytest.approx(1 / 8)


def test_they_agree_once_every_note_is_judged() -> None:
    state = _state(4)
    for index in range(4):
        _finish(state, index % 6, float(index))
    result = Result.from_state(state, title="Synthetic")
    assert result.accuracy == pytest.approx(state.accuracy)


def test_an_empty_chart_does_not_divide_by_zero() -> None:
    from dataclasses import replace

    # `make_chart([])` refuses, which is correct -- there is no such tab. An empty
    # chart still reaches the results screen when a library scan lands mid-copy, so it
    # is built by stripping the notes off a real one.
    stripped = replace(make_chart([(0.0, 0, 0)], collapse=False), notes=())
    state = GameState(stripped)
    result = Result.from_state(state, title="Nothing")
    assert result.accuracy == 0.0
    assert result.is_full_combo is False
    assert result.completed is True, "there was nothing to play, so nothing is missing"


# --- identity and context ------------------------------------------------------


def test_the_title_falls_back_to_the_track_name() -> None:
    """A tab with no title header is common, and a blank heading helps nobody."""
    state = _state(1)
    result = Result.from_state(state, title="", track_name="Solo Guitar 1")
    assert result.title == "Solo Guitar 1"


def test_the_run_records_the_tempo_it_was_played_at() -> None:
    """Two runs of one song at 76 and 57 BPM are not comparable on accuracy alone."""
    state = _state(1)
    result = Result.from_state(state, title="Slow", bpm=57.0, rate=0.75)
    assert result.bpm == pytest.approx(57.0)
    assert result.rate == pytest.approx(0.75)


def test_a_result_is_frozen() -> None:
    """The screen and the settings both hold one, and neither may edit it."""
    result = Result.from_state(_state(1), title="Frozen")
    with pytest.raises(AttributeError):
        result.perfect = 99  # type: ignore[misc]


def test_an_abandoned_run_says_so() -> None:
    """A result built from a state that was walked away from is not `completed`."""
    state = _state(8)
    _finish(state, 0, 0.0)
    result = Result.from_state(state, title="Left early")
    assert result.completed is False
    assert result.accuracy == pytest.approx(1 / 8)


# --- the per-song best ---------------------------------------------------------


def test_the_first_run_is_always_a_new_best() -> None:
    """Including one that scored nothing: it has nothing to beat.

    Tested against a *missing* key rather than 0.0, which is why the first draft of
    `record_accuracy_for` reported "not a best" for a first run of 0% while still
    storing it -- a contradiction the screen would have had to explain.
    """
    settings = Settings()
    assert settings.best_accuracy_for("hotel") == 0.0
    is_new, previous = settings.record_accuracy_for("hotel", 0.0)
    assert is_new is True
    assert previous is None, "nothing was there to beat"


def test_a_worse_run_does_not_overwrite_the_best() -> None:
    settings = Settings()
    settings.record_accuracy_for("hotel", 0.94)
    is_new, previous = settings.record_accuracy_for("hotel", 0.51)
    assert is_new is False
    assert previous == pytest.approx(0.94), "and it names the best it failed to beat"
    assert settings.best_accuracy_for("hotel") == pytest.approx(0.94)


def test_the_same_run_twice_is_not_a_new_best() -> None:
    """Otherwise a screen that congratulates you would be teaching you to ignore it."""
    settings = Settings()
    assert settings.record_accuracy_for("hotel", 0.94)[0] is True
    assert settings.record_accuracy_for("hotel", 0.94)[0] is False
    assert settings.best_accuracy_for("hotel") == pytest.approx(0.94)


def test_the_best_is_per_song() -> None:
    settings = Settings()
    settings.record_accuracy_for("hotel", 0.94)
    settings.record_accuracy_for("sweet_child", 0.71)
    assert settings.best_accuracy_for("hotel") == pytest.approx(0.94)
    assert settings.best_accuracy_for("sweet_child") == pytest.approx(0.71)
    assert settings.best_accuracy_for("never_played") == 0.0


def test_an_empty_slug_records_nothing() -> None:
    """A per-song store with one nameless entry is a bug waiting to be read."""
    settings = Settings()
    assert settings.record_accuracy_for("", 0.99) == (False, None)
    assert settings.song_best_accuracy == {}


def test_out_of_range_accuracies_are_clamped_not_discarded() -> None:
    settings = Settings()
    settings.record_accuracy_for("a", 1.4)
    settings.record_accuracy_for("b", -0.5)  # clamped to 0.0, stored as a first run
    assert settings.best_accuracy_for("a") == pytest.approx(1.0)
    assert settings.best_accuracy_for("b") == 0.0


def test_the_best_survives_a_round_trip(tmp_path) -> None:
    path = tmp_path / "settings.json"
    Settings().record_accuracy_for("hotel", 0.94)
    Settings(song_best_accuracy={"hotel": 0.94}).save(path)
    assert Settings.load(path).best_accuracy_for("hotel") == pytest.approx(0.94)


def test_a_stored_value_outside_the_range_is_clamped_on_load() -> None:
    """A hand-edited 1.4 is nonsense, but it is obviously meant to be the best run."""
    loaded = Settings.from_dict({"version": 5, "song_best_accuracy": {"a": 1.4, "b": -2.0}})
    assert loaded.best_accuracy_for("a") == pytest.approx(1.0)
    assert loaded.best_accuracy_for("b") == 0.0


def test_a_non_numeric_stored_value_is_dropped_not_fatal() -> None:
    loaded = Settings.from_dict({"version": 5, "song_best_accuracy": {"a": "ninety"}})
    assert loaded.best_accuracy_for("a") == 0.0


def test_a_whole_song_with_chords_expanded_is_the_denominator() -> None:
    """Full chords are the default (§21), so the denominator is every note, not onsets."""
    chart = make_chart([(0.0, 0, 0), (0.0, 1, 2), (0.0, 2, 2)], collapse=False)
    state = GameState(chart)
    for note in chart.notes:
        state.press(note.lane, note.time)
    result = Result.from_state(state, title="Chord")
    assert result.note_count == 3
    assert result.perfect == 3
    assert result.accuracy == pytest.approx(1.0)
    assert Verdict.STRAY not in result.__slots__
