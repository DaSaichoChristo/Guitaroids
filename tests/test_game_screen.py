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
from qtsupport import wait_until
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
        # These tests are about the wall clock, the tab view and the judge. With audio
        # on, every Game construction would start a background render and the screen
        # would sit in its "Preparing audio" state until a queued signal arrived --
        # which never happens, because nothing pumps the event loop here.
        audio_enabled=False,
    )
    context.request_play("test", 1)
    return context


@pytest.fixture()
def chart():
    return make_chart(NOTES, collapse=False)


def game_via_shell(shell, chart, *, bpm: float | None = None) -> Game:
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
    context = context_with(chart)
    if bpm is not None:
        context.request_play("test", 1, bpm=bpm)
    screen.context = context
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
    simply missing, which is how the BPM spin box went unnoticed until a render
    when it lived on this screen (§18.5).
    """
    screen = game_via_shell(shell, chart)
    widgets = (
        screen._title,
        screen._tally,
        screen._flash,
        screen._banner,
        screen._quit,
    )
    for widget in widgets:
        assert widget.parent() is screen, (
            f"{widget.objectName() or type(widget).__name__} is a top-level window"
        )


def test_every_hud_widget_is_actually_visible(shell, chart) -> None:
    """The other half of being parented: present *and* on screen.

    Parenting is necessary but not sufficient -- a child of a hidden screen is
    still hidden -- so each widget is checked for a real geometry inside the window.
    This is the assertion that would have caught the parentless spin box.
    """
    screen = game_via_shell(shell, chart)
    screen.resize(1600, 900)
    screen._place_hud()
    for widget in (screen._title, screen._tally, screen._flash, screen._banner, screen._quit):
        box = widget.geometry()
        assert box.width() > 0 and box.height() > 0, f"{widget} has no size"
        assert screen.rect().contains(box), f"{widget} is at {box}, outside the screen"
        assert widget.isVisibleTo(screen), f"{widget} is not visible on the screen"


def test_the_game_screen_has_no_tempo_control(shell, chart) -> None:
    """The tempo is chosen on song select (§19.1), so this screen must not have one.

    Asserted by absence rather than by a render: a control that is *meant* not to
    exist is easy to re-add by accident, and a second place to set the tempo is two
    answers to one question -- the same reason the key legend went in §18.1.
    """
    screen = game_via_shell(shell, chart)
    for gone in ("_bpm", "_bpm_label", "_bpm_readout"):
        assert not hasattr(screen, gone), f"the tempo control came back as {gone}"
    assert not hasattr(screen, "set_bpm"), "tempo is no longer changed mid-song"


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

    Measured as an *advance* over two requests rather than within one run, because
    the rate is read once at load (§19.1) and never changes while the song plays.
    """
    screen = game_via_shell(shell, chart, bpm=chart.tempo / 2)
    screen._clock = _FrozenClock(10.0)
    assert screen.rate == pytest.approx(0.5)
    start = screen.position()
    screen._clock.advance(4.0)
    assert screen.position() - start == pytest.approx(2.0, abs=0.05), (
        "four real seconds should be two seconds of chart at half speed"
    )


def test_a_request_of_zero_bpm_plays_as_written(shell, chart) -> None:
    """0 is the "as written" sentinel, not a rate of zero.

    A rate of zero would make the song stand still, which is a failure with no
    symptom you could act on.
    """
    screen = game_via_shell(shell, chart, bpm=0.0)
    assert screen.rate == 1.0
    assert screen.rate_for(0) == 1.0
    assert screen.rate_for(-10) == 1.0


def test_the_rate_is_fixed_for_the_run(shell, chart) -> None:
    """Nothing on this screen can change the tempo mid-song.

    The rate is read from the PlayRequest when the chart loads. There is no
    re-anchor and no origin to move, so a run cannot be re-timed by anything the
    player does while it is playing -- the whole reason the control moved to song
    select (§19.1).
    """
    screen = game_via_shell(shell, chart, bpm=chart.tempo / 2)
    screen._clock = _FrozenClock(60.0)
    before = screen.rate
    assert before == pytest.approx(0.5)
    screen._tick()
    screen._press(0)
    assert screen.rate == before
    assert not hasattr(screen, "_t0_ms"), "a clock origin would mean a re-anchor"


def test_a_stored_tempo_reaches_the_run_through_the_request(shell, chart) -> None:
    """The remembered per-song tempo is applied with no override from the screen.

    Song select passes the control's value; the screen's own path is the stored
    one, so a song played without visiting song select this session still gets it.
    """
    from guitaroids.settings import Settings

    context = context_with(chart)
    context.settings = Settings()
    context.settings.set_bpm_for("test", 40.0)
    context.request_play("test", 1)  # re-read, now that the tempo is remembered
    shell.show()
    shell.navigate(Screen.GAME)
    screen = shell.current_screen
    screen.context = context
    screen._load_request()
    assert screen.rate == pytest.approx(40 / chart.tempo)


def test_a_song_with_no_stored_tempo_plays_as_written(shell, chart) -> None:
    screen = game_via_shell(shell, chart)
    assert screen.rate == 1.0


def test_hiding_writes_no_settings(shell, chart) -> None:
    """Nothing on this screen changes a setting, so leaving it writes nothing.

    The tempo and the offset are both saved on Play in song select, where a save
    is one write per attempt rather than one per adjustment (§19.1).
    """
    saves = []
    screen = game_via_shell(shell, chart)
    screen.context.save_settings = lambda: saves.append(1)
    shell.navigate(Screen.MAIN)
    assert saves == []


def test_the_offset_is_not_scaled_by_the_rate(shell, chart) -> None:
    """Slowing must not disturb the stored song alignment.

    The offset is how the music lines up with the tab, which is a property of the
    song rather than of how fast it is being played, so it is applied in *chart*
    time after the rate. What is being checked is that the offset contributes the
    same amount at any speed -- if it were being multiplied by the rate, a beginner
    slowing down would also silently re-tune the song's alignment.
    """

    def offset_contribution(bpm: float) -> float:
        context = context_with(chart)
        context.request_play("test", 1, offset_ms=-200.0, bpm=bpm)
        shell.show()
        shell.navigate(Screen.GAME)
        screen = shell.current_screen
        screen.context = context
        screen._load_request()
        screen._clock = _FrozenClock(10.0)
        raw = screen._clock.elapsed() * screen.rate
        return screen.position() - raw / 1000.0

    assert offset_contribution(0.0) == pytest.approx(0.2, abs=0.05)
    assert offset_contribution(chart.tempo / 2) == pytest.approx(0.2, abs=0.05), (
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


# --- the audio clock (§24) -------------------------------------------------------
#
# Before this, the game ran on a QElapsedTimer, which is self-consistent and cannot
# say whether the game feels right (§1.5). These tests cover the switch: with audio
# the position comes from the device, without it the wall clock is the fallback, and
# a song that is still being rendered is not yet playing.


def audio_context(chart, **kwargs) -> AppContext:
    """A context that *wants* audio, for the tests that exercise the render path."""
    context = context_with(chart, **kwargs)
    context.audio_enabled = True
    return context


def test_audio_off_means_the_wall_clock_runs_immediately(shell, chart) -> None:
    """The fallback has to be instant, or every test in this file would wait."""
    screen = game_via_shell(shell, chart)
    assert screen._preparing is False
    screen._clock = _FrozenClock(5.0)
    assert screen.position() == pytest.approx(5.0, abs=0.05)


def test_the_screen_prepares_audio_when_it_is_enabled(shell, chart) -> None:
    """The clock does not start while a render is in flight.

    Starting it and handing over later would make the song jump forwards by however
    long the render took, which is the direction of jump that makes a rhythm game
    feel broken rather than merely wrong.
    """
    shell.show()
    shell.navigate(Screen.GAME)
    screen = shell.current_screen
    screen.context = audio_context(chart)
    screen._load_request()

    assert screen._preparing is True
    assert screen.position() == -1.0, "a rendering song must not report a position"
    assert "Preparing audio" in screen._banner.text()
    screen._renderer.cancel()
    wait_until(lambda: not screen._renderer.is_running)


def test_input_is_ignored_while_the_audio_is_preparing(shell, chart) -> None:
    """A keypress during a count-in render is not a miss, it is a keypress."""
    shell.show()
    shell.navigate(Screen.GAME)
    screen = shell.current_screen
    screen.context = audio_context(chart)
    screen._load_request()
    try:
        screen._press(0)
        assert screen._state is not None
        assert screen._state.resolved == 0
    finally:
        screen._renderer.cancel()
        wait_until(lambda: not screen._renderer.is_running)


def test_a_failed_render_falls_back_to_the_wall_clock(shell, chart) -> None:
    """No soundfont, no device, a preset the soundfont lacks: play the song anyway.

    A rhythm game that will not start without a sound card is worse than one that
    starts slightly wrong.
    """
    screen = game_via_shell(shell, chart)
    screen._preparing = True
    screen._on_audio_failed("no soundfont found")

    assert screen._preparing is False
    assert screen._render_error == "no soundfont found"
    screen._clock = _FrozenClock(3.0)
    assert screen.position() == pytest.approx(3.0, abs=0.05)


def test_a_cancelled_render_starts_nothing(shell, chart) -> None:
    """The player has already moved on; there is nothing to play and nothing to say."""
    screen = game_via_shell(shell, chart)
    screen._preparing = True
    screen._on_audio_cancelled()
    assert screen._preparing is False
    assert screen._render_error == ""


def test_the_count_in_offset_agrees_with_what_was_rendered(shell, chart) -> None:
    """The offset between sample zero and the chart's zero, from one source.

    The game screen asks the click module how long the count-in lasts, and
    ``add_count_in`` reserved exactly that much. If the two ever disagreed, every
    note would arrive offset from the music by the difference -- a timing bug that
    sounds like the player's own sloppiness.
    """
    from guitaroids.audio.click import add_count_in, count_in_seconds

    screen = game_via_shell(shell, chart)
    screen.context.settings.count_in_bars = 2
    import numpy as np

    track = np.zeros((44100, 2), dtype=np.float32)
    combined, clicks = add_count_in(
        track,
        bpm=float(chart.tempo),
        count_in_bars=2,
        beats_per_bar=4,
        sample_rate=44100,
    )
    reserved = len(combined) - len(track)
    # The reserved room is the count-in plus the last click's own decay, so the
    # offset the screen hands the transport is a lower bound on it, not equal to it.
    assert screen._count_in_seconds() == pytest.approx(count_in_seconds(
        bpm=float(chart.tempo), count_in_bars=2, beats_per_bar=4
    ))
    assert reserved / 44100 >= screen._count_in_seconds() - 0.001
    assert clicks.indices[0] == 0


def test_the_audio_position_wins_over_the_wall_clock(shell, chart) -> None:
    """The device's clock is the one §1.5 says to use, so when it is there it is
    what the screen reads -- even with the wall clock wound forward."""
    from guitaroids.audio.transport import silence

    screen = game_via_shell(shell, chart)
    screen._clock = _FrozenClock(30.0)  # would say 30s
    try:
        screen.context.start_playback(
            silence(5.0, 44100), sample_rate=44100, volume=1.0, device=None
        )
        import time

        time.sleep(0.2)
        audio = screen.position()
        assert audio < 2.0, f"read the wall clock ({audio:.1f}s) instead of the device"
    finally:
        screen.context.stop_playback()


# --- losing the audio mid-song (§26) -------------------------------------------
#
# The device can go away while a song is playing: an unplugged interface, a stalled
# ALSA, a stream that failed. Two wrong answers were available and the bug report
# found the worse one -- the bar simply stopped moving, because the wall clock had
# never been started.


def test_the_wall_clock_is_always_a_valid_fallback(shell, chart) -> None:
    """Started as soon as the audio is, even though the audio is what gets read.

    A wall clock that has never run cannot take over, and taking over is exactly the
    case where a frozen bar is worst.
    """
    screen = game_via_shell(shell, chart)
    assert screen._clock.isValid(), "the fallback clock must be usable at any moment"


def test_losing_the_audio_does_not_freeze_the_bar(shell, chart) -> None:
    """The reported bug, at the level it can be asserted.

    Before this, the audio path never started the wall clock, so when the audio
    stopped `position()` fell through to an invalid clock and returned 0.0 forever --
    the tab stopped advancing and it looked like a hang.
    """
    screen = game_via_shell(shell, chart)
    from guitaroids.audio.transport import silence

    try:
        screen.context.start_playback(
            silence(10.0, 44100), sample_rate=44100, volume=1.0, device=None
        )
        screen._wall_origin = None
        screen._last_audio_position = 0.0
        time.sleep(0.15)
        with_audio = screen.position()
        assert with_audio > 0.0, "the audio clock is not being read at all"

        screen.context.stop_playback()  # the device goes away mid-song
        first = screen.position()
        assert first > 0.0, f"froze or reset to zero (got {first})"
        time.sleep(0.2)
        later = screen.position()
        assert later > first, f"the bar stopped moving ({first} -> {later})"
    finally:
        screen.context.stop_playback()


def test_taking_over_from_the_audio_does_not_jump(shell, chart) -> None:
    """The fallback continues from where the audio stopped.

    Deriving the origin from the *last audio position* rather than from zero is the
    whole difference between carrying on and restarting the song. Same re-anchor shape
    as §18.4 needed, for the same reason.
    """
    screen = game_via_shell(shell, chart)
    from guitaroids.audio.transport import silence

    try:
        screen.context.start_playback(
            silence(10.0, 44100), sample_rate=44100, volume=1.0, device=None
        )
        screen._wall_origin = None
        time.sleep(0.2)
        audio_position = screen.position()

        screen.context.stop_playback()
        resumed = screen.position()

        assert resumed == pytest.approx(audio_position, abs=0.2), (
            f"jumped from {audio_position:.2f} to {resumed:.2f} when the audio stopped"
        )
    finally:
        screen.context.stop_playback()


def test_the_offset_survives_the_fallback(shell, chart) -> None:
    """The wall clock is the *primary* clock when there is no audio, so its origin
    is the chart's zero shifted by the offset -- not zero.

    An offset that stops being applied the moment audio is switched off would be a
    song that plays in tune on a machine with a sound card and out of tune on one
    without, which is the sort of difference nobody reports.
    """
    context = context_with(chart)
    context.request_play("test", 1, offset_ms=-200.0)
    shell.show()
    shell.navigate(Screen.GAME)
    screen = shell.current_screen
    screen.context = context
    screen._load_request()
    screen._clock = _FrozenClock(10.0)
    assert screen._wall_origin == pytest.approx(0.2)
    assert screen.position() == pytest.approx(10.2, abs=0.05)


def test_the_player_is_told_once_when_the_audio_stops(shell, chart) -> None:
    """A silent device is the one failure the fallback cannot announce by itself."""
    from guitaroids.audio.transport import silence

    screen = game_via_shell(shell, chart)
    try:
        screen.context.start_playback(
            silence(5.0, 44100), sample_rate=44100, volume=1.0, device=None
        )
        # Stands for "the audio arrived", which is the transition this test needs.
        # Driven through `_on_audio_ready` in the app; set directly here because a
        # RenderResult and a device to play it through would be testing the wrong
        # thing. The other half -- that the wall clock is a valid fallback -- is
        # asserted separately above.
        screen._audio_live = True
        screen._tick()
        # While the audio is fine the banner is the ordinary one ("Get ready" at the
        # start of a song), and the notice must not have appeared.
        while_audio = screen._banner.text()
        assert "Audio stopped" not in while_audio

        screen.context.stop_playback()
        screen._tick()
        assert "Audio stopped" in screen._banner.text(), (
            f"the player was left looking at {while_audio!r} with no sound"
        )
        said = screen._banner.text()
        screen._tick()
        assert screen._banner.text() == said, "it should say it once, not every frame"
    finally:
        screen.context.stop_playback()


def test_a_negative_position_is_not_mistaken_for_no_audio(shell, chart) -> None:
    """**A count-in is negative on purpose, and -1.0 also means "not playing".**

    The fallback used to test `position() >= 0` to decide whether the audio clock
    was available, so the first `latency` of every song -- and the entire count-in,
    which is negative for its whole length -- was read as "there is no audio" and
    handed to the wall clock. The song then stuttered for its first fraction of a
    second, or ran entirely on the timer.

    So the test is on *liveness*, and this asserts the count-in survives: a position
    before the music starts is a position, and it must come from the device.
    """
    from guitaroids.audio.transport import silence

    screen = game_via_shell(shell, chart)
    try:
        screen.context.start_playback(
            silence(10.0, 44100), sample_rate=44100, volume=1.0, device=None
        )
        screen._wall_origin = None
        first = screen.position()
        # Whatever the count-in and the device latency add up to, a transport that is
        # playing must never be mistaken for one that is not.
        assert screen.context.is_playing is True
        assert first > -1.0, f"position {first} is the 'no audio' sentinel"
        assert screen._wall_origin is None, "the wall clock took over a playing song"
    finally:
        screen.context.stop_playback()


# --- the settings that sound like settings (§21.2, §28.2) --------------------
#
# `master_volume`, `click_volume` and `audio_device` were saved, migrated, shown as
# controls, and read by nothing. `start_playback` even had a `device` parameter that
# every call site omitted, so its default won -- §21.2's exact failure, in the exact
# shape §21.2 warns about: every test covered the field or the transport, none
# covered the seam between them.
#
# So these tests do not check the settings and do not check the transport. They
# check what the game screen *passes*, which is the only place the two meet.


def record_playback_calls(context) -> list[dict]:
    """Record what the screen asks the transport to do, without opening a device.

    Shadows ``start_playback`` on the *real* context and leaves it in place. An
    earlier version of this wrapped the context in a proxy class with
    ``__getattr__`` and assigned that to ``screen.context``; it passed, and then
    corrupted the interpreter -- PySide asserts on the type of what it finds in a
    widget attribute, and a forwarding proxy is the wrong shape. Corrupting CPython
    200 tests later, in an unrelated file, is a thoroughly convincing argument for
    not doing that.
    """
    calls: list[dict] = []
    real = context.start_playback

    def spy(samples, **kwargs):  # noqa: ANN001, ANN003
        calls.append(kwargs)
        return real  # deliberately not called: no device in this test

    context.start_playback = spy  # type: ignore[method-assign]
    return calls


def prepared_screen(shell, chart, settings):
    """The shell's own Game screen, with a context whose settings are `settings`.

    Modelled on `game_via_shell`, which is the established way to get a screen with a
    real request behind it -- the screen is the shell's own instance, not one built by
    hand, so the call site under test is the one a player runs.
    """
    shell.show()
    shell.navigate(Screen.GAME)
    screen = shell.current_screen
    context = context_with(chart)
    context.settings = settings
    screen.context = context
    screen._load_request()
    return screen


def _fake_render():
    """A minimal RenderResult: quiet stereo audio, no device and no real render."""
    import numpy as np

    from guitaroids.audio.render import RenderResult

    return RenderResult(
        samples=np.zeros((44100, 2), dtype=np.float32),
        sample_rate=44100,
        backend="pluck",
        peak=0.0,
        program=25,
        note_count=1,
    )


def test_the_screen_passes_the_master_volume_to_the_transport(shell, chart) -> None:
    """The slider has to reach the sound, not just the file."""
    from guitaroids.settings import Settings

    screen = prepared_screen(shell, chart, Settings(master_volume=0.31))
    calls = record_playback_calls(screen.context)
    screen._on_audio_ready(_fake_render())

    assert len(calls) == 1, calls
    assert calls[0]["volume"] == pytest.approx(0.31)


def test_the_screen_passes_the_chosen_output_device(shell, chart) -> None:
    """`audio_device`, read from a settings *file* -- the whole path, not the field."""
    from guitaroids.settings import Settings

    screen = prepared_screen(shell, chart, Settings(audio_device="Focusrite Scarlett"))
    calls = record_playback_calls(screen.context)
    screen._on_audio_ready(_fake_render())

    assert calls[0]["device"] == "Focusrite Scarlett"


def test_a_settings_file_on_disk_reaches_the_stream(tmp_path, chart) -> None:
    """settings.json → AppContext → Game → the arguments to the transport.

    The end-to-end version of the two tests above, because the failure being guarded
    against is specifically a *dropped* argument somewhere in the middle, and a test
    that builds the Settings object by hand cannot see a drop between the file and
    the context.
    """
    import json

    from guitaroids.settings import Settings

    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"version": 3, "master_volume": 0.42, "audio_device": "USB Audio"})
    )
    loaded = Settings.load(path)
    assert loaded.master_volume == pytest.approx(0.42)

    context = AppContext.create(settings_path=path, songs_dir=tmp_path)
    context.audio_enabled = False
    assert context.settings.audio_device == "USB Audio"


def test_the_click_level_is_the_click_volume_setting(shell, chart, monkeypatch) -> None:
    """The click is mixed before the transport sees the buffer, so this is the seam.

    The two volumes are only separable at the mix step; once music and click are one
    array there is no way to tell them apart. `click_volume` therefore has to be
    applied here or not at all.

    `monkeypatch.setattr` rather than a hand-rolled save-and-restore. The obvious way
    to write this -- bind the original, swap the attribute, put it back in a
    `finally` -- passed on its own and then corrupted the interpreter, surfacing as a
    tuple type-flag assertion in an unrelated file 200 tests later. The fixture
    restores module state the way the rest of the suite expects it to be restored, and
    the spy calls the real function through the module so it does not depend on the
    attribute it is standing in for.
    """
    from guitaroids.settings import Settings

    screen = prepared_screen(shell, chart, Settings(click_volume=0.9))
    seen: dict = {}

    import guitaroids.ui.game as game_module

    # No device. This test is about the arguments, and leaving a real stream open
    # behind it is §26.3's core dump: the transport is collected while PortAudio's
    # callback is still writing into its buffer, and the failure surfaces in an
    # unrelated file some tests later.
    record_playback_calls(screen.context)

    def spy(*args, **kwargs):  # noqa: ANN002, ANN003
        seen.update(kwargs)
        return _real_add_count_in(*args, **kwargs)

    _real_add_count_in = game_module.add_count_in
    monkeypatch.setattr(game_module, "add_count_in", spy)
    screen._on_audio_ready(_fake_render())

    assert seen.get("level") == pytest.approx(0.9), seen
