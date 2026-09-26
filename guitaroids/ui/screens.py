"""The set of screens, and the base class they share.

Screens never own game objects (DESIGN.md §1.7): they read what they need and hand
a request back. The shell owns the ``PlayRequest``, so leaving a screen tears down
its widgets without taking a game session, an audio stream or a camera handle with
it.
"""

from __future__ import annotations

from enum import Enum

from PySide6 import QtCore, QtWidgets


class Screen(Enum):
    """Where the app can be."""

    MAIN = "main"
    SONG_SELECT = "song_select"
    GAME = "game"
    RESULTS = "results"
    PREFERENCES = "preferences"

    @property
    def is_root(self) -> bool:
        """Screens with nothing behind them. Escape quits rather than going back."""
        return self in (Screen.MAIN,)


class ScreenBase(QtWidgets.QWidget):
    """Common behaviour: Escape goes back, and the shell is reachable.

    Subclasses set ``TITLE`` and build their widgets in ``__init__``. They should
    not call ``show``/``hide`` on themselves; the shell does that.
    """

    TITLE: str = ""

    def __init__(self, shell: "ShellBase", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.shell = shell

    def keyPressEvent(self, event: QtCore.QKeyEvent) -> None:  # noqa: N802 - Qt naming
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.shell.go_back()
            return
        super().keyPressEvent(event)


class ShellBase:
    """What a screen may assume its shell provides.

    Declared as a Protocol-shaped class rather than imported, so ``screens`` does
    not depend on ``shell`` and the two can be imported in any order.
    """

    def navigate(self, screen: Screen) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def go_back(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    @property
    def current(self) -> Screen:  # pragma: no cover - interface
        raise NotImplementedError


def content_column(
    parent: QtWidgets.QWidget,
    *,
    margin: int = 40,
    max_width: int = 520,
    vertical_centred: bool = False,
) -> QtWidgets.QVBoxLayout:
    """A vertical layout for page content, width-constrained and centred.

    A plain QVBoxLayout stretches its children across the whole window, so buttons
    end up full-bleed and the page reads as a form rather than a menu.

    The column is wrapped in a container widget because ``QBoxLayout.addLayout``
    takes no ``alignment`` argument in PySide6 -- only ``addWidget`` does. The
    container is width-capped and centred; widgets inside keep their own size hints,
    so :func:`constrained_button` controls how wide they are.

    Args:
        vertical_centred: pad above and below so sparse pages sit in the middle of
            the window. Right for a title screen, wrong for song select or
            preferences, which fill the page and should stay top-aligned.
    """
    outer = QtWidgets.QVBoxLayout(parent)
    outer.setContentsMargins(margin, margin, margin, margin)
    if vertical_centred:
        outer.addStretch(1)

    container = QtWidgets.QWidget(parent)
    container.setObjectName("column")
    container.setMaximumWidth(max_width)
    column = QtWidgets.QVBoxLayout(container)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(12)

    outer.addWidget(container, alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)
    if vertical_centred:
        outer.addStretch(1)
    return column


def heading(text: str, *, kind: str = "heading") -> QtWidgets.QLabel:
    """A themed heading or subtitle label."""
    label = QtWidgets.QLabel(text)
    label.setObjectName(kind)
    label.setWordWrap(True)
    return label


def constrained_button(text: str, *, object_name: str = "", width: int = 240) -> QtWidgets.QPushButton:
    """A button that will not stretch across the window."""
    button = QtWidgets.QPushButton(text)
    if object_name:
        button.setObjectName(object_name)
    button.setMaximumWidth(width)
    button.setMinimumWidth(width)
    return button

