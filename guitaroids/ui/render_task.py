"""Turning a chart into audio, off the GUI thread.

The same problem as :mod:`guitaroids.ui.library_loader`, in the same shape, because
it has the same three constraints:

- **Rendering a tab takes seconds.** Measured: Hotel California is 6:32 of audio in
  7.1s -- fast enough to be worth doing and far too slow to do on the thread that
  is drawing the "Get ready" banner. §1.4 forbids blocking the GUI thread.
- **The worker never touches a widget.** It renders and emits; the screen decides
  what the player sees.
- **A reference to the task is kept.** A ``QRunnable`` with no Python reference can
  be collected mid-``run``, and the symptom is a silent hang in a thread pool
  rather than an exception.

**Cancellation is coarser than a scan's, and honestly so.** A scan checks between
files, so it can stop promptly. Rendering is one long call, and a half-rendered
buffer is worth nothing, so :meth:`cancel` sets a flag that is checked *between
onsets* -- the renderer checks it at every chord boundary and stops with a
:class:`~guitaroids.audio.render.RenderError` rather than returning a truncated
song. That is a real improvement on "abandon the work at the end": seven seconds of
CPU is spent, but none of it is delivered, and the next song starts immediately.

The renderer itself is pure numpy, so it stays in :mod:`guitaroids.audio` and this
module is the Qt wrapper around it -- the same split as songlib and library_loader.
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6 import QtCore

from ..audio.render import RenderCancelled, RenderError, RenderResult, render_chart
from ..model.chart import Chart


class _RenderTask(QtCore.QRunnable):
    """Renders one chart. Runs off the GUI thread; emits exactly one outcome."""

    def __init__(
        self,
        chart: Chart,
        *,
        sample_rate: int,
        backend: str,
        soundfont: Path | None,
        cancel: threading.Event,
        ready: QtCore.Signal,
        failed: QtCore.Signal,
        cancelled: QtCore.Signal,
    ) -> None:
        super().__init__()
        self._chart = chart
        self._sample_rate = sample_rate
        self._backend = backend
        self._soundfont = soundfont
        self._cancel = cancel
        self._ready = ready
        self._failed = failed
        self._cancelled = cancelled

    def run(self) -> None:  # noqa: D102 - QRunnable interface
        if self._cancel.is_set():
            self._cancelled.emit()
            return
        try:
            result = render_chart(
                self._chart,
                sample_rate=self._sample_rate,
                backend=self._backend,
                soundfont=self._soundfont,
                should_stop=self._cancel.is_set,
            )
        except RenderCancelled:
            self._cancelled.emit()
            return
        except RenderError as exc:
            # A chart that cannot be rendered is a real outcome, not a crash: no
            # soundfont fetched, no notes, a preset the soundfont does not have.
            self._failed.emit(str(exc))
            return
        except MemoryError:
            # A five-minute tab is ~130MB of float32 stereo. On a small machine that
            # is a legitimate refusal, and saying so beats being OOM-killed.
            self._failed.emit("not enough memory to render this song")
            return
        if self._cancel.is_set():
            # Finished, but the player has moved on. Throwing a rendered buffer away
            # at this point is the cheapest of the three outcomes.
            self._cancelled.emit()
            return
        self._ready.emit(result)


class ChartRenderer(QtCore.QObject):
    """Render charts in the background. One at a time.

    Exactly one of :attr:`ready`, :attr:`failed` or :attr:`cancelled` arrives per
    :meth:`start`, so a screen waiting on "the audio is ready" is never left waiting
    forever -- the same guarantee :class:`LibraryLoader` makes for a scan.
    """

    ready = QtCore.Signal(object)
    """Carries a :class:`~guitaroids.audio.render.RenderResult`."""
    failed = QtCore.Signal(str)
    cancelled = QtCore.Signal()

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self._cancel = threading.Event()
        self._task: _RenderTask | None = None
        self._running = False
        # Connected once, here: connecting inside start() would stack a duplicate
        # connection per render and clear the guard N times for one render.
        self.ready.connect(self._settle)
        self.failed.connect(self._settle)
        self.cancelled.connect(self._settle)

    def start(
        self,
        chart: Chart,
        *,
        sample_rate: int = 44100,
        backend: str = "auto",
        soundfont: Path | None = None,
    ) -> bool:
        """Begin rendering. ``False`` if one is already in flight.

        A second render is refused rather than queued: two renders of the same chart
        would race to deliver two buffers, and the loser's would start playing at the
        wrong moment. Call :meth:`cancel`, wait for :attr:`cancelled`, then start.
        """
        if self._running:
            return False
        self._cancel.clear()
        self._task = _RenderTask(
            chart,
            sample_rate=sample_rate,
            backend=backend,
            soundfont=soundfont,
            cancel=self._cancel,
            # The task emits this object's own signals, so a screen connects to the
            # renderer and never to a task it holds no reference to.
            ready=self.ready,
            failed=self.failed,
            cancelled=self.cancelled,
        )
        self._running = True
        QtCore.QThreadPool.globalInstance().start(self._task)
        return True

    def _settle(self, *_args) -> None:
        """A terminal signal arrived: clear the guard and drop the task reference.

        Dropping the reference matters. ``autoDelete`` destroys the underlying
        QRunnable once ``run`` returns, so a Python reference to it afterwards is a
        dangling wrapper.
        """
        self._running = False
        self._task = None

    def cancel(self) -> None:
        """Ask the render to stop. Prompt, and the work in flight is discarded.

        Not instant: the renderer is inside ``generate()`` and cannot be interrupted
        mid-block, so this takes effect at the next onset. Seven seconds of CPU may
        be spent; none of it is delivered.
        """
        self._cancel.set()

    @property
    def is_running(self) -> bool:
        return self._running


def estimate_seconds(chart: Chart, *, sample_rate: int = 44100) -> float:
    """Roughly how long rendering ``chart`` will take, for a progress message.

    Measured on this machine: the soundfont backend renders about 55x real time
    (6:32 of audio in 7.1s), and the numpy pluck about 650x. The estimate is
    deliberately pessimistic -- it is shown to a player waiting, and being wrong in
    the direction of "longer than it was" is better than promising two seconds and
    taking seven.
    """
    audio_seconds = chart.duration * (sample_rate / 44100.0)
    return audio_seconds / 40.0


__all__ = ["ChartRenderer", "RenderResult", "estimate_seconds"]
