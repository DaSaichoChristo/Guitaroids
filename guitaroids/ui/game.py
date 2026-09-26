"""The game screen: a tab view, a clock, six keys, and a tally.

This is the first screen that needs a *position*, and therefore the first that needs
a clock. The clock here is a **wall clock** (``QElapsedTimer``) and that is a
deliberate limitation, not an oversight:

- It is self-consistent. The number drawn on the tab view and the number judged
  against come from the same source, so pressing a key exactly as a note crosses the
  playline scores PERFECT. That is enough to verify lane/key alignment, note
  timing, hit feedback and the whole judging path.
- It cannot tell you whether the game *feels* right. That needs the audio clock of
  DESIGN.md §1.5, with a metronome the player can hear. DESIGN.md §3.5 calls the
  click-placement test "the highest-value test in the project" and schedules it
  before the highway widget; it did not get there first, so the audio transport is
  the next milestone rather than a rewrite of this screen.

Everything below the clock is already testable without any of it: the widget is
pure render, and ``GameState`` is pure logic.

The screen owns the clock and a ``GameState``, and hands the widget a position each
frame. It owns no device, so DESIGN.md §1.7 -- screens never own game objects --
holds here for a slightly different reason than it was written for: there is no
audio stream or camera handle to leak *yet*.

Key mapping is ``1``-``6`` for lanes 0-5, chosen because it needs no legend to be
understood. It is a hard-coded table rather than a setting because there is exactly
one mapping to have.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

from ..model.chart import Chart
from ..session.judge import GameState, Verdict
from .screens import ScreenBase, constrained_button, content_column, heading
from .theme import COLORS, px
from .widgets.tabview import TabView

if TYPE_CHECKING:  # pragma: no cover - types only
    from ..context import AppContext

_CENTRED = QtCore.Qt.AlignmentFlag.AlignHCenter

#: Digit keys 1-6 for lanes 0-5.
KEY_LANES: dict[int, int] = {
    QtCore.Qt.Key.Key_1: 0,
    QtCore.Qt.Key.Key_2: 1,
    QtCore.Qt.Key.Key_3: 2,
    QtCore.Qt.Key.Key_4: 3,
    QtCore.Qt.Key.Key_5: 4,
    QtCore.Qt.Key.Key_6: 5,
}

#: The frame timer. 60Hz is the display; nothing here needs more, and the widget
#: draws from a position rather than integrating anything.
FRAME_MS = 16


class Game(ScreenBase):
    """Play a chart with six keys."""

    TITLE = "game"

    def __init__(
        self,
        shell,
        context: "AppContext",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(shell, context, parent)

        self._chart: Chart | None = None
        self._state: GameState | None = None
        self._clock = QtCore.QElapsedTimer()
        self._offset = 0.0
        self._finished = False
        self._last_verdict: tuple[Verdict, float] | None = None

        self._view = TabView(self)
        self._build_hud()

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)

        self._place_hud()
        self._load_request()

    # --- construction --------------------------------------------------------

    def _build_hud(self) -> None:
        """The score row, laid out over the top of the tab view.

        Built as children of the screen and floated by ``resizeEvent``, so the
        playfield keeps the whole window -- a rhythm game that shrinks it to
        make room for a score is worse than one with no score. Every label is
        **parented to the screen**: a parentless QLabel is a top-level window, and
        showing one opens a second window floating over the game.
        """
        self._title = heading("", kind="heading", parent=self)
        self._title.setObjectName("gameTitle")
        self._title.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        self._tally = heading("", kind="stat", parent=self)
        self._tally.setObjectName("gameTally")
        self._tally.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        self._flash = heading("", kind="heading", parent=self)
        self._flash.setObjectName("gameFlash")
        self._flash.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self._banner = heading("", kind="subtitle", parent=self)
        self._banner.setObjectName("gameBanner")
        self._banner.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._banner.setWordWrap(True)

        self._legend = self._build_legend()
        self._quit = constrained_button("Back to menu", width=200, parent=self)
        self._quit.clicked.connect(self._leave)
        # Deliberately no setFocus here. The shell focuses the screen after building
        # it, and that is what should hold focus -- a key press goes to the focused
        # widget, so focus on this screen is what makes the six keys work at all.
        # Escape already navigates back, so the button does not need the keyboard.

    def _build_legend(self) -> QtWidgets.QLabel:
        from .theme import LANE_COUNT

        names = ["high E", "B", "G", "D", "A", "low E"][:LANE_COUNT]
        label = heading(
            "   ".join(f"{i + 1} = {name}" for i, name in enumerate(names)),
            kind="dim",
            parent=self,
        )
        label.setObjectName("gameLegend")
        return label

    def _place_hud(self) -> None:
        """Float the HUD, and make the tab view fill the window.

        Manual geometry rather than a layout, because the HUD is an overlay: the
        view is the screen, and a QVBoxLayout holding both would shrink the
        playfield to make room for a score.

        Called from ``resizeEvent`` *and* once at the end of ``__init__``, because a
        widget that has never been resized has not received a ``resizeEvent`` and
        the view would otherwise sit at its default 640x480 on first show.
        """
        self._view.setGeometry(self.rect())
        width, height = self.width(), self.height()
        pad = px(20)
        self._title.setGeometry(pad, px(12), width // 2, px(40))
        self._tally.setGeometry(width // 2, px(12), width // 2 - pad, px(40))
        self._flash.setGeometry(0, height // 5, width, px(80))
        self._banner.setGeometry(pad, height // 2 - px(40), width - pad * 2, px(80))
        # The legend starts clear of the button rather than under it. Both are on
        # the bottom row, and the button is opaque, so sharing a left edge hides
        # the first four entries behind it.
        button_right = pad + px(200) + px(16)
        self._legend.setGeometry(button_right, height - px(56), width - button_right - pad, px(24))
        self._quit.setGeometry(pad, height - px(30) - px(28), px(200), px(28))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._place_hud()

    # --- loading -------------------------------------------------------------

    def _load_request(self) -> None:
        """Resolve the context's play request into a chart and start a run.

        A missing request, or one whose chart will not resolve, is an **empty
        state**, not a crash: ``context.chart_for()`` returns ``None`` by design and
        the player has a way back.
        """
        chart = self.context.chart_for()
        if chart is None or not chart.notes:
            self._chart = None
            self._state = None
            self._view.set_chart(None)
            self._title.setText("")
            self._tally.setText("")
            self._banner.setText(
                "No song selected.\n\nGo back and choose a song to play."
            )
            self._flash.setText("")
            return

        self._chart = chart
        self._state = GameState(chart)
        self._offset = float(self.context.play_request.offset_seconds)
        self._finished = False
        self._last_verdict = None
        self._view.set_chart(chart)
        self._title.setText(chart.title or chart.track_name)
        self._banner.setText("")
        self._flash.setText("")
        self._clock.start()
        self._timer.start()
        self._refresh_tally()

    # --- the clock -----------------------------------------------------------

    def position(self) -> float:
        """Chart position, in seconds. The one number everything else reads."""
        if not self._clock.isValid():
            return 0.0
        return self._clock.elapsed() / 1000.0 - self._offset

    def _tick(self) -> None:
        position = self.position()
        if self._state is not None:
            self._state.update(position)
        self._view.set_position(position)
        self._refresh_tally()
        if self._state is not None and not self._finished:
            if self._state.outstanding == 0:
                self._finished = True
        self._refresh_banner(position)

    def _refresh_banner(self, position: float) -> None:
        """Three states, one label: empty library, get ready, complete.

        The get-ready state exists because the real tab's first note is 3.16s in.
        Three seconds of empty notation looks like a broken game rather than like a
        song about to start, and there is nothing else on screen to explain it.
        """
        if self._chart is None or self._state is None:
            return
        if self._finished:
            self._banner.setText("Song complete.")
            return
        first = self._chart.notes[0].time
        if position < first and self._state.outstanding == self._state.note_count:
            self._banner.setText("Get ready")
        elif self._banner.text() == "Get ready":
            self._banner.setText("")

    # --- input ---------------------------------------------------------------

    def eventFilter(self, watched, event) -> bool:  # noqa: N802, ANN001 - Qt naming
        """Catch a lane key wherever it lands, while this screen is up.

        A rhythm game cannot depend on Qt focus. Focus is only granted to an
        *active* window, so a digit key goes to the window background or to the Back
        button instead of here, and the game is silently unplayable. In the offscreen
        test platform no widget has focus at all, which is what made this visible.

        Only the six lane keys are consumed. Everything else -- Space on the button,
        Escape to go back -- is left to propagate as normal, and an event aimed at
        this screen is passed straight through so ``keyPressEvent`` handles it
        exactly once.
        """
        if event.type() == QtCore.QEvent.Type.KeyPress and watched is not self:
            lane = KEY_LANES.get(event.key())
            if lane is not None:
                self._press(lane)
                return True
        return False

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:  # noqa: N802 - Qt naming
        lane = KEY_LANES.get(event.key())
        if lane is None:
            super().keyPressEvent(event)
            return
        event.accept()
        self._press(lane)

    def _press(self, lane: int) -> None:
        """Judge a keypress. Separate from the Qt event so tests can drive it."""
        if self._state is None or self._finished:
            return
        judgement = self._state.press(lane, self.position())
        if judgement is None:
            return
        self._last_verdict = (judgement.verdict, judgement.delta_seconds)
        self._flash.setText(_verdict_text(judgement))

    def _refresh_tally(self) -> None:
        if self._state is None:
            return
        tally = self._state.counts()
        self._tally.setText(
            f"{tally[Verdict.PERFECT]} perfect   "
            f"{tally[Verdict.GOOD]} good   "
            f"{tally[Verdict.MISS]} miss   "
            f"{self._state.accuracy * 100:.0f}%"
        )

    # --- lifecycle ------------------------------------------------------------

    def _leave(self) -> None:
        self._stop()
        self.shell.go_back()

    def _stop(self) -> None:
        self._timer.stop()
        self._clock.invalidate()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # noqa: N802 - Qt naming
        """Stop the clock and uninstall the key filter when navigated away from.

        A QTimer left running would keep calling ``_tick`` -- and keep mutating the
        tally -- on a screen the player is no longer looking at. The filter has to go
        with it or the keys keep driving the game from the menu.
        """
        self._stop()
        self._stop_filter()
        super().hideEvent(event)

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 - Qt naming
        """Install the key filter, and reload on every visit.

        The shell keeps built screens, so this is not constructed afresh the second
        time. Re-entering GAME therefore needs an explicit reload, or the player
        would resume a finished song.
        """
        super().showEvent(event)
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self.setFocus()
        if self._chart is not None and not self._timer.isActive():
            self._load_request()

    def _stop_filter(self) -> None:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)

    # --- accessors for tests --------------------------------------------------

    @property
    def view(self) -> TabView:
        return self._view

    @property
    def state(self) -> GameState | None:
        return self._state

    @property
    def chart(self) -> Chart | None:
        return self._chart


def _verdict_text(judgement) -> str:  # noqa: ANN001 - Judgement
    """The flash shown when a note resolves."""
    if judgement.verdict is Verdict.STRAY:
        return ""
    if judgement.verdict is Verdict.PERFECT:
        return "PERFECT"
    if judgement.verdict is Verdict.GOOD:
        return "GOOD"
    return "MISS"
