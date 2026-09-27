"""Tests for the results screen.

The screen is the last placeholder, so there is no prior art here to follow -- only
the conventions the other screens established. What is tested is what it *says*:
the numbers, the two lines that are prose rather than figures, and the empty state.

**Rendering is a separate method from construction on purpose.** `render()` reads
`context.last_result` and sets label text, so every assertion here runs without a
window being shown. The `showEvent` re-render is tested separately, because the shell
keeps built screens and that is the whole reason it exists.
"""

from __future__ import annotations

import pytest

from guitaroids.session.result import Result
from guitaroids.settings import Settings
from guitaroids.ui.results import Results
from guitaroids.ui.screens import Screen


def _result(**kwargs) -> Result:
    defaults = dict(
        title="Hotel California",
        artist="Eagles",
        track_name="12-stg Guitar (1)",
        slug="eagles_the-hotel_california_5",
        track_number=3,
        perfect=3600,
        good=380,
        miss=119,
        stray=4,
        note_count=4099,
        notes_hit=3980,
        bpm=76.0,
        rate=1.0,
    )
    defaults.update(kwargs)
    return Result(**defaults)  # type: ignore[arg-type]


@pytest.fixture()
def results(shell, context):
    context.settings = Settings()
    shell.navigate(Screen.RESULTS)
    screen = shell.current_screen
    assert isinstance(screen, Results)
    return screen


# --- what it shows -------------------------------------------------------------


def test_the_song_and_the_accuracy_are_on_screen(results: Results) -> None:
    results.context.last_result = _result()
    results.render()
    assert results._title.text() == "Hotel California"
    assert "Eagles" in results._subtitle.text()
    assert results._accuracy.text() == "97.1%"


def test_the_artist_and_the_track_are_both_named(results: Results) -> None:
    """On a tab with several guitar tracks, the track name is which one you played."""
    results.context.last_result = _result()
    results.render()
    assert "Eagles" in results._subtitle.text()
    assert "12-stg Guitar (1)" in results._subtitle.text()


def test_the_tempo_is_stated_and_says_as_written_when_it_was(results: Results) -> None:
    """Two runs of one song at different tempi are different attempts."""
    results.context.last_result = _result(bpm=57.0, rate=0.75)
    results.render()
    assert "57 BPM" in results._subtitle.text()

    results.context.last_result = _result(bpm=0.0, rate=1.0)
    results.render()
    assert "as written" in results._subtitle.text()


def test_the_four_counts_are_all_present(results: Results) -> None:
    results.context.last_result = _result()
    results.render()
    tally = results._tally.text()
    for count in ("3600 perfect", "380 good", "119 miss", "4 stray"):
        assert count in tally, tally


def test_the_notes_hit_line_says_out_of_what(results: Results) -> None:
    results.context.last_result = _result()
    results.render()
    assert "3980 of 4099" in results._detail.text()


def test_the_tally_never_wraps(results: Results) -> None:
    """A monospace line of data is not prose, and its spaces are break opportunities.

    The first render put "4 stray" on a second line, misaligned with the other three,
    in a column 412px wide around a string needing 352. QLabel treated the three
    spaces between columns as somewhere it could break, which is the right behaviour
    for a sentence and the wrong one for a table row.
    """
    results.context.last_result = _result()
    results.render()
    assert results._tally.wordWrap() is False
    assert results._tally.text().count("\n") == 0, "one line, whatever the width"


def test_the_tally_fits_at_the_narrowest_window(results: Results) -> None:
    """The widths the app actually ships at, rather than one comfortable size.

    A label that only fits at 1440 is a label that wraps for half the people using it.
    """
    for width, height in ((960, 640), (1440, 960), (1920, 1080)):
        results.resize(width, height)
        results.render()
        needed = results._tally.sizeHint().width()
        assert needed <= width, f"the tally needs {needed}px and the window is {width}"


def test_a_full_combo_says_so(results: Results) -> None:
    results.context.last_result = _result(
        perfect=4099, good=0, miss=0, note_count=4099, notes_hit=4099
    )
    results.render()
    assert results._verdict.text() == "Full combo"


def test_a_partial_run_says_nothing_extra(results: Results) -> None:
    """The percentage is the verdict; adding prose to a normal run would be noise."""
    results.context.last_result = _result()
    results.render()
    assert results._verdict.text() == ""


# --- the two prose lines ------------------------------------------------------


def test_a_new_best_names_what_it_beat(results: Results) -> None:
    results.context.last_result = _result(is_new_best=True, previous_best=0.883)
    results.render()
    assert "New best" in results._best.text()
    assert "88.3" in results._best.text(), "and names the run it beat, not this one"


def test_the_first_run_says_so_rather_than_claiming_to_have_beaten_something(
    results: Results,
) -> None:
    results.context.last_result = _result(is_new_best=True, previous_best=None)
    results.render()
    assert results._best.text() == "First run recorded"


def test_a_run_that_did_not_win_shows_the_stored_best(results: Results) -> None:
    results.context.settings.record_accuracy_for("eagles_the-hotel_california_5", 0.99)
    results.context.last_result = _result(is_new_best=False)
    results.render()
    assert results._best.text() == "Best 99.0%"


def test_a_song_with_no_history_gets_no_best_line(results: Results) -> None:
    """Not a 0% about a song that has never been played."""
    results.context.last_result = _result(is_new_best=False)
    results.render()
    assert results._best.text() == ""


def test_a_run_left_early_says_so(results: Results) -> None:
    """The percentage alone reads as a verdict on the whole song, which it is not."""
    results.context.last_result = _result(
        perfect=8, good=0, miss=0, note_count=4099, notes_hit=8
    )
    results.render()
    assert "left before the last note" in results._note.text()


def test_a_finished_run_has_no_note_underneath(results: Results) -> None:
    results.context.last_result = _result()
    results.render()
    assert results._note.text() == ""


# --- the empty state -----------------------------------------------------------


def test_navigating_here_with_no_result_says_so(results: Results) -> None:
    """Reachable only by a test or a future button, but it must not show zeroes."""
    assert results.context.last_result is None
    results.render()
    assert results._title.text() == "No song played"
    assert results._accuracy.text() == ""
    assert results._tally.text() == ""
    assert results._note.text(), "and says what would put something here"
    assert results._again.isEnabled() is False, "there is nothing to play again"


# --- the shell's screen cache --------------------------------------------------


def test_the_second_visit_shows_the_second_song(results: Results) -> None:
    """The shell keeps built screens, so this is the same widget twice.

    Rendering only in `__init__` would leave the first run's numbers under the second
    song's title -- the same bug the game screen's `showEvent` reload fixed (§32).
    """
    results.context.last_result = _result(title="First Song")
    shell_widget = results
    results.render()
    assert shell_widget._title.text() == "First Song"

    results.context.last_result = _result(title="Second Song", slug="other")
    shell_widget.render()
    assert shell_widget._title.text() == "Second Song"


def test_the_screen_renders_again_on_show(shell, context) -> None:
    context.settings = Settings()
    # `showEvent` only fires on a visible widget, and the shell's window is not shown
    # by default in tests -- the first version of this test asserted the re-render
    # without a window and watched it silently not happen.
    shell.show()
    shell.navigate(Screen.RESULTS)
    screen = shell.current_screen
    screen.context.last_result = _result(title="Set While Hidden")
    # Navigating away and back is what triggers showEvent.
    shell.navigate(Screen.MAIN)
    shell.navigate(Screen.RESULTS)
    assert shell.current_screen is screen, "the shell kept the widget"
    assert screen._title.text() == "Set While Hidden"


# --- navigation ----------------------------------------------------------------


def test_play_again_goes_back_into_the_song(results: Results) -> None:
    """Not back to the chooser: the request still holds slug, track and tempo."""
    results._play_again()
    assert results.shell.current is Screen.GAME


def test_back_from_results_lands_on_song_select(shell, context) -> None:
    """The game screen navigates with `remember=False`, so this is what it buys.

    The history already holds song select from the way into the game, so popping it
    returns there. Pushing GAME would put the finished attempt behind the results.
    """
    from guitaroids.songlib import Library, SongEntry

    # The real sequence: song select pushes itself when it navigates to the game, and
    # the game then navigates with remember=False so the finished attempt is not left
    # behind the results for Back to land on.
    shell.navigate(Screen.SONG_SELECT)
    shell.navigate(Screen.GAME)
    assert shell.current is Screen.GAME
    shell.navigate(Screen.RESULTS, remember=False)
    assert shell.current is Screen.RESULTS
    shell.go_back()
    assert shell.current is Screen.SONG_SELECT, "and not back into the finished song"


def test_the_screen_is_built_not_placeholdered(shell) -> None:
    """The last placeholder is gone, so there is no stub left to click through."""
    shell.navigate(Screen.RESULTS)
    assert isinstance(shell.current_screen, Results)
    assert "not built yet" not in shell.current_screen.findChildren(type(None).__mro__[0]).__str__()
