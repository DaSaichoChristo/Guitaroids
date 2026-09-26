"""Locks in the --no-deps install decision for tinysoundfont.

`pyaudio` has no Linux wheel and cannot be built on a plain box, so tinysoundfont
must be installed with --no-deps (see requirements-optional.txt). That is only
safe because pyaudio is a lazy import used solely for real-time playback, and we
render offline.

These tests fail if that ever stops being true -- for example if a future
requirements change pulls pyaudio back in, or if tinysoundfont starts importing it
at module load.
"""

from __future__ import annotations

import importlib.util

import pytest

tsf = pytest.importorskip("tinysoundfont", reason="tinysoundfont not installed")

from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SOUNDFONTS = [
    ROOT / "assets" / "soundfont.sf2",
    ROOT / "assets" / "soundfont.sf3",
]
soundfont = next((p for p in SOUNDFONTS if p.is_file()), None)


def test_pyaudio_is_not_installed() -> None:
    """We install tinysoundfont with --no-deps, so pyaudio should be absent.

    If this fails, something pulled it in -- and it could only have been installed
    by compiling against portaudio.h, which a plain Linux box does not have.
    """
    assert importlib.util.find_spec("pyaudio") is None


def test_tinysoundfont_imports_without_pyaudio() -> None:
    import tinysoundfont

    assert tinysoundfont.Synth is not None


@pytest.mark.skipif(soundfont is None, reason="no soundfont fetched")
def test_soundfont_loads_and_renders() -> None:
    import numpy as np

    synth = tsf.Synth(samplerate=44100)
    sfid = synth.sfload(str(soundfont))
    assert sfid >= 0

    synth.program_select(0, sfid, bank=0, preset=25)  # GM 25, acoustic guitar steel
    synth.noteon(0, 64, 110)

    audio = np.frombuffer(synth.generate_simple(44100), dtype=np.int16)
    assert audio.size > 0
    # int16 range, so a real signal is well above a couple of counts.
    assert np.abs(audio).max() > 100, "rendered silence - soundfont produced no output"


@pytest.mark.skipif(soundfont is None, reason="no soundfont fetched")
def test_render_is_hot_and_needs_post_gain() -> None:
    """Documents the §7.5 finding: soundfonts clip, so gain is applied after.

    If a future soundfont is well-behaved this test should be revisited -- but the
    renderer must keep applying post-gain regardless, since a single quiet
    soundfont must not make every song inaudible.
    """
    import numpy as np

    synth = tsf.Synth(samplerate=44100)
    sfid = synth.sfload(str(soundfont))
    synth.program_select(0, sfid, bank=0, preset=25)
    for key in (40, 45, 50, 55, 59, 64):  # a six-note chord
        synth.noteon(0, key, 100)

    audio = np.frombuffer(synth.generate_simple(44100), dtype=np.int16).astype(np.float32)
    audio /= 32768.0
    assert np.abs(audio).max() > 0.9, "expected a hot render; post-gain is required"


def test_sequencer_needs_no_audio_device() -> None:
    """Sequencer.process() drives the synth without pyaudio or a device.

    This is what makes offline rendering possible at all.
    """
    from tinysoundfont import Sequencer

    assert hasattr(Sequencer, "process")
    assert hasattr(Sequencer, "midi_load")
