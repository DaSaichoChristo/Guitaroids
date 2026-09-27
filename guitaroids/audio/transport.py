"""The master clock, and the one place that talks to a sound card.

DESIGN.md §1.5 is the whole design in one line:

    song_pos = (stream.time - t0) - stream.latency

``stream.time`` is PortAudio's own count of frames the device has *consumed*, so it
is the only clock in the system that knows what the listener is hearing. A GUI
timer is self-consistent and cannot say whether the game feels right, and omitting
``latency`` biases every note 10-20ms early because the device has already buffered
audio we believe we have not sent yet.

**The arithmetic is here and the device is not.** :class:`Position` is a pure
function of (stream time, origin, latency, offset) and is tested with no sound card
at all -- which is the property §3.5 asked for and the reason this is a separate
module from the sounddevice wrapper. A test that needs a device is a test that is
flaky on a machine with no sound card, which is most CI.

**Only the feeder thread touches the stream, including closing it.** This is not
tidiness. Closing a PortAudio stream while another thread is blocked inside
``write()`` on it is a use-after-free inside C: on this machine it aborted the
process with "corrupted double-linked list" and a PulseAudio refcount assertion,
and took the interpreter with it. So :meth:`Transport.stop` sets a flag and waits;
the feeder's own ``finally`` does the stopping and closing.

**``play()`` returns immediately, because ``write()`` does not.** Measured on this
machine: a 0.4s buffer blocks 0.44s, a 5s buffer blocks 4.97s. ``OutputStream.write``
is *blocking* -- it returns when the device has consumed the data, not when it has
queued it. So writing a six-minute song in one call freezes the caller for six
minutes, which on the GUI thread means a frozen window and no navigation for the
length of the song. The stream is therefore fed from a daemon thread, one block at a
time, and ``play()`` returns in milliseconds.

The clock is unaffected: :attr:`Stream.time` counts what the device has consumed
whether or not we are in the middle of feeding it, so the position a player sees is
the same either way. And because the writes happen on our own thread rather than in
a PortAudio callback, there is no real-time constraint to honour at all -- the
"callback allocates nothing" rule applies to the ``callback=`` API, which this does
not use.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

from .click import count_in_seconds

Samples = np.ndarray

#: Frames per callback. 1024 at 44.1kHz is 23ms: under the 46ms a rhythm game
#: tolerates for a whole frame, and large enough that the callback is not the
#: bottleneck. Also the block size the pitch detector will want.
BLOCK = 1024

#: Report a stall rather than hiding it. A stream that has not consumed anything
#: for this long is broken, and a silent infinite wait is the worst failure mode
#: for a thing you are watching a progress bar for.
STALL_SECONDS = 2.0

#: Frames handed to the device per write. Not a latency knob -- the position comes
#: from ``stream.time``, not from how much we have queued -- just a compromise
#: between Python-loop overhead and syscall count. 4096 frames is 93ms at 44.1kHz.
FEED_BLOCK = 4096


class TransportError(Exception):
    """The stream could not be opened or driven."""


@dataclass(frozen=True, slots=True)
class Position:
    """Where we are in the song, in seconds. Everything else reads this.

    Pure arithmetic, so it is testable with no device -- and it is the only place
    the clock is defined, so there is one formula rather than one per caller.
    """

    stream_time: float
    """Frames the device has consumed, divided by the sample rate."""

    t0: float
    """The stream time at which the song starts."""

    latency: float
    """What PortAudio reports it has buffered. Subtract it, or every note is late."""

    offset: float = 0.0
    """The player's audio alignment, in seconds. Positive means the audio is ahead
    of the tab, so the tab position runs behind."""

    def song_position(self) -> float:
        """Seconds into the chart. Negative during the count-in."""
        return (self.stream_time - self.t0 - self.latency) - self.offset

    def elapsed_since_start(self) -> float:
        """Seconds since the song began, ignoring the player's offset.

        This is what a progress bar wants: the offset is a correction to the
        *music*, not to how long the player has been listening.
        """
        return self.stream_time - self.t0 - self.latency


def plan(
    *,
    chart_duration: float,
    bpm: float,
    count_in_bars: int,
    beats_per_bar: int = 4,
    lead_in: float = 0.25,
) -> dict[str, float]:
    """The timing facts a transport needs, before any device exists.

    ``lead_in`` is silence prepended before the count-in clicks, so the first click
    is not the first thing the device is handed -- a stream that starts on a
    non-zero sample can click on the way in, and a count-in that starts instantly
    gives no room to lift a pick.
    """
    count_in = count_in_seconds(
        bpm=bpm, count_in_bars=count_in_bars, beats_per_bar=beats_per_bar
    )
    return {
        "count_in": count_in,
        "lead_in": lead_in,
        # Where the chart's own time zero sits in the buffer.
        "song_start": lead_in + count_in,
        "total": lead_in + count_in + chart_duration,
    }


def interleaved(samples: Samples) -> np.ndarray:
    """``(n, 2)`` to the ``(n * 2,)`` interleaved float32 PortAudio wants.

    Copied rather than viewed, deliberately, and this is the one place the project's
    "no allocation in the callback" rule is bent -- so it is done *here*, once,
    before the stream opens, and the callback gets a buffer that is already in the
    right shape. A view would be free but ``np.ascontiguousarray`` on a
    non-contiguous view does copy anyway, so the copy is explicit and once.
    """
    return np.ascontiguousarray(samples, dtype=np.float32).reshape(-1)


def fit(samples: Samples, frames: int) -> Samples:
    """Zero-pad or truncate to exactly ``frames``. Done before opening the stream."""
    if len(samples) == frames:
        return samples
    if len(samples) > frames:
        return samples[:frames]
    out = np.zeros((frames, 2), dtype=np.float32)
    out[: len(samples)] = samples
    return out


def device_report() -> str:
    """One line per device, for a script or a bug report.

    ``sounddevice`` is imported inside the function so that importing this module --
    which the pure clock tests do -- never touches PortAudio.
    """
    import sounddevice as sd

    rate = sd.default.samplerate
    lines = [
        "default output: "
        f"{sd.default.device[1]} @ {f'{rate:.0f}Hz' if rate else 'rate unset until a stream opens'}"
    ]
    for index, device in enumerate(sd.query_devices()):
        if device["max_output_channels"] < 1:
            continue
        lines.append(
            f"  [{index}] {device['name']}  out={device['max_output_channels']}ch "
            f"{device['default_samplerate']:.0f}Hz"
        )
    return "\n".join(lines)


class Transport:
    """A pre-rendered buffer, played through one output stream.

    Owns a sound card handle, so it lives in :mod:`guitaroids.audio` and not in a
    screen: §1.7 says screens never own devices, because they are built once and
    hidden, and a handle owned by a hidden screen is a handle nobody can close.
    """

    def __init__(
        self,
        samples: Samples,
        *,
        sample_rate: int,
        song_start: float = 0.0,
        offset: float = 0.0,
        device: str | None = None,
    ) -> None:
        if samples.dtype != np.float32:
            raise TransportError(f"expected float32 audio, got {samples.dtype}")
        if samples.ndim != 2 or samples.shape[1] != 2:
            raise TransportError(f"expected (n, 2) stereo audio, got {samples.shape}")
        # Both forms, once. `(frames, 2)` is what ``OutputStream.write`` takes;
        # the flat interleaved form is what a *raw* callback signature needs. Kept
        # as one array and derived, so they cannot drift.
        self._frames = samples
        self._buffer = interleaved(samples)
        self._sample_rate = sample_rate
        self._song_start = song_start
        self._offset = offset
        self._device = device
        self._stream = None
        self._frames_written = 0
        self._cursor = 0
        self._feeder: threading.Thread | None = None
        #: Set by stop(); the feeder checks it between blocks and closes the stream
        #: on its way out. A plain bool guarded by the GIL is enough, and cheaper
        #: than an Event to read on every 93ms block.
        self._stopping = False
        #: PortAudio's clock reading at which the chart's time zero lands. Read
        #: from the device in `play`, never assumed -- see there.
        self._t0: float | None = None

    # --- properties --------------------------------------------------------

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def duration(self) -> float:
        """Seconds of audio in the buffer, count-in and all."""
        return len(self._buffer) / 2 / self._sample_rate

    @property
    def song_start(self) -> float:
        """Seconds of lead-in and count-in before the chart's time zero."""
        return self._song_start

    @property
    def is_running(self) -> bool:
        """True while the device is playing and the feeder is alive.

        Checked without calling into PortAudio when there is no feeder, because
        ``stream.active`` on a stream another thread is closing is the race that
        aborts the process.
        """
        if self._stream is None:
            return False
        if self._feeder is not None and not self._feeder.is_alive():
            return False
        return True

    @property
    def frames_written(self) -> int:
        """How much has been handed to the device so far.

        Not the same as how much has been *heard*: the device buffers. The
        difference is exactly ``stream.latency``, which is why the clock reads
        ``stream.time`` rather than counting frames written.
        """
        return self._frames_written

    # --- the clock ---------------------------------------------------------

    def position(self) -> float:
        """Seconds into the chart, or ``-1.0`` when nothing is playing.

        The single number the rest of the system reads. When the stream is stopped
        this returns ``-1.0`` rather than 0.0, so "not started yet" cannot be
        confused with "at the first note" -- which for the real tab is three
        seconds of empty notation with a note at the end of it.
        """
        if not self.is_running or self._t0 is None:
            return -1.0
        try:
            return Position(
                stream_time=self._stream.time,
                t0=self._t0,
                latency=float(self._stream.latency),
                offset=self._offset,
            ).song_position()
        except Exception:  # noqa: BLE001 - the stream was closed under us
            return -1.0

    def elapsed(self) -> float:
        """Seconds of audio played, ignoring the player's offset."""
        if self._stream is None or self._t0 is None:
            return 0.0
        return Position(
            stream_time=self._stream.time,
            t0=self._t0,
            latency=float(self._stream.latency),
        ).elapsed_since_start()

    def latency(self) -> float:
        """Seconds the device has buffered. Zero when not playing."""
        if self._stream is None:
            return 0.0
        return float(self._stream.latency)

    # --- lifecycle ---------------------------------------------------------

    def play(self) -> None:
        """Open the stream, start it, and hand it to a feeder thread. Returns at once.

        Not "blocks until the buffer is queued": there is no such moment, because
        ``write`` returns when the device has *consumed* the data. A 0.4s buffer
        blocks 0.44s and a six-minute one blocks six minutes, so the writes happen on
        :meth:`_feed` instead.
        """
        import sounddevice as sd

        if self._stream is not None:
            raise TransportError("already playing; stop() first")
        self._stream = sd.OutputStream(
            samplerate=self._sample_rate,
            channels=2,
            dtype="float32",
            blocksize=BLOCK,
            device=self._device,
        )
        self._stream.start()
        # **t0 is read from the device here, and this is not optional.**
        # `stream.time` is not a count of seconds since we opened the stream: on
        # this machine's PipeWire default it reports 1790470436.39, which is about
        # fifty-five years. §1.5's `stream.time - t0` is only a position if t0 came
        # from the same clock, and a t0 of 0 does not raise -- it returns a position
        # of 1.8 billion seconds, which looks like a bug in the caller.
        #
        # The song's zero sits `song_start` frames into the buffer, so it lands on
        # the device clock at `now + song_start`. Subtracting `latency` further down
        # is what stops every note being reported early.
        self._t0 = float(self._stream.time) + self._song_start
        # Written once, in one call, so the device never has to be refilled and the
        # callback never has to do anything at all. A whole six-minute buffer is
        # fine: PortAudio queues it.
        # The (frames, 2) form, not the flat one: sounddevice's array API reads a
        # flat buffer as mono, and then refuses it for a 2-channel stream.
        self._cursor = 0
        self._stopping = False
        self._feeder = threading.Thread(
            target=self._feed, name="guitaroids-audio", daemon=True
        )
        self._feeder.start()

    def _feed(self) -> None:
        """Hand the buffer to the device a block at a time, then close it.

        On the feeder thread, never the caller's, and it owns the stream's whole
        lifetime. A slice of a numpy array is a view, so each write costs one
        allocation rather than a copy of the song, and ``write`` releases the GIL, so
        the GUI thread is not held up either.
        """
        stream = self._stream
        if stream is None:
            return
        try:
            while not self._stopping and self._cursor < len(self._frames):
                if stream.stopped:
                    return
                chunk = self._frames[self._cursor : self._cursor + FEED_BLOCK]
                written = stream.write(chunk)
                self._cursor += written
                self._frames_written = self._cursor
            # Everything queued; the stream stays active until the device drains it.
        except Exception:  # noqa: BLE001 - the device went away mid-song
            # An unplugged interface, a suspended PulseAudio daemon. A daemon thread
            # that lets this escape prints a traceback nobody reads and leaves the
            # transport looking like it is still playing.
            pass
        finally:
            # Closing here rather than in stop() is the whole point: nobody else may
            # touch this stream while a write could be in flight.
            for close in (stream.stop, stream.close):
                try:
                    close()
                except Exception:  # noqa: BLE001 - already gone
                    pass

    def wait(self, *, timeout: float | None = None, poll: float = 0.05) -> bool:
        """Block until the buffer has been played. ``False`` if the timeout hit.

        Polling ``stream.active`` rather than sleeping for :attr:`duration`, because
        the two disagree: ``write`` returns once the data is *queued*, not once it is
        *heard*, so a fixed sleep either cuts the song off or pads it with silence.

        The default timeout is the buffer length plus :data:`STALL_SECONDS`, so a
        device that stops consuming is reported rather than waited on forever.
        """
        if self._stream is None or self._t0 is None:
            return False
        # Compared against the *clock*, not against `active`: the stream stays
        # active after its buffer is exhausted, so waiting for `active` to clear
        # waits forever, and sleeping for `duration` cuts the song off by whatever
        # the device had buffered. The clock is the only honest end point.
        end = self._t0 + self.duration
        limit = timeout if timeout is not None else self.duration + STALL_SECONDS
        waited = 0.0
        while waited < limit:
            if float(self._stream.time) >= end:
                return True
            time.sleep(poll)
            waited += poll
        return float(self._stream.time) >= end

    def stop(self) -> None:
        """Ask the feeder to stop, and wait for it. Safe to call twice.

        This thread does **not** close the stream. It sets a flag and joins; the
        feeder's ``finally`` does the closing, because closing a stream out from
        under a thread blocked in ``write()`` aborts the process inside PortAudio.

        The join is bounded and generous: a write returns as soon as the device has
        consumed one 93ms block, so the flag is seen almost immediately. The bound
        exists so a wedged device cannot hang the caller -- the GUI thread on quit,
        in particular.
        """
        feeder = self._feeder
        self._stopping = True
        if feeder is not None and feeder is not threading.current_thread():
            feeder.join(timeout=2.0)
        self._stream = None
        self._feeder = None
        self._t0 = None

    def __enter__(self) -> "Transport":
        self.play()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()


def silence(seconds: float, sample_rate: int) -> Samples:
    """A silent stereo buffer, for padding and for tests."""
    return np.zeros((max(0, int(seconds * sample_rate)), 2), dtype=np.float32)


def describe(timing: dict[str, float], sample_rate: int) -> str:
    """One line per timing fact, for the practice script's banner."""
    return (
        f"sample rate {sample_rate}Hz | count-in {timing['count_in']:.2f}s | "
        f"lead-in {timing['lead_in']:.2f}s | song starts at "
        f"{timing['song_start']:.2f}s | total {timing['total']:.2f}s"
    )
