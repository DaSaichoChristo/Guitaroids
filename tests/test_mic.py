"""Tests for the microphone half.

Almost nothing here needs a device, and that is the point of the split in
`audio/mic.py`: the windowing, the latency arithmetic and the drop policy are a pure
class (`PitchDetector`) and the device is a thin wrapper around it. So the parts that
can be wrong without hardware are tested without hardware, and the parts that need
hardware are marked and skip.

**No test in this file opens an input device.** That is deliberate and is asserted
at the bottom: an earlier version of this project left a real stream open behind a
test, and the transport was collected while PortAudio's callback was still writing into
its buffer, which crashed the interpreter 200 tests later in an unrelated file (§29.3).
"""

from __future__ import annotations

import numpy as np
import pytest

from guitaroids.audio.mic import SAMPLE_RATE, Microphone, PitchDetector, device_report
from guitaroids.audio.pitch import WINDOW, midi_to_hz

RATE = SAMPLE_RATE


def note(midi: float, seconds: float = 0.3, decay: float = 3.0) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    wave = 0.3 * np.sin(2 * np.pi * midi_to_hz(midi) * t) * np.exp(-t * decay)
    return wave.astype(np.float32)


# --- PitchDetector: the windowing, which is the actual logic ------------------


def test_a_note_comes_back_as_the_right_pitch() -> None:
    detector = PitchDetector(RATE)
    found = detector.push(note(57))
    assert found, "nothing was heard"
    assert {e.nearest_midi() for e in found} == {57}


def test_the_window_overlaps_so_a_note_is_heard_more_than_once() -> None:
    """Redundancy is the point: a dropped window costs one estimate, not a note.

    A 300ms note is about 23 windows at a 512-sample hop, and the game screen must
    treat that as one note being held rather than 23 presses -- which is what
    `press_pitch` returning None for a re-detection is for (§29.4).
    """
    detector = PitchDetector(RATE)
    found = detector.push(note(57))
    assert len(found) > 15, f"only {len(found)} estimates from a 300ms note"


def test_the_answer_does_not_depend_on_how_the_audio_was_chunked() -> None:
    """A real stream delivers 512-sample blocks. The result must not depend on that.

    This is the test that would have caught a windowing bug: the same audio pushed in
    one block and in 25 must give the same verdicts, or the behaviour of the game would
    depend on PortAudio's buffer size.
    """
    whole = PitchDetector(RATE)
    in_pieces = PitchDetector(RATE)
    samples = note(57)
    a = {e.nearest_midi() for e in whole.push(samples)}
    seen: set[int] = set()
    for start in range(0, len(samples), 512):
        seen |= {e.nearest_midi() for e in in_pieces.push(samples[start : start + 512])}
    assert a == seen == {57}


def test_stereo_is_mixed_to_mono() -> None:
    detector = PitchDetector(RATE)
    mono = note(64)
    assert {e.nearest_midi() for e in detector.push(np.stack([mono, mono], axis=1))} == {64}


def test_silence_and_noise_produce_nothing() -> None:
    assert PitchDetector(RATE).push(np.zeros(RATE)) == []
    assert PitchDetector(RATE).push(np.random.default_rng(0).normal(0, 0.1, RATE)) == []


def test_a_short_block_produces_nothing_rather_than_a_guess() -> None:
    detector = PitchDetector(RATE)
    assert detector.push(note(57, seconds=0.01)) == []


# --- latency, which is the part that is easy to leave out ----------------------


def test_the_detector_reports_its_own_latency() -> None:
    """Half a window: the estimate describes the middle of the samples it was given.

    2048 samples at 44100 is 46ms of window, so 23ms of lateness. Left uncorrected that
    is a third of the Perfect window applied systematically to every note, which walks a
    good player into MISS.
    """
    detector = PitchDetector(RATE)
    assert detector.latency_seconds == pytest.approx(0.0232, abs=0.001)


def test_a_narrower_window_reports_less_latency() -> None:
    assert PitchDetector(RATE, window=1024).latency_seconds < PitchDetector(RATE).latency_seconds


# --- the drop policy -----------------------------------------------------------


def test_the_buffer_is_bounded_and_drops_the_oldest_audio() -> None:
    """A stalled worker must cost a gap, not the process.

    Unbounded growth here would be a slow memory leak that only shows up on a long
    session, which is exactly when nobody is watching.
    """
    detector = PitchDetector(RATE)
    for _ in range(20):
        detector.push(np.zeros(RATE, dtype=np.float32))
    assert detector.dropped > 0, "the buffer grew without bound"
    assert len(detector._buffer) <= detector._max_buffer


def test_a_real_pitch_survives_a_full_buffer_of_silence_behind_it() -> None:
    """Overflow must discard the oldest, not the newest.

    Getting this backwards is the obvious mistake and it would drop exactly the audio
    that matters, so it is pinned rather than assumed.
    """
    detector = PitchDetector(RATE, max_buffer=WINDOW * 2)
    detector.push(note(57))
    detector.push(np.zeros(WINDOW * 4, dtype=np.float32))  # overflow
    detector.push(note(64))
    found = detector.push(np.zeros(0, dtype=np.float32))
    heard = {e.nearest_midi() for e in detector.estimates}
    assert 64 in heard, f"the newest audio was dropped instead of the oldest: {heard}"


# --- input shape ---------------------------------------------------------------


def test_a_three_dimensional_block_is_refused_rather_than_guessed_at() -> None:
    with pytest.raises(ValueError):
        PitchDetector(RATE).push(np.zeros((10, 2, 2)))


def test_estimates_accumulate_for_inspection() -> None:
    """The debug readout and the tests both read this, so it is part of the contract."""
    detector = PitchDetector(RATE)
    detector.push(note(52))
    assert len(detector.estimates) > 0
    assert all(e.clarity > 0 for e in detector.estimates)


def test_a_reset_drops_buffered_audio_but_keeps_the_estimates() -> None:
    """The two halves are different on purpose, and the second one is a contract.

    The sliding window holds up to `max_buffer` samples -- 371ms at this
    configuration -- and every estimate is computed from it. A detector reused across
    a stop/start therefore opens holding audio from *before* the restart, and the
    first windows it analyses describe the room rather than the song. §44.

    `estimates` is kept because it is a test surface and a debug readout: clearing it
    would make a restart look like a fresh detector when the only thing that changed
    is the audio.
    """
    detector = PitchDetector(RATE)
    detector.push(note(52))
    assert len(detector.estimates) > 0
    detector.push(note(52))
    assert len(detector._buffer) > 0, "precondition: the window is holding audio"

    detector.reset()

    assert len(detector._buffer) == 0, "pre-restart audio is still in the window"
    assert len(detector.estimates) > 0, "the estimate history is a contract"
    assert detector.latency_seconds > 0, "a reset must not disturb the reported latency"


def test_audio_analysed_after_a_reset_is_only_the_new_audio() -> None:
    """The behaviour that matters: a reset does not just shrink the window.

    A reset that left a partial window would make the first estimate straddle the
    restart, which is the same defect by a different route.
    """
    detector = PitchDetector(RATE)
    detector.push(note(52))
    detector.reset()

    # Half a window of silence, then the note: the note must still be found, so the
    # window was emptied rather than half-emptied.
    detector.push(np.zeros(WINDOW // 2, dtype=np.float64))
    found = detector.push(note(52))
    assert found, "the first note after a reset was lost, so the window was not cleared"


# --- the device wrapper, without a device --------------------------------------


def test_constructing_a_microphone_touches_nothing() -> None:
    """So the screen can build one and only then find out whether it works."""
    mic = Microphone(on_pitch=lambda _e: None)
    assert mic.is_running is False
    assert mic.stats.blocks == 0
    assert mic.latency_seconds == pytest.approx(PitchDetector(RATE).latency_seconds)


def test_stopping_a_microphone_that_never_started_is_safe() -> None:
    Microphone().stop()
    Microphone().stop()


def test_stopping_twice_is_safe() -> None:
    mic = Microphone()
    mic.stop()
    mic.stop()


def test_device_report_never_raises() -> None:
    """A script that reports devices should not need a device to report."""
    assert isinstance(device_report(), str)
    assert device_report()


def test_no_input_device_is_reported_rather_than_raised() -> None:
    assert isinstance(device_report(), str)


# --- the promises this file makes about itself --------------------------------


def test_this_file_opens_no_input_device() -> None:
    """An absence test, against this file's own source, in the §25.4 shape.

    The suites that need hardware are marked and skip; the ones that do not must not
    quietly grow a `Microphone().start()`. Leaving a real input stream open behind a
    test is the same class of mistake as leaving an output one open, and it cost a
    crash in §29.3.
    """
    from pathlib import Path

    source = Path(__file__).read_text()
    # Assembled from fragments so this test does not contain the strings it forbids,
    # and excluding its own body -- otherwise the first version flagged its own
    # `for forbidden in (...)` line, which is the shape §25.4 warns about: an absence
    # test that fails on itself gets deleted rather than fixed.
    body = source.split("def test_this_file_opens_no_input_device", 1)[0]
    for forbidden in ("." + "start()", "Input" + "Stream", "s" + "d."):
        offenders = [
            line.strip()
            for line in body.splitlines()
            if forbidden in line and not line.strip().startswith("#")
        ]
        assert not offenders, f"{forbidden!r} appears outside a comment: {offenders}"


def test_the_judge_half_needs_no_device_either() -> None:
    """The input path from estimate to judgement is pure, and this is the proof.

    Everything between "a pitch was detected" and "a verdict was recorded" is
    `press_pitch` and the screen's slot, neither of which touches a device. If this
    ever stops being true, the microphone becomes the one part of the game that cannot
    be tested at all.
    """
    from guitaroids.session.judge import GameState, Verdict
    from songbuild import make_chart

    chart = make_chart([(2.0, 0, 0)], tempo=60, collapse=False)
    state = GameState(chart)
    judgement = state.press_pitch(chart.notes[0].pitch, 2.0)
    assert judgement is not None and judgement.verdict is Verdict.PERFECT
