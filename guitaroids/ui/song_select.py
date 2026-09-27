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

from ..settings import MAX_BPM, MIN_BPM
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

        # Title centred on its own row, status centred beneath it.
        #
        # It was one row: title, then the status right-aligned, then a stretch. That
        # cannot centre the title, because the status is in the same row and the
        # title is centred in whatever is left over rather than in the window. Two
        # rows is the only way "Song Select" is actually in the middle.
        self._header = QtWidgets.QVBoxLayout()
        header = self._header
        # No spacing between the title and the status. They are one header, and the
        # default item spacing is what pushed the Practice tempo row out of the
        # detail card's visible area -- a centred title is not worth a control the
        # player now has to scroll to find.
        header.setSpacing(px(2))
        title_row = QtWidgets.QHBoxLayout()
        title_row.addStretch(1)
        title_row.addWidget(heading("Song Select", kind="title"))
        title_row.addStretch(1)
        header.addLayout(title_row)
        self._status = heading("", kind="dim")
        self._status.setAlignment(QtCore.Qt.AlignmentFlag.AlignHCenter)
        header.addWidget(self._status)

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

        # **Under the list, not in the detail card.** It is a list action, and the
        # detail card is already taller than a 640px window — a button at the bottom of
        # it is below the fold on every screen the project actually runs at, which
        # makes a feature that exists and cannot be found. The list column has the
        # room, and the button sits directly under the thing it acts on.
        #
        # Labelled **Delete**, not "Remove", and it is the only such control on the
        # screen. §46 first shipped this as a *hide*, behind a "Hidden (N)" toggle,
        # on the reasoning that a tab in `songs/` is the player's and unlinking it
        # could lose work. That was overruled: a hide does not solve the problem it
        # was built for, because re-importing the tab brings it straight back and the
        # song is on the list again. So this unlinks, the label says Delete, and there
        # is exactly one way to remove a song rather than two overlapping ones.
        self._delete = constrained_button("Delete song", object_name="danger", width=200)
        self._delete.setToolTip(
            "Deletes the tab file from your songs folder. This cannot be undone."
        )
        self._delete.clicked.connect(self._on_delete_clicked)
        layout.addWidget(self._delete, alignment=_CENTRED)
        return panel

    def _build_detail_column(self) -> QtWidgets.QWidget:
        """The card on the right, with its contents on a scroll area.

        This column is a fixed 340px and its content is now tall enough to
        exceed a 640px-tall window: a QVBoxLayout that does not fit does not clip,
        it **compresses**, and the six fact rows were drawn on top of each other
        (the §13 failure, reached again from the other direction). A scroll area
        is the answer the project already uses for tall forms, and it degrades to
        exactly the old layout when there is room.
        """
        panel = QtWidgets.QFrame()
        panel.setObjectName("card")
        panel.setFixedWidth(px(340))
        outer = QtWidgets.QVBoxLayout(panel)
        outer.setContentsMargins(px(12), px(12), px(12), px(12))

        scroll = QtWidgets.QScrollArea(panel)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        # Transparent, so the card's own background shows through rather than the
        # scroll area painting a second, slightly different rectangle inside it.
        scroll.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        scroll.viewport().setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TranslucentBackground
        )
        outer.addWidget(scroll)

        content = QtWidgets.QWidget()
        content.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        scroll.setWidget(content)
        layout = QtWidgets.QVBoxLayout(content)
        # A right margin, because the vertical scrollbar sits over the right edge
        # of the viewport and a word-wrapped label painted flush against it loses
        # its last glyph: "Tune once" rendered as "Tune onc".
        layout.setContentsMargins(0, 0, px(6), 0)
        # 8 rather than 10: at 960x640 this column does not fit without the tempo
        # control sitting on the fold, half drawn, which reads as a broken widget
        # rather than as something to scroll. Every gap here is px()'d like the
        # rest, so this tightens with the scale.
        layout.setSpacing(px(8))
        # Returns the *card*. Handing the layout the scroll area's own widget
        # would reparent it out of the scroll area, which then deletes it.
        self._build_detail_content(content, layout)
        return panel

    def _build_detail_content(
        self, content: QtWidgets.QWidget, layout: QtWidgets.QVBoxLayout
    ) -> None:
        """Fill ``layout`` with the details. The caller owns the widgets' geometry."""
        self._detail_title = heading("", kind="title")
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

        layout.addWidget(_divider())

        layout.addWidget(heading("Practice tempo", kind="dim"))
        tempo_row = QtWidgets.QHBoxLayout()
        self._bpm = QtWidgets.QSpinBox(content)
        self._bpm.setRange(int(MIN_BPM), int(MAX_BPM))
        self._bpm.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._bpm.setToolTip(
            "Practise this tab slower. It cannot go above the tempo it is written "
            "at, because playing a tab faster than written breaks the music."
        )
        self._bpm.valueChanged.connect(self._on_bpm_changed)
        self._bpm_label = heading("", kind="stat")
        tempo_row.addWidget(self._bpm)
        tempo_row.addWidget(self._bpm_label)
        layout.addLayout(tempo_row)
        layout.addWidget(
            heading(
                # One line on purpose. This card is 340px wide and its content is
                # already taller than a 640px window, so every extra line of prose
                # here is a line the player has to scroll to read (§19.2).
                "Slower than written, and remembered per song.",
                kind="dim",
            )
        )

        layout.addStretch(1)

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
        self._problems_box.setVisible(self._problems.count() > 0)
        self._problems_box.setTitle(f"Problems ({self._problems.count()})")

        if library.entries:
            self._set_status(f"{len(library.playable)} of {len(library.entries)} playable")
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

    def _row_text(self, entry: SongEntry) -> str:
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
        self._bpm.setEnabled(playable)
        self._bpm_label.setEnabled(playable)
        self._tracks.setEnabled(playable)
        self._play.setEnabled(playable)
        self._refresh_delete_button()

        if not playable:
            self._clear_facts()
            self._bpm_label.setText("")
            return

        self._offset.blockSignals(True)
        self._offset.setValue(int(self.context.settings.offset_for(entry.slug)))
        self._offset.blockSignals(False)
        self._on_offset_changed(self._offset.value())
        self._load_bpm(entry)

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
        # **The band carries its own input**: "Hard (3.90 ops)" rather than a separate
        # row. Two reasons. The card is already tight enough that Practice tempo sits
        # at the very bottom of it, and an added row pushed that out of view. And the
        # number belongs next to the label it explains -- §45 is that "10.77 nps"
        # beside "Medium" read as a broken label, because note density and onset
        # density are different measures and only one of them was on screen.
        onsets = chart.onsets_per_second
        band = entry.difficulty
        shown = band if onsets <= 0 else f"{band} ({onsets:.2f} ops)"
        for name, value in (
            ("Tempo", f"{chart.tempo} BPM"),
            ("Length", format_duration(chart.duration)),
            ("Notes", f"{chart.note_count}"),
            ("Note density", f"{chart.notes_per_second:.2f} nps"),
            ("Difficulty", shown),
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

    # --- practice tempo ------------------------------------------------------

    def _written_bpm(self) -> int:
        """The tempo the selected tab is written at, or 0 when it has no chart.

        Read from the *entry* rather than a resolved chart, so the control can be
        set up before a track is chosen -- the tempo belongs to the song, not to
        the track.
        """
        entry = self._selected_entry()
        if entry is None or entry.chart is None:
            return 0
        return int(round(entry.chart.tempo))

    def _load_bpm(self, entry: SongEntry) -> None:
        """Point the tempo control at the newly selected song.

        The range is clamped to the tab's own tempo, so the control **cannot
        express a value that would break the music** (§18.3): the maximum is the
        written tempo, never above it. The remembered value is clamped into that
        range rather than trusted, because a settings file is hand-editable and a
        tab's tempo can change under a stored value.
        """
        written = max(1, self._written_bpm())
        self._bpm.blockSignals(True)
        try:
            self._bpm.setRange(int(MIN_BPM), written)
            stored = self.context.settings.bpm_for(entry.slug, float(written))
            self._bpm.setValue(max(int(MIN_BPM), min(written, round(stored))))
        finally:
            self._bpm.blockSignals(False)
        self._refresh_bpm_label()

    def _on_bpm_changed(self, value: int) -> None:
        self._refresh_bpm_label()

    def _refresh_bpm_label(self) -> None:
        """Say in words what the number is doing.

        The box holds an absolute tempo, which is the stored value (§18.3), but
        "50" next to a Tempo fact reading 76 does not say whether that is slower or
        a different song. The percentage is a *display* of an absolute choice, not
        the thing being stored.
        """
        written = self._written_bpm()
        if written <= 0:
            self._bpm_label.setText("")
            return
        chosen = self._bpm.value()
        if chosen >= written:
            self._bpm_label.setText("as written")
        else:
            self._bpm_label.setText(f"{chosen / written * 100:.0f}% of written")

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
        chosen_bpm = self._bpm.value()
        # The written tempo is stored as 0, not as its own number: "as written" and
        # "no entry" mean the same thing, and pinning 76 into a file would hold the
        # song at 76 after the tab is re-exported at 84.
        bpm = 0.0 if chosen_bpm >= self._written_bpm() else float(chosen_bpm)
        # Remembered only on Play: a settings save is an fsync, and a drag or a
        # spin emits valueChanged continuously.
        self.context.settings.set_offset_for(entry.slug, offset_ms)
        self.context.settings.set_bpm_for(entry.slug, bpm)
        try:
            self.context.save_settings()
        except OSError as exc:
            # A read-only config directory must not stop the song being played.
            self._set_status(f"could not save the offset: {exc}")

        self.context.request_play(
            entry.slug, int(track_number), offset_ms=offset_ms, bpm=bpm
        )
        self.shell.navigate(Screen.GAME)

    def _rescan_library(self) -> None:
        if not self._loader.start(
            self.context.songs_dir, collapse=self.context.settings.collapse_chords
        ):
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

    # --- deleting a song (§46) ------------------------------------------------

    def _ask_confirmation(self, message: str) -> bool:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Delete song?",
            message,
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            # **No, not Yes.** A confirmation whose default is the destructive answer
            # is a mis-click waiting to happen, and Import GP's equivalent already
            # does it.
            QtWidgets.QMessageBox.StandardButton.No,
        )
        return answer == QtWidgets.QMessageBox.StandardButton.Yes

    def _on_delete_clicked(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        if not self._ask_confirmation(self._delete_message(entry)):
            return
        self._delete_song(entry)

    def _delete_message(self, entry: SongEntry) -> str:
        """What is about to happen, named exactly.

        The filename is in the message rather than only the song's title, because the
        file is what is being destroyed and a title can be the same for two tabs. And
        it says **cannot be undone**, because unlike §46's first attempt at this there
        is no second copy and no way back — a hide could be reversed from the Hidden
        toggle, and this cannot be reversed at all.
        """
        message = (
            f"Delete '{entry.title}'?\n\n"
            f"{entry.tab_path.name} will be deleted from your songs folder. "
            "This cannot be undone."
        )
        if entry.audio_path is not None:
            # Said rather than done. The audio is not what puts a song on this list,
            # and it can be large, so it is left for the player to remove deliberately.
            message += f"\n\nIts audio file, {entry.audio_path.name}, is left in place."
        return message

    def _delete_song(self, entry: SongEntry) -> None:
        """Unlink the tab, say whether it worked, and rebuild the list from disk.

        Named ``_delete_song`` and not ``_delete`` because the **button** is
        ``self._delete``, and an instance attribute shadows a method of the same name:
        the first version of this was ``_delete``, and ``self._delete(entry)`` called
        the button -- `TypeError: 'QPushButton' object is not callable`. The same
        mistake as the duplicate ``_selected_entry`` in §46.4, in the same session.


        **The library is rebuilt by rescanning rather than by removing the entry from
        the in-memory list.** A delete is exactly the moment for the list to stop
        trusting what it was handed: anything derived from the entry -- the status
        counts, the problems panel -- is then true of the folder rather than true of a
        list someone edited. The rescan is asynchronous because parsing every tab is
        seconds of work and §1.4 forbids doing that on the GUI thread.
        """
        # **Refuse to unlink anything outside the songs folder.** `tab_path` comes from
        # a directory scan, so in practice it is always inside -- but this unlinks a
        # file with no undo, and "the path we were handed" is not a boundary worth
        # trusting when the cost of being wrong is the player's tab.
        #
        # This is not hypothetical. A test whose library entry carried the relative
        # path `songs/beta.gp5` resolved that against the **repository's own songs
        # directory** and would have deleted a real tab, had the name happened to
        # match one. Nothing was lost only because no tab in this repository is called
        # `beta.gp5`. That is a near miss found by writing the test, not by reading
        # the code.
        songs_dir = self.context.songs_dir.resolve()
        try:
            target = entry.tab_path.resolve()
        except OSError as exc:  # pragma: no cover - a path that will not resolve
            self._set_status(f"could not resolve {entry.tab_path.name}: {exc}")
            return
        if not target.is_relative_to(songs_dir):
            self._set_status(
                f"refused to delete {entry.tab_path.name}: it is outside your songs folder"
            )
            return

        try:
            target.unlink()
        except OSError as exc:
            # A failure here is a real outcome and the song is still on disk, so it is
            # reported rather than swallowed. Silently doing nothing would leave a
            # row that looks deletable and is not.
            self._set_status(f"could not delete {entry.tab_path.name}: {exc}")
            return
        self._set_status(f"deleted {entry.tab_path.name}")
        self._rescan_library()

    def _refresh_delete_button(self) -> None:
        """Enabled only with something selected, so it never looks like a no-op."""
        has_selection = self._selected_entry() is not None
        self._delete.setVisible(True)
        self._delete.setEnabled(has_selection)

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
