"""The application shell: a stacked window that owns navigation.

Screens are registered as **factories, not instances**, so each is built the first
time it is shown. That matters as soon as a screen owns something expensive: once
the game screen opens an audio device and the calibration screen a camera, building
every screen at startup would open both just to display the main menu.

The shell also **owns the :class:`~guitaroids.context.AppContext`**. That is what
makes the context outlive every screen: a screen can borrow the library, but
navigating away cannot lose it. See ``context.py`` for why the shared state is not
simply a wider shell.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from ..context import AppContext
from .game import Game
from .import_gp import ImportGp
from .main_menu import MainMenu
from .preferences import Preferences
from .screens import Screen, ScreenBase, constrained_button, content_column, heading
from .theme import px
from .song_select import SongSelect

_CENTRED = QtCore.Qt.AlignmentFlag.AlignHCenter

#: Where each screen can go back to. MAIN is a root, so it is absent here.
_BACK: dict[Screen, Screen] = {
    Screen.SONG_SELECT: Screen.MAIN,
    Screen.GAME: Screen.SONG_SELECT,
    Screen.RESULTS: Screen.MAIN,
    Screen.PREFERENCES: Screen.MAIN,
    Screen.IMPORT_GP: Screen.MAIN,
}


class PlaceholderScreen(ScreenBase):
    """Stands in for a screen that has not been built yet.

    Deliberately obvious rather than plausible: a stub that looks finished is worse
    than one that admits it is not. The title is a constructor argument rather than
    a class attribute assigned afterwards, because the heading label is built
    during __init__ and would otherwise render empty.
    """

    def __init__(
        self,
        shell,
        context: AppContext,
        label: str,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(shell, context, parent)
        self.screen_label = label
        column = content_column(self)

        column.addWidget(heading(label))
        column.addWidget(
            heading(
                "This screen is not built yet.\n\n"
                "The shell, the theme and the navigation are real; this page is a "
                "placeholder so the flow can be clicked through end to end.",
                kind="subtitle",
            )
        )
        column.addStretch(1)

        back = constrained_button("Back", width=200)
        back.clicked.connect(self.shell.go_back)
        column.addWidget(back, alignment=_CENTRED)


class MainWindow(QtWidgets.QMainWindow):
    """Top-level window. Owns the stack, the history, and the context."""

    def __init__(
        self,
        context: AppContext,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Guitaroids")
        # Scaled, because a UI that has been enlarged past what the minimum size
        # allows cannot lay out properly -- and DESIGN.md §11.6 and §13 record what
        # a layout does when it is given less room than it needs.
        self.setMinimumSize(px(720), px(480))

        self.context = context
        self._stack = QtWidgets.QStackedWidget()
        self.setCentralWidget(self._stack)

        self._factories: dict[Screen, Callable[[], ScreenBase]] = {
            Screen.MAIN: lambda: MainMenu(self, self.context),
            Screen.SONG_SELECT: lambda: SongSelect(self, self.context),
            Screen.GAME: lambda: Game(self, self.context),
            Screen.RESULTS: self._make_placeholder(Screen.RESULTS),
            Screen.PREFERENCES: lambda: Preferences(self, self.context),
            Screen.IMPORT_GP: lambda: ImportGp(self, self.context),
        }
        self._built: dict[Screen, ScreenBase] = {}
        self._history: list[Screen] = []

        self.navigate(Screen.MAIN)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:  # noqa: N802 - Qt naming
        """Stop the audio on the way out.

        There was no close handler anywhere in the project, so quitting mid-song
        relied on process teardown to release the output device. That works, but it is
        a device being closed by a process that is already dying, which is the
        situation §26.3's two core dumps came from. `stop_playback` is idempotent and
        cheap when nothing is open, so this is the belt to `hideEvent`'s braces.
        """
        self.context.stop_playback()
        super().closeEvent(event)

    # --- screen construction ---------------------------------------------------

    def _make_placeholder(self, screen: Screen) -> Callable[[], ScreenBase]:
        def build() -> ScreenBase:
            return PlaceholderScreen(self, self.context, screen.label)

        return build

    def _widget_for(self, screen: Screen) -> ScreenBase:
        if screen not in self._built:
            self._built[screen] = self._factories[screen]()
            self._stack.addWidget(self._built[screen])
        return self._built[screen]

    # --- navigation -----------------------------------------------------------

    def navigate(self, screen: Screen, *, remember: bool = True) -> None:
        """Show ``screen``. Back is appended to the history unless told otherwise."""
        current = self.current
        if remember and current is not None and current is not screen:
            self._history.append(current)
        widget = self._widget_for(screen)
        self._stack.setCurrentWidget(widget)
        self.setWindowTitle(f"Guitaroids - {screen.label}")
        widget.setFocus()

    def go_back(self) -> None:
        """Return to the previous screen, or do nothing at a root."""
        target = self._back_target
        if target is not None:
            self.navigate(target, remember=False)

    @property
    def _back_target(self) -> Screen | None:
        if self._history:
            return self._history.pop()
        current = self.current
        if current is None or current.is_root:
            return None
        return _BACK.get(current)

    @property
    def current(self) -> Screen:
        widget = self._stack.currentWidget()
        for screen, built in self._built.items():
            if built is widget:
                return screen
        return Screen.MAIN

    @property
    def current_screen(self) -> ScreenBase:
        widget = self._stack.currentWidget()
        assert isinstance(widget, ScreenBase)
        return widget

    @property
    def history(self) -> tuple[Screen, ...]:
        return tuple(self._history)

    def unload_all(self) -> None:
        """Drop every built screen. Used by tests to prove laziness."""
        for screen in list(self._built):
            widget = self._built.pop(screen)
            self._stack.removeWidget(widget)
            widget.deleteLater()
        self._history.clear()
