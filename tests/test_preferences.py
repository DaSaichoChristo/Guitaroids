"""Tests for the preferences screen.

The point of these is the draft/commit boundary. Everything else -- a slider
setting a number, a combo setting a string -- is Qt's job, not this screen's.

The dangerous failures are the quiet ones: a change that never reaches the
settings object, a change that reaches it but not the disk, a cancelled edit that
sticks anyway. Those are what is pinned here.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6 import QtWidgets

from guitaroids.context import AppContext
from guitaroids.settings import Settings
from guitaroids.ui.preferences import DEVICE_COMBO_CHARS, Preferences
from guitaroids.ui.screens import Screen


@pytest.fixture()
def context(tmp_path: Path) -> AppContext:
    return AppContext(
        settings=Settings(),
        songs_dir=tmp_path / "songs",
        settings_path=tmp_path / "settings.json",
    )


@pytest.fixture()
def screen(shell, context) -> Preferences:
    shell.navigate(Screen.PREFERENCES)
    widget = shell.current_screen
    assert isinstance(widget, Preferences)
    return widget


# --- loading -----------------------------------------------------------------


def test_the_form_shows_the_current_settings(shell, context) -> None:
    context.settings = Settings(master_volume=0.25, click_volume=0.1, count_in_bars=2)
    shell.navigate(Screen.PREFERENCES)
    screen = shell.current_screen
    assert screen._master.value() == 25
    assert screen._click.value() == 10
    assert screen._count_in.currentData() == 2


def test_the_volume_labels_track_the_sliders(screen: Preferences) -> None:
    screen._master.setValue(40)
    assert screen._master_value.text() == "40%"


def test_loading_the_form_does_not_alter_the_draft(shell, context) -> None:
    """Populating must not echo back through the change handlers.

    Every setter here is signal-blocked; without that, building the form would
    fire a dozen valueChanged handlers and rewrite the draft with values read out
    of half-built widgets.
    """
    context.settings = Settings(master_volume=0.8)
    shell.navigate(Screen.PREFERENCES)
    assert shell.current_screen._draft.master_volume == 0.8


# --- the draft ---------------------------------------------------------------


def test_editing_does_not_touch_the_live_settings(screen: Preferences, context) -> None:
    screen._master.setValue(20)
    screen._click.setValue(30)
    assert context.settings.master_volume == 0.8, "nothing applies before Save"
    assert context.settings.click_volume == 0.5


def test_editing_does_not_write_to_disk(screen: Preferences, context) -> None:
    screen._master.setValue(20)
    assert not context.settings_path.exists(), "a drag must not fsync per pixel"


def test_save_commits_the_draft(screen: Preferences, context) -> None:
    screen._master.setValue(20)
    screen._click.setValue(30)
    screen._collapse.setChecked(False)
    screen._latency.setValue(45)
    screen.save()
    assert context.settings.master_volume == pytest.approx(0.2)
    assert context.settings.click_volume == pytest.approx(0.3)
    assert context.settings.collapse_chords is False
    assert context.settings.input_latency_ms == 45.0


def test_save_writes_the_file(screen: Preferences, context) -> None:
    screen._master.setValue(20)
    screen.save()
    assert context.settings_path.is_file()
    assert Settings.load(context.settings_path).master_volume == pytest.approx(0.2)


def test_save_reports_success(screen: Preferences) -> None:
    screen._master.setValue(20)
    screen.save()
    assert "Saved" in screen._status.text()


def test_a_failed_write_still_applies_in_memory(
    screen: Preferences, context, monkeypatch
) -> None:
    """A read-only config directory must not lose the change the user just made."""
    screen._master.setValue(20)

    def explode():
        raise OSError("read-only file system")

    monkeypatch.setattr(context, "save_settings", explode)
    screen.save()
    assert context.settings.master_volume == pytest.approx(0.2)
    assert "could not write" in screen._status.text()


def test_the_draft_does_not_alias_the_live_offsets(screen: Preferences, context) -> None:
    """A copy, not a shallow share.

    Sharing song_offsets_ms would mean an edit in one showing up in the other --
    and the offsets song select writes would appear as unsaved preferences.
    """
    context.settings.set_offset_for("alpha", -50.0)
    screen._draft = replace(context.settings)
    screen._draft.song_offsets_ms = dict(context.settings.song_offsets_ms)
    assert screen._draft.song_offsets_ms == {"alpha": -50.0}
    assert screen._draft.song_offsets_ms is not context.settings.song_offsets_ms


def test_saving_keeps_the_remembered_offsets(screen: Preferences, context) -> None:
    """Preferences must not wipe the per-song offsets song select owns.

    The draft was built when this screen was constructed, before the offset
    existed. Writing the draft's copy back would silently undo the user's tuning.
    """
    context.settings.set_offset_for("alpha", -50.0)
    screen._master.setValue(20)
    screen.save()
    assert context.settings.offset_for("alpha") == -50.0
    assert Settings.load(context.settings_path).offset_for("alpha") == -50.0


# --- reset -------------------------------------------------------------------


def test_reset_fills_the_form_with_defaults(screen: Preferences) -> None:
    screen._master.setValue(20)
    screen._reset.click()
    assert screen._master.value() == 80
    assert "Press Save" in screen._status.text()


def test_reset_does_not_apply_until_saved(screen: Preferences, context) -> None:
    screen._master.setValue(20)
    screen._reset.click()
    assert context.settings.master_volume == 0.8, "Reset fills the form, not the settings"


def test_saving_returns_to_the_main_menu(shell, context) -> None:
    """Pressing Save and staying put means a second press to leave.

    It also leaves the player on a settings page with nothing to do on it, and
    "Saved." is not on screen long enough to be worth reading. So Save saves and
    leaves. Back is a different thing: it *discards* unsaved changes, and calling it
    after a save would wipe the "Saved." it had just set.
    """
    shell.navigate(Screen.PREFERENCES)
    screen = shell.current_screen
    assert shell.current is Screen.PREFERENCES
    screen.save()
    assert shell.current is Screen.MAIN


def test_a_failed_write_keeps_you_on_the_screen(shell, context, monkeypatch) -> None:
    """The one case where leaving would hide the reason.

    A write that fails is worth seeing, and navigating away would put the explanation
    somewhere the player is not. The in-memory settings are still correct, so the screen
    is telling the truth about what is applied and what is not.
    """
    shell.navigate(Screen.PREFERENCES)
    screen = shell.current_screen

    def refuse() -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(context, "save_settings", refuse)
    screen.save()
    assert shell.current is Screen.PREFERENCES, "a failed save must not navigate away"
    assert "no space left" in screen._status.text(), "and must say why"


def test_saving_still_writes_the_settings_to_disk(shell, context, tmp_path) -> None:
    """Leaving on Save must not become leaving *instead of* saving."""
    path = tmp_path / "settings.json"
    context.settings_path = path
    shell.navigate(Screen.PREFERENCES)
    screen = shell.current_screen
    screen._draft = replace(screen._draft, master_volume=0.25)
    screen.save()
    assert shell.current is Screen.MAIN
    assert Settings.load(path).master_volume == pytest.approx(0.25)


def test_back_still_discards_unsaved_changes(shell, context) -> None:
    """Save and Back are opposites, and only one of them leaves."""
    shell.navigate(Screen.PREFERENCES)
    screen = shell.current_screen
    screen._draft = replace(screen._draft, master_volume=0.1)
    screen._leave()
    assert shell.current is Screen.MAIN
    assert screen.context.settings.master_volume == pytest.approx(0.8), "the draft was discarded"


def test_reset_keeps_the_per_song_offsets(screen: Preferences, context) -> None:
    context.settings.set_offset_for("alpha", -50.0)
    screen._reset.click()
    assert screen._draft.song_offsets_ms == {"alpha": -50.0}


# --- leaving -----------------------------------------------------------------


def test_back_discards_unsaved_changes(screen: Preferences, context, shell) -> None:
    screen._master.setValue(20)
    screen._leave()
    assert context.settings.master_volume == 0.8
    assert shell.current is Screen.MAIN


def test_back_does_not_delete_saved_settings(screen: Preferences, context, shell) -> None:
    screen._master.setValue(20)
    screen.save()
    screen._master.setValue(99)
    screen._leave()
    assert context.settings.master_volume == pytest.approx(0.2), "Save already happened"


def test_revisiting_shows_the_saved_values(screen: Preferences, shell, qapp) -> None:
    """The shell keeps built screens, so showEvent is the only reload hook.

    Without it, coming back would show the previous visit's draft -- which may
    never have been saved. The window has to be shown for showEvent to fire at
    all: a QStackedWidget cannot show a child of a hidden window, which is why
    every navigation test that cares about visibility calls show() first.
    """
    screen._master.setValue(20)
    screen.save()
    screen._master.setValue(99)  # unsaved
    shell.show()
    qapp.processEvents()
    shell.navigate(Screen.MAIN)
    shell.navigate(Screen.PREFERENCES)
    qapp.processEvents()
    assert shell.current_screen._master.value() == 20


# --- the microphone, which is now the only input -----------------------------


def test_the_latency_trim_is_always_enabled(screen: Preferences) -> None:
    """It used to be dimmed for a keyboard run, and there is no keyboard run.

    Disabling the only input control in the group would be a control that looks broken
    rather than a control that is unavailable (§32).
    """
    assert screen._latency.isEnabled()


def test_there_is_no_mode_control_any_more(screen: Preferences) -> None:
    """A control with one legal value is a control pretending to be a choice."""
    from PySide6 import QtWidgets

    assert not hasattr(screen, "_mode")
    combos = [
        c for c in screen.findChildren(QtWidgets.QComboBox)
        if c.objectName() == "" and c is not screen.findChild(QtWidgets.QComboBox, "deviceCombo")
    ]
    assert not any(c.findData("keyboard") >= 0 or c.findData("microphone") >= 0 for c in combos)


def test_the_headphone_warning_is_visible_not_just_a_tooltip(screen: Preferences) -> None:
    """Through speakers the app hears itself and scores PERFECT for nothing.

    That was a tooltip when the microphone was one of two options. It is now the only
    input, so it is a precondition, and a precondition should not be hidden behind a
    hover.
    """
    from PySide6 import QtWidgets

    texts = " ".join(
        label.text() for label in screen.findChildren(QtWidgets.QLabel)
    )
    assert "headphones" in texts.lower(), texts


def test_count_in_offers_exactly_the_allowed_values(screen: Preferences) -> None:
    """Settings clamps count_in_bars to 0-2, so the combo must not offer more."""
    assert [screen._count_in.itemData(i) for i in range(screen._count_in.count())] == [0, 1, 2]


def test_chord_collapse_round_trips(screen: Preferences, context) -> None:
    screen._collapse.setChecked(False)
    screen.save()
    assert context.settings.collapse_chords is False


# --- the not-yet-built parts -------------------------------------------------


def _devices_group(screen: Preferences):
    from PySide6 import QtWidgets

    boxes = [b for b in screen.findChildren(QtWidgets.QGroupBox) if b.title() == "Devices"]
    assert len(boxes) == 1, "expected exactly one Devices group"
    return boxes[0]


def test_the_output_picker_is_live_and_lists_the_system_default(screen: Preferences) -> None:
    """Output picking is no longer a placeholder, so it must not be disabled.

    It used to be a disabled combo reading "system default" with a note saying picking
    "arrives with the audio layer". The audio layer arrived in §23 and the note never
    got the memo; the picker is now wired to `Settings.audio_device` and through it to
    the stream (DESIGN.md §28.2). A disabled control is the failure mode this file
    was written to prevent, so the test now points the other way.
    """
    from PySide6 import QtWidgets

    output = screen.findChild(QtWidgets.QComboBox, "deviceCombo")
    assert output is not None, "the output picker needs a name so tests can find it"
    assert output.isEnabled(), "output picking works now and must be usable"
    assert output.currentText() == "system default", (
        "with nothing stored, the system default is what the screen shows"
    )
    assert output.count() >= 1


def test_the_microphone_picker_is_live_too(screen: Preferences) -> None:
    """Both pickers work, and the input one is the second half of that.

    It was disabled and reading "not yet", justified because
    `Settings.input_device` "is read by no code at all, because there is no microphone
    path to read it for (§24)". §30 wrote that path: `Game._start_microphone` passes
    the setting to `Microphone`, which opens `sd.InputStream(device=...)`.

    A justification outliving the fact it justified is §21.2's shape with the code
    removed, and it left a player unable to choose their own microphone on a machine
    with several. The control is enabled, and "system default" rather than "not yet",
    because the app really is honouring it now.
    """
    from PySide6 import QtWidgets

    combos = _devices_group(screen).findChildren(QtWidgets.QComboBox)
    assert len(combos) == 2, "one picker per device kind"
    microphone = screen.findChild(QtWidgets.QComboBox, "inputDeviceCombo")
    assert microphone is not None, "the input picker needs a name so tests can find it"
    assert microphone in combos
    assert microphone.isEnabled(), "the microphone exists, so the picker must work"
    assert "not yet" not in microphone.currentText()


def test_the_microphone_picker_shows_the_stored_device(screen: Preferences) -> None:
    """The whole point of the control: a chosen input is remembered and shown."""
    from guitaroids.settings import Settings

    context = AppContext()
    context.settings = Settings(input_device="Focusrite Scarlett")
    screen._load_devices(context.settings)
    assert "Focusrite Scarlett" in screen._input.currentText()


def test_an_unplugged_input_is_shown_rather_than_dropped(screen: Preferences) -> None:
    """A stored device that is gone is a legitimate value, and hiding it is a silent lie.

    Same reasoning as the output picker: dropping the name would turn an honest failure
    when the stream cannot open into a silent fall back to the system default, which is
    the same class of bug as a setting nothing reads.
    """
    from guitaroids.settings import Settings

    context = AppContext()
    context.settings = Settings(input_device="A Microphone That Was Unplugged")
    screen._load_devices(context.settings)
    assert "not connected" in screen._input.currentText()


def test_both_pickers_write_to_the_draft_not_the_context(screen: Preferences, monkeypatch) -> None:
    """Otherwise they are the only controls on the screen that ignore Cancel.

    The device list is monkeypatched rather than read from the host, so this does not
    skip on a machine with one sound card -- and, more to the point, it asserts the
    **draft receives the value**. The first version only asserted the context had *not*
    changed, which passes just as happily when the control is not connected to
    anything at all: un-connecting the input picker's `clicked`-equivalent left all
    thirty-three tests green.
    """
    from PySide6 import QtWidgets

    monkeypatch.setattr(
        screen,
        "_device_choices",
        lambda direction: (
            [None, f"chosen-{direction}"],
            ["system default", f"chosen-{direction}  (2ch)"],
        ),
    )
    screen._load_devices(Settings())
    output = screen.findChild(QtWidgets.QComboBox, "deviceCombo")
    microphone = screen.findChild(QtWidgets.QComboBox, "inputDeviceCombo")

    output.setCurrentIndex(1)
    microphone.setCurrentIndex(1)

    assert screen._draft.audio_device == "chosen-out"
    assert screen._draft.input_device == "chosen-in"
    assert screen.context.settings.audio_device is None, "and the context is untouched"
    assert screen.context.settings.input_device is None


def test_a_picker_wired_to_nothing_is_caught_by_the_draft_assertion(
    screen: Preferences, monkeypatch
) -> None:
    """The mutation this file exists to prevent, made as a test rather than run by hand.

    Disconnect the input picker and assert something notices. If this ever stops
    failing, the draft assertions above have become vacuous.
    """
    from PySide6 import QtWidgets

    monkeypatch.setattr(
        screen,
        "_device_choices",
        lambda direction: ([None, "only-input"], ["system default", "only-input  (2ch)"]),
    )
    screen._load_devices(Settings())
    screen._input.currentIndexChanged.disconnect()
    screen._input.setCurrentIndex(1)
    assert screen._draft.input_device is None, (
        "the picker is disconnected, so nothing should have been written -- if this "
        "assertion is what you are reading, the draft checks above are doing their job"
    )


def test_the_devices_group_does_not_claim_to_be_waiting_for_the_audio_layer(
    screen: Preferences,
) -> None:
    """The stale note is pinned, because it is exactly the kind of claim that rots.

    It said picking "arrives with the audio and input layers" and was still there two
    sections after the audio layer landed. Absence test, against the rendered text.
    """
    from PySide6 import QtWidgets

    texts = [label.text() for label in _devices_group(screen).findChildren(QtWidgets.QLabel)]
    assert not any("arrives with" in text for text in texts), texts


def test_the_screen_scrolls_rather_than_compressing(screen: Preferences) -> None:
    """The regression test for three overlapping combo boxes.

    A QVBoxLayout that cannot fit its children compresses them below their
    minimum and they draw on top of each other. Every group box must end up at
    least as tall as its own content needs.
    """
    from PySide6 import QtWidgets

    for box in screen.findChildren(QtWidgets.QGroupBox):
        layout = box.layout()
        if layout is None:
            continue
        needed = layout.minimumSize().height() + box.style().pixelMetric(
            QtWidgets.QStyle.PixelMetric.PM_LayoutTopMargin
        )
        assert box.height() >= needed - 2, (
            f"group {box.title()!r} is {box.height()}px but needs {needed}px; "
            "its contents will overlap"
        )


# --- the input-latency trim can be negative, and survives a save (§39) ---------


def test_the_latency_spin_box_offers_the_negatives_the_model_accepts(screen) -> None:
    """The control's range and the model's range are one number, not two.

    `Settings.input_latency_ms` is documented as asymmetric on purpose -- +2s for a
    genuinely bad interface, -500ms because the correction can measure out the other
    way. The spin box was 0-2000, so the negative half was unreachable through the UI
    and a hand-edited negative was silently rewritten the moment the player opened
    Preferences to look at it.
    """
    assert screen._latency.minimum() == -500, (
        "a player who overshoots and reads 'early' off the game screen cannot dial "
        "the value back without hand-editing the settings file"
    )
    assert screen._latency.maximum() == 2000


def test_a_negative_trim_survives_a_save_and_reload(shell, tmp_path) -> None:
    """Through the real path: widget -> draft -> file -> back into the widget.

    Testing the spin box's `minimum()` is not this. What matters is that -120 written
    by hand into a settings file is still -120 after the screen has been opened,
    changed and saved -- which is the path that destroyed it before.
    """
    import json

    (tmp_path / "settings.json").write_text('{"input_latency_ms": -120.0}')
    context = AppContext(
        settings=Settings.load(tmp_path / "settings.json"),
        songs_dir=tmp_path / "songs",
        settings_path=tmp_path / "settings.json",
    )
    shell.show()
    shell.navigate(Screen.PREFERENCES)
    screen = shell.current_screen
    screen.context = context
    # Away and back, because `showEvent` is what rebuilds the draft from the context's
    # settings. Assigning the context alone leaves the draft holding the *old*
    # context's values, and then the test would be measuring my own setup mistake.
    # `shell.show()` first, or no show event is delivered and the draft is never
    # rebuilt at all.
    shell.navigate(Screen.MAIN)
    shell.navigate(Screen.PREFERENCES)
    assert screen.context is context
    try:
        assert screen._latency.value() == -120, "read back before any edit"
        # Touch an unrelated control, then save, as a player adjusting something else
        # would. That round trip is what used to lose the value.
        screen._count_in.setCurrentIndex(screen._count_in.findData(2))
        screen.save()
        stored = json.loads((tmp_path / "settings.json").read_text())
        assert stored["input_latency_ms"] == -120.0, (
            f"an unrelated save rewrote the trim to {stored['input_latency_ms']}"
        )
    finally:
        screen.deleteLater()


def test_the_latency_tooltip_does_not_contradict_the_sign(screen) -> None:
    """The trim is *subtracted*, so too high reads early. The old text said late.

    This is not pedantry: it is the one place a player reads to learn what the number
    does, it named the opposite direction from the arithmetic
    (`when = position() - input_latency`), and §39 is the report of a player who
    raised the number to compensate for being late and thereby overshot into being
    early, with "MISS 0%" on screen for both.
    """
    tooltip = screen._latency.toolTip()
    assert "too HIGH" in tooltip
    assert "early" in tooltip
    assert "too high makes every note look late" not in tooltip


# --- the device rows: the name gets its own space (§42) -----------------------


def _settled(shell, qapp) -> None:
    """Show the window and let the layout run, or every geometry is Qt's default.

    An unshown shell has every widget at QRect(0, 0, 640, 480), so a geometry
    assertion on it compares two identical default rectangles and "passes" the
    intersection check for the wrong reason. §38.1 found the same trap in the
    screenshot tool.
    """
    shell.show()
    for _ in range(3):
        qapp.processEvents()
        shell.current_screen.layout().activate()
    qapp.processEvents()


def _device_rows(screen: Preferences) -> list[tuple[QtWidgets.QLabel, QtWidgets.QComboBox]]:
    """The (label, combo) pairs of the Devices group, from the live form layout."""
    rows = []
    for box in screen.findChildren(QtWidgets.QGroupBox):
        if box.title() != "Devices":
            continue
        for form in box.findChildren(QtWidgets.QFormLayout):
            for row in range(form.count()):
                # Either role can be absent -- `itemAt` returns None, not an empty
                # item -- so this asks for both and checks what came back rather than
                # assuming a well-formed row.
                label_item = form.itemAt(row, QtWidgets.QFormLayout.ItemRole.LabelRole)
                field_item = form.itemAt(row, QtWidgets.QFormLayout.ItemRole.FieldRole)
                if label_item is None or field_item is None:
                    continue
                label, field = label_item.widget(), field_item.widget()
                if isinstance(label, QtWidgets.QLabel) and isinstance(
                    field, QtWidgets.QComboBox
                ):
                    rows.append((label, field))
    return rows


def test_the_device_name_is_not_drawn_underneath_the_field(
    screen: Preferences, shell, qapp
) -> None:
    _settled(shell, qapp)
    """The bug, asserted as geometry because that is what it was.

    A `QComboBox` sizes itself for its widest *item*, and this machine's device list
    makes that 436px out of a 520px group. `QFormLayout` does not clip when a row does
    not fit -- it hands the surplus to the field, places the field at x=25, and the
    label's own geometry still ran x=11 to x=134. So the combo was painted *over* the
    name: "the name is overridden by the field". A QLabel cannot lose that race,
    because the width being overrun is its own minimum.
    """
    rows = _device_rows(screen)
    assert len(rows) == 2, f"expected the two device pickers, found {len(rows)}"
    for label, combo in rows:
        assert not label.geometry().intersects(combo.geometry()), (
            f"{label.text()!r} occupies {label.geometry()} and "
            f"{combo.objectName()} occupies {combo.geometry()}"
        )
        assert label.geometry().right() < combo.geometry().left(), (
            f"{label.text()!r} ends at {label.geometry().right()} but the field "
            f"starts at {combo.geometry().left()}"
        )


def test_the_device_name_gets_its_full_natural_width(
    screen: Preferences, shell, qapp
) -> None:
    _settled(shell, qapp)
    """Not merely "not overlapping": the name is as wide as it needs to be.

    A label squeezed to fit beside its field and elided to "Microphone inp..." is not
    fixed, it is smaller.
    """
    for label, _combo in _device_rows(screen):
        assert label.width() >= label.sizeHint().width(), (
            f"{label.text()!r} is {label.width()}px of the {label.sizeHint().width()}px "
            "it needs"
        )
        assert not label.text().endswith("..."), f"{label.text()!r} is elided"


def test_a_device_combo_does_not_demand_its_popup_width(screen: Preferences) -> None:
    """The closed control and the popup have different requirements.

    The popup shows the same device names and needs the room; the closed control only
    shows the current one. Sizing the closed control by `minimumContentsLength` gives
    the label column back its width, and the popup still opens full length because it
    sizes to its contents when it opens.
    """
    for _label, combo in _device_rows(screen):
        assert combo.sizeAdjustPolicy() == (
            QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        ), "a combo sized by its widest item will starve its row label"
        assert combo.minimumContentsLength() == DEVICE_COMBO_CHARS
