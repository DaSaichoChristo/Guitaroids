"""The main menu.

The one screen that is genuinely built, so it is a real screen rather than a
placeholder with buttons injected into it. Doing it properly here also sets the
layout convention the other screens will follow: a centred column of
fixed-width controls, not a full-bleed form.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtCore, QtWidgets

from .screens import Screen, ScreenBase, constrained_button, content_column, heading

if TYPE_CHECKING:  # pragma: no cover - types only
    from ..context import AppContext

_CENTRED = QtCore.Qt.AlignmentFlag.AlignHCenter


class MainMenu(ScreenBase):
    """Title, and the three things you can do from here."""

    TITLE = "main"

    def __init__(
        self,
        shell,
        context: "AppContext",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        # context is unused here -- the menu needs no library -- but it is part of
        # the base signature, so every screen takes it. A menu that grew a "your
        # library has 3 unplayable tabs" warning would have it to hand.
        super().__init__(shell, context, parent)
        # A title screen, so it sits in the middle of the window rather than
        # hugging the top.
        column = content_column(self, margin=56, vertical_centred=True)

        column.addWidget(heading("GUITAROIDS", kind="title"))
        column.addWidget(
            heading(
                "Turn your real guitar into the controller and play your favorite tabs live.",
                kind="subtitle",
            )
        )
        column.addSpacing(18)

        play = constrained_button("Play", object_name="primary")
        play.clicked.connect(lambda: self.shell.navigate(Screen.SONG_SELECT))
        column.addWidget(play, alignment=_CENTRED)

        preferences = constrained_button("Preferences")
        preferences.clicked.connect(lambda: self.shell.navigate(Screen.PREFERENCES))
        column.addWidget(preferences, alignment=_CENTRED)

        import_gp = constrained_button("Import GP")
        import_gp.clicked.connect(lambda: self.shell.navigate(Screen.IMPORT_GP))
        column.addWidget(import_gp, alignment=_CENTRED)

        column.addSpacing(18)
        quit_button = constrained_button("Quit", object_name="danger")
        quit_button.clicked.connect(self._quit)
        column.addWidget(quit_button, alignment=_CENTRED)

        # Play is the default action, so Enter from here starts a song.
        play.setDefault(True)
        play.setFocus()

    def _quit(self) -> None:
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.quit()
