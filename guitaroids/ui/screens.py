"""The set of screens, and the base class they share.

Screens never own game objects (DESIGN.md §1.7): they read what they need and hand
a request back. The shell owns the ``PlayRequest``, so leaving a screen tears down
its widgets without taking a game session, an audio stream or a camera handle with
it.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

if TYPE_CHECKING:  # pragma: no cover - import cycle avoidance, types only
    from ..context import AppContext

from . import theme
from .theme import px

#: Human-facing names, declared rather than derived from the enum value.
#:
#: Module level, not inside the ``Screen`` class body: a dict in an Enum body
#: silently becomes a member (the leading underscore does not protect it), so
#: ``for screen in Screen`` would yield this dict and every parametrised screen
#: test would break.
#:
#: Declared because ``str.title()`` lowercases the rest of each word and so can
#: never render an acronym: ``import_gp`` gave "Import Gp", and a version number
#: would come out as "Gp5 Import".
_SCREEN_LABELS: dict[str, str] = {
    "main": "Main Menu",
    "song_select": "Song Select",
    "import_gp": "Import GP",
    "game": "Game",
    "results": "Results",
    "preferences": "Preferences",
}


class Screen(Enum):
    """Where the app can be."""

    MAIN = "main"
    SONG_SELECT = "song_select"
    GAME = "game"
    RESULTS = "results"
    PREFERENCES = "preferences"
    IMPORT_GP = "import_gp"

    @property
    def is_root(self) -> bool:
        """Screens with nothing behind them. Escape quits rather than going back."""
        return self in (Screen.MAIN,)

    @property
    def label(self) -> str:
        """Human-facing name, for window titles and headings.

        Declared, not derived. ``str.title()`` lowercases the rest of each word,
        so it can never render an acronym -- ``import_gp`` became "Import Gp" --
        and it mangles version numbers too, giving "Gp5 Import".
        """
        return _SCREEN_LABELS[self.value]


class ScreenBase(QtWidgets.QWidget):
    """Common behaviour: Escape goes back, and the shared state is reachable.

    Subclasses build their widgets in ``__init__``. They should not call
    ``show``/``hide`` on themselves; the shell does that.

    Takes the ``context`` explicitly rather than reading it off ``self.shell``.
    A screen can then be built against a context with no window behind it, which
    is what makes a screen unit-testable; reaching through the shell would make
    every screen test construct a whole ``MainWindow`` first.

    The shell is still passed, because navigation is the shell's job and a screen
    has no business knowing the ``Screen`` enum's history rules.
    """

    TITLE: str = ""

    def __init__(
        self,
        shell: "ShellBase",
        context: "AppContext",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.shell = shell
        #: Shared, long-lived state. Outlives this widget: the shell owns it, so
        #: navigating away from a screen must not lose the library or the request.
        self.context = context

    def keyPressEvent(self, event: QtCore.QKeyEvent) -> None:  # noqa: N802 - Qt naming
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.shell.go_back()
            return
        super().keyPressEvent(event)

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 - Qt naming
        # Before super(), so the pinning is in place for the layout pass this
        # event triggers.
        pin_wrapped_label_heights(self)
        super().showEvent(event)


def pin_wrapped_label_heights(widget: QtWidgets.QWidget) -> int:
    """Stop a word-wrapped ``QLabel`` being given less height than its text needs.

    Returns how many labels were adjusted.

    A word-wrapped ``QLabel`` reports two different heights: ``sizeHint`` for the
    width it will actually have, and ``minimumSizeHint``, which Qt derives from
    ``heightForWidth``. When a layout has to economise -- or, as happened on Import
    GP, when the stylesheet has only just been applied and the cached container size
    is a few pixels short -- it hands out the *minimum*, and the last line of the
    text is silently not drawn. The user sees a sentence that stops mid-thought with
    no indication anything is missing.

    The height cannot be pinned at construction: the stylesheet owns typography, and
    the label's size hint is wrong until that is applied. By ``showEvent`` it is
    right, so pin it there and let the layout re-run.

    Applied to whole screens rather than at the two call sites that were broken,
    because the same mistake is available to every word-wrapped label in the app.
    """
    adjusted = 0
    for label in widget.findChildren(QtWidgets.QLabel):
        if not label.wordWrap():
            continue
        needed = label.sizeHint().height()
        if needed > label.minimumHeight():
            label.setMinimumHeight(needed)
            adjusted += 1
    if adjusted:
        layout = widget.layout()
        if layout is not None:
            layout.invalidate()
    return adjusted


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
    scale_factor: float | None = None,
) -> QtWidgets.QVBoxLayout:
    """A vertical layout for page content, width-constrained and centred.

    A plain QVBoxLayout stretches its children across the whole window, so buttons
    end up full-bleed and the page reads as a form rather than a menu.

    The column is wrapped in a container widget because ``QBoxLayout.addLayout``
    takes no ``alignment`` argument in PySide6 -- only ``addWidget`` does. The
    container is width-capped and centred; widgets inside keep their own size hints,
    so :func:`constrained_button` controls how wide they are.

    ``margin`` and ``max_width`` are 1080p design units and are scaled by
    ``scale_factor``, or by the process-wide scale when that is ``None``. That is
    what stops a 3440x1440 display rendering a 520px column in the middle of it.

    Args:
        vertical_centred: pad above and below so sparse pages sit in the middle of
            the window. Right for a title screen, wrong for song select or
            preferences, which fill the page and should stay top-aligned.
    """
    f = theme.scale() if scale_factor is None else scale_factor
    margin = px(margin, f)
    max_width = px(max_width, f)

    outer = QtWidgets.QVBoxLayout(parent)
    outer.setContentsMargins(margin, margin, margin, margin)
    if vertical_centred:
        outer.addStretch(1)

    container = QtWidgets.QWidget(parent)
    container.setObjectName("column")
    container.setMaximumWidth(max_width)
    column = QtWidgets.QVBoxLayout(container)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(px(12, f))

    outer.addWidget(container, alignment=QtCore.Qt.AlignmentFlag.AlignHCenter)
    if vertical_centred:
        outer.addStretch(1)
    return column


def heading(
    text: str,
    *,
    kind: str = "heading",
    parent: QtWidgets.QWidget | None = None,
) -> QtWidgets.QLabel:
    """A themed heading or subtitle label.

    ``parent`` matters for the game HUD, which floats labels over the highway: a
    parentless QLabel is a *top-level window*, so ``show()`` on one would open a
    second window floating over the game rather than drawing into it.

    ``kind="title"`` is a **centred** screen title, and it is a separate role rather
    than an alignment set on ``"heading"`` because not every heading is a title. The
    song-select detail card's heading sits above a column of left-aligned fact rows,
    so centring that one would leave it straddling them; a screen's own title has
    nothing under it to disagree with. Anything that wants a centred heading that is
    *not* a screen title has to say ``kind="title"`` and own the consequences.
    """
    label = QtWidgets.QLabel(text, parent)
    label.setObjectName(kind)
    label.setWordWrap(True)
    if kind == "title":
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
    return label


def constrained_button(
    text: str,
    *,
    object_name: str = "",
    width: int = 240,
    parent: QtWidgets.QWidget | None = None,
    scale_factor: float | None = None,
) -> QtWidgets.QPushButton:
    """A button that will not stretch across the window.

    ``width`` is a 1080p design unit, scaled the same way as :func:`content_column`.
    """
    f = theme.scale() if scale_factor is None else scale_factor
    width = px(width, f)
    button = QtWidgets.QPushButton(text, parent)
    if object_name:
        button.setObjectName(object_name)
    button.setMaximumWidth(width)
    button.setMinimumWidth(width)
    return button
