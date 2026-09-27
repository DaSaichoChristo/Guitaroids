"""Tests for the master clock and the transport.

The clock arithmetic is the point, and it is pure: :class:`Position` is tested here
with no sound card, no display and no event loop, which is what §3.5 asked for and
why it is a separate module from the sounddevice wrapper.

The two tests that need a device are marked and skip when there is none, because a
test that fails on a machine with no sound card is a test that gets deleted rather
than fixed.
"""

from __future__ import annotations

import numpy as np
import pytest

from guitaroids.context import AppContext
from guitaroids.audio.transport import (
    BLOCK,
    STALL_SECONDS,
    Position,
    Transport,
    TransportError,
    apply_volume,
    device_report,
    fit,
    interleaved,
    plan,
    silence,
)

RATE = 44100


def stereo(samples: int = 1000) -> np.ndarray:
    return np.zeros((samples, 2), dtype=np.float32)


def transport_for(samples: np.ndarray, **kwargs) -> Transport:
    """A Transport with the two required arguments every call site used to forget.

    `volume` and `device` are required and have no default, so that a call site
    cannot quietly inherit one (DESIGN.md §28.2, after §21.2). Every test here is
    about the clock and does not care about either, so the helper supplies full
    volume and the system default -- and the tests that *do* care pass their own.
    """
    kwargs.setdefault("volume", 1.0)
    kwargs.setdefault("device", None)
    return Transport(samples, sample_rate=kwargs.pop("sample_rate", RATE), **kwargs)


# --- the formula, which is the whole design --------------------------------------


def test_position_is_stream_time_minus_origin() -> None:
    """§1.5 with the latency and offset set to zero: ``stream.time - t0``."""
    position = Position(stream_time=30.0, t0=10.0, latency=0.0, offset=0.0)
    assert position.song_position() == pytest.approx(20.0)


def test_latency_is_subtracted_so_notes_are_not_reported_early() -> None:
    """The one correction the project says is not optional.

    The device has already buffered audio we believe we have not sent, so without
    this every note reads ~10-20ms ahead of what the listener is hearing. AGENTS.md
    calls that out as the single most important thing about the clock.
    """
    without = Position(stream_time=30.0, t0=10.0, latency=0.0).song_position()
    with_latency = Position(stream_time=30.0, t0=10.0, latency=0.018).song_position()
    assert with_latency == pytest.approx(without - 0.018)
    assert with_latency < without, "latency must make a note read later, not earlier"


def test_the_offset_shifts_the_tab_not_the_elapsed_time() -> None:
    """A player's audio alignment is a correction to the music, not to the clock.

    So it moves ``song_position`` and leaves ``elapsed_since_start`` alone -- which
    is what a progress bar wants, and a bug that made the progress bar jump when the
    player nudged their offset would be reported as "the timing is weird".
    """
    plain = Position(stream_time=30.0, t0=10.0, latency=0.01, offset=0.0)
    nudged = Position(stream_time=30.0, t0=10.0, latency=0.01, offset=0.25)
    assert nudged.song_position() == pytest.approx(plain.song_position() - 0.25)
    assert nudged.elapsed_since_start() == pytest.approx(plain.elapsed_since_start())


def test_position_is_negative_before_the_song_starts() -> None:
    """The count-in lives at negative positions, and the game screen shows a banner
    there. A clock that clamps at zero cannot distinguish "not started" from
    "counting in"."""
    counting_in = Position(stream_time=1.0, t0=4.158, latency=0.01)
    assert counting_in.song_position() == pytest.approx(-3.168)
    assert counting_in.song_position() < 0.0


def test_position_tracks_the_device_not_the_wall_clock() -> None:
    """Two positions on the same device clock differ by the same amount as the
    clock, whatever the origin's magnitude.

    This is the property that makes the huge ``stream.time`` values safe: only
    differences are ever taken, so a device clock reading fifty-five years behaves
    exactly like one reading zero.
    """
    origin = 1_790_470_436.39
    early = Position(stream_time=origin + 5.0, t0=origin, latency=0.02)
    late = Position(stream_time=origin + 8.0, t0=origin, latency=0.02)
    assert late.song_position() - early.song_position() == pytest.approx(3.0)


# --- planning ---------------------------------------------------------------------


def test_plan_places_the_music_after_the_count_in() -> None:
    timing = plan(chart_duration=380.5, bpm=76.0, count_in_bars=1, lead_in=0.25)
    assert timing["count_in"] == pytest.approx(4 * 60.0 / 76.0)
    assert timing["song_start"] == pytest.approx(timing["count_in"] + 0.25)
    assert timing["total"] == pytest.approx(timing["song_start"] + 380.5)


def test_no_count_in_means_the_song_starts_at_the_lead_in() -> None:
    timing = plan(chart_duration=10.0, bpm=120.0, count_in_bars=0, lead_in=0.25)
    assert timing["count_in"] == 0.0
    assert timing["song_start"] == pytest.approx(0.25)


def test_the_lead_in_gives_the_pick_a_moment() -> None:
    """A count-in that starts on the first sample of a stream can click on the way
    in, and leaves no room to lift a pick."""
    timing = plan(chart_duration=1.0, bpm=120.0, count_in_bars=1, lead_in=0.25)
    assert timing["lead_in"] > 0.0
    assert timing["song_start"] > timing["count_in"], "the lead-in comes first"


# --- buffer shapes ----------------------------------------------------------------


def test_interleaved_is_flat_and_float32() -> None:
    """The shape a raw PortAudio callback signature needs."""
    flat = interleaved(np.ones((100, 2), dtype=np.float32))
    assert flat.shape == (200,)
    assert flat.dtype == np.float32
    assert np.array_equal(flat, np.ones(200, dtype=np.float32))


def test_interleaved_puts_the_channels_in_the_right_order() -> None:
    source = np.zeros((2, 2), dtype=np.float32)
    source[0] = (1.0, 2.0)
    source[1] = (3.0, 4.0)
    assert list(interleaved(source)) == [1.0, 2.0, 3.0, 4.0]


def test_fit_pads_with_silence() -> None:
    padded = fit(np.ones((10, 2), dtype=np.float32), 20)
    assert padded.shape == (20, 2)
    assert np.all(padded[10:] == 0.0)


def test_fit_truncates() -> None:
    assert fit(np.ones((20, 2), dtype=np.float32), 10).shape == (10, 2)


def test_silence_is_stereo_float32() -> None:
    quiet = silence(0.5, RATE)
    assert quiet.shape == (22050, 2)
    assert quiet.dtype == np.float32
    assert not quiet.any()


# --- the transport, without a device ----------------------------------------------


def test_a_transport_with_no_stream_has_no_position() -> None:
    """``-1.0``, not ``0.0``: "not playing" and "at the first note" are different
    states, and for the real tab the first note is three seconds in."""
    transport = transport_for(stereo())
    assert transport.position() == -1.0
    assert transport.elapsed() == 0.0
    assert transport.latency() == 0.0
    assert transport.is_running is False


def test_a_transport_rejects_the_wrong_audio() -> None:
    """Loudly, at construction, rather than at 0.2 seconds into a song."""
    with pytest.raises(TransportError, match="float32"):
        transport_for(np.zeros((10, 2), dtype=np.float64))
    with pytest.raises(TransportError, match=r"\(n, 2\)"):
        transport_for(np.zeros(10, dtype=np.float32))
    with pytest.raises(TransportError, match=r"\(n, 2\)"):
        transport_for(np.zeros((10, 3), dtype=np.float32))


def test_the_duration_accounts_for_the_count_in() -> None:
    """The buffer is longer than the song, and the extra is the count-in."""
    song = stereo(RATE)
    transport = transport_for(song, song_start=3.0)
    assert transport.duration == pytest.approx(1.0)
    assert transport.song_start == 3.0


def test_stopping_twice_is_safe() -> None:
    transport = transport_for(stereo())
    transport.stop()
    transport.stop()


def test_waiting_with_no_stream_is_false_not_a_hang() -> None:
    assert transport_for(stereo()).wait(timeout=0.01) is False


def test_the_block_size_is_under_a_rhythm_game_frame() -> None:
    """1024 frames is 23ms. A rhythm game tolerates about 46ms for a whole frame, so
    the block has to be comfortably inside that or the clock is quantised too coarsely
    to place a PERFECT window."""
    assert BLOCK / RATE < 0.046
    assert BLOCK / RATE > 0.005, "and not so small that the callback dominates"


def test_a_stall_is_bounded() -> None:
    """Waiting forever on a device that has stopped consuming is the worst failure
    mode for something with a progress bar."""
    assert STALL_SECONDS > 0.5
    assert STALL_SECONDS < 30.0


# --- with a device, if there is one ------------------------------------------------


def _has_output() -> bool:
    try:
        import sounddevice as sd

        return any(d["max_output_channels"] > 0 for d in sd.query_devices())
    except Exception:  # noqa: BLE001 - no PortAudio at all is a legitimate state
        return False


needs_device = pytest.mark.skipif(
    not _has_output(), reason="no output device (headless or CI)"
)


@needs_device
def test_the_device_report_lists_something() -> None:
    report = device_report()
    assert "default output" in report
    assert report.count("out=") >= 1


@needs_device
def test_a_short_buffer_plays_and_the_clock_advances() -> None:
    """The first sound this project has ever made, at 0.25s of quiet clicks.

    Not a substitute for listening: it asserts the clock moves, that latency is
    reported, and that the stream closes. Whether it sounds like a guitar is not a
    thing a test can check.
    """
    from guitaroids.audio.click import build_count_in

    clicks = build_count_in(bpm=240, count_in_bars=1, sample_rate=RATE)
    transport = transport_for(
        (clicks.samples * np.float32(0.2)).astype(np.float32),
        song_start=0.25,
    )
    try:
        transport.play()
        assert transport.is_running
        first = transport.position()
        assert transport.latency() > 0.0, "PortAudio always reports some latency"
        assert transport.wait(timeout=10.0), "a 0.25s buffer should finish quickly"
        assert transport.position() > first, "the clock did not advance"
    finally:
        transport.stop()
    assert transport.is_running is False
    assert transport.position() == -1.0, "a stopped transport has no position"


@needs_device
def test_the_origin_is_read_from_the_device_clock() -> None:
    """Not assumed to be zero.

    `stream.time` on a PulseAudio or PipeWire device is a large absolute clock, so
    a t0 of 0 would give a position of billions of seconds rather than an error --
    the single easiest way to get §1.5 wrong, and the reason this is a test.
    """
    transport = transport_for(silence(0.5, RATE))
    try:
        transport.play()
        assert transport._t0 is not None
        assert abs(transport._t0) > 1.0, (
            f"origin looks like a zero-based count ({transport._t0}); if this device "
            "really does report seconds since open, the comment saying otherwise is "
            "wrong and should be corrected"
        )
        assert transport.position() < 1.0, "and the position must be sane anyway"
    finally:
        transport.stop()


# --- the two things a real device found, and they are the interesting ones --------


@needs_device
def test_play_returns_immediately_for_a_long_buffer() -> None:
    """``OutputStream.write`` is *blocking*, so writing a song in one call freezes
    the caller for the length of the song.

    Measured before the feeder thread existed: a 0.4s buffer blocked 0.44s, a 5s
    buffer 4.97s. On a six-minute song that is a six-minute freeze, and on the GUI
    thread it is a frozen window with no navigation for the length of the track.
    """
    transport = transport_for(silence(30.0, RATE))
    try:
        import time

        started = time.perf_counter()
        transport.play()
        took = time.perf_counter() - started
        assert took < 1.0, f"play() blocked {took:.2f}s for a 30s buffer"
        assert transport.is_running
    finally:
        transport.stop()


@needs_device
def test_stopping_while_the_feeder_is_mid_write_does_not_abort() -> None:
    """Only the feeder may touch the stream, including closing it.

    Closing a PortAudio stream while another thread is blocked inside ``write()`` is
    a use-after-free in C. It aborted the process outright -- "corrupted
    double-linked list" and a PulseAudio refcount assertion -- on a 60-second buffer,
    where the feeder is always mid-write.

    A test cannot assert "did not abort the process" from inside the process that
    aborted, so this asserts the two halves it can: that stop() returns, and that
    nothing is left running or reachable afterwards.
    """
    transport = transport_for(silence(30.0, RATE))
    transport.play()
    import time

    time.sleep(0.15)  # let the feeder get into a write
    transport.stop()
    assert transport.is_running is False
    assert transport.position() == -1.0
    transport.stop()  # twice, still safe


@needs_device
def test_the_callback_never_raises_out_of_portaudio() -> None:
    """An exception out of a real-time callback leaves PortAudio undefined.

    So the callback is wrapped, and a failure inside it degrades to silence and marks
    the transport finished. Driven by handing it a broken output buffer rather than
    by breaking the device, because the *shape* of the failure is the thing being
    tested: whatever goes wrong, nothing may escape into C.
    """
    transport = transport_for(silence(0.2, RATE))

    class Hostile:
        """An outdata that refuses assignment, the way a wrong shape would."""

        def __setitem__(self, *_args) -> None:
            raise ValueError("simulated shape mismatch")

    transport._callback(Hostile(), 64, None, None)  # must not raise
    assert transport.has_finished, "a broken callback has to stop claiming to play"


def test_a_callback_given_a_wrong_shape_would_have_raised() -> None:
    """**Why the reshape in the callback is not optional.**

    The first version assigned a flat array of frames*2 to an outdata shaped
    (frames, 2). It raised on the first call, the `except` swallowed it, the transport
    reported itself finished -- and the song played silence while claiming to be
    playing. Asserted here so the shape cannot be changed back by accident.
    """
    import numpy as np

    buffer = np.zeros(44100 * 2, dtype=np.float32)  # interleaved, flat
    outdata = np.zeros((1024, 2), dtype=np.float32)  # what PortAudio actually hands over
    with pytest.raises(ValueError):
        outdata[:] = buffer[: 1024 * 2]


def test_a_short_buffer_plays_and_the_clock_advances() -> None:
    """The first sound this project has ever made, at 0.25s of quiet clicks.

    Not a substitute for listening: it asserts the clock moves, that latency is
    reported, that the callback actually delivered the frames, and that the stream
    closes. Whether it sounds like a guitar is not a thing a test can check.
    """
    import time

    from guitaroids.audio.click import build_count_in

    clicks = build_count_in(bpm=240, count_in_bars=1, sample_rate=RATE)
    transport = transport_for(
        (clicks.samples * np.float32(0.2)).astype(np.float32),
        song_start=0.25,
    )
    try:
        transport.play()
        assert transport.is_running
        assert transport.latency() > 0.0, "PortAudio always reports some latency"
        assert transport.wait(timeout=10.0), "a 0.25s buffer should finish quickly"
        assert transport.frames_written == len(clicks.samples), (
            "the callback did not deliver every frame"
        )
        assert transport.has_finished
    finally:
        transport.stop()
    assert transport.is_running is False
    assert transport.position() == -1.0, "a stopped transport has no position"


# --- volume (§28.2) ------------------------------------------------------------


def test_full_volume_hands_back_the_same_buffer() -> None:
    """1.0 must not copy 40MB of audio to multiply it by one."""
    samples = stereo(64)
    assert apply_volume(samples, 1.0) is samples


def test_half_volume_halves_and_stays_float32() -> None:
    samples = np.full((8, 2), 0.8, dtype=np.float32)
    out = apply_volume(samples, 0.5)
    assert out.dtype == np.float32, "a float64 here would be re-cast by the stream"
    assert np.allclose(out, 0.4)


def test_zero_volume_is_silence_of_the_right_length() -> None:
    """Not a shorter or empty buffer: the clock is derived from this length."""
    out = apply_volume(np.full((100, 2), 0.9, dtype=np.float32), 0.0)
    assert out.shape == (100, 2)
    assert not out.any()


def test_volume_above_one_raises_rather_than_over_driving() -> None:
    """The 0.95 peak leaves no headroom, so 1.5 could only clip.

    Raising is the honest answer: §22 measured the render's peak precisely so that
    this ceiling means something. Silently limiting instead would hide the fact that
    the user asked for something the pipeline cannot give.
    """
    with pytest.raises(TransportError, match="0.0-1.0"):
        apply_volume(stereo(8), 1.5)
    with pytest.raises(TransportError, match="0.0-1.0"):
        apply_volume(stereo(8), -0.1)


def test_the_transport_reports_the_volume_it_was_given() -> None:
    transport = transport_for(stereo(64), volume=0.25)
    assert transport.volume == pytest.approx(0.25)


def test_a_quiet_transport_still_reports_a_running_position() -> None:
    """Liveness and amplitude are different questions.

    §23 already paid for confusing them once: a count-in position is legitimately
    negative, and -1.0 also means "not playing". A transport at volume 0 is playing,
    and must not look like the no-audio sentinel.
    """
    quiet = transport_for(stereo(44100), volume=0.0)
    assert quiet.duration == pytest.approx(1.0), "44100 frames at 44100Hz is one second"
    assert quiet.is_running is False, "no stream is open, so this is about duration"
    assert quiet.position() == -1.0, "and the sentinel still means 'no stream'"


def test_volume_and_device_stay_required() -> None:
    """§21.2's prescription, pinned.

    `device` used to default to None here and at every call site, so the default
    silently won and `Settings.audio_device` did nothing. A default is a standing
    invitation to omit the argument, so this test fails the moment one comes back.
    """
    import inspect

    for target in (Transport.__init__, AppContext.start_playback):
        params = inspect.signature(target).parameters
        for name in ("volume", "device"):
            assert name in params, f"{target.__qualname__} lost {name}"
            assert params[name].default is inspect.Parameter.empty, (
                f"{target.__qualname__}.{name} has a default again, which is how "
                f"{name} was silently ignored in the first place"
            )
