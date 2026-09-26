"""Import GP: copy a Guitar Pro tab into the song library.

Thin on purpose. Every decision about *whether* to import lives in
:mod:`guitaroids.importer`, which is pure and tested without a dialog; this screen
only asks the user the questions that module reports and then does the copy.

The file dialog and the overwrite prompt are injectable so the screen is testable
without a user or a modal. Qt's own dialogs are not mockable in any honest way --
``QFileDialog.getOpenFileName`` is a blocking static call that would hang a test --
so the screen calls through attributes that default to the real dialogs.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

from ..importer import Outcome, ImportPlan, import_tab, plan_import
from ..songlib import TAB_EXTENSIONS
from .library_loader import LibraryLoader
from .screens import ScreenBase, constrained_button, content_column, heading

if TYPE_CHECKING:  # pragma: no cover - types only
    from ..context import AppContext

_CENTRED = QtCore.Qt.AlignmentFlag.AlignHCenter

_FILE_FILTER = "Guitar Pro tabs (*.gp3 *.gp4 *.gp5);;All files (*)"


class ImportGp(ScreenBase):
    """Pick a tab, copy it in, and rescan the library."""

    TITLE = "import_gp"

    def __init__(
        self,
        shell,
        context: "AppContext",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(shell, context, parent)

        #: Overridable so tests do not need a modal dialog.
        self.choose_file: Callable[[], str] = self._ask_for_file
        self.confirm: Callable[[str], bool] = self._ask_confirmation

        self._loader = LibraryLoader(self)
        self._loader.completed.connect(self._on_scan_completed)
        self._loader.failed.connect(lambda m: self._report(m, error=True))
        self._loader.progress.connect(self._on_progress)

        column = content_column(self, margin=40, max_width=560, vertical_centred=True)

        column.addWidget(heading("Import GP"))
        column.addWidget(
            heading(
                "Copy a Guitar Pro tab into the song library.\n\n"
                "The original file is left where it is. If a tab with the same name "
                "is already in the library you will be asked before it is replaced.",
                kind="subtitle",
            )
        )
        column.addSpacing(12)
        column.addWidget(
            heading(
                f"Supported formats: {', '.join(e.lstrip('.') for e in TAB_EXTENSIONS)}.\n"
                f"Destination: {context.songs_dir}",
                kind="dim",
            )
        )
        column.addSpacing(12)

        self._report_label = heading("", kind="dim")
        self._report_label.setWordWrap(True)
        column.addWidget(self._report_label)

        column.addStretch(1)

        # Primary action first, as on the main menu. The vertical order is the
        # reading order here, and putting Back above it invites pressing the wrong
        # one.
        self._choose_button = constrained_button("Choose a tab...", object_name="primary", width=260)
        self._choose_button.clicked.connect(self._choose_and_import)
        column.addWidget(self._choose_button, alignment=_CENTRED)

        back = constrained_button("Back", width=200)
        back.clicked.connect(self.shell.go_back)
        column.addWidget(back, alignment=_CENTRED)

        self._choose_button.setDefault(True)
        self._choose_button.setFocus()

    # --- overridable dialogs --------------------------------------------------

    def _ask_for_file(self) -> str:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Choose a Guitar Pro tab", str(Path.home()), _FILE_FILTER
        )
        return path

    def _ask_confirmation(self, message: str) -> bool:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Replace existing tab?",
            message,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        return answer == QtWidgets.QMessageBox.StandardButton.Yes

    # --- the action -----------------------------------------------------------

    def _choose_and_import(self) -> None:
        source = self.choose_file()
        if not source:
            return  # the user cancelled the dialog
        self.import_file(Path(source))

    def import_file(self, source: Path) -> ImportPlan:
        """Import one file, asking before replacing anything. Returns the plan.

        Split out from the button handler so a test can drive the whole flow
        without going through a file dialog.
        """
        plan = plan_import(source, self.context.songs_dir)

        if plan.outcome is Outcome.REJECT:
            self._report(plan.message, error=True)
            return plan

        if plan.outcome is Outcome.CONFIRM:
            if not self.confirm(plan.message):
                self._report("Not imported; the existing tab was left alone.")
                return plan
            plan = plan_import(source, self.context.songs_dir, replace=True)

        try:
            destination = import_tab(plan)
        except OSError as exc:
            # A full disk or a read-only library must say so, not vanish.
            self._report(f"Could not copy the tab: {exc}", error=True)
            return plan

        self._report(f"Copied {destination.name} into the library. Scanning...")
        self._rescan()
        return plan

    def _rescan(self) -> None:
        """Refresh the library so the new tab appears without a restart."""
        if not self._loader.start(self.context.songs_dir):
            self._report("Already scanning; the new tab will appear shortly.")

    # --- reporting ------------------------------------------------------------

    def _report(self, message: str, *, error: bool = False) -> None:
        self._report_label.setText(message)
        self._report_label.setObjectName("dim" if not error else "stat")

    def _on_progress(self, done: int, total: int) -> None:
        self._report(f"Scanning {done}/{total}...")

    def _on_scan_completed(self, library) -> None:  # noqa: ANN001 - Library
        self.context.set_library(library)
        playable = len(library.playable)
        problems = len(library.problems)
        suffix = f", {problems} needing attention" if problems else ""
        self._report(f"Library: {playable} playable{suffix}.")

    # --- lifecycle ------------------------------------------------------------

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # noqa: N802 - Qt naming
        """Stop any scan when navigated away from. See song_select for why hide."""
        self._loader.cancel()
        super().hideEvent(event)
