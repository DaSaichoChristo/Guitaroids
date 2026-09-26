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
from guitaroids.ui.widgets.tabview import TabView

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
        assert screen.view.chart is chart
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
        assert screen.view.chart is None
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


def test_the_view_follows_the_position(game: Game) -> None:
    game._clock.restart()
    game._tick()
    game._view.set_position(game.position())
    assert game.view.position == pytest.approx(game.position(), abs=0.05)


def test_the_ticker_pushes_the_position_into_the_widget(game: Game) -> None:
    game._clock = _FrozenClock(2.0)
    game._tick()
    assert game.view.position == pytest.approx(2.0)


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


def test_the_screen_owns_a_tab_view(game: Game) -> None:
    assert isinstance(game.view, TabView)


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
    """The HUD is an overlay: the tab view keeps the whole window.

    A layout holding both would shrink the view, and in a rhythm game the
    playfield is the thing that must not move.
    """
    screen = game_via_shell(shell, chart)
    assert screen.layout() is None, "nothing constrains the view or the HUD"
    assert screen.view.geometry() == screen.rect(), "the tab view fills the screen"


def test_every_hud_widget_is_a_child_of_the_screen(shell, chart) -> None:
    """A parentless widget is a *top-level window*, not a HUD element.

    It does not render inside the game at all, and nothing raises: the control is
    simply missing, which is how the BPM spin box went unnoticed until a render.
    """
    from PySide6 import QtWidgets

    screen = game_via_shell(shell, chart)
    widgets = (
        screen._title,
        screen._tally,
        screen._flash,
        screen._banner,
        screen._bpm_label,
        screen._bpm_readout,
        screen._bpm,
        screen._quit,
    )
    for widget in widgets:
        assert widget.parent() is screen, (
            f"{widget.objectName() or type(widget).__name__} is a top-level window"
        )


def test_the_tempo_control_is_actually_visible(shell, chart) -> None:
    """The other half of being parented: present *and* on screen.

    Parenting is necessary but not sufficient -- a child of a hidden screen is
    still hidden -- so the control is checked for a real geometry inside the window.
    """
    screen = game_via_shell(shell, chart)
    screen.resize(1600, 900)
    screen._place_hud()
    box = screen._bpm.geometry()
    assert box.width() > 0 and box.height() > 0, "the spin box has no size"
    assert screen.rect().contains(box), f"the spin box is at {box}, outside the screen"
    assert screen._bpm.isVisibleTo(screen)


def test_the_key_legend_is_gone(shell, chart) -> None:
    """Replaced by the string names on the cords themselves (§18).

    The legend told you what key pressed which lane. That is now on the tab, where
    the notation puts it, so keeping both would be two answers to one question.
    """
    from PySide6 import QtWidgets

    from guitaroids.ui import theme

    screen = game_via_shell(shell, chart)
    assert not hasattr(screen, "_legend")
    names = {b.objectName() for b in screen.findChildren(QtWidgets.QLabel)}
    assert "gameLegend" not in names
    assert "gameLegend" not in theme.build_stylesheet(), "the QSS rule outlived its widget"


def test_the_screen_has_no_game_objects_of_its_own(shell, chart) -> None:
    """DESIGN.md §1.7. Asserted structurally: no audio or device attributes."""
    screen = Game(shell, context_with(chart))
    try:
        for attribute in ("_stream", "_sounddevice", "_capture", "_tracker", "_session"):
            assert not hasattr(screen, attribute), f"the screen grew a {attribute}"
    finally:
        screen._stop()
        screen.deleteLater()


# --- practice tempo ----------------------------------------------------------


def test_the_rate_is_one_at_the_written_tempo(shell, chart) -> None:
    screen = game_via_shell(shell, chart)
    assert screen.rate_for(chart.tempo) == 1.0


def test_a_slower_tempo_gives_a_proportionally_slower_rate(shell, chart) -> None:
    screen = game_via_shell(shell, chart)
    assert screen.rate_for(chart.tempo / 2) == pytest.approx(0.5)


def test_it_cannot_be_practised_faster_than_written(shell, chart) -> None:
    """A tab played faster than it is written is a different piece of music.

    And it is the opposite of what the control is for, so it is refused rather than
    offered and then ignored.
    """
    screen = game_via_shell(shell, chart)
    assert screen.rate_for(chart.tempo * 2) == 1.0
    assert screen.rate_for(chart.tempo + 40) == 1.0


def test_a_chart_with_no_tempo_plays_at_full_speed(shell) -> None:
    """A missing tempo should not stop a song being playable."""
    from dataclasses import replace

    from songbuild import make_chart

    chart = replace(make_chart([(2.0, 0, 0)], collapse=False), tempo=0)
    shell.show()
    shell.navigate(Screen.GAME)
    screen = shell.current_screen
    screen.context = context_with(chart)
    screen._load_request()
    assert screen.rate_for(60) == 1.0


def test_the_position_slows_with_the_tempo(shell, chart) -> None:
    """The point of the feature: the chart advances more slowly in real time.

    Measured as an *advance* rather than a position, because a re-anchor holds the
    position still by design -- so a single frozen instant cannot show a slowdown.
    """
    screen = game_via_shell(shell, chart)
    screen._clock = _FrozenClock(10.0)

    start = screen.position()
    screen._clock.advance(4.0)
    assert screen.position() - start == pytest.approx(4.0, abs=0.05)

    screen.set_bpm(chart.tempo // 2)
    assert screen._rate == pytest.approx(0.5)
    start = screen.position()
    screen._clock.advance(4.0)
    assert screen.position() - start == pytest.approx(2.0, abs=0.05), (
        "four real seconds should be two seconds of chart at half speed"
    )


def test_changing_the_tempo_does_not_teleport_the_song(shell, chart) -> None:
    """The invariant that justifies re-anchoring over restarting the clock.

    A rate applied to a running clock would move the position back by a fifth of the
    elapsed time -- a minute, on a five-minute song. This is the test that fails if
    anyone replaces the re-anchor with a plain multiply.
    """
    screen = game_via_shell(shell, chart)
    screen._clock = _FrozenClock(200.0)
    before = screen.position()
    screen.set_bpm(chart.tempo // 2)
    assert screen.position() == pytest.approx(before, abs=0.05)
    assert before == pytest.approx(200.0, abs=0.05), "the fixture must be well into the song"


def test_a_tempo_of_zero_is_refused(shell, chart) -> None:
    """A zero rate would make the song stand still rather than fail."""
    screen = game_via_shell(shell, chart)
    for bad in (0, -1, -0.5):
        with pytest.raises(ValueError, match="positive"):
            screen._reanchor(bad)


def test_the_spin_box_cannot_exceed_the_written_tempo(shell, chart) -> None:
    screen = game_via_shell(shell, chart)
    assert screen._bpm.maximum() == chart.tempo
    screen._bpm.setValue(chart.tempo + 25)
    assert screen._bpm.value() == chart.tempo


def test_the_spin_box_floors_at_the_minimum(shell, chart) -> None:
    from guitaroids.settings import MIN_BPM

    screen = game_via_shell(shell, chart)
    screen._bpm.setValue(1)
    assert screen._bpm.value() == int(MIN_BPM)


def test_the_readout_shows_the_written_tempo_and_the_percentage(shell, chart) -> None:
    screen = game_via_shell(shell, chart)
    screen.set_bpm(chart.tempo)
    assert screen._bpm_readout.text() == f"of {chart.tempo} · 100%"
    screen.set_bpm(round(chart.tempo * 0.5))
    assert f"of {chart.tempo}" in screen._bpm_readout.text()
    assert "50%" in screen._bpm_readout.text()


def test_a_stored_tempo_is_remembered_per_song(shell, chart) -> None:
    from guitaroids.settings import Settings

    context = context_with(chart)
    context.settings = Settings()
    context.settings.set_bpm_for("test", 40.0)
    shell.show()
    shell.navigate(Screen.GAME)
    screen = shell.current_screen
    screen.context = context
    screen._load_request()
    assert screen._bpm.value() == 40
    assert screen._rate == pytest.approx(40 / chart.tempo)


def test_a_song_with_no_stored_tempo_plays_as_written(shell, chart) -> None:
    screen = game_via_shell(shell, chart)
    assert screen._bpm.value() == chart.tempo
    assert screen._rate == 1.0


def test_changing_the_tempo_records_it(shell, chart) -> None:
    screen = game_via_shell(shell, chart)
    screen._bpm.setValue(40)
    assert screen.context.settings.bpm_for("test") == 40.0


def test_the_tempo_is_written_to_disk_once_on_hide(shell, chart) -> None:
    """A save is an fsync, and stepping 76 down to 60 would otherwise be sixteen."""
    saves = []
    shell.show()
    shell.navigate(Screen.GAME)
    screen = shell.current_screen
    screen.context = context_with(chart)
    screen._load_request()
    screen.context.save_settings = lambda: saves.append(1)

    # Below the written tempo: the spin box refuses anything above it, and every
    # value at or above the starting one would be a no-op that emits no signal.
    for value in (50, 45, 40):
        screen._bpm.setValue(value)
    assert screen._bpm.value() == 40
    assert saves == [], "nothing is written while playing"

    shell.navigate(Screen.MAIN)
    assert len(saves) == 1, "one write on the way out"


def test_hiding_with_no_change_writes_nothing(shell, chart) -> None:
    saves = []
    screen = game_via_shell(shell, chart)
    screen.context.save_settings = lambda: saves.append(1)
    shell.navigate(Screen.MAIN)
    assert saves == []


def test_a_failed_save_does_not_stop_the_song(shell, chart) -> None:
    """A read-only config directory must not break playback."""
    screen = game_via_shell(shell, chart)
    screen._bpm.setValue(40)

    def explode():
        raise OSError("read-only file system")

    screen.context.save_settings = explode
    shell.navigate(Screen.MAIN)  # must not raise
    assert screen._bpm.value() == 40


def test_the_offset_is_not_scaled_by_the_rate(shell, chart) -> None:
    """Slowing must not disturb the stored song alignment.

    The offset is how the music lines up with the tab, which is a property of the
    song rather than of how fast it is being played, so it is applied in *chart*
    time after the rate. What is being checked is that the offset contributes the
    same amount at any speed -- if it were being multiplied by the rate, a beginner
    slowing down would also silently re-tune the song's alignment.
    """
    context = context_with(chart)
    context.request_play("test", 1, offset_ms=-200.0)
    shell.show()
    shell.navigate(Screen.GAME)
    screen = shell.current_screen
    screen.context = context
    screen._load_request()

    def offset_contribution(rate_divisor: int) -> float:
        screen._clock = _FrozenClock(10.0)
        if rate_divisor > 1:
            screen.set_bpm(chart.tempo // rate_divisor)
        raw = (screen._t0_ms + screen._clock.elapsed()) * screen._rate
        return screen.position() - raw / 1000.0

    assert offset_contribution(1) == pytest.approx(0.2, abs=0.05)
    assert offset_contribution(2) == pytest.approx(0.2, abs=0.05), (
        "the offset must not be scaled by the playback rate"
    )


class _FrozenClock:
    """Stands in for QElapsedTimer with a position we choose.

    The real one is monotonic and cannot be asked to read an exact second, and a
    test that sleeps until a note comes up is a test that is slow when it passes and
    flaky when it does not.

    ``start`` and ``restart`` reset to zero, as ``QElapsedTimer``'s do. An earlier
    version of this stub left the value alone, which quietly broke every re-anchor
    test: after ``_reanchor`` restarts the clock, a real one reads ~0 and this one
    read the same 4 seconds as before, so the position appeared to jump.
    """

    def __init__(self, seconds: float = 0.0) -> None:
        self._ms = int(seconds * 1000)
        self._valid = True

    def isValid(self) -> bool:  # noqa: N802 - Qt naming
        return self._valid

    def start(self) -> None:
        self._ms = 0
        self._valid = True

    def restart(self) -> None:
        self._ms = 0
        self._valid = True

    def invalidate(self) -> None:
        self._valid = False

    def elapsed(self) -> int:
        return self._ms if self._valid else 0

    def advance(self, seconds: float) -> None:
        self._ms += int(seconds * 1000)

    def set(self, seconds: float) -> None:
        """Put the clock at a time without going through a restart."""
        self._ms = int(seconds * 1000)


def test_the_spin_box_and_the_programmatic_setter_agree(shell, chart) -> None:
    """The control must not be able to lie about the tempo in use.

    Found by driving the screen the way a caller would rather than the way a click
    would: the rate and the readout moved, and the spin box stayed where it was, so
    the box read 76 while the game played at 50.
    """
    screen = game_via_shell(shell, chart)
    screen.set_bpm(50)
    assert screen._bpm.value() == 50
    assert screen._rate == pytest.approx(50 / chart.tempo)
    assert f"of {chart.tempo}" in screen._bpm_readout.text()


def test_set_bpm_does_not_re_enter_through_the_spin_box(shell, chart) -> None:
    """Signals are blocked; otherwise setting the box would re-enter the handler."""
    screen = game_via_shell(shell, chart)
    calls = []
    original = screen._reanchor
    screen._reanchor = lambda rate: (calls.append(rate), original(rate))[1]
    screen.set_bpm(50)
    assert len(calls) == 1, f"the spin box re-entered the handler: {len(calls)} anchors"
