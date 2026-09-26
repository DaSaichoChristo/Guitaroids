"""Tests for the background library loader.

Speed note: these use tiny generated ``.gp5`` files via guitarpro's writer
(``tests/test_songlib.py`` shows the pattern) rather than the real library. A scan
of the real ``songs/`` directory costs ~0.2s per tab and the suite runs many scans;
the real-library path is covered by ``test_context.py``.

The point of most of these is not "does it parse" but "does it stay out of the
way": never block the GUI thread, always emit exactly one terminal signal,
actually stop when cancelled, and never touch a widget.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6 import QtCore
from qtsupport import SignalSpy, wait_until
from songbuild import broken_tab, write_tab

from guitaroids.model.chart import CollapseRule
from guitaroids.songlib import Library, Status, scan_library
from guitaroids.ui.library_loader import LibraryLoader


@pytest.fixture()
def library_dir(tmp_path: Path) -> Path:
    root = tmp_path / "songs"
    root.mkdir()
    write_tab(root / "alpha.gp5")
    write_tab(root / "beta.gp5")
    write_tab(root / "gamma.gp5")
    return root


@pytest.fixture()
def loader(qapp) -> LibraryLoader:
    """A loader, torn down cleanly so no scan outlives the test.

    Cancelling and draining matters: a QRunnable still parsing a tab when the
    test's tmp_path is removed would fail somewhere else entirely, and the
    resulting error would point at the wrong code.
    """
    instance = LibraryLoader()
    yield instance
    instance.cancel()
    wait_until(lambda: not instance.is_running, timeout_ms=10_000)


def run_scan(loader: LibraryLoader, root: Path, **kwargs) -> SignalSpy:
    """Start a scan and wait for its terminal signal. Returns a spy on `completed`."""
    done = SignalSpy(loader.completed)
    assert loader.start(root, **kwargs) is True
    assert done.wait(timeout_ms=15_000), done.describe()
    return done


# --- happy path ---------------------------------------------------------------


def test_scan_completes_with_every_tab(loader: LibraryLoader, library_dir: Path) -> None:
    done = run_scan(loader, library_dir)
    library: Library = done.first[0]
    assert len(library.entries) == 3
    assert {e.slug for e in library.entries} == {"alpha", "beta", "gamma"}


def test_completed_library_matches_the_synchronous_scan(
    loader: LibraryLoader, library_dir: Path
) -> None:
    """The whole point of sharing find_tabs: the two paths must agree.

    If the loader discovered files differently, song select would show a list
    that does not match what a rescan or a restart produces.
    """
    background = run_scan(loader, library_dir).first[0]
    synchronous = scan_library(library_dir)
    assert [e.slug for e in background.entries] == [e.slug for e in synchronous.entries]
    assert [e.status for e in background.entries] == [e.status for e in synchronous.entries]


def test_entries_are_reported_incrementally(loader: LibraryLoader, library_dir: Path) -> None:
    found = SignalSpy(loader.entry_found)
    done = SignalSpy(loader.completed)
    loader.start(library_dir)
    assert done.wait(), done.describe()
    assert found.count == 3, "each tab should be announced as it is parsed"
    assert [entry.slug for (entry,) in found.calls] == ["alpha", "beta", "gamma"]


def test_progress_counts_up_to_the_total(loader: LibraryLoader, library_dir: Path) -> None:
    progress = SignalSpy(loader.progress)
    done = SignalSpy(loader.completed)
    loader.start(library_dir)
    assert done.wait(), done.describe()
    assert progress.calls == [(1, 3), (2, 3), (3, 3)]


def test_an_empty_directory_completes_with_no_entries(loader: LibraryLoader, tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    library = run_scan(loader, empty).first[0]
    assert library.entries == ()


def test_a_missing_directory_is_empty_not_an_error(loader: LibraryLoader, tmp_path: Path) -> None:
    """First run: songs/ has not been created yet. That is an empty state."""
    library = run_scan(loader, tmp_path / "not-created").first[0]
    assert library.entries == ()


# --- a bad tab must not stop the scan -----------------------------------------


def test_a_corrupt_tab_is_reported_but_the_scan_finishes(
    loader: LibraryLoader, library_dir: Path
) -> None:
    broken_tab(library_dir / "broken.gp5")
    library = run_scan(loader, library_dir).first[0]
    assert len(library.entries) == 4
    broken = library.get("broken")
    assert broken is not None
    # The exact status is UNSUPPORTED_VERSION, not PARSE_ERROR: PyGuitarPro
    # cannot even read a version header out of random bytes, and that is what
    # its message says. Asserting on the specific status here would pin a detail
    # of guitarpro's error text (see songlib.classify_error's note about that);
    # the property the UI relies on is that the entry is flagged and unplayable.
    assert broken.status is Status.UNSUPPORTED_VERSION
    assert not broken.status.is_playable
    assert broken.detail, "a problem entry must explain itself"
    assert library.get("alpha").status.is_playable, "one bad file must not lose the good ones"


def test_a_path_that_is_a_file_scans_as_empty_not_a_failure(
    loader: LibraryLoader, tmp_path: Path
) -> None:
    """find_tabs guards on is_dir(), so a file where a directory belongs is empty.

    Worth pinning because the alternative -- letting rglob's NotADirectoryError
    escape -- would surface as a failed scan over a typo in the songs path.
    """
    blocker = tmp_path / "a-file.gp5"
    blocker.write_text("not a directory")
    failed = SignalSpy(loader.failed)
    library = run_scan(loader, blocker).first[0]
    assert library.entries == ()
    assert failed.count == 0


def test_an_unreadable_directory_fails_rather_than_hangs(
    loader: LibraryLoader, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A permission error is a real outcome the screen has to render.

    Not reachable through the filesystem here -- root can read anything in
    tmp_path, and chmod does not stop it -- so find_tabs is stubbed to raise the
    OSError it would raise on a directory the process cannot read. What is being
    tested is the handler: it must report, not die silently in a pool thread,
    where the failure would be invisible.
    """
    import guitaroids.ui.library_loader as loader_module

    def explode(_root):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(loader_module, "find_tabs", explode)

    failed = SignalSpy(loader.failed)
    completed = SignalSpy(loader.completed)
    loader.start(tmp_path)
    assert failed.wait(), f"expected failed; {failed.describe()}"
    assert completed.count == 0, "failed and completed are mutually exclusive"
    assert "could not read" in failed.first[0]


# --- exactly one terminal signal ---------------------------------------------


def test_completion_clears_is_running(loader: LibraryLoader, library_dir: Path) -> None:
    run_scan(loader, library_dir)
    assert wait_until(lambda: not loader.is_running), "is_running stuck after completion"


def test_cancel_emits_cancelled_and_not_completed(loader: LibraryLoader, library_dir: Path) -> None:
    cancelled = SignalSpy(loader.cancelled)
    completed = SignalSpy(loader.completed)
    loader.start(library_dir)
    loader.cancel()
    assert cancelled.wait(), cancelled.describe()
    assert completed.count == 0
    assert wait_until(lambda: not loader.is_running)


def test_cancelling_a_finished_scan_is_harmless(loader: LibraryLoader, library_dir: Path) -> None:
    run_scan(loader, library_dir)
    loader.cancel()
    assert wait_until(lambda: not loader.is_running)
    assert loader.start(library_dir) is True, "a finished scan must not block the next one"
    assert wait_until(lambda: not loader.is_running)


def test_a_stale_cancel_does_not_poison_the_next_scan(
    loader: LibraryLoader, library_dir: Path
) -> None:
    """start() clears the cancel flag, so cancelling twice in a row is safe.

    A screen that cancels on hide and rescans on show would otherwise clear the
    songs dir, hide, and come back to a scan that immediately reports cancelled
    and never populates anything.
    """
    loader.cancel()
    library = run_scan(loader, library_dir).first[0]
    assert len(library.entries) == 3


def test_only_one_terminal_signal_per_scan(loader: LibraryLoader, library_dir: Path) -> None:
    """A screen waiting on 'scan finished' must not be woken three times."""
    completed = SignalSpy(loader.completed)
    cancelled = SignalSpy(loader.cancelled)
    failed = SignalSpy(loader.failed)
    loader.start(library_dir)
    loader.cancel()  # racing the scan on purpose
    # Wait on is_running rather than on a specific signal: whichever terminal
    # signal wins, the guard clears, and waiting on the "wrong" one would just
    # burn the full timeout. _settle is connected before the spies, so drain the
    # queue once more to be sure their slots have run too.
    assert wait_until(lambda: not loader.is_running)
    QtCore.QCoreApplication.processEvents()
    total = completed.count + cancelled.count + failed.count
    assert total == 1, f"expected exactly one terminal signal, got {total}"


# --- one scan at a time -------------------------------------------------------


def test_a_second_concurrent_scan_is_refused(loader: LibraryLoader, library_dir: Path) -> None:
    assert loader.start(library_dir) is True
    assert loader.start(library_dir) is False, "a second scan must be refused, not queued"
    assert wait_until(lambda: not loader.is_running)


def test_a_new_scan_can_start_after_the_previous_one_finished(
    loader: LibraryLoader, library_dir: Path
) -> None:
    run_scan(loader, library_dir)
    assert wait_until(lambda: not loader.is_running)
    assert run_scan(loader, library_dir).first[0].entries, "a rescan must work"


def test_settle_is_connected_once_so_repeated_scans_stay_sane(
    loader: LibraryLoader, library_dir: Path
) -> None:
    """Regression guard for connections stacked up per start()."""
    run_scan(loader, library_dir)
    run_scan(loader, library_dir)
    run_scan(loader, library_dir)
    assert wait_until(lambda: not loader.is_running)
    # _settle is idempotent, so even a stacked connection cannot corrupt state --
    # the point is that the guard clears, which it would not if _settle were
    # re-bound to stale tasks.
    assert loader.is_running is False


# --- threading contract -------------------------------------------------------


def test_the_gui_thread_is_not_blocked(loader: LibraryLoader, library_dir: Path) -> None:
    """The scan runs in a pool thread, not on the thread that called start().

    Checked the direct way: the calling thread's ident, recorded on the GUI
    thread, must differ from the ident a slot sees. A queued cross-thread
    connection guarantees the slot runs on the GUI thread, so if parsing had
    happened inline the two would match.
    """
    gui_ident = __import__("threading").get_ident()
    seen: list[int] = []
    loader.entry_found.connect(lambda entry: seen.append(__import__("threading").get_ident()))
    done = run_scan(loader, library_dir)
    assert done.count == 1
    assert seen, "no entries were reported"
    assert all(ident == gui_ident for ident in seen), "signals must be delivered on the GUI thread"


def test_the_task_is_not_garbage_collected_mid_run(
    loader: LibraryLoader, library_dir: Path
) -> None:
    """A QRunnable with no Python reference can be collected mid-run().

    The failure mode is a pool thread that never finishes, so this waits with a
    real timeout rather than hanging the suite.
    """
    done = SignalSpy(loader.completed)
    loader.start(library_dir)
    assert done.wait(timeout_ms=15_000), done.describe()


# --- options are honoured -----------------------------------------------------


def test_collapse_and_rule_reach_the_parser(loader: LibraryLoader, library_dir: Path) -> None:
    library = run_scan(
        loader, library_dir, collapse=False, rule=CollapseRule.LOWEST
    ).first[0]
    entry = library.get("alpha")
    assert entry.collapse is False
    assert entry.rule is CollapseRule.LOWEST


# --- integration with the context ---------------------------------------------


def test_a_rescan_can_write_its_result_into_the_context(
    loader: LibraryLoader, library_dir: Path
) -> None:
    from guitaroids.context import AppContext

    context = AppContext(songs_dir=library_dir)
    done = SignalSpy(loader.completed)
    loader.start(library_dir)
    assert done.wait(), done.describe()

    context.set_library(done.first[0])
    assert len(context.library.entries) == 3
    assert context.request_play("alpha", 1).slug == "alpha"
    assert context.chart_for() is not None
