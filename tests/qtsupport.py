"""Test support for waiting on signals from worker threads.

The awkward part of testing a ``QThreadPool`` worker is that its signals cross a
thread boundary and are delivered *queued* -- on the GUI thread, via the event
loop. So a test cannot just call ``start()`` and assert: nothing has been
delivered yet, and for a fast scan (an empty or one-file directory) the signal
may already have been emitted and dropped before the test attaches to it.

:class:`SignalSpy` is therefore attached *before* the work starts, and
:meth:`SignalSpy.wait` runs a nested event loop until the expected number of
calls arrive or a timeout expires. A nested ``QEventLoop`` is safe here because
pytest is not itself driving a Qt loop; there is no outer loop to re-enter.

Tests assert on the recorded calls rather than on a return value, and every
wait has a timeout, so a regression shows up as a clear assertion failure
("expected 1 call, got 0") instead of a hung suite.
"""

from __future__ import annotations

from PySide6 import QtCore

DEFAULT_TIMEOUT_MS = 15_000


class SignalSpy:
    """Records a signal's emissions, and can wait for them.

    Args:
        signal: the bound signal to record.
        minimum: how many emissions :meth:`wait` should wait for by default.
    """

    def __init__(self, signal: QtCore.Signal, minimum: int = 1) -> None:
        self.calls: list[tuple] = []
        self._minimum = minimum
        self._loop: QtCore.QEventLoop | None = None
        self._timer: QtCore.QTimer | None = None
        self.timed_out = False
        signal.connect(self._record)

    def _record(self, *args) -> None:
        self.calls.append(args)
        if self._loop is not None and len(self.calls) >= self._minimum:
            self._loop.quit()

    def wait(self, minimum: int | None = None, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bool:
        """Pump the event loop until ``minimum`` calls arrive. Returns success.

        Returns ``True`` if enough arrived, ``False`` on timeout -- and sets
        :attr:`timed_out` so a failure can be reported as a timeout rather than
        as a bare assertion about a call count.
        """
        target = self._minimum if minimum is None else minimum
        if len(self.calls) >= target:
            return True

        self.timed_out = True
        self._loop = QtCore.QEventLoop()
        self._timer = QtCore.QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._loop.quit)
        self._timer.start(timeout_ms)
        self._loop.exec()
        self._timer.stop()
        self._loop = None
        self._timer = None
        return len(self.calls) >= target

    # --- convenience accessors ------------------------------------------------

    @property
    def count(self) -> int:
        return len(self.calls)

    @property
    def first(self) -> tuple:
        return self.calls[0]

    @property
    def last(self) -> tuple:
        return self.calls[-1]

    def describe(self) -> str:
        """A failure message that says what was actually received."""
        state = "timed out" if self.timed_out else "returned early"
        return f"{state}: {self.count} call(s) recorded, wanted {self._minimum}"


def wait_until(predicate, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> bool:
    """Spin the event loop until ``predicate()`` is true. Returns success.

    For waiting on something that is not a signal -- ``loader.is_running``
    flipping false, say, after the terminal signal has been delivered.

    Needs a poller because ``QEventLoop.exec()`` returns only on ``quit()``:
    without a timer re-evaluating the predicate, it would sit there until the
    timeout no matter how quickly the condition became true.
    """
    if predicate():
        return True

    loop = QtCore.QEventLoop()
    timer = QtCore.QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)

    state = {"done": False}

    def check() -> None:
        if predicate():
            state["done"] = True
            loop.quit()

    poller = QtCore.QTimer()
    poller.timeout.connect(check)
    poller.start(10)
    loop.exec()
    poller.stop()
    timer.stop()
    return bool(state["done"]) or predicate()
