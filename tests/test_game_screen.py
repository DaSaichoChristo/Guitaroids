"""Tests for the game screen.

The screen is the first thing in the project that needs a *position*, so most of
these are about the plumbing between the clock, the widget and ``GameState`` --
that one number reaches all three, and that the screen starts and stops cleanly.

Timing is driven by calling ``_tick``/``_press`` directly rather than by sleeping.
A real ``QElapsedTimer`` cannot be made to report an exact millisecond, and a test
that sleeps is a test that is either slow or flaky. The clock is exercised for what
it is -- a monotonic source -- and the arithmetic is exercised for exactness.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from songbuild import make_chart

from guitaroids.context import AppContext
from guitaroids.session.judge import Verdict
from guitaroids.songlib import Library, SongEntry, Status
from guitaroids.ui.game import KEY_LANES, Game
from guitaroids.ui.screens import Screen
from guitaroids.ui.widgets.highway import Highway

#: Notes one per lane, all at t=2.0, so a press in one lane cannot be satisfied by
#: another lane's note. collapse=False because six notes on one onset would be
#: merged into one by the chord rule.
NOTES = [(2.0, lane, lane) for lane in range(6)]


def entry_for(chart, *, audio: Path | None = None) -> SongEntry:
    return SongEntry(
        tab_path=Path("songs/test.gp5"),
        status=Status.OK if audio else Status.NO_AUDIO,
        chart=chart,
        audio_path=audio,
    )


def context_with(chart, **kwargs) -> AppContext:
    context = AppContext(
        library=Library(root=Path("songs"), entries=(entry_for(chart, **kwargs),)),
        songs_dir=Path("songs"),
        settings_path=Path("/nonexistent/settings.json"),
    )
    context.request_play("test", 1)
    return context


@pytest.fixture()
def chart():
    return make_chart(NOTES, collapse=False)


def game_via_shell(shell, chart) -> Game:
    """The screen the *shell* built, with a chart loaded into it.

    Navigating to GAME constructs a Game from the shell's context, which the
    conftest fixture leaves empty. Constructing a second Game by hand and testing
    that would test an object the player never sees, so instead the shell's own
    instance is given a real context and reloaded -- which also exercises the
    reload path ``showEvent`` uses.
    """
    shell.show()
    shell.navigate(Screen.GAME)
    screen = shell.current_screen
    screen.context = context_with(chart)
    screen._load_request()
    return screen


@pytest.fixture()
def game(shell, chart) -> Game:
    """A game screen with a real request behind it.

    Built directly rather than through the shell fixture, so the context can carry
    a synthetic chart that finishes in a couple of seconds rather than six minutes.
    """
    context = context_with(chart)
    screen = Game(shell, context)
    yield screen
    screen._stop()
    screen.deleteLater()


# --- loading -----------------------------------------------------------------


def test_it_loads_the_request_from_the_context(shell, chart) -> None:
    screen = Game(shell, context_with(chart))
    try:
        assert screen.chart is not None
        assert screen.state is not None
        assert screen.state.note_count == len(chart.notes)
        assert screen.highway.chart is chart
    finally:
        screen._stop()
        screen.deleteLater()


def test_no_request_is_an_empty_state_not_a_crash(shell) -> None:
    """Someone can navigate to GAME without picking a song."""
    context = AppContext(songs_dir=Path("songs"))
    screen = Game(shell, context)
    try:
        assert screen.state is None
        assert screen.chart is None
        assert screen.highway.chart is None
        assert "No song selected" in screen._banner.text()
    finally:
        screen._stop()
        screen.deleteLater()


def test_an_unplayable_request_is_an_empty_state(shell, chart) -> None:
    """A chart with no notes cannot be played, and must not look playable."""
    from dataclasses import replace

    context = context_with(replace(chart, notes=()))
    screen = Game(shell, context)
    try:
        assert screen.state is None
        assert "No song selected" in screen._banner.text()
    finally:
        screen._stop()
        screen.deleteLater()


def test_the_title_comes_from_the_chart(shell, chart) -> None:
    screen = Game(shell, context_with(chart))
    try:
        assert screen._title.text() == chart.title
    finally:
        screen._stop()
        screen.deleteLater()


# --- the clock ---------------------------------------------------------------


def test_the_position_starts_at_the_offset(game: Game) -> None:
    """A negative offset means the song starts *later* than the clock does."""
    assert game.position() == pytest.approx(0.0, abs=0.05)


def test_the_offset_shifts_the_position(shell, chart) -> None:
    context = context_with(chart)
    context.request_play("test", 1, offset_ms=-250.0)
    screen = Game(shell, context)
    try:
        assert screen.position() == pytest.approx(0.25, abs=0.05)
    finally:
        screen._stop()
        screen.deleteLater()


def test_the_position_advances(game: Game) -> None:
    first = game.position()
    time.sleep(0.05)
    assert game.position() > first


def test_the_highway_follows_the_position(game: Game) -> None:
    game._clock.restart()
    game._tick()
    game._highway.set_position(game.position())
    assert game.highway.position == pytest.approx(game.position(), abs=0.05)


def test_the_ticker_pushes_the_position_into_the_widget(game: Game) -> None:
    game._clock = _FrozenClock(2.0)
    game._tick()
    assert game.highway.position == pytest.approx(2.0)


# --- input -------------------------------------------------------------------


def test_a_digit_key_judges_its_lane(game: Game) -> None:
    """Pressed at exactly t=2.0, so a PERFECT is a real signal, not luck."""
    game._clock.restart()
    game._press(2)
    # The clock has barely moved, so drive the clock back to the note instead.
    game._press(0)
    assert game.state.judgements, "a press must record something"
    assert game.state.judgements[0].lane in (0, 2)


def test_every_digit_key_maps_to_a_distinct_lane() -> None:
    lanes = set(KEY_LANES.values())
    assert lanes == set(range(6))
    assert len(KEY_LANES) == 6


def test_the_key_table_uses_one_through_six() -> None:
    from PySide6 import QtCore

    assert KEY_LANES[QtCore.Qt.Key.Key_1] == 0
    assert KEY_LANES[QtCore.Qt.Key.Key_6] == 5
    assert QtCore.Qt.Key.Key_0 not in KEY_LANES
    assert QtCore.Qt.Key.Key_7 not in KEY_LANES


def test_pressing_at_the_note_time_is_perfect(game: Game) -> None:
    """Drives ``_press`` at a known position by freezing the clock first.

    ``_clock`` is replaced with a stub rather than slept against, because
    QElapsedTimer cannot be made to report an exact time and a test that waits for
    one is either slow or flaky.
    """
    game._clock = _FrozenClock(2.0)
    game._press(3)
    judgement = game.state.judgements[-1]
    assert judgement.verdict is Verdict.PERFECT
    assert judgement.lane == 3
    assert game._flash.text() == "PERFECT"


def test_a_miss_flashes_miss(game: Game) -> None:
    game._clock = _FrozenClock(2.0 + 0.100)
    game._press(1)
    assert game.state.judgements[-1].verdict is Verdict.MISS
    assert game._flash.text() == "MISS"
    assert game.state.misses == 1


def test_a_stray_flashes_nothing(game: Game) -> None:
    """Faking should not put a word on screen for every stray press."""
    game._clock = _FrozenClock(50.0)
    game._press(0)
    assert game.state.judgements[-1].verdict is Verdict.STRAY
    assert game._flash.text() == ""


def test_pressing_with_no_song_does_nothing(shell) -> None:
    context = AppContext(songs_dir=Path("songs"))
    screen = Game(shell, context)
    try:
        screen._press(0)
        assert screen.state is None
    finally:
        screen._stop()
        screen.deleteLater()


def test_escape_still_goes_back(shell, chart) -> None:
    """The base class's job must survive a screen that consumes key presses."""
    from PySide6 import QtCore, QtGui, QtWidgets

    screen = Game(shell, context_with(chart))
    try:
        shell.show()
        shell.navigate(Screen.GAME)
        event = QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            QtCore.Qt.Key.Key_Escape,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        QtWidgets.QApplication.sendEvent(shell.current_screen, event)
        assert shell.current is not Screen.GAME
    finally:
        screen._stop()
        screen.deleteLater()


def test_a_real_key_event_reaches_the_judge(shell, chart) -> None:
    """The Qt path end to end, not just ``_press``."""
    from PySide6 import QtCore, QtGui, QtWidgets

    screen = Game(shell, context_with(chart))
    try:
        screen._clock = _FrozenClock(50.0)  # far from any note: a stray
        event = QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            QtCore.Qt.Key.Key_3,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
        QtWidgets.QApplication.sendEvent(screen, event)
        assert screen.state.strays == 1
    finally:
        screen._stop()
        screen.deleteLater()


# --- the tally ---------------------------------------------------------------


def test_the_tally_starts_empty(game: Game) -> None:
    game._refresh_tally()
    text = game._tally.text()
    assert "0 perfect" in text and "0 good" in text and "0 miss" in text


def test_the_tally_counts_a_hit(game: Game) -> None:
    game._clock = _FrozenClock(2.0)
    game._press(0)
    game._refresh_tally()
    assert "1 perfect" in game._tally.text()
    assert "100%" in game._tally.text(), "one note judged, one hit"


def test_a_miss_shows_in_the_tally(game: Game) -> None:
    game._clock = _FrozenClock(2.0 + 0.100)
    game._press(0)
    game._refresh_tally()
    assert "1 miss" in game._tally.text()
    assert "0%" in game._tally.text()


# --- finishing ---------------------------------------------------------------


def test_the_song_reports_completion(game: Game) -> None:
    game._clock = _FrozenClock(3.0)
    game._tick()
    assert "complete" in game._banner.text().lower()


def test_input_is_ignored_after_the_song_ends(game: Game) -> None:
    game._clock = _FrozenClock(3.0)
    game._tick()
    before = len(game.state.judgements)
    game._clock = _FrozenClock(4.0)
    game._press(0)
    assert len(game.state.judgements) == before


# --- lifecycle ---------------------------------------------------------------


def test_hiding_stops_the_clock(shell, chart) -> None:
    """Through the shell, because navigating to GAME is what builds the screen.

    Constructing a Game directly and then navigating would test a *second* instance
    that the player never sees, which passes while the real one keeps ticking.
    """
    screen = game_via_shell(shell, chart)
    assert screen._timer.isActive()
    shell.navigate(Screen.MAIN)
    assert not screen._timer.isActive(), "a hidden screen must not keep ticking"


def test_leaving_stops_the_clock(shell, chart) -> None:
    shell.show()
    shell.navigate(Screen.GAME)
    screen = shell.current_screen
    screen._quit.click()
    assert not screen._timer.isActive()
    assert shell.current is Screen.MAIN


def test_revisiting_starts_a_fresh_run(shell, chart) -> None:
    """The shell keeps built screens, so GAME is not new the second time."""
    screen = game_via_shell(shell, chart)
    screen._clock = _FrozenClock(2.0)
    screen._press(0)
    assert screen.state.resolved == 1
    shell.navigate(Screen.MAIN)
    shell.navigate(Screen.GAME)
    assert screen.state.resolved == 0, "re-entering must not resume a finished run"


# --- structure ---------------------------------------------------------------


def test_the_screen_owns_a_highway(game: Game) -> None:
    assert isinstance(game.highway, Highway)


def test_a_lane_key_works_even_when_it_does_not_reach_the_screen(
    shell, chart
) -> None:
    """The game must not depend on Qt focus.

    Focus is only granted to an *active* window, so a digit key can land on the
    window background or the Back button instead of the game screen -- and in the
    offscreen test platform nothing has focus at all. Delivering the key to the
    button is the reproducible version of that failure: without the application-wide
    filter the press is swallowed and the game is unplayable.
    """
    from PySide6 import QtCore, QtGui, QtWidgets

    screen = game_via_shell(shell, chart)
    screen._clock = _FrozenClock(50.0)  # far from any note: a stray
    event = QtGui.QKeyEvent(
        QtCore.QEvent.Type.KeyPress,
        QtCore.Qt.Key.Key_4,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(screen._quit, event)
    assert screen.state.strays == 1
    assert screen.state.judgements[-1].lane == 3, "key 4 is lane 3"


def test_the_filter_lets_other_keys_through(shell, chart) -> None:
    """Space must still reach the button, and Escape must still navigate back."""
    from PySide6 import QtCore, QtGui, QtWidgets

    screen = game_via_shell(shell, chart)
    screen._clock = _FrozenClock(50.0)
    space = QtGui.QKeyEvent(
        QtCore.QEvent.Type.KeyPress,
        QtCore.Qt.Key.Key_Space,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    assert screen.eventFilter(screen._quit, space) is False, "Space was swallowed"

    escape = QtGui.QKeyEvent(
        QtCore.QEvent.Type.KeyPress,
        QtCore.Qt.Key.Key_Escape,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    assert screen.eventFilter(screen._quit, escape) is False, "Escape was swallowed"


def test_a_lane_key_aimed_at_the_screen_is_handled_once(shell, chart) -> None:
    """No double judging: the filter passes its own screen's events through."""
    from PySide6 import QtCore, QtGui, QtWidgets

    screen = game_via_shell(shell, chart)
    screen._clock = _FrozenClock(50.0)
    event = QtGui.QKeyEvent(
        QtCore.QEvent.Type.KeyPress,
        QtCore.Qt.Key.Key_1,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    assert screen.eventFilter(screen, event) is False
    QtWidgets.QApplication.sendEvent(screen, event)
    assert screen.state.strays == 1, "one press must record one judgement"


def test_keys_stop_driving_the_game_once_it_is_hidden(shell, chart) -> None:
    """A filter left installed would keep playing the game from the menu."""
    from PySide6 import QtCore, QtGui, QtWidgets

    screen = game_via_shell(shell, chart)
    screen._clock = _FrozenClock(50.0)
    shell.navigate(Screen.MAIN)
    event = QtGui.QKeyEvent(
        QtCore.QEvent.Type.KeyPress,
        QtCore.Qt.Key.Key_1,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(shell, event)
    assert screen.state.strays == 0, "the filter must be removed on hide"


def test_the_hud_does_not_cover_the_playfield(shell, chart) -> None:
    """The HUD is an overlay: the highway keeps the whole window.

    A layout holding both would shrink the highway, and in a rhythm game the
    playfield is the thing that must not move.
    """
    screen = game_via_shell(shell, chart)
    assert screen.layout() is None, "nothing constrains the highway or the HUD"
    assert screen.highway.geometry() == screen.rect(), "the highway fills the screen"


def test_the_hud_labels_are_children_of_the_screen(shell, chart) -> None:
    """A parentless QLabel is a top-level window; showing one opens a second one."""
    screen = game_via_shell(shell, chart)
    for label in (screen._title, screen._tally, screen._flash, screen._banner, screen._legend):
        assert label.parent() is screen, f"{label.objectName()} is a top-level window"


def test_the_screen_has_no_game_objects_of_its_own(shell, chart) -> None:
    """DESIGN.md §1.7. Asserted structurally: no audio or device attributes."""
    screen = Game(shell, context_with(chart))
    try:
        for attribute in ("_stream", "_sounddevice", "_capture", "_tracker", "_session"):
            assert not hasattr(screen, attribute), f"the screen grew a {attribute}"
    finally:
        screen._stop()
        screen.deleteLater()


class _FrozenClock:
    """Stands in for QElapsedTimer with a position we choose.

    The real one is monotonic and cannot be asked to read an exact second, and a
    test that sleeps until a note comes up is a test that is slow when it passes and
    flaky when it does not.
    """

    def __init__(self, seconds: float) -> None:
        self._ms = int(seconds * 1000)
        self._valid = True

    def isValid(self) -> bool:  # noqa: N802 - Qt naming
        return self._valid

    def start(self) -> None:
        self._valid = True

    def restart(self) -> None:
        self._valid = True

    def invalidate(self) -> None:
        self._valid = False

    def elapsed(self) -> int:
        return self._ms if self._valid else 0

    def advance(self, seconds: float) -> None:
        self._ms += int(seconds * 1000)
