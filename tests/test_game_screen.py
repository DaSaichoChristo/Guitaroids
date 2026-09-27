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
from guitaroids.ui.game import TIMING_SAMPLES, Game
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





def _hit(screen, lane: int) -> None:
    """Detect the pitch of the chart's first note on ``lane``, at the current position.

    The keyboard tests this replaces asked for a *lane*; the input is a pitch now, and
    the pitch has to come from the chart or the test would be asserting that a number
    happens to match another number.
    """
    note = next(n for n in screen.chart.notes if n.lane == lane)
    screen._on_pitch(note.pitch)


def test_pressing_at_the_note_time_is_perfect(game: Game) -> None:
    """Drives ``_on_pitch`` at a known position by freezing the clock first.

    ``_clock`` is replaced with a stub rather than slept against, because
    QElapsedTimer cannot be made to report an exact time and a test that waits for
    one is either slow or flaky. The input is a detected pitch rather than a key, and
    it is the microphone's path all the way to the verdict (§30.2).
    """
    game._clock = _FrozenClock(2.0)
    _hit(game, 3)
    judgement = game.state.judgements[-1]
    assert judgement.verdict is Verdict.PERFECT
    assert judgement.lane == 3, "the CHART note's lane, not a guess at the string"
    assert game._flash.text() == "PERFECT"


def test_a_miss_flashes_miss(game: Game) -> None:
    """A missed note says *how* it was missed, because that is the actionable half.

    §39. A bare "MISS" cannot be acted on: 100ms late and 100ms early need opposite
    corrections to the input-latency trim, and the tally reads "MISS 0%" for both.
    """
    game._clock = _FrozenClock(2.0 + 0.100)
    _hit(game, 1)
    assert game.state.judgements[-1].verdict is Verdict.MISS
    assert game._flash.text() == "MISS  100ms late"
    assert game.state.misses == 1


def test_an_early_miss_says_early(game: Game) -> None:
    """The other direction, because the trim is *subtracted* and overshoots early.

    This is the trap the setting walked the user into: they read "consistently late",
    raised the trim, overshot, and every note then read as early by a larger amount
    -- with a bare "MISS" on screen, which looks exactly like the original problem.
    """
    game._clock = _FrozenClock(2.0 - 0.100)
    _hit(game, 1)
    assert game._flash.text() == "MISS  100ms early"


def test_a_perfect_within_five_ms_says_just_perfect(game: Game) -> None:
    """The direction is only worth printing when it is big enough to act on.

    A note hit 3ms early is PERFECT, and "PERFECT 3ms early" is noise on a screen
    that flashes per note. The threshold is the same 5ms the timing readout uses to
    say "on time", so the two never contradict each other.
    """
    game._clock = _FrozenClock(2.0 - 0.003)
    _hit(game, 3)
    assert game.state.judgements[-1].verdict is Verdict.PERFECT
    assert game._flash.text() == "PERFECT"


def test_a_note_that_expired_with_no_press_reports_no_error(game: Game) -> None:
    """An expiry is not an attempt, so it carries no timing to act on.

    `update()` resolves a note that nobody played, and its `delta_seconds` is how far
    past the MISS window the clock had travelled -- always a little over 140ms, and
    about the player. Printing it would put a permanent "MISS 141ms late" on screen
    for a note that was never played. §39.
    """
    game._clock = _FrozenClock(2.0 + 0.150)
    game.state.update(game.position())
    expired = game.state.judgements[-1]
    assert expired.verdict is Verdict.MISS
    assert expired.pressed is False
    assert game._flash.text() == "", "no press, no flash"
    assert game._timing.text() == "", "and nothing for the timing readout to report"


def test_a_stray_flashes_nothing(game: Game) -> None:
    """A pitch the song never asks for is a stray, and puts no word on screen.

    This is the microphone's version of "pressed an empty lane": the only detectable
    wrong note is one the chart does not contain, because a pitch it *does* contain with
    nothing resolvable now is a held note still sounding (§30.2).
    """
    game._clock = _FrozenClock(2.0)
    used = {n.pitch for n in game.chart.notes}
    game._on_pitch(max(used) + 6)
    assert game.state.judgements[-1].verdict is Verdict.STRAY
    assert game._flash.text() == ""


def test_pressing_with_no_song_does_nothing(shell) -> None:
    context = AppContext(songs_dir=Path("songs"))
    screen = Game(shell, context)
    try:
        screen._on_pitch(64)
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


def test_the_fake_microphone_is_autouse(shell, chart) -> None:
    """Guarding the guard, because §32 made this the difference between a suite and a
    crash.

    Every test in this file builds a Game, every Game opens an input device, and an
    input device opened by a test is a segfault some tests later. If this assertion
    ever fails, the fixture lost its `autouse=True` and the whole file leaks again.
    """
    screen = game_via_shell(shell, chart)
    assert type(screen._mic).__name__ == "FakeMicrophone", (
        f"a real {type(screen._mic).__name__} was opened: the fake_mic fixture is not "
        "autouse any more, and every test in this file now opens an input device"
    )


def test_the_microphone_signal_reaches_the_judge(shell, chart) -> None:
    """The path a real detection takes, minus the device.

    A worker thread calls the screen's callback, which emits a signal, which Qt delivers
    on the GUI thread. Emitting the signal is exactly the last step of that, so this
    covers the wiring without an input device -- the same trick the renderer tests use.
    """
    from guitaroids.audio.pitch import PitchEstimate
    from guitaroids.audio.pitch import midi_to_hz

    screen = game_via_shell(shell, chart)
    screen._clock = _FrozenClock(2.0)
    note = next(n for n in screen.chart.notes if abs(n.time - 2.0) < 0.01)

    # What the microphone's worker does: hand the screen an estimate.
    screen._on_estimate(PitchEstimate(hz=midi_to_hz(note.pitch), midi=note.pitch,
                                      clarity=0.95, rms=0.2))
    assert screen.state.resolved == 1, "the detection did not reach the judge"
    assert screen.state.judgements[-1].verdict is Verdict.PERFECT


# --- the tally ---------------------------------------------------------------


def test_the_tally_starts_empty(game: Game) -> None:
    game._refresh_tally()
    text = game._tally.text()
    assert "0 perfect" in text and "0 good" in text and "0 miss" in text


def test_the_tally_counts_a_hit(game: Game) -> None:
    game._clock = _FrozenClock(2.0)
    _hit(game, 0)
    game._refresh_tally()
    assert "1 perfect" in game._tally.text()
    assert "100%" in game._tally.text(), "one note judged, one hit"


def test_a_miss_shows_in_the_tally(game: Game) -> None:
    game._clock = _FrozenClock(2.0 + 0.100)
    _hit(game, 0)
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
    _hit(game, 0)
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


def test_leaving_stops_the_sound(shell, chart) -> None:
    """The bug: the song carried on playing into the main menu.

    `hideEvent` stopped the QTimer and the clock and nothing else. The only
    `stop_playback()` in the UI was in `_render_audio`, which runs when a *new* song
    starts, so nothing ever stopped what was already playing. The timer assertion
    above passed the whole time the music did not.
    """
    from guitaroids.audio.transport import silence

    screen = game_via_shell(shell, chart)
    try:
        screen.context.start_playback(
            silence(30.0, 44100), sample_rate=44100, volume=1.0, device=None
        )
        assert screen.context.is_playing is True

        shell.navigate(Screen.MAIN)

        assert screen.context.is_playing is False
        assert screen.context.playback is None, "the stream was not closed"
    finally:
        screen.context.stop_playback()


def test_leaving_mid_render_cancels_it(shell, chart) -> None:
    """The worse half of the same bug, and it is the one you would hear.

    `ChartRenderer` has had a `cancel()` since it was written and `should_stop` is
    polled at every onset -- and `test_render_task.py` has proved that a cancelled
    render emits `cancelled` and never delivers a buffer. But nothing in the UI ever
    called `cancel()`. So pressing Back during a six-second render let the render
    finish, and `_on_audio_ready` -- whose only guard was `if self._chart is None` --
    opened the stream. The song started in the menu.

    The seam is what is asserted here: that leaving *calls* `cancel`. Asserting
    `is_running` went false would be vacuous, because a render that simply finished
    would satisfy it too.
    """
    screen = game_via_shell(shell, chart)
    calls: list[bool] = []
    real = screen._renderer.cancel

    def recorder() -> None:
        calls.append(True)
        real()

    screen._renderer.cancel = recorder  # type: ignore[method-assign]
    shell.navigate(Screen.MAIN)
    assert calls, "leaving did not cancel the render in flight"

    # And the late result, if one were already queued, must not open a stream.
    screen._on_audio_ready(_fake_render())
    assert screen.context.playback is None, "a late render started a song on a hidden screen"


def test_quitting_stops_the_sound(shell, chart) -> None:
    """No close handler existed at all, so quitting mid-song relied on teardown."""
    from guitaroids.audio.transport import silence

    screen = game_via_shell(shell, chart)
    try:
        screen.context.start_playback(
            silence(30.0, 44100), sample_rate=44100, volume=1.0, device=None
        )
        shell.close()
        assert screen.context.playback is None
    finally:
        screen.context.stop_playback()


def test_revisiting_starts_a_fresh_run(shell, chart) -> None:
    """The shell keeps built screens, so GAME is not new the second time."""
    screen = game_via_shell(shell, chart)
    screen._clock = _FrozenClock(2.0)
    _hit(screen, 0)
    assert screen.state.resolved == 1
    shell.navigate(Screen.MAIN)
    shell.navigate(Screen.GAME)
    assert screen.state.resolved == 0, "re-entering must not resume a finished run"


# --- structure ---------------------------------------------------------------


def test_the_screen_owns_a_tab_view(game: Game) -> None:
    assert isinstance(game.view, TabView)






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
    _hit(screen, 0)
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
        _hit(screen, 0)
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


# --- the practice tempo actually slowing something (§29.2) --------------------
#
# The rate was saved, carried in `PlayRequest`, read into `screen._rate`, printed by
# `rate` and asserted by a dozen tests -- and applied to `position()` on ONE of its
# two branches, the wall-clock fallback. A playing song takes the other branch, so a
# practice tempo changed nothing at all. Every existing test checked the arithmetic
# (`rate_for`) or the field (`screen.rate`); none checked the seam. This is §21.2's
# failure for the third time in this project.


def test_slowing_the_tempo_slows_the_notes_itself(shell, chart) -> None:
    """The chart the game hands to the judge and the view is a slower chart.

    Not a field check: these are the note times `GameState` will judge against.
    """
    written = [n.time for n in chart.notes]
    screen = game_via_shell(shell, chart, bpm=chart.tempo / 2)
    assert screen.rate == pytest.approx(0.5)
    assert [n.time for n in screen.chart.notes] == pytest.approx([t / 0.5 for t in written])
    assert screen.chart.tempo == max(1, round(chart.tempo * 0.5))


def test_slowing_the_tempo_makes_the_rendered_audio_longer(shell, chart) -> None:
    """The music has to slow, or the bar crawls under a song playing at full speed.

    This is the half that makes the bug audible rather than merely measurable: the
    rendered buffer is what the sound card plays, and until §29 it was rendered from
    the un-retimed chart while the clock was the audio clock.
    """
    from guitaroids.settings import Settings

    screen = prepared_screen(shell, chart, Settings())
    record_playback_calls(screen.context)
    rendered: list = []
    real_render = screen._render_audio

    def spy(chart_arg, request):  # noqa: ANN001
        rendered.append(chart_arg)
        return real_render(chart_arg, request)

    screen._render_audio = spy  # type: ignore[method-assign]
    screen.context.settings.set_bpm_for("test", chart.tempo / 2)
    screen.context.request_play("test", 1, bpm=chart.tempo / 2)
    screen._load_request()

    assert rendered, "no render was started"
    assert rendered[-1].notes[-1].time == pytest.approx(chart.notes[-1].time / 0.5)


def test_the_written_tempo_is_not_the_slowed_one(shell, chart) -> None:
    """`rate_for` divides by the tab's written tempo, so it must not see the slow one.

    Both orderings are load-bearing and one of them is counter-intuitive: resolving
    the rate *after* retiming asks a chart already halved for a half-speed request and
    gets 1.0 back, which is exactly what three tests reported until this was run.
    """
    screen = game_via_shell(shell, chart, bpm=chart.tempo / 2)
    assert screen.written_bpm() == max(1, round(chart.tempo * 0.5))
    # The *rate* was computed from the written tempo, which is why it is 0.5 and not
    # 0.25 (asking a halved chart for half of itself) or 1.0.
    assert screen.rate == pytest.approx(0.5)


def test_a_song_at_its_written_tempo_is_untouched(shell, chart) -> None:
    """`retime` returns the same object at 1.0, so the common case allocates nothing."""
    from guitaroids.model.chart import retime

    assert retime(chart, 1.0) is chart
    screen = game_via_shell(shell, chart)
    assert screen.chart.notes == chart.notes


def test_the_count_in_slows_with_the_song(shell, chart) -> None:
    """At half speed a four-beat count-in is twice as long, and that is the intent.

    §29 records the cost honestly: at 0.5 of a 76 BPM tab a bar is 6.3 seconds, so
    the wait before the first note is long. It is a constant offset rather than one
    that grows, and the click slowing with the music is what makes the bar line land
    where the player is expecting it.
    """
    written = game_via_shell(shell, chart)._count_in_seconds()
    screen = game_via_shell(shell, chart, bpm=chart.tempo / 2)
    assert screen._count_in_seconds() == pytest.approx(written / 0.5)


# --- the microphone's way in (§29.4) ------------------------------------------
#
# Driven by emitting the screen's signal, exactly as the worker thread would. No
# device, no thread and no test-only code in the product: the same path a real
# detection takes, minus the microphone.


class FakeMicrophone:
    """A `Microphone` that records and opens nothing. See the `fake_mic` fixture."""

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        self.kwargs = kwargs
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    class detector:  # noqa: N801 - a stand-in, not a real class
        # **Zero, deliberately.** A real detector contributes 23.2ms (§30.3), and that
        # is a microphone fact. Every test that is not *about* the latency would
        # otherwise have 23ms baked into the position it asserts on -- a 100ms-late
        # note reads GOOD rather than MISS, which is how four unrelated tests failed
        # when the microphone became the only input. The latency tests set the number
        # they need themselves.
        latency_seconds = 0.0


@pytest.fixture(autouse=True)
def fake_mic(monkeypatch):
    """Replace the `Microphone` class, so no test in this file can open a real device.

    **A test that opens an input stream corrupts the interpreter.** This file leaked
    one exactly like that -- `test_leaving_stops_the_input_device_too` asked for
    the microphone without patching, `prepared_screen` called
    `_start_microphone`, a real device opened, and the test then replaced
    `screen._mic` with a fake, leaving the stream running with nothing holding it. The
    suite passed on its own and dumped core in the full run, in a test that had nothing
    to do with audio: the same failure as §29.3, from the same mistake, two commits
    later.

    **Autouse**, and that is the change §32 forced. While the microphone was one of two
    modes, only the tests that asked for it needed this. It is now the only input, so
    *every* test in this file constructs a screen that opens an input device -- the
    `game` fixture included, which is most of the file. Leaving it non-autouse meant
    that four tests judged against a real detector's 23.2ms of latency (a 100ms-late
    note reading GOOD instead of MISS) and the process dumped core on teardown.

    So: autouse, and a test below asserts it, because an autouse fixture that quietly
    stops being autouse reopens the hole with nothing to say so.
    """
    import guitaroids.ui.game as game_module

    monkeypatch.setattr(game_module, "Microphone", FakeMicrophone)
    return FakeMicrophone


def mic_screen(shell, chart, **settings_kwargs):
    from guitaroids.settings import Settings

    return prepared_screen(shell, chart, Settings(**settings_kwargs))
def _judge_at(screen, seconds: float) -> None:
    """Freeze the screen's clock at `seconds`, so `position()` is exactly that."""
    screen._preparing = False
    screen._clock = _FrozenClock(seconds)
    screen._wall_origin = 0.0
    screen.context.stop_playback()


def test_a_detected_pitch_is_judged(shell, chart) -> None:
    screen = mic_screen(shell, chart)
    note = screen.chart.notes[0]
    _judge_at(screen, note.time)
    screen._on_pitch(note.pitch)
    assert screen.state.resolved == 1, "the detected note was not judged"
    assert screen.state.judgements[0].verdict is Verdict.PERFECT
    assert screen._flash.text(), "no feedback was shown"


def test_a_detection_with_nothing_to_hit_is_silent(shell, chart) -> None:
    """Most of what a microphone hears is not a note, and must cost nothing."""
    screen = mic_screen(shell, chart)
    _judge_at(screen, 1.0)  # between notes
    before = (screen.state.strays, screen.state.resolved)
    screen._on_pitch(chart.notes[0].pitch)
    assert (screen.state.strays, screen.state.resolved) == before


def test_the_analysis_latency_is_subtracted_before_judging(shell, chart) -> None:
    """The bug this prevents is invisible: every note a few milliseconds late.

    An estimate describes the middle of its 2048-sample window, so it is ~23ms behind
    the moment it was emitted. Uncorrected that is a systematic lateness on every note,
    and `input_latency_ms` on top of it is the player's own trim.

    A 100ms trim is used deliberately. Perfect is ±35ms and a note expires at 140ms, so
    a 100ms error is comfortably PERFECT when the correction has the right sign and
    comfortably MISS when it does not -- a 23ms error would pass either way, which
    would make this test prove nothing.
    """
    screen = mic_screen(shell, chart, input_latency_ms=100)
    note = screen.chart.notes[0]
    assert screen._input_latency() == pytest.approx(0.100)

    _judge_at(screen, note.time + 0.100)
    screen._on_pitch(note.pitch)
    assert screen.state.judgements[0].verdict is Verdict.PERFECT, (
        "the position was not corrected for the analysis latency"
    )

    # The control: the same detection with no trim at all is 100ms late, and a 100ms
    # error is a miss. Without this, the test above would also pass if `_on_pitch`
    # simply ignored the position.
    untrimmed = mic_screen(shell, chart, input_latency_ms=0)
    _judge_at(untrimmed, note.time + 0.100)
    untrimmed._on_pitch(note.pitch)
    assert untrimmed.state.judgements[0].verdict is Verdict.MISS


def test_the_detector_latency_is_included_in_the_trim(shell, chart) -> None:
    """The two latencies add: the computed half-window and the player's own."""
    screen = mic_screen(shell, chart, input_latency_ms=20)

    class FakeDetector:
        latency_seconds = 0.0232

    class FakeMic:
        # `stop` because the screen closes its microphone in `hideEvent`, and a fake
        # that cannot be stopped fails every later test in the file when the screen is
        # torn down. The first version omitted it and the error surfaced in an
        # unrelated test's teardown.
        def stop(self) -> None:
            pass

    fake = FakeMic()
    fake.detector = FakeDetector()
    screen._mic = fake
    assert screen._input_latency() == pytest.approx(0.0432, abs=1e-6)


def test_the_manual_latency_trim_is_read_at_all(shell, chart) -> None:
    """`input_latency_ms` is the first thing this setting has ever been read for.

    It was saved, migrated, shown as a spin box, and read by nothing (§24's Not done
    list). The latency correction is its only honest use: a computed 23ms cannot
    account for a player's own setup, and this is the trim for that.
    """
    screen = mic_screen(shell, chart, input_latency_ms=40)
    assert screen._input_latency() == pytest.approx(0.040)
    assert screen.context.settings.input_latency_ms == 40


def test_a_run_opens_the_microphone_exactly_once(shell, chart, monkeypatch) -> None:
    """Opening a stream is not idempotent, and the input is no longer optional.

    §32 made the microphone the only input, so every run opens a device -- which is
    exactly why the `fake_mic` fixture is not optional either, and why `_start_microphone`
    keeps its own guard rather than being called once per song by accident.

    `Microphone` is replaced so the real `_start_microphone` runs, **including its
    `if self._mic is not None: return`**. An earlier version replaced
    `_start_microphone` instead, which removed the guard along with the device, and so
    "opened twice" -- it was measuring its own spy.
    """
    import guitaroids.ui.game as game_module
    from guitaroids.settings import Settings

    started: list[bool] = []

    class FakeMic:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.kwargs = kwargs

        def start(self) -> None:
            started.append(True)

        def stop(self) -> None:
            pass

        class detector:  # noqa: N801 - a stand-in, not a real class
            # **Zero, deliberately.** A real detector contributes 23.2ms (§30.3), and
            # that is a microphone fact. Every test that is not *about* the latency
            # would otherwise have 23ms of it baked into the position it is asserting
            # on -- a 100ms-late note reads as GOOD rather than MISS, which is how two
            # unrelated tests failed when the microphone became unconditional. The
            # latency tests set the number they need explicitly.
            latency_seconds = 0.0

    monkeypatch.setattr(game_module, "Microphone", FakeMic)
    screen = prepared_screen(shell, chart, Settings())
    _judge_at(screen, 1.0)
    screen._load_request()
    assert started == [True], "a run did not open the input device"

    # A second run must not open it again: the stream lives with the screen.
    screen._load_request()
    assert started == [True], "the input device was opened twice"
    assert screen._mic_error == ""


def test_leaving_stops_the_input_device_too(shell, chart, fake_mic) -> None:
    """§29.1 fixed the output stream. The input one is held for the screen's life."""
    from guitaroids.settings import Settings

    screen = prepared_screen(shell, chart, Settings())
    stopped: list[bool] = []

    class FakeMic:
        def stop(self) -> None:
            stopped.append(True)

    assert isinstance(screen._mic, fake_mic), "the fixture did not take"
    screen._mic = FakeMic()
    shell.navigate(Screen.MAIN)
    assert stopped, "the input device was left open on a hidden screen"


# --- publishing the result and going to it (§34) -------------------------------
#
# The transition is the fiddly part, because "every note is judged" and "the song has
# finished sounding" are about twelve seconds apart on a real tab, and the guard that
# noticed the audio stopping used to be `not self._finished` -- which is exactly the
# moment the results screen needs.


def _finished_screen(shell, chart, *, with_audio: bool):
    """A game screen whose last note has been judged, and whose audio is as described."""
    from guitaroids.audio.transport import silence

    screen = game_via_shell(shell, chart)
    _judge_at(screen, 0.0)
    # Judge every note, so outstanding is 0.
    for note in screen.chart.notes:
        screen.state.press_pitch(note.pitch, note.time)
    assert screen.state.outstanding == 0
    if with_audio:
        screen.context.start_playback(
            silence(30.0, 44100), sample_rate=44100, volume=1.0, device=None
        )
        screen._audio_live = True
    else:
        screen._audio_live = False
    return screen


def test_the_song_does_not_go_to_results_while_it_is_still_playing(shell, chart) -> None:
    """The whole point: a note being judged is not the song ending.

    Hotel California's last note is at 380.5s and its buffer runs to 392.5s. Leaving
    on the judgement would cut the tail off mid-phrase because a counter hit zero.
    """
    screen = _finished_screen(shell, chart, with_audio=True)
    try:
        screen._tick()
        assert screen._result_published is False
        assert shell.current is Screen.GAME
    finally:
        screen.context.stop_playback()


def test_the_song_goes_to_results_when_the_sound_ends(shell, chart) -> None:
    screen = _finished_screen(shell, chart, with_audio=True)
    try:
        screen.context.stop_playback()  # the tail ran out
        screen._tick()
        assert screen._result_published is True
        assert shell.current is Screen.RESULTS
        assert screen.context.last_result is not None
        assert screen.context.last_result.accuracy == pytest.approx(1.0)
    finally:
        screen.context.stop_playback()


def test_a_song_with_no_audio_goes_immediately(shell, chart) -> None:
    """The wall clock has no tail and no sound, so the last judgement IS the end."""
    screen = _finished_screen(shell, chart, with_audio=False)
    screen._tick()
    assert shell.current is Screen.RESULTS
    assert screen.context.last_result is not None


def test_it_goes_exactly_once(shell, chart) -> None:
    """Several frames will pass with the audio already stopped."""
    screen = _finished_screen(shell, chart, with_audio=False)
    screen._tick()
    first = screen.context.last_result
    shell.navigate(Screen.MAIN)
    shell.navigate(Screen.RESULTS)
    screen._tick()
    screen._tick()
    assert screen.context.last_result is first, "a second tick republished it"


def test_leaving_mid_song_publishes_nothing(shell, chart) -> None:
    """The attempt was abandoned; recording it as a run would be a lie."""
    screen = game_via_shell(shell, chart)
    _judge_at(screen, 0.0)
    screen._tick()
    shell.navigate(Screen.MAIN)
    assert screen.context.last_result is None


def test_a_device_lost_mid_song_is_not_mistaken_for_the_end(shell, chart) -> None:
    """The audio stopping before the last note is a failure, not a finished song.

    This is the pair the old `not self._finished` guard was protecting, and the reason
    `_maybe_publish` re-checks for itself: both look like "the stream is not playing".
    """
    from guitaroids.audio.transport import silence

    screen = game_via_shell(shell, chart)
    _judge_at(screen, 0.0)
    # No wall origin: this is the audio-live shape, and `_note_audio_lost` returns
    # early when one is set -- so leaving it in place tested the guard rather than the
    # behaviour. (The helper sets it because the wall path needs it to report a time.)
    screen._wall_origin = None
    screen.context.start_playback(
        silence(30.0, 44100), sample_rate=44100, volume=1.0, device=None
    )
    screen._audio_live = True
    try:
        screen.context.stop_playback()
        screen._tick()
        assert screen._result_published is False
        assert shell.current is Screen.GAME, "it must not navigate away mid-song"
        assert screen._audio_lost is True, "and it must say the audio stopped"
    finally:
        screen.context.stop_playback()


def test_a_run_at_the_written_tempo_says_as_written(shell, chart) -> None:
    """0.0, so the screen says "as written" rather than "practice tempo 60 BPM".

    A run at the tab's own tempo is not a practice tempo, and printing the number for
    it would be a small lie in the one line a player uses to compare two attempts.
    """
    screen = _finished_screen(shell, chart, with_audio=False)
    screen._tick()
    result = screen.context.last_result
    assert result.bpm == 0.0
    assert result.rate == pytest.approx(1.0)


def test_a_slowed_run_records_the_slowed_tempo(shell, chart) -> None:
    """A run at 75% is a different attempt from one at the written tempo."""
    screen = game_via_shell(shell, chart, bpm=chart.tempo * 0.75)
    _judge_at(screen, 0.0)
    for note in screen.chart.notes:
        screen.state.press_pitch(note.pitch, note.time)
    screen._audio_live = False
    screen._tick()
    result = screen.context.last_result
    assert result.rate == pytest.approx(0.75, abs=0.01)
    # §29.2 already scaled the chart's tempo, so the retimed chart IS the played
    # tempo. An earlier version multiplied by the rate again and reported 33.75 for
    # this run -- a tempo nobody played -- and a test at rate 1.0 could not see it.
    assert result.bpm == pytest.approx(chart.tempo * 0.75, abs=1.0)
    assert screen.chart.tempo == pytest.approx(chart.tempo * 0.75, abs=1.0)


def test_the_song_is_identified_on_the_result(shell, chart) -> None:
    screen = game_via_shell(shell, chart)
    _judge_at(screen, 0.0)
    for note in screen.chart.notes:
        screen.state.press_pitch(note.pitch, note.time)
    screen._audio_live = False
    screen._tick()
    result = screen.context.last_result
    assert result.title == (chart.title or chart.track_name)
    assert result.slug == "test", "and the slug the per-song best is keyed on"


def test_the_best_accuracy_is_recorded_by_the_game_screen(shell, chart) -> None:
    """The setting is written by the same code that publishes the result.

    §28.2's lesson: a preference that is saved, migrated and shown but written by
    nothing is the project's most repeated bug. This asserts the write happens on the
    real path, not that the settings class can store a number.
    """
    screen = _finished_screen(shell, chart, with_audio=False)
    screen._tick()
    assert screen.context.settings.best_accuracy_for("test") == pytest.approx(1.0)


# --- the timing readout: the number that makes the trim discoverable (§39) -----


def _spread_chart(notes: int = 2 * TIMING_SAMPLES + 2) -> "Chart":
    """A chart with notes spread over time, one lane at a time.

    The shared ``chart`` fixture is six notes all at 2.0s, which is the right shape
    for judging one note at a time and useless for anything that needs a *sequence*
    of notes to summarise. `tempo=60` makes a requested second land on that second.
    """
    from tests.songbuild import make_chart

    return make_chart(
        [(2.0 + index * 2.0, index % 6, index % 6) for index in range(notes)],
        collapse=False,
    )


def _play_offset(game: Game, offset: float, count: int, *, skip: int = 0) -> None:
    """Play ``count`` real notes, each ``offset`` seconds from where it is due.

    Presses the chart's own pitches at the chart's own times, so a miss is a real
    miss against a real note and the offset under test is the only variable. `skip`
    steps over the first few notes, for the test that needs the window to forget.
    """
    for note in game.chart.notes[skip : skip + count]:
        game._clock = _FrozenClock(note.time + offset)
        game._on_pitch(note.pitch)


@pytest.fixture()
def timed(shell) -> Game:
    """A game screen over a chart with notes in sequence."""
    context = context_with(_spread_chart())
    screen = Game(shell, context)
    yield screen
    screen._stop()
    screen.deleteLater()


def test_the_timing_readout_starts_empty(timed: Game) -> None:
    """Nothing played, nothing claimed. An offset invented from no evidence is a lie."""
    assert timed._timing.text() == ""


def test_the_timing_readout_reports_the_median_as_late(timed: Game) -> None:
    """Five notes 200ms late, then one at 900ms because the player fumbled.

    The median is 200 and the mean is 300. The mean would send them to set the trim
    to 300, overshoot by 100ms, and conclude the readout was broken -- which is the
    failure this exists to prevent. §39.
    """
    _play_offset(timed, 0.200, 5)
    assert timed._timing.text() == "timing  200ms late"
    _play_offset(timed, 0.900, 1, skip=5)
    assert timed._timing.text() == "timing  200ms late", "one fumble must not move it"


def test_the_timing_readout_says_early_when_the_notes_were_early(timed: Game) -> None:
    """The direction is the whole value. Early and late need opposite trims."""
    _play_offset(timed, -0.200, 5)
    assert timed._timing.text() == "timing  200ms early"


def test_the_timing_readout_says_on_time_inside_five_ms(timed: Game) -> None:
    _play_offset(timed, 0.002, 1)
    assert timed._timing.text() == "timing  on time"


def test_the_timing_readout_ignores_notes_that_expired_unplayed(timed: Game) -> None:
    """An expiry is not an attempt, so it must not colour the offset.

    `update()` resolves every note past its MISS window whether or not anything was
    played, and each such judgement carries a delta of a little over +140ms. Counting
    them would drag any answer toward +140ms and make a correctly-set trim look
    wrong. §39, and `Judgement.pressed`.
    """
    timed._clock = _FrozenClock(40.0)
    timed.state.update(timed.position())
    assert timed.state.misses > 5, "the fixture should have expired several notes"
    assert timed._timing.text() == "", "six unplayed expiries are not a timing"


def test_the_timing_readout_forgets_notes_beyond_its_window(timed: Game) -> None:
    """Bounded, so a bad patch of playing stops colouring the number.

    A player who flubbed the first bar and settled afterwards must see their *current*
    timing, not an average dominated by notes they have played past.
    """
    _play_offset(timed, 0.400, TIMING_SAMPLES)
    assert timed._timing.text() == "timing  400ms late"
    _play_offset(timed, 0.0, TIMING_SAMPLES, skip=TIMING_SAMPLES)
    assert timed._timing.text() == "timing  on time", "the old 400ms must be gone"


def test_the_timing_readout_does_not_overlap_the_tally_or_the_flash(game: Game) -> None:
    """The HUD uses manual geometry, so nothing would stop these colliding but a test.

    The tally is monospaced with space-aligned columns, so the timing cannot be
    appended to it -- it goes underneath, and "underneath" has to be checked rather
    than assumed, because `setGeometry` will happily place a label on top of its
    neighbour. §19.2's failure, by hand.
    """
    game.resize(960, 640)
    game._place_hud()
    tally, timing, flash = game._tally.geometry(), game._timing.geometry(), game._flash.geometry()
    assert not tally.intersects(timing), f"tally {tally} overlaps timing {timing}"
    assert not timing.intersects(flash), f"timing {timing} overlaps flash {flash}"
    assert timing.top() >= tally.bottom(), "the readout belongs under the tally"
    assert timing.right() <= game.width(), f"{timing} runs off a {game.width()}px window"
