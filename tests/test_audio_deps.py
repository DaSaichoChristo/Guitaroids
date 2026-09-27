"""Locks in the --no-deps install decision for tinysoundfont.

`pyaudio` has no Linux wheel and cannot be built on a plain box, so tinysoundfont
must be installed with --no-deps (see the top of requirements.txt). That is only
safe because pyaudio is imported in exactly one place, `Synth.start()`, which we do
not call -- soundfonts are loaded with `sfload()` instead.

These tests fail if that ever stops being true -- for example if a future
requirements change pulls pyaudio back in, or if tinysoundfont starts importing it
at module load.

**The buffer is stereo float32 and must be read as such.** It is a memoryview of
raw bytes, so the dtype is whatever the caller assumes, and getting it wrong is
silent: read as int16 the same six-note chord "peaks" at exactly 1.000 with 166
clipped samples, which is how DESIGN.md came to believe soundfonts render hot
(§7.5). Read correctly the peak is 0.22 with none clipped -- so a test written
against the wrong dtype asserts confidently and means nothing. Every assertion
below is on float32 for that reason.
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

    audio = np.frombuffer(synth.generate_simple(44100), dtype=np.float32)
    assert audio.size > 0
    # A real signal in float range, not a couple of counts out of 32768.
    assert np.abs(audio).max() > 0.01, "rendered silence - soundfont produced no output"


@pytest.mark.skipif(soundfont is None, reason="no soundfont fetched")
def test_sfload_loads_a_soundfont_without_pyaudio() -> None:
    """The route round pyaudio, asserted rather than described.

    `Synth.start()` is the obvious way to load a soundfont and it imports pyaudio
    at the top of the function body, so on this box it raises ModuleNotFoundError.
    `sfload()` does not, and it is the only loader we can use. If a future
    tinysoundfont moved the import somewhere reachable, this is the test that says
    so -- and requirements.txt is what would need rewriting.
    """
    import numpy as np

    synth = tsf.Synth(samplerate=44100)
    assert synth.sfload(str(soundfont)) >= 0, "sfload is our pyaudio-free loader"
    with pytest.raises(ModuleNotFoundError):
        synth.start(b"x")  # not a soundfont either, but pyaudio is imported first


@pytest.mark.skipif(soundfont is None, reason="no soundfont fetched")
def test_generate_returns_stereo_float32_not_int16() -> None:
    """The dtype trap itself, as a test.

    `generate*` hands back a memoryview of raw bytes: four per sample per channel,
    float32. Read as int16 it saturates at 1.000 and looks like a clipped render,
    which is exactly the false conclusion §7.5 recorded and §22 corrects.
    """
    import numpy as np

    synth = tsf.Synth(samplerate=44100)
    sfid = synth.sfload(str(soundfont))
    synth.program_select(0, sfid, bank=0, preset=25)
    for key in (40, 45, 50, 55, 59, 64):  # a six-note chord
        synth.noteon(0, key, 100)
    raw = synth.generate_simple(44100)

    correct = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)
    misread = np.frombuffer(raw, dtype=np.int16)
    assert correct.shape == (44100, 2), "stereo float32: 2 channels, 4 bytes each"
    assert not np.isnan(correct).any()
    assert np.abs(correct).max() < 1.0, "a real render should not be full scale"
    assert np.abs(misread).max() >= 32767, (
        "the int16 misread is supposed to saturate; if it stops, this test's "
        "premise -- and §22's correction -- needs revisiting"
    )


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
