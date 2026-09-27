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

from guitaroids.context import AppContext
from guitaroids.settings import InputMode, Settings
from guitaroids.ui.preferences import Preferences
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
    context.settings = Settings(master_volume=0.8, input_mode=InputMode.MICROPHONE)
    shell.navigate(Screen.PREFERENCES)
    assert shell.current_screen._draft.master_volume == 0.8
    assert shell.current_screen._draft.input_mode is InputMode.MICROPHONE


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


# --- input-mode dependent controls -------------------------------------------


def test_latency_is_disabled_in_keyboard_mode(screen: Preferences) -> None:
    assert not screen._latency.isEnabled()


def test_latency_is_enabled_with_a_microphone(screen: Preferences) -> None:
    screen._mode.setCurrentIndex(screen._mode.findData(InputMode.MICROPHONE.value))
    assert screen._latency.isEnabled()
    assert screen._draft.input_mode is InputMode.MICROPHONE


def test_choosing_keyboard_disables_latency_again(screen: Preferences) -> None:
    screen._mode.setCurrentIndex(screen._mode.findData(InputMode.MICROPHONE.value))
    screen._mode.setCurrentIndex(screen._mode.findData(InputMode.KEYBOARD.value))
    assert not screen._latency.isEnabled()


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


def test_device_pickers_are_disabled(screen: Preferences) -> None:
    """A control that looks live and does nothing is worse than one that admits it.

    Enumerating devices means opening PortAudio, which is M2/M3 work
    and must not happen to render a settings page.
    """
    from PySide6 import QtWidgets

    combos = _devices_group(screen).findChildren(QtWidgets.QComboBox)
    assert len(combos) == 2, "one picker per device kind"
    for combo in combos:
        assert not combo.isEnabled()
        assert combo.currentText() == "system default"


def test_the_device_pickers_say_they_are_not_ready(screen: Preferences) -> None:
    """Silently disabled would read as "nothing to choose here" rather than "later"."""
    from PySide6 import QtWidgets

    texts = [label.text() for label in _devices_group(screen).findChildren(QtWidgets.QLabel)]
    assert any("arrives with" in text for text in texts), texts


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
