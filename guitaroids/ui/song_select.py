"""Song select: pick a tab, a track and an alignment, then play.

Layout is two columns -- the list on the left, the details of whatever is selected
on the right. Song select fills the page, so it is top-aligned and wider than the
narrow centred column the menu and preferences use; it reuses
:func:`~guitaroids.ui.screens.content_column` with a larger ``max_width`` rather than
growing a second layout convention.

Three decisions worth stating, because each has a defensible alternative:

- **Playable and unplayable tabs are in separate lists.** A problem row in the
  main list would be selectable and would need a "no" path out of it. The problems
  list appears only when there is something in it, which is the collapsed section
  DESIGN.md §6.6 asks for.
- **The whole ``SongEntry`` is stashed in ``UserRole``**, not the slug. One object,
  no second lookup, and the detail pane cannot disagree with the row. It shares
  the parsed song with the library rather than copying it, and the list is rebuilt
  on every rescan, so a stale entry cannot survive a scan.
- **The offset slider writes settings on Play, not on every move.** Dragging it
  would otherwise do an atomic temp-file-and-fsync write per pixel.

Play and double-click both start a song. Play is also the default button, so Enter
works from the list.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

from ..songlib import SongEntry, Status, format_duration
from .library_loader import LibraryLoader
from .screens import Screen, ScreenBase, constrained_button, content_column, heading
from .theme import px

if TYPE_CHECKING:  # pragma: no cover - types only
    from ..context import AppContext

_CENTRED = QtCore.Qt.AlignmentFlag.AlignHCenter
_ROLE = QtCore.Qt.ItemDataRole.UserRole

#: Short badge for a non-OK status. Keyed by Status rather than derived from
#: ``status.value`` so the wording is decided in one place.
_STATUS_TEXT: dict[Status, str] = {
    Status.NO_AUDIO: "metronome only",
    Status.UNSUPPORTED_VERSION: "unsupported version",
    Status.PARSE_ERROR: "could not be read",
    Status.TEMPO_CHANGE: "tempo changes",
    Status.NOT_GUITAR: "no guitar track",
    Status.EMPTY: "no notes",
}

#: Offset slider travel, in milliseconds. Matches the clamp in PlayRequest, so the
#: slider cannot offer a value the request would reject.
OFFSET_MIN_MS = -500
OFFSET_MAX_MS = 500
OFFSET_STEP_MS = 5

def format_offset(ms: float) -> str:
    """Signed milliseconds, for a label."""
    return f"{ms:+.0f} ms"

class SongSelect(ScreenBase):
    """The song list, the details of the selection, and the controls to play it."""

    TITLE = "song_select"

    def __init__(
        self,
        shell,
        context: "AppContext",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(shell, context, parent)

        # Parented to the screen, so PySide keeps it alive and it dies with the
        # screen. Not on the context: that stays free of Qt (see context.py).
        self._loader = LibraryLoader(self)
        self._loader.completed.connect(self._on_scan_completed)
        self._loader.cancelled.connect(self._on_scan_cancelled)
        self._loader.failed.connect(self._on_scan_failed)
        self._loader.progress.connect(self._on_scan_progress)

        column = content_column(self, margin=24, max_width=980)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(heading("Song Select"))
        self._status = heading("", kind="dim")
        header.addWidget(self._status, alignment=QtCore.Qt.AlignmentFlag.AlignRight
                         | QtCore.Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        column.addLayout(header)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(px(16))
        column.addLayout(body, 1)

        body.addWidget(self._build_list_column(), 1)
        body.addWidget(self._build_detail_column())

        self._problems_box = self._build_problems_box()
        column.addWidget(self._problems_box)

        column.addLayout(self._build_button_row())

        self._populate()

    # --- construction --------------------------------------------------------

    def _build_list_column(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setObjectName("card")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(px(12), px(12), px(12), px(12))
        layout.setSpacing(px(8))

        self._songs = QtWidgets.QListWidget()
        self._songs.setObjectName("songs")
        # Wrap and hide the horizontal bar rather than letting a long row produce a
        # scrollbar along the bottom of the list, which reads as a broken widget.
        # A scrollbar per song is worse than a row that wraps to two lines.
        self._songs.setWordWrap(True)
        self._songs.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._songs.currentRowChanged.connect(self._on_selection_changed)
        self._songs.itemDoubleClicked.connect(lambda _item: self._play_selected())
        layout.addWidget(self._songs, 1)

        self._empty = heading(
            "No tabs yet.\n\nPut .gp5 files in the songs directory, or use Import GP "
            "to copy one in from elsewhere.",
            kind="subtitle",
        )
        self._empty.setVisible(False)
        layout.addWidget(self._empty)
        return panel

    def _build_detail_column(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setObjectName("card")
        panel.setFixedWidth(px(340))
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(px(16), px(16), px(16), px(16))
        layout.setSpacing(px(10))

        self._detail_title = heading("", kind="heading")
        self._detail_artist = heading("", kind="subtitle")
        layout.addWidget(self._detail_title)
        layout.addWidget(self._detail_artist)

        layout.addWidget(_divider())

        self._facts = QtWidgets.QFormLayout()
        self._facts.setSpacing(px(6))
        # The label column sizes to its widest label, so "Difficulty" ends up flush
        # against the longest value. Horizontal spacing is what separates them.
        self._facts.setHorizontalSpacing(14)
        layout.addLayout(self._facts)

        layout.addSpacing(6)
        layout.addWidget(heading("Track", kind="dim"))
        self._tracks = QtWidgets.QComboBox()
        self._tracks.currentIndexChanged.connect(self._on_track_changed)
        layout.addWidget(self._tracks)

        layout.addWidget(heading("Audio offset", kind="dim"))
        offset_row = QtWidgets.QHBoxLayout()
        self._offset = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self._offset.setRange(OFFSET_MIN_MS, OFFSET_MAX_MS)
        self._offset.setSingleStep(OFFSET_STEP_MS)
        self._offset.setPageStep(OFFSET_STEP_MS * 4)
        self._offset.valueChanged.connect(self._on_offset_changed)
        self._offset_label = heading("+0 ms", kind="stat")
        offset_row.addWidget(self._offset, 1)
        offset_row.addWidget(self._offset_label)
        layout.addLayout(offset_row)
        layout.addWidget(
            heading(
                "Negative if the notes arrive late. Tune once per song and it sticks.",
                kind="dim",
            )
        )

        layout.addStretch(1)
        return panel

    def _build_problems_box(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Problems")
        layout = QtWidgets.QVBoxLayout(box)
        layout.setContentsMargins(px(8), px(8), px(8), px(8))
        self._problems = QtWidgets.QListWidget()
        self._problems.setMaximumHeight(px(110))
        layout.addWidget(self._problems)
        return box

    def _build_button_row(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(px(12))

        back = constrained_button("Back", width=140)
        back.clicked.connect(self.shell.go_back)
        row.addWidget(back, alignment=_CENTRED)

        row.addStretch(1)

        self._rescan = constrained_button("Rescan", width=140)
        self._rescan.clicked.connect(self._rescan_library)
        row.addWidget(self._rescan, alignment=_CENTRED)

        self._play = constrained_button("Play", object_name="primary", width=180)
        self._play.clicked.connect(self._play_selected)
        self._play.setDefault(True)
        row.addWidget(self._play, alignment=_CENTRED)
        return row

    # --- populating ----------------------------------------------------------

    def _populate(self) -> None:
        """Rebuild both lists from the context's library.

        Rebuilds rather than updating in place, so a row can never show a mix of
        old and new data after a rescan removed a song.
        """
        library = self.context.library
        previous = self._selected_slug()

        self._songs.clear()
        for entry in library.playable:
            self._songs.addItem(self._row_text(entry))
            item = self._songs.item(self._songs.count() - 1)
            item.setData(_ROLE, entry)

        self._problems.clear()
        for entry in library.problems:
            self._problems.addItem(self._problem_text(entry))

        has_songs = bool(library.playable)
        self._songs.setVisible(has_songs)
        self._empty.setVisible(not has_songs)
        self._problems_box.setVisible(bool(library.problems))
        self._problems_box.setTitle(f"Problems ({len(library.problems)})")

        if library.entries:
            self._set_status(
                f"{len(library.playable)} of {len(library.entries)} playable"
            )
        else:
            self._set_status("no tabs found")

        # Re-select by slug so a rescan does not lose the player's place; fall back
        # to the first row, or to nothing if the library is now empty.
        if previous is not None:
            self._select_slug(previous)
        elif self._songs.count():
            self._songs.setCurrentRow(0)
        else:
            self._on_selection_changed()

    @staticmethod
    def _row_text(entry: SongEntry) -> str:
        artist = f" - {entry.artist}" if entry.artist else ""
        badge = _STATUS_TEXT.get(entry.status, "")
        suffix = f"  [{badge}]" if badge else ""
        return f"{entry.title}{artist}   {entry.difficulty}{suffix}"

    @staticmethod
    def _problem_text(entry: SongEntry) -> str:
        badge = _STATUS_TEXT.get(entry.status, entry.status.value)
        detail = f": {entry.detail}" if entry.detail else ""
        return f"{entry.tab_path.name} - {badge}{detail}"

    # --- selection -----------------------------------------------------------

    def _selected_entry(self) -> SongEntry | None:
        item = self._songs.currentItem()
        return item.data(_ROLE) if item is not None else None

    def _selected_slug(self) -> str | None:
        entry = self._selected_entry()
        return entry.slug if entry is not None else None

    def _select_slug(self, slug: str) -> None:
        for row in range(self._songs.count()):
            item = self._songs.item(row)
            entry = item.data(_ROLE)
            if entry is not None and entry.slug == slug:
                self._songs.setCurrentRow(row)
                return
        self._on_selection_changed()

    def _on_selection_changed(self, *_args) -> None:
        entry = self._selected_entry()
        playable = entry is not None

        self._detail_title.setText(entry.title if playable else "")
        self._detail_artist.setText(entry.artist if playable else "")
        self._tracks.clear()
        self._offset.setEnabled(playable)
        self._offset_label.setEnabled(playable)
        self._tracks.setEnabled(playable)
        self._play.setEnabled(playable)

        if not playable:
            self._clear_facts()
            return

        self._offset.blockSignals(True)
        self._offset.setValue(int(self.context.settings.offset_for(entry.slug)))
        self._offset.blockSignals(False)
        self._on_offset_changed(self._offset.value())

        for track in entry.tracks:
            self._tracks.addItem(track.label, track.number)
        default = entry.default_track
        if default is not None:
            self._tracks.setCurrentIndex(self._tracks.findData(default.number))
        self._refresh_facts()

    def _refresh_facts(self) -> None:
        entry = self._selected_entry()
        if entry is None or entry.chart is None:
            self._clear_facts()
            return
        chart = entry.chart
        self._clear_facts()
        for name, value in (
            ("Tempo", f"{chart.tempo} BPM"),
            ("Length", format_duration(chart.duration)),
            ("Notes", f"{chart.note_count}"),
            ("Density", f"{chart.notes_per_second:.2f} nps"),
            ("Difficulty", entry.difficulty),
            ("Audio", entry.audio_path.name if entry.audio_path else "none (click only)"),
        ):
            self._facts.addRow(heading(name, kind="dim"), heading(value, kind="stat"))

    def _clear_facts(self) -> None:
        """Empty a QFormLayout, which has no clear().

        removeRow in a loop: taking rows from the front shifts the rest down, and
        the loop condition re-reads the count each time.
        """
        while self._facts.rowCount():
            self._facts.removeRow(0)

    def _on_track_changed(self, _index: int) -> None:
        self._refresh_facts()

    def _on_offset_changed(self, ms: int) -> None:
        self._offset_label.setText(format_offset(ms))

    # --- actions -------------------------------------------------------------

    def _play_selected(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        # addItem's user data lands on UserRole, the same role the list uses --
        # different widgets, so no clash.
        track_number = self._tracks.currentData()
        if track_number is None:
            default = entry.default_track
            if default is None:
                return
            track_number = default.number

        offset_ms = float(self._offset.value())
        # Remembered only on Play: a settings save is an fsync, and a drag emits
        # valueChanged continuously.
        self.context.settings.set_offset_for(entry.slug, offset_ms)
        try:
            self.context.save_settings()
        except OSError as exc:
            # A read-only config directory must not stop the song being played.
            self._set_status(f"could not save the offset: {exc}")

        self.context.request_play(entry.slug, int(track_number), offset_ms=offset_ms)
        self.shell.navigate(Screen.GAME)

    def _rescan_library(self) -> None:
        if not self._loader.start(self.context.songs_dir):
            self._set_status("already scanning")
            return
        self._rescan.setEnabled(False)
        self._set_status("scanning...")

    # --- loader callbacks ----------------------------------------------------

    def _on_scan_completed(self, library) -> None:  # noqa: ANN001 - Library
        self.context.set_library(library)
        self._rescan.setEnabled(True)
        self._populate()

    def _on_scan_cancelled(self) -> None:
        self._rescan.setEnabled(True)
        self._set_status("scan cancelled")

    def _on_scan_failed(self, message: str) -> None:
        self._rescan.setEnabled(True)
        self._set_status(message)

    def _on_scan_progress(self, done: int, total: int) -> None:
        self._set_status(f"scanning {done}/{total}...")

    def _set_status(self, text: str) -> None:
        self._status.setText(text)

    # --- lifecycle -----------------------------------------------------------

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # noqa: N802 - Qt naming
        """Stop any scan when the screen is navigated away from.

        hideEvent rather than closeEvent: the shell keeps built screens in a stack
        and only hides them, so a screen is never closed and a closeEvent handler
        would never run. The screen outlives the scan, but not the other way round
        -- a scan that outlives the screen would emit into a destroyed widget.
        """
        self._loader.cancel()
        super().hideEvent(event)

def _divider() -> QtWidgets.QFrame:
    line = QtWidgets.QFrame()
    line.setObjectName("divider")
    line.setFrameShape(QtWidgets.QFrame.Shape.HLine)
    return line
