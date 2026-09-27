"""Preferences: the settings the player can change.

Edits go to a **draft**, a copy of the context's settings, and are only committed
on Save. Two other designs were rejected:

- Applying live means a half-finished set of changes takes effect, and navigating
  away silently keeps them.
- Writing to disk on every change means an atomic temp-file-and-fsync per slider
  pixel.

A draft plus an explicit Save is the boring, predictable one: Cancel really
discards, and nothing is on disk until the user says so.

Both device pickers are live. Enumerating devices opens PortAudio, which was
M2/M3 work when the input picker was a disabled placeholder; §23 wrote the
playback path and §30 the microphone path, and each of those left the greyed-out
control and its justification behind. Opening PortAudio to *enumerate* is not
opening a stream, so it costs nothing the audio layer was not already paying.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

from ..settings import Settings
from .theme import px
from .screens import ScreenBase, constrained_button, content_column, heading

if TYPE_CHECKING:  # pragma: no cover - types only
    from ..context import AppContext


def _unwrapping(label: QtWidgets.QLabel) -> QtWidgets.QLabel:
    """A row label in a form column, which must not wrap.

    ``heading()`` returns a word-wrapped label, which is right for a paragraph and
    wrong for a form row. In a 92px column "Microphone input" wrapped to two lines
    while the stylesheet's own ``sizeHint`` said one, and the row's second line was
    drawn over the text beneath it -- a clipped label that no test caught, because
    every test on this screen checked behaviour rather than what got drawn.

    Turning wrapping *off* fixes the overlap, and the label then needs a minimum
    width as wide as its text or the form clips it sideways instead. Both are
    needed, and neither is enough alone: wrapping makes a second line that is
    drawn over the row below, and no-wrap without a width cuts the word off at
    the column edge.

    Why not pin the height from ``heightForWidth``, which is right in principle?
    Because it was measured to be worse. A taller label makes ``QFormLayout``
    narrow the label column further, which wraps the *other* row labels, and ten
    clipping assertions on three other screens went with it. A layout economising
    hands out widths as well as heights, so demanding more room at one label
    shrinks everyone else's.
    """
    label.setWordWrap(False)
    # The width the text actually needs, as a floor. `sizeHint` on an unwrapped
    # label is the text width, so this is asking the form for a column that fits.
    label.setMinimumWidth(label.sizeHint().width())
    return label


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
        layout.setSpacing(px(4))

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
        layout.setSpacing(px(14))

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
        layout.setSpacing(px(10))

        # A form, not stacked hboxes: a label and its control then share one row
        # baseline instead of each being laid out independently and drifting.
        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(10)
        layout.addLayout(form)

        # There is no mode control any more (§32): the microphone is the only input,
        # so there is nothing to choose between. What the control used to carry is a
        # precondition, not a tip -- the app renders the tab and plays it from this
        # machine, so through speakers the microphone hears the app scoring itself --
        # and a precondition deserves a line a player cannot miss.
        note = heading(
            "The microphone is the input. Use headphones: the app plays the tab out "
            "of this machine, and through speakers it would hear its own music as "
            "your playing.",
            kind="dim",
        )
        note.setWordWrap(True)
        form.addRow(note)

        self._latency = QtWidgets.QSpinBox()
        # The model's range, not an arbitrary one. It is asymmetric on purpose
        # (settings.py): +2s for a genuinely bad interface such as Bluetooth, and
        # -500ms because the correction can measure out in the other direction.
        # This control used to be 0-2000, which made the negative half unreachable
        # exactly when a player needs it -- overshoot, read "early" off the game
        # screen, and come here to dial it back.
        self._latency.setRange(-500, 2000)
        self._latency.setSingleStep(5)
        self._latency.setSuffix(" ms")
        self._latency.setToolTip(
            "How far behind the sound your playing is, subtracted before a note is "
            "judged, so a correct note does not read as an early one.\n\n"
            "Start at zero. The analysis window's own 23ms is already compensated "
            "for; this is the trim on top of it, for your own room and your own "
            "microphone.\n\n"
            "The game screen shows your timing as 'Nms late' or 'Nms early'. Put "
            "that number here: if it says you are 180ms late, enter 180. A value "
            "that is too HIGH makes every note read as early, because the trim is "
            "subtracted -- and that is the opposite of the obvious reading, which "
            "is why it is written down here. Negative is allowed for the same "
            "reason."
        )
        self._latency.valueChanged.connect(self._on_latency_changed)
        form.addRow(heading("Input latency", kind="dim"), self._latency)

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
        # The tooltip used to say that keeping every note "is what keyboard play
        # needs", which is the opposite of the truth and the opposite of §7.4's
        # reasoning: one fretting-hand position is one lane, so a six-note chord is
        # six keys at once. A player reading this needs to know what they are
        # trading, and it now says both directions.
        self._collapse.setToolTip(
            "Off by default: every note in a chord is kept and drawn, and a chord "
            "is several notes at once.\n\n"
            "On reduces each chord to a single note -- one key per beat, much "
            "easier on a keyboard, but most of what is written down disappears "
            "from the tab."
        )
        self._collapse.toggled.connect(self._on_collapse_changed)
        layout.addWidget(self._collapse)

        return box

    def _build_devices_group(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Devices")
        layout = QtWidgets.QVBoxLayout(box)
        layout.setSpacing(px(8))

        form = QtWidgets.QFormLayout()
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        layout.addLayout(form)

        # Output picking is live and reaches the stream: `Transport` and
        # `start_playback` both take `device`, and the game screen passes
        # `Settings.audio_device` (DESIGN.md §28.2). It used to be a disabled combo
        # saying picking "arrives with the audio layer", which arrived in §23.
        self._output = QtWidgets.QComboBox()
        self._output.setObjectName("deviceCombo")
        self._output.currentIndexChanged.connect(self._on_output_changed)
        form.addRow(_unwrapping(heading("Audio output", kind="dim")), self._output)

        # Both pickers are live and both reach a stream. This used to be a disabled
        # combo reading "not yet", justified because "`Settings.input_device` is read by
        # no code at all, because there is no microphone path to read it for (§24)".
        # §30 wrote that path: `Game._start_microphone` passes the setting to
        # `Microphone`, which opens `sd.InputStream(device=...)`. The control was
        # greyed out for a reason that had stopped being true, which is the same shape
        # as §21.2's unread setting -- a justification outliving the fact, and a
        # player left unable to choose their own microphone.
        self._input = QtWidgets.QComboBox()
        self._input.setObjectName("inputDeviceCombo")
        self._input.currentIndexChanged.connect(self._on_input_changed)
        form.addRow(_unwrapping(heading("Microphone input", kind="dim")), self._input)

        # Short enough not to wrap at this column width. A word-wrapped QLabel in a
        # tight vertical stack reports a height for the width it happens to have,
        # and the text then spills over whatever is below it -- which is how the
        # first version of this screen ended up with three overlapping widgets.
        note = heading(
            "Both are read when a song starts, not while this page is open.",
            kind="dim",
        )
        note.setWordWrap(False)
        layout.addWidget(note)
        return box

    def _device_choices(self, direction: str) -> tuple[list[str | None], list[str]]:
        """``(values, labels)`` for a device combo: system default, then each device.

        ``direction`` is ``"out"`` or ``"in"``, which is the only thing that differs
        between the two combos -- the enumeration, the labels and the failure
        behaviour are identical, so they are one function. It was two copies when
        only the output picker existed, and the input picker was a disabled placeholder
        saying device picking "arrives with the audio layer" (which arrived in §23) and
        then "is read by no code" (which stopped being true in §30, when
        `Settings.input_device` reached `sd.InputStream`).

        ``sounddevice`` is imported inside the function, exactly as
        `transport.device_report` does it, so that merely building this screen never
        opens PortAudio. A machine with no device in this direction gets the single
        "system default" row rather than an empty box, which is what the real failure
        looks like anyway.
        """
        channel_key = "max_output_channels" if direction == "out" else "max_input_channels"
        values: list[str | None] = [None]
        labels: list[str] = ["system default"]
        try:
            import sounddevice as sd
        except Exception:  # noqa: BLE001 - no PortAudio means no list, not a crash
            return values, labels
        try:
            devices = sd.query_devices()
        except Exception:  # noqa: BLE001 - a broken host still gets a usable screen
            return values, labels
        for device in devices:
            channels = device[channel_key]
            if channels < 1:
                continue
            values.append(device["name"])
            labels.append(f"{device['name']}  ({channels}ch)")
        return values, labels

    def _load_device(
        self,
        combo: QtWidgets.QComboBox,
        chosen: str | None,
        direction: str,
    ) -> None:
        """Point a device combo at the stored name, adding the row if it is not listed.

        A device that has been unplugged since the setting was written is still a
        legitimate stored value, and the stream will fail loudly on it if it is really
        gone. Dropping the name from the combo would turn that honest failure into a
        silent fall back to the system default, which is the same class of bug as a
        setting that nothing reads.

        With no device stored, the combo shows the system default and the draft keeps
        `None` -- so merely opening and saving this screen cannot invent a device.
        """
        values, labels = self._device_choices(direction)
        combo.clear()
        combo.addItems(labels)
        if chosen is None:
            combo.setCurrentIndex(0)
            return
        if chosen in values:
            combo.setCurrentIndex(values.index(chosen))
            return
        combo.addItem(f"{chosen}  (not connected)")
        combo.setCurrentIndex(combo.count() - 1)

    def _on_device_changed(self, combo: QtWidgets.QComboBox, direction: str, attribute: str) -> None:
        """Record a device choice on the **draft**, like every other control here.

        `save` commits with `replace(self._draft)`, so writing to `context.settings`
        directly would change the setting behind a Cancel button -- and these would then
        be the only controls on the screen that ignore it.
        """
        values, _labels = self._device_choices(direction)
        index = combo.currentIndex()
        if 0 <= index < len(values):
            setattr(self._draft, attribute, values[index])

    def _on_output_changed(self, _index: int) -> None:
        self._on_device_changed(self._output, "out", "audio_device")

    def _on_input_changed(self, _index: int) -> None:
        self._on_device_changed(self._input, "in", "input_device")

    def _build_button_bar(self) -> QtWidgets.QHBoxLayout:
        """The pinned action row, centred without a fixed width.

        Centred with stretches rather than a width-capped container, because this
        row is a sibling of the scroll area rather than a child of the form column
        and must line up with it at any window size.
        """
        row = QtWidgets.QHBoxLayout()
        row.setContentsMargins(px(40), px(10), px(40), px(24))
        row.setSpacing(px(12))
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
            self._latency,
            self._count_in,
            self._collapse,
            self._output,
        ):
            widget.blockSignals(True)
        try:
            self._master.setValue(round(settings.master_volume * 100))
            self._click.setValue(round(settings.click_volume * 100))
            self._latency.setValue(round(settings.input_latency_ms))
            self._count_in.setCurrentIndex(max(0, self._count_in.findData(settings.count_in_bars)))
            self._collapse.setChecked(settings.collapse_chords)
            self._load_devices(settings)
        finally:
            for widget in (
                self._master,
                self._click,
                    self._latency,
                self._count_in,
                self._collapse,
                self._output,
            ):
                widget.blockSignals(False)

        self._refresh_value_labels()
        self._sync_enabled()

    def _load_devices(self, settings: Settings) -> None:
        """Point both device combos at what the settings say."""
        self._load_device(self._output, settings.audio_device, "out")
        self._load_device(self._input, settings.input_device, "in")

    def _refresh_value_labels(self) -> None:
        self._master_value.setText(f"{self._master.value()}%")
        self._click_value.setText(f"{self._click.value()}%")

    def _sync_enabled(self) -> None:
        """Nothing to sync: the latency trim is always live.

        It used to be dimmed for a keyboard run, on the reasoning that with six keys the
        latency was the keyboard's own and a number the player invented would only add
        to it. There is no keyboard now, so it is the only input control this group has
        and disabling it would be a control that looks broken.

        Kept as a method because `_load` still calls it: a screen-wide place to hang
        any future conditional, and a hook that costs nothing to keep.
        """
        self._latency.setEnabled(True)

    # --- draft edits ---------------------------------------------------------

    def _on_master_changed(self, value: int) -> None:
        self._draft.master_volume = value / 100
        self._refresh_value_labels()

    def _on_click_changed(self, value: int) -> None:
        self._draft.click_volume = value / 100
        self._refresh_value_labels()

    def _on_mode_changed(self, _index: int) -> None:
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
        """Commit the draft to the context and to disk, then leave.

        **Leaving is part of saving.** Pressing Save and staying put means the next
        thing to do is press Back, which is a second press that reads as "I have not
        finished" when the settings are already written. It also left the player on a
        settings page with nothing to do on it, and "Saved." is not visible long enough
        to be worth anything.

        **But not when the write failed.** A failed write is the one case where the
        player has to see the reason, and navigating away would hide it behind a menu.
        The in-memory settings are still correct in that case, so the screen is showing
        the truth about what is applied and what is not.

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
        # `go_back`, not `_leave`: that method exists to *discard* unsaved changes
        # before going back, and calling it after a successful save would wipe the
        # "Saved." it had just set and reload a draft nobody needs reloaded. The
        # status is set first anyway, so a navigation that went nowhere would still
        # leave the truth on screen.
        self.shell.go_back()

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
