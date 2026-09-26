"""Tests for the Import GP screen.

The decisions are in ``guitaroids.importer`` and tested there without Qt. What is
left to test here is the part only the screen can get wrong: it must ask before
replacing anything, it must not hang on a modal dialog, and it must leave the
library and the user informed either way.

The dialogs are injected (``screen.choose_file`` / ``screen.confirm``) because
``QFileDialog.getOpenFileName`` is a blocking static call that would hang a test
outright -- there is no honest way to mock it.
"""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore

import pytest
from qtsupport import SignalSpy, wait_until
from songbuild import write_tab

from guitaroids.context import AppContext
from guitaroids.importer import Outcome
from guitaroids.ui.import_gp import ImportGp
from guitaroids.ui.screens import Screen


@pytest.fixture()
def context(tmp_path: Path) -> AppContext:
    return AppContext(songs_dir=tmp_path / "songs", settings_path=tmp_path / "settings.json")


@pytest.fixture()
def screen(shell, context) -> ImportGp:
    shell.navigate(Screen.IMPORT_GP)
    widget = shell.current_screen
    assert isinstance(widget, ImportGp)
    return widget


@pytest.fixture()
def incoming(tmp_path: Path) -> Path:
    return write_tab(tmp_path / "elsewhere" / "song.gp5")


def use_file(screen: ImportGp, path: Path) -> None:
    screen.choose_file = lambda: str(path)


def use_cancel(screen: ImportGp) -> None:
    screen.choose_file = lambda: ""


def choose(screen: ImportGp, path: Path) -> None:
    """Step one: go through the dialog and arm the screen. Copies nothing."""
    use_file(screen, path)
    screen._choose_file()


def add(screen: ImportGp, path: Path) -> None:
    """Both steps, the way a player does it: pick a file, then add it."""
    choose(screen, path)
    screen._import_selected()


def answer(screen: ImportGp, *, yes: bool) -> list[str]:
    """Install a confirmation that records what it was asked."""
    asked: list[str] = []

    def confirm(message: str) -> bool:
        asked.append(message)
        return yes

    screen.confirm = confirm
    return asked


# --- the happy path ----------------------------------------------------------


def test_choosing_a_tab_copies_it_in(screen: ImportGp, context, incoming) -> None:
    add(screen, incoming)
    assert wait_until(lambda: not screen._loader.is_running)
    assert (context.songs_dir / "song.gp5").is_file()


def test_the_song_shows_up_in_the_library(screen: ImportGp, context, incoming) -> None:
    """The whole point of the screen: after importing, the song is playable."""
    add(screen, incoming)
    assert wait_until(lambda: not screen._loader.is_running)
    assert [e.slug for e in context.library.playable] == ["song"]


def test_the_source_file_is_untouched(screen: ImportGp, context, incoming) -> None:
    before = incoming.read_bytes()
    add(screen, incoming)
    assert wait_until(lambda: not screen._loader.is_running)
    assert incoming.read_bytes() == before, "import is a copy, never a move"


def test_the_screen_reports_what_happened(screen: ImportGp, context, incoming) -> None:
    add(screen, incoming)
    assert wait_until(lambda: not screen._loader.is_running)
    assert "1 playable" in screen._report_label.text()


def test_the_destination_is_shown_up_front(screen: ImportGp, context) -> None:
    """The user can see where it will go before choosing anything."""
    labels = [label.text() for label in screen.findChildren(type(screen._report_label))]
    assert any(str(context.songs_dir) in text for text in labels)


def test_the_supported_formats_are_listed(screen: ImportGp) -> None:
    labels = [label.text() for label in screen.findChildren(type(screen._report_label))]
    assert any("gp3" in text and "gp4" in text and "gp5" in text for text in labels)


# --- the two steps -----------------------------------------------------------


def test_add_is_dead_until_a_tab_is_chosen(screen: ImportGp) -> None:
    """The button the player is looking for is there, and does not lie.

    A live-looking Add that silently ignores a click is worse than a dead one: it
    looks like the screen is broken rather than like there is nothing to add yet.
    """
    assert not screen._import_button.isEnabled()
    assert screen._import_button.text() == "Add to library"
    screen._import_selected()  # nothing armed: must not raise
    assert screen._report_label.text() == "", "and must not pretend to have done anything"


def test_both_buttons_are_on_screen(screen: ImportGp) -> None:
    """The whole complaint was a missing button, so assert both are *visible*.

    Parented is not enough -- §18.5's control was parented and still rendered
    nowhere. This screen's buttons are built through ``constrained_button`` with no
    parent and parented by the layout, which is the arrangement that has bitten
    before, so the assertion is about visibility rather than about the tree.
    """
    assert screen._import_button.isVisibleTo(screen)
    assert screen._choose_button.isVisibleTo(screen)


def test_choosing_a_tab_does_not_copy_it(screen: ImportGp, context, incoming) -> None:
    """The bug this screen shipped: a file dialog that went straight to copying.

    There is now a moment between picking a file and adding it, and nothing is
    written in that moment.
    """
    choose(screen, incoming)
    assert not (context.songs_dir / "song.gp5").exists()
    assert screen._loader.is_running is False, "choosing must not start a scan"


def test_choosing_names_the_file_and_arms_add(screen: ImportGp, incoming) -> None:
    choose(screen, incoming)
    assert screen._chosen_label.isVisibleTo(screen)
    assert "song.gp5" in screen._chosen_label.text()
    assert screen._import_button.isEnabled()


def test_choosing_says_where_the_tab_will_land(screen: ImportGp, incoming) -> None:
    """The destination name is knowable before committing, so it is shown."""
    choose(screen, incoming)
    assert "song.gp5" in screen._report_label.text()
    assert "Will be added" in screen._report_label.text()


def test_choosing_something_already_there_says_it_will_ask(screen: ImportGp, context, incoming) -> None:
    """But it does not ask yet.

    Asking on selection would put a modal in front of a player who has not yet
    committed to adding anything -- and on a native dialog there is no way to
    un-ask it.
    """
    context.songs_dir.mkdir(parents=True, exist_ok=True)
    (context.songs_dir / "song.gp5").write_bytes(b"an older version")
    asked = answer(screen, yes=True)

    choose(screen, incoming)

    assert asked == [], "choosing must not open the replace dialog"
    assert "ask before replacing" in screen._report_label.text()
    assert screen._import_button.isEnabled(), "still armed: the player may well want it"


def test_adding_copies_the_chosen_tab(screen: ImportGp, context, incoming) -> None:
    choose(screen, incoming)
    screen._import_selected()
    assert wait_until(lambda: not screen._loader.is_running)
    assert (context.songs_dir / "song.gp5").is_file()


def test_adding_clears_the_selection(screen: ImportGp, context, incoming) -> None:
    """One press, one import.

    Left armed, a second press would copy the same file again and report a second
    "Copied ... into the library" for a tab that was already there.
    """
    choose(screen, incoming)
    screen._import_selected()
    assert wait_until(lambda: not screen._loader.is_running)

    assert screen._import_button.isEnabled() is False
    assert not screen._chosen_label.isVisibleTo(screen)


def test_a_refused_file_leaves_add_dead(screen: ImportGp, tmp_path) -> None:
    """Nothing to add means nothing to press."""
    gpx = tmp_path / "modern.gpx"
    gpx.write_bytes(b"GPX")

    choose(screen, gpx)

    assert not screen._import_button.isEnabled()
    assert not screen._chosen_label.isVisibleTo(screen)


def test_declining_the_replace_disarms_add(screen: ImportGp, context, incoming) -> None:
    """The file stays armed only if something is going to happen with it."""
    context.songs_dir.mkdir(parents=True, exist_ok=True)
    (context.songs_dir / "song.gp5").write_bytes(b"an older version")
    answer(screen, yes=False)

    choose(screen, incoming)
    screen._import_selected()

    assert not screen._import_button.isEnabled()
    assert (context.songs_dir / "song.gp5").read_bytes() == b"an older version"


def test_choosing_again_replaces_the_armed_file(screen: ImportGp, tmp_path) -> None:
    """Picking a second file must not leave the first one armed."""
    first = write_tab(tmp_path / "one.gp5")
    second = write_tab(tmp_path / "two.gp5")

    choose(screen, first)
    choose(screen, second)
    screen._import_selected()

    assert wait_until(lambda: not screen._loader.is_running)
    assert (screen.context.songs_dir / "two.gp5").is_file()
    assert not (screen.context.songs_dir / "one.gp5").exists()


def test_the_row_agrees_with_the_focus_step(screen: ImportGp, incoming, monkeypatch) -> None:
    """Focus moves to Add once a file is armed.

    Asserted through the *call* rather than through ``hasFocus``, because the
    offscreen platform has no focused widget at all (the same trap that made the
    game screen's key filter necessary) -- so ``hasFocus()`` is False whatever we
    do, and a test asserting it would pass for the wrong reason.
    """
    asked: list[object] = []
    real = type(screen._choose_button).setFocus

    def record(self, reason=QtCore.Qt.FocusReason.OtherFocusReason):
        asked.append(self)
        return real(self, reason)

    monkeypatch.setattr(type(screen._choose_button), "setFocus", record)
    choose(screen, incoming)

    assert screen._import_button in asked, "focus must move to the armed step"
    assert screen._choose_button not in asked


def test_the_buttons_sit_in_one_row(screen: ImportGp, shell, qapp) -> None:
    """They are two steps of one action, and a row says that better than a column.

    Asserted through geometry rather than through the layout object, so it is the
    rendered arrangement that is checked: the same y, different x.
    """
    # The screen lives in the shell's stack, so showing the screen itself does not
    # lay it out; the shell has to be up. Every geometry assertion in this suite
    # needs this and it is easy to forget.
    shell.resize(960, 640)
    shell.show()
    qapp.processEvents()

    one = screen._import_button.geometry()
    other = screen._choose_button.geometry()
    assert abs(one.y() - other.y()) <= 2, "the two steps must be side by side"
    assert one.x() != other.x()


# --- cancelling --------------------------------------------------------------


def test_cancelling_the_dialog_does_nothing(screen: ImportGp, context, incoming) -> None:
    use_cancel(screen)
    screen._choose_file()
    assert not (context.songs_dir / "song.gp5").exists()
    assert screen._loader.is_running is False, "cancelling must not start a scan"
    assert screen._report_label.text() == ""


# --- refusals ----------------------------------------------------------------


def test_an_unsupported_file_is_refused_with_a_reason(
    screen: ImportGp, context, tmp_path
) -> None:
    gpx = tmp_path / "modern.gpx"
    gpx.write_bytes(b"GPX")
    asked = answer(screen, yes=True)
    choose(screen, gpx)
    assert "not a Guitar Pro tab" in screen._report_label.text()
    assert asked == [], "a refusal must not also ask about replacing"
    assert not (context.songs_dir / "modern.gpx").exists()


def test_a_missing_file_is_refused(screen: ImportGp, context, tmp_path) -> None:
    choose(screen, tmp_path / "gone.gp5")
    assert "no longer exists" in screen._report_label.text()


def test_a_failed_copy_is_reported_not_swallowed(
    screen: ImportGp, context, incoming, monkeypatch
) -> None:
    """A full disk must say so rather than appearing to work."""
    import guitaroids.ui.import_gp as module

    def explode(*_args, **_kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(module, "import_tab", explode)
    add(screen, incoming)
    assert "Could not copy" in screen._report_label.text()
    assert screen._loader.is_running is False, "a failed copy must not scan"


# --- overwriting -------------------------------------------------------------


def test_an_existing_tab_is_not_replaced_without_asking(
    screen: ImportGp, context, incoming
) -> None:
    add(screen, incoming)
    assert wait_until(lambda: not screen._loader.is_running)

    destination = context.songs_dir / "song.gp5"
    destination.write_bytes(b"an older version")

    asked = answer(screen, yes=False)
    add(screen, incoming)
    assert asked, "replacing a tab must ask"
    assert destination.read_bytes() == b"an older version", "declining must not write"


def test_declining_leaves_the_existing_tab_and_says_so(
    screen: ImportGp, context, incoming
) -> None:
    answer(screen, yes=False)
    add(screen, incoming)
    assert wait_until(lambda: not screen._loader.is_running)

    destination = context.songs_dir / "song.gp5"
    destination.write_bytes(b"an older version")
    add(screen, incoming)
    assert "left alone" in screen._report_label.text()
    assert destination.read_bytes() == b"an older version"


def test_confirming_replaces_the_tab(screen: ImportGp, context, incoming) -> None:
    answer(screen, yes=True)
    add(screen, incoming)
    assert wait_until(lambda: not screen._loader.is_running)

    destination = context.songs_dir / "song.gp5"
    destination.write_bytes(b"an older version")
    screen.import_file(incoming)
    assert wait_until(lambda: not screen._loader.is_running)
    assert destination.read_bytes() == incoming.read_bytes()


def test_the_confirmation_names_the_file(screen: ImportGp, context, incoming) -> None:
    """The user must be able to tell what they are about to overwrite."""
    answer(screen, yes=True)
    add(screen, incoming)
    assert wait_until(lambda: not screen._loader.is_running)
    asked = answer(screen, yes=False)
    add(screen, incoming)
    assert asked and "song.gp5" in asked[0]
    assert "Replace it?" in asked[0]


def test_importing_a_file_already_in_the_library_is_refused(
    screen: ImportGp, context, incoming
) -> None:
    """Asking to replace a file with itself is a confusing dead end."""
    answer(screen, yes=True)
    add(screen, incoming)
    assert wait_until(lambda: not screen._loader.is_running)

    asked = answer(screen, yes=True)
    plan = screen.import_file(context.songs_dir / "song.gp5")
    assert plan.outcome is Outcome.REJECT
    assert asked == []
    assert "already in the library" in screen._report_label.text()


# --- lifecycle ---------------------------------------------------------------


def test_the_screen_owns_its_loader(screen: ImportGp) -> None:
    assert screen._loader.parent() is screen


def test_navigating_away_cancels_a_scan(screen: ImportGp, shell, qapp, monkeypatch) -> None:
    import time

    import guitaroids.ui.library_loader as loader_module

    real = loader_module.load_tab

    def slow(path, **kwargs):
        time.sleep(0.05)
        return real(path, **kwargs)

    monkeypatch.setattr(loader_module, "load_tab", slow)
    (screen.context.songs_dir).mkdir(parents=True, exist_ok=True)
    write_tab(screen.context.songs_dir / "song.gp5")

    cancelled = SignalSpy(screen._loader.cancelled)
    screen._rescan()
    assert screen._loader.is_running is True
    shell.navigate(Screen.MAIN)
    assert wait_until(lambda: not screen._loader.is_running)
    assert cancelled.count == 1


def test_a_scan_that_is_already_running_is_reported(
    screen: ImportGp, shell, qapp, monkeypatch
) -> None:
    import time

    import guitaroids.ui.library_loader as loader_module

    real = loader_module.load_tab

    def slow(path, **kwargs):
        time.sleep(0.05)
        return real(path, **kwargs)

    monkeypatch.setattr(loader_module, "load_tab", slow)
    screen.context.songs_dir.mkdir(parents=True, exist_ok=True)
    write_tab(screen.context.songs_dir / "a.gp5")
    write_tab(screen.context.songs_dir / "b.gp5")

    screen._rescan()
    screen._rescan()  # a second request while the first is in flight
    assert "Already scanning" in screen._report_label.text()
    assert wait_until(lambda: not screen._loader.is_running)
