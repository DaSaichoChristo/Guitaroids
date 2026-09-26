"""Scanning the song library off the GUI thread.

Why this is a separate object rather than a method on the context: the scan
parses every tab in the directory, at the measured ~0.2s each, so 50 songs is
about ten seconds of frozen UI. ``AppContext.create`` scans synchronously and
that is fine at startup -- it means the app always has something to show -- but
a *rescan* triggered by importing a tab must not do that on the GUI thread.

Three rules shape the design:

- **The worker never touches a widget.** It parses and emits; the screen decides
  what to put on screen. A QRunnable has no business knowing about a QListWidget.
- **Cancellation is checked between files, not abandoned at the end.** Throwing
  the result away would still burn ten seconds of CPU after the user navigated
  away, on a laptop that is also trying to run the webcam preview.
- **A reference to the task is kept.** A ``QRunnable`` with no Python reference
  can be garbage collected mid-``run``, and the failure is a silent hang in a
  thread pool rather than an exception.

Signals are delivered on the GUI thread by Qt's automatic queued connections, so
slots may touch widgets.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6 import QtCore

from ..model.chart import CollapseRule
from ..songlib import Library, SongEntry, find_tabs, load_tab


class _ScanTask(QtCore.QRunnable):
    """Parses tabs one at a time, reporting as it goes. Runs off the GUI thread."""

    def __init__(
        self,
        root: Path,
        *,
        collapse: bool,
        rule: CollapseRule,
        cancel: threading.Event,
        entry_found: QtCore.Signal,
        progress: QtCore.Signal,
        completed: QtCore.Signal,
        cancelled: QtCore.Signal,
        failed: QtCore.Signal,
    ) -> None:
        super().__init__()
        self._root = root
        self._collapse = collapse
        self._rule = rule
        self._cancel = cancel
        self._entry_found = entry_found
        self._progress = progress
        self._completed = completed
        self._cancelled = cancelled
        self._failed = failed

    def run(self) -> None:  # noqa: D102 - QRunnable interface
        try:
            tabs = find_tabs(self._root)
        except OSError as exc:
            # An unreadable directory is a real outcome, not a crash: the screen
            # shows the problem and the user can pick another songs folder.
            self._failed.emit(f"could not read {self._root}: {exc}")
            return

        total = len(tabs)
        entries: list[SongEntry] = []
        for done, path in enumerate(tabs, start=1):
            if self._cancel.is_set():
                self._cancelled.emit()
                return
            # load_tab never raises for a bad file, so one broken tab cannot stop
            # the scan -- the same guarantee the synchronous path makes.
            entries.append(load_tab(path, collapse=self._collapse, rule=self._rule))
            self._entry_found.emit(entries[-1])
            self._progress.emit(done, total)

        self._completed.emit(Library(root=self._root, entries=tuple(entries)))


class LibraryLoader(QtCore.QObject):
    """Rescans the library in the background, reporting progress as it goes.

    Emits:

    - :attr:`entry_found` once per tab, in scan order, as each is parsed
    - :attr:`progress` as ``(done, total)`` after each tab
    - exactly one of :attr:`completed` (a ``Library``), :attr:`cancelled`, or
      :attr:`failed` (a message)

    One of the three terminal signals always arrives, including on cancellation,
    so a screen waiting on a "scan finished" state is never left waiting forever.
    """

    entry_found = QtCore.Signal(object)
    progress = QtCore.Signal(int, int)
    completed = QtCore.Signal(object)
    cancelled = QtCore.Signal()
    failed = QtCore.Signal(str)

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        self._task: _ScanTask | None = None
        self._running = False
        # Connected once, here, rather than per start(): connecting inside start()
        # would stack a duplicate connection on every rescan and call _settle N
        # times for one scan.
        self.completed.connect(self._settle)
        self.cancelled.connect(self._settle)
        self.failed.connect(self._settle)

    def start(
        self,
        root: str | Path,
        *,
        collapse: bool,
        rule: CollapseRule = CollapseRule.HIGHEST,
    ) -> bool:
        """Begin a scan. Returns ``False`` if one is already in flight.

        A second concurrent scan is refused rather than queued: two scans of the
        same directory would race to write the library, and the loser's result
        would be the one shown. Call :meth:`cancel` and wait for
        :attr:`cancelled` if a rescan is genuinely wanted.

        ``collapse`` is **required, and deliberately has no default** (§21). It
        used to default to True, and both call sites omitted it -- so the
        "Collapse chords" preference in Preferences was saved, persisted and then
        ignored, and every library came back collapsed whatever the player chose.
        A required argument is the fix that cannot recur: a third call site now
        has to say which it wants.
        """
        if self._running:
            return False

        self._cancel.clear()
        self._task = _ScanTask(
            Path(root),
            collapse=collapse,
            rule=rule,
            cancel=self._cancel,
            # The task emits this object's own signals, so a screen connects to
            # the loader and never to a task it holds no reference to.
            entry_found=self.entry_found,
            progress=self.progress,
            completed=self.completed,
            cancelled=self.cancelled,
            failed=self.failed,
        )
        self._running = True
        QtCore.QThreadPool.globalInstance().start(self._task)
        return True

    def _settle(self, *_args) -> None:
        """A terminal signal arrived: clear the guard and drop the task reference.

        Dropping the reference matters. ``autoDelete`` destroys the underlying
        QRunnable once ``run`` returns, so holding a Python reference to it after
        that point is a dangling wrapper.
        """
        self._running = False
        self._task = None

    def cancel(self) -> None:
        """Ask the scan to stop at the next file boundary.

        Does not block and does not abandon the work in flight: the file being
        parsed finishes, then the scan stops and emits :attr:`cancelled`. A
        single tab is ~0.2s, so this is prompt enough to feel immediate.
        """
        self._cancel.set()

    @property
    def is_running(self) -> bool:
        return self._running
