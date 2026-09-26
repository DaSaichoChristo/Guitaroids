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
    use_file(screen, incoming)
    screen._choose_and_import()
    assert wait_until(lambda: not screen._loader.is_running)
    assert (context.songs_dir / "song.gp5").is_file()


def test_the_song_shows_up_in_the_library(screen: ImportGp, context, incoming) -> None:
    """The whole point of the screen: after importing, the song is playable."""
    use_file(screen, incoming)
    screen._choose_and_import()
    assert wait_until(lambda: not screen._loader.is_running)
    assert [e.slug for e in context.library.playable] == ["song"]


def test_the_source_file_is_untouched(screen: ImportGp, context, incoming) -> None:
    before = incoming.read_bytes()
    use_file(screen, incoming)
    screen._choose_and_import()
    assert wait_until(lambda: not screen._loader.is_running)
    assert incoming.read_bytes() == before, "import is a copy, never a move"


def test_the_screen_reports_what_happened(screen: ImportGp, context, incoming) -> None:
    use_file(screen, incoming)
    screen._choose_and_import()
    assert wait_until(lambda: not screen._loader.is_running)
    assert "1 playable" in screen._report_label.text()


def test_the_destination_is_shown_up_front(screen: ImportGp, context) -> None:
    """The user can see where it will go before choosing anything."""
    labels = [label.text() for label in screen.findChildren(type(screen._report_label))]
    assert any(str(context.songs_dir) in text for text in labels)


def test_the_supported_formats_are_listed(screen: ImportGp) -> None:
    labels = [label.text() for label in screen.findChildren(type(screen._report_label))]
    assert any("gp3" in text and "gp4" in text and "gp5" in text for text in labels)


# --- cancelling --------------------------------------------------------------


def test_cancelling_the_dialog_does_nothing(screen: ImportGp, context, incoming) -> None:
    use_cancel(screen)
    screen._choose_and_import()
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
    use_file(screen, gpx)
    screen._choose_and_import()
    assert "not a Guitar Pro tab" in screen._report_label.text()
    assert asked == [], "a refusal must not also ask about replacing"
    assert not (context.songs_dir / "modern.gpx").exists()


def test_a_missing_file_is_refused(screen: ImportGp, context, tmp_path) -> None:
    use_file(screen, tmp_path / "gone.gp5")
    screen._choose_and_import()
    assert "no longer exists" in screen._report_label.text()


def test_a_failed_copy_is_reported_not_swallowed(
    screen: ImportGp, context, incoming, monkeypatch
) -> None:
    """A full disk must say so rather than appearing to work."""
    import guitaroids.ui.import_gp as module

    def explode(*_args, **_kwargs):
        raise OSError("No space left on device")

    monkeypatch.setattr(module, "import_tab", explode)
    use_file(screen, incoming)
    screen._choose_and_import()
    assert "Could not copy" in screen._report_label.text()
    assert screen._loader.is_running is False, "a failed copy must not scan"


# --- overwriting -------------------------------------------------------------


def test_an_existing_tab_is_not_replaced_without_asking(
    screen: ImportGp, context, incoming
) -> None:
    use_file(screen, incoming)
    screen._choose_and_import()
    assert wait_until(lambda: not screen._loader.is_running)

    destination = context.songs_dir / "song.gp5"
    destination.write_bytes(b"an older version")

    asked = answer(screen, yes=False)
    use_file(screen, incoming)
    screen._choose_and_import()
    assert asked, "replacing a tab must ask"
    assert destination.read_bytes() == b"an older version", "declining must not write"


def test_declining_leaves_the_existing_tab_and_says_so(
    screen: ImportGp, context, incoming
) -> None:
    answer(screen, yes=False)
    use_file(screen, incoming)
    screen._choose_and_import()
    assert wait_until(lambda: not screen._loader.is_running)

    destination = context.songs_dir / "song.gp5"
    destination.write_bytes(b"an older version")
    use_file(screen, incoming)
    screen._choose_and_import()
    assert "left alone" in screen._report_label.text()
    assert destination.read_bytes() == b"an older version"


def test_confirming_replaces_the_tab(screen: ImportGp, context, incoming) -> None:
    answer(screen, yes=True)
    use_file(screen, incoming)
    screen._choose_and_import()
    assert wait_until(lambda: not screen._loader.is_running)

    destination = context.songs_dir / "song.gp5"
    destination.write_bytes(b"an older version")
    screen.import_file(incoming)
    assert wait_until(lambda: not screen._loader.is_running)
    assert destination.read_bytes() == incoming.read_bytes()


def test_the_confirmation_names_the_file(screen: ImportGp, context, incoming) -> None:
    """The user must be able to tell what they are about to overwrite."""
    answer(screen, yes=True)
    use_file(screen, incoming)
    screen._choose_and_import()
    assert wait_until(lambda: not screen._loader.is_running)
    asked = answer(screen, yes=False)
    use_file(screen, incoming)
    screen._choose_and_import()
    assert asked and "song.gp5" in asked[0]
    assert "Replace it?" in asked[0]


def test_importing_a_file_already_in_the_library_is_refused(
    screen: ImportGp, context, incoming
) -> None:
    """Asking to replace a file with itself is a confusing dead end."""
    answer(screen, yes=True)
    use_file(screen, incoming)
    screen._choose_and_import()
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
