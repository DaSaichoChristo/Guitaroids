"""The master clock, and the one place that talks to a sound card.

DESIGN.md §1.5 is the whole design in one line:

    song_pos = (stream.time - t0) - stream.latency

``stream.time`` is PortAudio's own count of frames the device has *consumed*, so it
is the only clock in the system that knows what the listener is hearing. A GUI timer
is self-consistent and cannot say whether the game feels right, and omitting
``latency`` biases every note 10–20ms early because the device has already buffered
audio we believe we have not sent yet.

**The arithmetic is here and the device is not.** :class:`Position` is a pure function
of (stream time, origin, latency, offset) and is tested with no sound card at all.

**Nothing ever blocks on the device, and no other thread touches the stream.** That
constraint is not a style preference. The first version of this module fed the buffer
from a daemon thread with blocking ``write()`` calls, and it crashed the interpreter
twice:

1. Closing a stream while the feeder was blocked inside ``write()`` — a use-after-free
   in C: *"corrupted double-linked list"*, a PulseAudio refcount assertion, core dump.
2. When the device stalled (ALSA: ``PaAlsaStream_WaitForFrames failed``), the
   feeder's write stopped returning, ``stop()``'s bounded join timed out, and the
   ``OutputStream`` — the last reference to it — was garbage collected *while C was
   still writing through it*. PortAudio freed heap it still owned:
   ``malloc(): unaligned tcache chunk detected``, ``Aborted (core dumped)``.

Both are the same mistake wearing different clothes: treating a device handle as
something you can hold across a blocking call. The callback API removes the whole
class. PortAudio calls *us*, on its own thread, and hands us a buffer to fill. There
is no thread of ours inside the driver, nothing to join, nothing to collect under, and
no write that can block.
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
    """``(n, 2)`` to the flat interleaved float32 the callback writes into.

    Copied rather than viewed. A view would be free, but the callback is handed a
    preallocated buffer and copying *into* it is a memcpy with no allocation, so the
    point of this function is only to build that source array once, before the stream
    is opened.
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


def apply_volume(samples: Samples, volume: float) -> Samples:
    """Scale a finished mix by ``volume``, in place where possible.

    **0.0 to 1.0, and that ceiling is the point.** `render_chart` normalises every
    render to a 0.95 peak (§22), so 1.0 is already the loudest correct output and any
    slider above it can only clip. A caller that wants more headroom wants a quieter
    *source*, not a louder master, so this raises rather than quietly over-driving.

    Applied once, to a buffer that is already built, which is why the real-time
    callback can stay a memcpy (§26.3). A volume of 0.0 returns real silence and the
    transport still runs: the clock is the device's, and a song you cannot hear is
    still a song whose position advances.
    """
    if not 0.0 <= volume <= 1.0:
        raise TransportError(f"volume must be 0.0-1.0, got {volume!r}")
    if volume == 1.0:
        return samples
    if volume == 0.0:
        return np.zeros_like(samples)
    return (samples * np.float32(volume)).astype(np.float32, copy=False)


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
        volume: float,
        song_start: float = 0.0,
        offset: float = 0.0,
        device: str | None,
    ) -> None:
        if samples.dtype != np.float32:
            raise TransportError(f"expected float32 audio, got {samples.dtype}")
        if samples.ndim != 2 or samples.shape[1] != 2:
            raise TransportError(f"expected (n, 2) stereo audio, got {samples.shape}")
        self._volume = volume
        #: The buffer as played, already scaled. Scaled ONCE here rather than in the
        #: callback: the gain belongs after `render_chart` has normalised the peak to
        #: 0.95 (§22), so it cannot be undone downstream, and a per-callback multiply
        #: would put arithmetic in the real-time path for no benefit.
        self._buffer = interleaved(apply_volume(samples, volume))
        self._sample_rate = sample_rate
        self._song_start = song_start
        self._offset = offset
        self._device = device
        self._stream = None
        self._cursor = 0
        #: PortAudio's clock reading at which the chart's time zero lands. Read from
        #: the device in `play`, never assumed -- see there.
        self._t0: float | None = None
        #: Cleared by the callback when the buffer runs out. Read by
        #: :attr:`is_running` so a song that has finished is not "still playing"
        #: forever, and by the game screen to know it must fall back.
        self._finished = threading.Event()
        self._underruns = 0

    # --- properties --------------------------------------------------------

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def volume(self) -> float:
        """The gain applied to the buffer at construction. Reported, not adjustable.

        There is no setter on purpose: the buffer is scaled once, before the stream
        opens, so changing your mind means opening a new transport. A live volume
        control would put a multiply in the callback, which §26.3 has good reason to
        avoid.
        """
        return self._volume

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
        """True while the device is playing and there is still audio to play.

        Once the buffer is exhausted this goes False. The stream stays open and the
        device stays at full speed -- it has to, or the clock would stop and every
        note after the end of the song would be judged against a frozen position.
        """
        if self._stream is None:
            return False
        return not self._finished.is_set()

    @property
    def has_finished(self) -> bool:
        """The buffer ran out. Distinct from :attr:`is_running` only in intent."""
        return self._finished.is_set()

    @property
    def underruns(self) -> int:
        """Callbacks the device asked for before we could fill them.

        Non-zero means the callback fell behind, which on a loaded machine means the
        block is too big or the process was descheduled. Worth counting rather than
        ignoring: it is the audio equivalent of a dropped frame.
        """
        return self._underruns

    @property
    def frames_written(self) -> int:
        """How much has been handed to the device.

        Not how much has been *heard*: the device buffers. The difference is exactly
        ``stream.latency``, which is why the clock reads ``stream.time`` rather than
        counting frames.
        """
        return self._cursor // 2

    # --- the callback -------------------------------------------------------

    def _callback(self, outdata, frames, time_info, status) -> None:  # noqa: ANN001
        """Fill one block of audio. Called by PortAudio, on PortAudio's thread.

        This runs at real-time priority, so it does the least work that is correct
        and nothing else:

        - **No allocation.** ``outdata`` is a preallocated view, and the copy into it
          is a memcpy. A ``numpy`` slice of ``self._buffer`` is a view too.
        - **No blocking.** Nothing here waits on a device, which is the entire point
          of the callback API (§ the module docstring).
        - **No exception may escape.** An exception out of a real-time callback
          leaves PortAudio in an undefined state, and the two crashes this module
          already had were both of the driver's own memory being freed while it was
          still using it. So the body is wrapped and a failure degrades to silence.
        """
        wanted = frames * 2  # interleaved
        try:
            available = len(self._buffer) - self._cursor
            if available <= 0:
                # Past the end: silence, and keep the device running at full speed so
                # the clock does not stall. The game sees is_running go False and
                # ends the song on its own terms.
                outdata[:] = 0.0
                self._finished.set()
                return
            if status:
                # PortAudio hands us underflow/overflow flags here. Counting them is
                # the only honest response: raising would abort the stream, and
                # ignoring them loses the evidence that something went wrong.
                self._underruns += 1
            if available < wanted:
                outdata[:] = 0.0
                if available > 0:
                    outdata[: available // 2] = self._buffer[
                        self._cursor :
                    ].reshape(-1, 2)
                self._cursor = len(self._buffer)
                self._finished.set()
            else:
                # **The reshape is not optional.** `outdata` is handed over shaped
                # (frames, 2); assigning a flat array of frames*2 to it raises, and a
                # raise inside a real-time callback means silence for the whole song
                # while the transport cheerfully reports itself finished. That is
                # exactly what the first version of this function did.
                outdata[:] = self._buffer[
                    self._cursor : self._cursor + wanted
                ].reshape(-1, 2)
                self._cursor += wanted
        except Exception:  # noqa: BLE001 - must not escape a real-time callback
            try:
                outdata[:] = 0.0
            except Exception:  # noqa: BLE001 - nothing left to try
                pass
            self._finished.set()

    # --- the clock ---------------------------------------------------------

    def position(self) -> float:
        """Seconds into the chart, or ``-1.0`` when nothing is playing.

        The single number the rest of the system reads. When the stream is stopped
        this returns ``-1.0`` rather than 0.0, so "not started yet" cannot be
        confused with "at the first note" -- which for the real tab is three seconds
        of empty notation with a note at the end of it.
        """
        if self._stream is None or self._t0 is None:
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
        try:
            return float(self._stream.latency)
        except Exception:  # noqa: BLE001 - closed under us
            return 0.0

    # --- lifecycle ---------------------------------------------------------

    def play(self) -> None:
        """Open the stream, start it, and return. Does not block.

        ``play()`` used to block for the length of the song, because a blocking
        ``write()`` of the whole buffer does exactly that (§23.3). The callback runs
        the audio, so opening the stream is all there is to do.
        """
        import sounddevice as sd

        if self._stream is not None:
            raise TransportError("already playing; stop() first")
        self._cursor = 0
        self._finished.clear()
        self._underruns = 0
        self._stream = sd.OutputStream(
            samplerate=self._sample_rate,
            channels=2,
            dtype="float32",
            blocksize=BLOCK,
            device=self._device,
            callback=self._callback,
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
        # the device clock at `now + song_start`.
        self._t0 = float(self._stream.time) + self._song_start

    def wait(self, *, timeout: float | None = None, poll: float = 0.05) -> bool:
        """Block until the buffer has been played. ``False`` if the timeout hit.

        Compared against the *device clock*, not against ``active``: the stream stays
        active after its buffer is exhausted, because the callback keeps feeding it
        silence, and sleeping for :attr:`duration` cuts the song off by whatever the
        device had buffered.

        The default timeout is the buffer length plus :data:`STALL_SECONDS`, so a
        device that stops consuming is reported rather than waited on forever.
        """
        if self._stream is None or self._t0 is None:
            return False
        end = self._t0 + self.duration
        limit = timeout if timeout is not None else self.duration + STALL_SECONDS
        waited = 0.0
        while not self._finished.is_set() and waited < limit:
            time.sleep(poll)
            waited += poll
        return self._finished.is_set()

    def stop(self) -> None:
        """Close the stream. Safe to call twice, and safe with no stream.

        No join, no timeout, no other thread: the callback is PortAudio's, and it
        has stopped by the time ``stop()`` returns because ``stop()`` waits for the
        stream to stop before closing it. That ordering is the whole safety argument
        -- the previous design got this wrong twice (§ the module docstring).
        """
        if self._stream is None:
            return
        stream, self._stream = self._stream, None
        try:
            stream.stop()
            stream.close()
        except Exception:  # noqa: BLE001 - already gone, or never opened
            pass
        finally:
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
    """One line per timing fact, for a practice script's banner."""
    return (
        f"sample rate {sample_rate}Hz | count-in {timing['count_in']:.2f}s | "
        f"lead-in {timing['lead_in']:.2f}s | song starts at "
        f"{timing['song_start']:.2f}s | total {timing['total']:.2f}s"
    )
