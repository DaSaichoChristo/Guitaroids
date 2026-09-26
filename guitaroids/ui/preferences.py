"""Preferences: the settings the player can change.

Edits go to a **draft**, a copy of the context's settings, and are only committed
on Save. Two other designs were rejected:

- Applying live means a half-finished set of changes takes effect, and navigating
  away silently keeps them.
- Writing to disk on every change means an atomic temp-file-and-fsync per slider
  pixel.

A draft plus an explicit Save is the boring, predictable one: Cancel really
discards, and nothing is on disk until the user says so.

The audio and camera device pickers are present but **disabled**, and say why.
Enumerating devices means opening PortAudio and a camera, which is M2/M3 work and
must not happen just to render a settings page. A control that looks live and does
nothing is worse than one that admits it is not ready.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

from ..settings import InputMode, Settings
from .screens import ScreenBase, constrained_button, content_column, heading

if TYPE_CHECKING:  # pragma: no cover - types only
    from ..context import AppContext


class Preferences(ScreenBase):
    """A form over a draft of the settings."""

    TITLE = "preferences"

    def __init__(
        self,
        shell,
        context: "AppContext",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(shell, context, parent)

        # A shallow copy would share song_offsets_ms with the live settings, so a
        # later edit to one would show up in the other. Only the scalar fields are
        # ever rebound here, but sharing a mutable dict between a draft and the
        # thing it is a draft of is a trap worth not setting.
        draft = replace(context.settings)
        draft.song_offsets_ms = dict(context.settings.song_offsets_ms)
        self._draft = draft

        # The form is taller than a 640px window, and a QVBoxLayout that cannot fit
        # its children does not clip them -- it *compresses* them below their
        # minimum, and the widgets end up drawn on top of each other. That is what
        # the first version of this screen did: three combo boxes overlapping in
        # the Input group. Scrolling degrades honestly; compressing lies about
        # what is on screen.
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        # The viewport paints its own background by default, which would cover the
        # themed app background.
        scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(scroll, 1)

        inner = QtWidgets.QWidget()
        scroll.setWidget(inner)

        column = content_column(inner, margin=40, max_width=520)

        column.addWidget(heading("Preferences"))
        column.addWidget(
            heading("Changes are saved when you press Save.", kind="subtitle")
        )
        column.addSpacing(10)

        column.addWidget(self._build_sliders())
        column.addWidget(self._build_input_group())
        column.addWidget(self._build_devices_group())

        self._status = heading("", kind="dim")
        self._status.setWordWrap(True)
        column.addWidget(self._status)

        # The actions are outside the scroll area, so the form column has no stretch
        # of its own. Without this the QVBoxLayout has no stretch item to absorb the
        # surplus height on a tall window, and it hands the extra out *equally* to
        # every widget that can grow -- QLabel and QGroupBox both can. At 1440px
        # that gave the "Preferences" title 203px for 31px of text, and put a
        # 120px hole between the title and the subtitle.
        column.addStretch(1)

        # The actions sit outside the scroll area, pinned to the bottom. A Save
        # button below the fold is a real wart: you move a slider, go looking for
        # Save, and cannot tell whether the change stuck.
        outer.addLayout(self._build_button_bar())

        # Populated here as well as in showEvent: a test that builds the screen
        # without showing it should still see a populated form, and the first
        # showEvent will load the same values again.
        self._load(self._draft)

    # --- construction --------------------------------------------------------

    @staticmethod
    def _labelled_slider(
        parent: QtWidgets.QWidget,
        title: str,
        *,
        maximum: int = 100,
    ) -> tuple[QtWidgets.QWidget, QtWidgets.QSlider, QtWidgets.QLabel]:
        """A titled percentage slider with a value readout."""
        box = QtWidgets.QWidget(parent)
        layout = QtWidgets.QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(heading(title, kind="dim"))
        value = heading("", kind="stat")
        header.addWidget(value, alignment=QtCore.Qt.AlignmentFlag.AlignRight)
        layout.addLayout(header)

        slider = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        slider.setRange(0, maximum)
        layout.addWidget(slider)
        return box, slider, value

    def _build_sliders(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Sound")
        layout = QtWidgets.QVBoxLayout(box)
        layout.setSpacing(14)

        self._master_box, self._master, self._master_value = self._labelled_slider(
            box, "Master volume"
        )
        self._master.valueChanged.connect(self._on_master_changed)
        layout.addWidget(self._master_box)

        self._click_box, self._click, self._click_value = self._labelled_slider(
            box, "Click track"
        )
        self._click.valueChanged.connect(self._on_click_changed)
        layout.addWidget(self._click_box)

        return box

    def _build_input_group(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Input")
        layout = QtWidgets.QVBoxLayout(box)
        layout.setSpacing(10)

        # A form, not stacked hboxes: a label and its control then share one row
        # baseline instead of each being laid out independently and drifting.
        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        layout.addLayout(form)

        self._mode = QtWidgets.QComboBox()
        for member in InputMode:
            label = "Keyboard" if member is InputMode.KEYBOARD else "Camera (webcam)"
            self._mode.addItem(label, member.value)
        self._mode.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow(heading("Mode", kind="dim"), self._mode)

        self._latency = QtWidgets.QSpinBox()
        self._latency.setRange(0, 2000)
        self._latency.setSingleStep(5)
        self._latency.setSuffix(" ms")
        self._latency.setToolTip(
            "Your camera and tracking lag, subtracted before a note is judged, so "
            "tracking does not read as a late hit."
        )
        self._latency.valueChanged.connect(self._on_latency_changed)
        form.addRow(heading("Camera latency", kind="dim"), self._latency)

        self._count_in = QtWidgets.QComboBox()
        for bars in (0, 1, 2):
            self._count_in.addItem(
                "none" if bars == 0 else f"{bars} bar" + ("s" if bars > 1 else ""), bars
            )
        self._count_in.setToolTip(
            "Clicks before the music starts. Many tracks open with their own count-in."
        )
        self._count_in.currentIndexChanged.connect(self._on_count_in_changed)
        form.addRow(heading("Count-in", kind="dim"), self._count_in)

        self._collapse = QtWidgets.QCheckBox("Collapse chords to one note per onset")
        self._collapse.setToolTip(
            "Off keeps every note in a chord, which is what keyboard play needs."
        )
        self._collapse.toggled.connect(self._on_collapse_changed)
        layout.addWidget(self._collapse)

        return box

    def _build_devices_group(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Devices")
        layout = QtWidgets.QVBoxLayout(box)
        layout.setSpacing(8)

        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        layout.addLayout(form)

        for title in ("Audio output", "Camera"):
            combo = QtWidgets.QComboBox()
            combo.addItem("system default")
            combo.setEnabled(False)
            form.addRow(heading(title, kind="dim"), combo)

        # Short enough not to wrap at this column width. A word-wrapped QLabel in a
        # tight vertical stack reports a height for the width it happens to have,
        # and the text then spills over whatever is below it -- which is how the
        # first version of this screen ended up with three overlapping widgets.
        note = heading("Device picking arrives with the audio and camera layers.", kind="dim")
        note.setWordWrap(False)
        layout.addWidget(note)
        return box

    def _build_button_bar(self) -> QtWidgets.QHBoxLayout:
        """The pinned action row, centred without a fixed width.

        Centred with stretches rather than a width-capped container, because this
        row is a sibling of the scroll area rather than a child of the form column
        and must line up with it at any window size.
        """
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(40, 10, 40, 24)
        row.setSpacing(12)
        row.addStretch(1)

        back = constrained_button("Back", width=140)
        back.clicked.connect(self._leave)
        row.addWidget(back)

        self._reset = constrained_button("Reset", width=140)
        self._reset.clicked.connect(self._reset_to_defaults)
        row.addWidget(self._reset)

        save = constrained_button("Save", object_name="primary", width=160)
        save.clicked.connect(self.save)
        save.setDefault(True)
        self._save_button = save
        row.addWidget(save)

        row.addStretch(1)
        return row

    # --- loading the draft into the widgets ----------------------------------

    def _load(self, settings: Settings) -> None:
        """Push a settings object into the form, without echoing back to the draft.

        Every setter is signal-blocked. Without that, populating the form would
        fire a dozen valueChanged handlers and rewrite the draft with values read
        back out of half-built widgets.
        """
        for widget in (
            self._master,
            self._click,
            self._mode,
            self._latency,
            self._count_in,
            self._collapse,
        ):
            widget.blockSignals(True)
        try:
            self._master.setValue(round(settings.master_volume * 100))
            self._click.setValue(round(settings.click_volume * 100))
            self._mode.setCurrentIndex(max(0, self._mode.findData(settings.input_mode.value)))
            self._latency.setValue(round(settings.input_latency_ms))
            self._count_in.setCurrentIndex(max(0, self._count_in.findData(settings.count_in_bars)))
            self._collapse.setChecked(settings.collapse_chords)
        finally:
            for widget in (
                self._master,
                self._click,
                self._mode,
                self._latency,
                self._count_in,
                self._collapse,
            ):
                widget.blockSignals(False)

        self._refresh_value_labels()
        self._sync_enabled()

    def _refresh_value_labels(self) -> None:
        self._master_value.setText(f"{self._master.value()}%")
        self._click_value.setText(f"{self._click.value()}%")

    def _sync_enabled(self) -> None:
        """Latency only means anything in camera mode, so dim it otherwise."""
        camera = self._draft.input_mode is InputMode.CAMERA
        self._latency.setEnabled(camera)
        self._latency.setToolTip(
            "" if camera else "Only used in camera mode."
        )

    # --- draft edits ---------------------------------------------------------

    def _on_master_changed(self, value: int) -> None:
        self._draft.master_volume = value / 100
        self._refresh_value_labels()

    def _on_click_changed(self, value: int) -> None:
        self._draft.click_volume = value / 100
        self._refresh_value_labels()

    def _on_mode_changed(self, _index: int) -> None:
        self._draft.input_mode = InputMode.parse(self._mode.currentData(), self._draft.input_mode)
        self._sync_enabled()

    def _on_latency_changed(self, value: int) -> None:
        self._draft.input_latency_ms = float(value)

    def _on_count_in_changed(self, _index: int) -> None:
        data = self._count_in.currentData()
        if isinstance(data, int):
            self._draft.count_in_bars = data

    def _on_collapse_changed(self, checked: bool) -> None:
        self._draft.collapse_chords = bool(checked)

    # --- actions -------------------------------------------------------------

    def save(self) -> None:
        """Commit the draft to the context and to disk.

        ``song_offsets_ms`` is deliberately **not** taken from the draft. This
        screen has no control for it -- song select owns it -- and a draft built
        when the screen was first constructed would otherwise write back a stale
        copy and silently wipe an offset the user set later in the session.
        """
        offsets = dict(self.context.settings.song_offsets_ms)
        self.context.settings = replace(self._draft)
        self.context.settings.song_offsets_ms = offsets
        try:
            self.context.save_settings()
        except OSError as exc:
            # The in-memory settings are still correct; only the file failed.
            self._status.setText(f"Applied, but could not write {self.context.settings_path}: {exc}")
            return
        self._status.setText("Saved.")

    def _reset_to_defaults(self) -> None:
        """Fill the form with the defaults. Still needs Save to take effect."""
        defaults = Settings()
        defaults.song_offsets_ms = dict(self._context_offsets())
        self._draft = defaults
        self._load(self._draft)
        self._status.setText("Defaults loaded. Press Save to keep them.")

    def _context_offsets(self) -> dict:
        return self.context.settings.song_offsets_ms

    def _leave(self) -> None:
        """Back, discarding unsaved changes without asking.

        Deliberately not a confirmation prompt: nothing has been applied, so there
        is nothing to lose but the edits on this page, and a dialog on every Back
        press would be worse than the mistake it prevents.
        """
        self._load(self.context.settings)
        self._status.setText("")
        self.shell.go_back()

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 - Qt naming
        """Re-read the context each time the screen is shown.

        The shell keeps built screens, so this is not constructed afresh on the
        second visit: without this, returning here would show the previous visit's
        draft, which may never have been saved.
        """
        draft = replace(self.context.settings)
        draft.song_offsets_ms = dict(self.context.settings.song_offsets_ms)
        self._draft = draft
        self._load(self._draft)
        self._status.setText("")
        super().showEvent(event)
