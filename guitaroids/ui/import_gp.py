"""Import GP: copy a Guitar Pro tab into the song library.

Thin on purpose. Every decision about *whether* to import lives in
:mod:`guitaroids.importer`, which is pure and tested without a dialog; this screen
only asks the user the questions that module reports and then does the copy.

**Two steps, not one.** Choosing a file and importing it are separate buttons, with
the chosen file named on screen in between. A single button that opens a dialog and
then immediately copies has no moment at which the user can see what they picked,
which file it will land as, or whether it already exists -- and a copy into a library
is not something to do as a side effect of a file dialog returning. The library
wording ("Add to library") is deliberate: the file is *copied*, and the screen says
so above, but "Save" would imply it is written back where it came from.

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

        #: The tab the user has picked but not yet imported. ``None`` until a
        #: dialog returns a path, and cleared again after an import, so a second
        #: press of Add cannot quietly copy the same file twice.
        self._source: Path | None = None

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

        # The chosen file, named. This is the step the single-button version did
        # not have: between picking a file and copying it there is now a moment in
        # which the player can see what they picked.
        self._chosen_label = heading("", kind="stat")
        self._chosen_label.setWordWrap(True)
        self._chosen_label.setVisible(False)
        column.addWidget(self._chosen_label)

        column.addStretch(1)

        # Primary action last, as on the main menu. The vertical order is the
        # reading order here, and putting Back above it invites pressing the wrong
        # one. The two import buttons are side by side: they are two steps of one
        # action, not two actions, and a row says that better than a column does.
        self._import_button = constrained_button(
            "Add to library", object_name="primary", width=260
        )
        self._import_button.clicked.connect(self._import_selected)
        # Disabled until a file is chosen. A live-looking Add button with nothing
        # behind it is a dead end, and a button that ignores the click is worse.
        self._import_button.setEnabled(False)
        self._import_button.setToolTip("Choose a tab first.")

        self._choose_button = constrained_button("Choose a tab...", width=260)
        self._choose_button.clicked.connect(self._choose_file)

        row = QtWidgets.QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self._import_button)
        row.addWidget(self._choose_button)
        column.addLayout(row)

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

    def _choose_file(self) -> None:
        """Step one: pick a file, and say what would happen to it.

        Nothing is written here. The plan is computed so the screen can show the
        destination name and say whether this would replace something, which is
        information the player cannot get from the file dialog they just used.
        """
        source = self.choose_file()
        if not source:
            return  # the user cancelled the dialog
        self.select_file(Path(source))

    def select_file(self, source: Path) -> ImportPlan | None:
        """Arm the screen with a file. Returns the plan, or ``None`` if refused.

        Split out from the button handler so a test can drive the whole flow
        without going through a file dialog, and so the preview and the import
        agree on what the plan is -- they both call this.
        """
        plan = plan_import(source, self.context.songs_dir)

        if plan.outcome is Outcome.REJECT:
            # Nothing to add, so the button stays dead and the reason is shown.
            self._source = None
            self._chosen_label.setVisible(False)
            self._refresh_buttons()
            self._report(plan.message, error=True)
            return None

        self._source = source
        self._chosen_label.setText(f"Selected: {source.name}")
        self._chosen_label.setVisible(True)
        self._refresh_buttons()
        self._report(self._preview(plan))
        return plan

    def _preview(self, plan: ImportPlan) -> str:
        """One line saying what Add will do, before Add is pressed."""
        destination = plan.destination
        where = destination.name if destination is not None else plan.source.name
        if plan.outcome is Outcome.CONFIRM:
            # The question is asked at import time, not now: asking on selection
            # would mean a dialog the player has not yet committed to answering.
            return f"Will ask before replacing {where} in the library."
        return f"Will be added to the library as {where}."

    def _import_selected(self) -> ImportPlan | None:
        """Step two: do the copy for the file chosen in step one."""
        if self._source is None:
            return None
        plan = self.import_file(self._source)
        # Cleared either way: a refusal or a cancellation leaves nothing armed, so
        # Add is not a button that can import the same file twice by accident.
        self._source = None
        self._chosen_label.setVisible(False)
        self._refresh_buttons()
        return plan

    def import_file(self, source: Path) -> ImportPlan:
        """Import one file, asking before replacing anything. Returns the plan.

        The whole flow in one call, for a caller that already has a file and does
        not need the two-step UI -- which is what the tests drive, and what a
        future "import this file the OS handed us" path would use.
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

    def _refresh_buttons(self) -> None:
        """Enable Add only when there is a file armed to add.

        Focus moves with it, so the keyboard follows the same two steps the mouse
        does: Enter adds the tab that is already chosen rather than reopening the
        file dialog.
        """
        armed = self._source is not None
        self._import_button.setEnabled(armed)
        self._import_button.setToolTip("" if armed else "Choose a tab first.")
        (self._import_button if armed else self._choose_button).setFocus()

    def _rescan(self) -> None:
        """Refresh the library so the new tab appears without a restart."""
        if not self._loader.start(
            self.context.songs_dir, collapse=self.context.settings.collapse_chords
        ):
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
