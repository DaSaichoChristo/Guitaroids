"""Listening to a guitar. One input stream, one worker, and the arithmetic between.

The last piece of the input half, and the only one that needs hardware. It is split in
two so that almost none of it does:

- :class:`PitchDetector` is **pure and synchronous** -- blocks in, estimates out, no
  thread and no device. The windowing, the latency arithmetic and the octave handling
  all live here, and all of it is tested with no microphone.
- :class:`Microphone` is a thin device wrapper: a `sounddevice` input stream whose
  callback only copies, a daemon thread that drains the queue, and a callback for the
  result.

**Nothing of ours is inside the audio callback**, which is §26.3's rule and the reason
this project's transport has had no core dumps since it moved to PortAudio's callback
API. The callback here does the least thing it can: write the incoming block into a
preallocated numpy buffer. It does not estimate, allocate, lock, or emit a signal --
a pitch estimate is a few milliseconds of numpy, and a few milliseconds in a callback
that runs at real-time priority is a dropout.

**The queue is bounded and drops the oldest audio**, rather than growing. If the worker
falls behind, the right failure is a gap -- a missed note the player did not play -- and
the wrong one is unbounded memory growth that eventually takes the process down.

**Latency is compensated, and it has to be.** The estimate describes the *centre* of
its 2048-sample window, so it is about 23ms behind the moment it was emitted, before
PortAudio's own buffering. Left uncorrected that is a systematic 23ms of lateness on
every note, which is a third of the Perfect window and shifts a good player toward
Miss. So :attr:`PitchDetector.latency_seconds` is subtracted by the caller, and
`Settings.input_latency_ms` is the manual trim on top of it.

**The app hears itself.** It renders the tab and plays it out of the same machine, so
through speakers the microphone picks up its own backing track and the judge awards
PERFECT for notes the player never touched. The answer is headphones, stated in the
Preferences tooltip (§24.3). Subtracting the known render is possible -- the app knows
exactly what it played -- and is not attempted here: it means deliberately discarding
correct notes, and the first version of a feature should not be the fiddly one.
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .pitch import HOP, WINDOW, PitchEstimate, estimate

#: The rate the whole project works at: `render_chart` produces 44100 and
#: `Transport` plays it, so the microphone analyses in the same domain rather than
#: in a new one. A device that cannot do 44100 is a device the player selects around.
SAMPLE_RATE = 44100

#: How many windows of audio the queue may hold before the oldest is dropped. At the
#: default block size this is a few hundred milliseconds of slack -- far more than the
#: worker's worst case, and bounded so a stalled worker cannot exhaust memory.
MAX_QUEUED_SAMPLES = WINDOW * 8


class PitchDetector:
    """Blocks of samples in, estimates out. No device, no thread, no Qt.

    The sliding window is the whole job: keep a buffer, take the most recent `WINDOW`
    samples, and after each estimate advance by `HOP` rather than by the window, so the
    window overlaps itself and a note is heard several times. That redundancy is
    deliberate -- it is what makes a dropped window cost one estimate rather than a
    note, and it is why the screen must not treat a detection as a keypress (§24.2).
    """

    def __init__(
        self,
        sample_rate: int,
        *,
        window: int = WINDOW,
        hop: int = HOP,
        max_buffer: int = MAX_QUEUED_SAMPLES,
    ) -> None:
        self._sample_rate = sample_rate
        self._window = window
        self._hop = hop
        self._max_buffer = max_buffer
        self._buffer = np.zeros(0, dtype=np.float64)
        #: Every estimate ever produced, for tests and for the debug readout.
        self.estimates: list[PitchEstimate] = []
        #: Windows discarded because the buffer overflowed, i.e. the worker fell behind.
        self.dropped = 0

    @property
    def latency_seconds(self) -> float:
        """How far behind the emission of an estimate the sound actually was.

        Half the window: the estimate describes the middle of the samples it was given,
        so by the time it exists, the note that produced it has already been heard for
        that long. Correcting it is the difference between a systematic 23ms of
        lateness and none.
        """
        return (self._window / 2.0) / self._sample_rate

    def reset(self) -> None:
        """Discard buffered audio, keeping the estimate history.

        Called when the stream reopens. The sliding window is *state*: it holds the
        last ``max_buffer`` samples and every estimate is computed from them, so a
        detector reused across a stop/start opens with up to ``max_buffer`` of
        pre-restart audio still in it -- 371ms on this configuration -- and the first
        windows analysed after the reopen describe the room, or the previous song,
        rather than the one that is starting.

        ``estimates`` is kept, deliberately. It is a test surface and a debug aid, and
        clearing it would make a restart look like a fresh detector when the only thing
        that actually changed is the audio.
        """
        self._buffer = np.zeros(0, dtype=np.float64)

    def push(self, block: np.ndarray) -> list[PitchEstimate]:
        """Add a block of mono or stereo audio, and return any new estimates.

        The window advances by `hop` per estimate, so a block long enough for several
        windows returns several estimates. Returns an empty list far more often than
        not -- most of what a microphone hears is not a note.
        """
        data = np.asarray(block, dtype=np.float64)
        if data.ndim == 2:
            if data.shape[1] == 1:
                data = data[:, 0]
            else:
                # Mono by averaging. A guitar panned centre sums coherently, so this is
                # the right answer for one source and a harmless one for a bad
                # microphone.
                data = data.mean(axis=1)
        elif data.ndim != 1:
            raise ValueError(f"expected mono or (n, channels) audio, got {data.shape}")

        self._buffer = np.concatenate([self._buffer, data])
        if len(self._buffer) > self._max_buffer:
            # Bounded, oldest first. A gap in the audio is a missed note; unbounded
            # growth is a dead process.
            self.dropped += len(self._buffer) - self._max_buffer
            self._buffer = self._buffer[-self._max_buffer :]

        found: list[PitchEstimate] = []
        while len(self._buffer) >= self._window:
            found_ = estimate(self._buffer[: self._window], self._sample_rate)
            if found_ is not None:
                found.append(found_)
                self.estimates.append(found_)
            self._buffer = self._buffer[self._hop :]
        return found


@dataclass(frozen=True, slots=True)
class MicrophoneStats:
    """What the input half has been doing, for a bug report or a tooltip."""

    blocks: int = 0
    samples: int = 0
    pitches: int = 0
    dropped: int = 0


class Microphone:
    """An input stream feeding a :class:`PitchDetector` on its own thread.

    **The device is only touched by `start` and `stop`.** Constructing one opens
    nothing, imports nothing from Qt, and raises nothing on a machine with no input
    device -- so the screen can build one and only then find out, which is the shape
    §24.3's "any device failure falls back" already assumes elsewhere.

    `on_pitch` is called on the worker thread, not the GUI thread. A Qt signal is the
    right way to cross that boundary and the caller's problem, not this class's: emit
    from the callback and Qt queues it onto the GUI thread automatically.
    """

    def __init__(
        self,
        *,
        device: str | None = None,
        sample_rate: int = 44100,
        block_size: int = HOP,
        on_pitch: Callable[[PitchEstimate], None] | None = None,
    ) -> None:
        self._device = device
        self._sample_rate = sample_rate
        self._block_size = block_size
        self._on_pitch = on_pitch
        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self._detector = PitchDetector(sample_rate)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._stream = None
        self._preallocated = np.zeros((block_size, 1), dtype=np.float32)
        self._stats = MicrophoneStats()

    # --- properties --------------------------------------------------------

    @property
    def detector(self) -> PitchDetector:
        return self._detector

    @property
    def latency_seconds(self) -> float:
        """See :attr:`PitchDetector.latency_seconds`."""
        return self._detector.latency_seconds

    @property
    def is_running(self) -> bool:
        return self._stream is not None

    @property
    def stats(self) -> MicrophoneStats:
        blocks, samples = self._stats.blocks, self._stats.samples
        pitches, dropped = len(self._detector.estimates), self._detector.dropped
        return MicrophoneStats(blocks, samples, pitches, dropped)

    # --- device ------------------------------------------------------------

    def _callback(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        """PortAudio's thread. Copy and return. Nothing else, ever.

        The copy is into a preallocated array rather than a fresh one, so the callback
        allocates nothing; the buffer is then handed to the queue and a new one is
        allocated *there*, on the worker. A status flag is a warning, not an error --
        an underflow is normal and reporting it as fatal would close the stream on the
        first hiccup.
        """
        count = min(frames, len(self._preallocated))
        self._preallocated[:count].reshape(count, -1)[:, :] = indata[:count]
        self._queue.put(self._preallocated[:count].copy())
        if status:
            self._overruns += 1

    _overruns = 0

    def start(self) -> None:
        """Open the stream and start the worker. Raises if the device refuses."""
        import sounddevice as sd

        if self._stream is not None:
            return
        # The detector is reused across a stop/start, and its sliding window holds
        # pre-restart audio. Drop it here rather than analysing the room as though it
        # were the first note of the song.
        self._detector.reset()
        self._stop.clear()
        self._overruns = 0
        try:
            self._stream = sd.InputStream(
                device=self._device,
                channels=1,
                samplerate=self._sample_rate,
                blocksize=self._block_size,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
        except Exception:
            # A device that will not open is a state the caller must handle, not an
            # exception it should have to guess the meaning of.
            self._stream = None
            raise
        self._thread = threading.Thread(target=self._drain, name="pitch", daemon=True)
        self._thread.start()

    def _drain(self) -> None:
        """The worker: take blocks, run the estimator, hand the result over."""
        while not self._stop.is_set():
            try:
                block = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            self._stats = MicrophoneStats(
                blocks=self._stats.blocks + 1,
                samples=self._stats.samples + len(block),
            )
            for found in self._detector.push(block):
                if self._on_pitch is not None:
                    self._on_pitch(found)

    def stop(self) -> None:
        """Close the stream and stop the worker. Safe to call twice, and with no stream."""
        self._stop.set()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # noqa: BLE001 - already gone, like `Transport.stop`
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            # The worker's own timeout bounds this; a join with no timeout is the
            # mistake §26.3 records twice.
            thread.join(timeout=0.5)

    def __enter__(self) -> "Microphone":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()


def device_report() -> str:
    """One line per input device, beside `transport.device_report`'s for outputs.

    The same lazy import, for the same reason: a script that reports devices should not
    need a device to report.
    """
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001 - no PortAudio, no report, not a crash
        return "  sounddevice is not installed"

    lines = [
        # `default.samplerate` is None until a stream has been opened, so the
        # transport's report already guards this and so does this one -- an f-string
        # on None raises, and this function's whole promise is that it never does.
        f"default input: {sd.default.device[0]} @ "
        f"{f'{sd.default.samplerate:.0f}Hz' if sd.default.samplerate else 'rate unset until a stream opens'}"
    ]
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] < 1:
            continue
        lines.append(
            f"  [{index}] {device['name']}  in={device['max_input_channels']}ch "
            f"{device['default_samplerate']:.0f}Hz"
        )
    if len(lines) == 1:
        lines.append("  (no input devices found)")
    return "\n".join(lines)
