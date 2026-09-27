"""`Microphone.start()` -- the one place in the suite allowed to call it (§44).

`tests/test_mic.py` carries an absence test in the §25.4 shape: its own source may not
contain `.start()`, `InputStream` or `sd.`, because leaving a real input stream open
behind a test cost a crash in §29.3. That guard is worth more than a convenient place
to put these two, so they live here instead, and **nothing in this file opens a
device**: `sounddevice.InputStream` is replaced with a fake for the duration of each
test, which is the only reason calling `start()` at all is safe.

The move is not a dodge. The absence test scans the source *before* its own
definition, so appending below it would have satisfied the letter of the guard while
leaving the file that states the rule looking broken. Splitting the file keeps both
true at once: `test_mic.py` never calls `start()`, and the tests that must live here
say why they are allowed to.
"""

from __future__ import annotations

import numpy as np
import pytest

from guitaroids.audio.mic import SAMPLE_RATE, Microphone
from guitaroids.audio.pitch import midi_to_hz

#: Defined here rather than imported from `test_mic`: a test module is not a shared
#: library, and a cross-test import that happens to work because `conftest.py` puts the
#: tests directory on `sys.path` is a latent breakage. Eight duplicated lines are
#: cheaper than that.
RATE = SAMPLE_RATE


def note(midi: float, seconds: float = 0.3, decay: float = 3.0) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    wave = 0.3 * np.sin(2 * np.pi * midi_to_hz(midi) * t) * np.exp(-t * decay)
    return wave.astype(np.float32)


def test_opening_the_stream_drops_the_previous_streams_buffer(monkeypatch) -> None:
    """The wiring: reopening the device is what makes the reset necessary, so the
    reset belongs in `start()` rather than in the caller.

    `sounddevice.InputStream` is replaced rather than allowed to open, because this
    suite never touches a real device -- and `start()` imports `sounddevice` *inside*
    the method, so patching the module attribute is the only seam there is.
    """
    import sounddevice as sd

    opened: list[bool] = []

    class FakeInputStream:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            self.callback = kwargs.get("callback")
            opened.append(True)

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(sd, "InputStream", FakeInputStream)

    mic = Microphone(on_pitch=lambda _e: None)
    mic.detector.push(note(52))
    assert len(mic.detector._buffer) > 0, "precondition: the window holds audio"

    mic.start()
    try:
        assert opened, "the device was never opened"
        assert len(mic.detector._buffer) == 0, (
            "reopening the stream left the previous stream's audio in the window, so "
            "the first estimates of the new run describe the room"
        )
    finally:
        mic.stop()


def test_starting_an_already_running_microphone_does_not_reset_it(monkeypatch) -> None:
    """The idempotence guard comes first, and it has to.

    A second `start()` on a live microphone returns before the reset, so a screen
    that reloads without leaving does not throw away the window it is actively
    filling.
    """
    import sounddevice as sd

    class FakeInputStream:
        def __init__(self, **kwargs) -> None:  # noqa: ANN003
            pass

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr(sd, "InputStream", FakeInputStream)

    mic = Microphone(on_pitch=lambda _e: None)
    mic.start()
    try:
        mic.detector.push(note(52))
        held = len(mic.detector._buffer)
        assert held > 0, "precondition: the window holds audio"

        mic.start()  # must be a no-op
        assert len(mic.detector._buffer) == held, (
            "a second start() on a live microphone reset the window it is filling"
        )
    finally:
        mic.stop()
