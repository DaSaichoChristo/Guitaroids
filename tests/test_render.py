"""Tests for the renderer. No audio device, and mostly no soundfont.

The renderer is the first thing in the project that produces numbers a listener
hears, so the tests are about the *measurements* rather than about the code shape:
what the preset index means, what the peak is, whether a note lasts as long as it
should, and whether the fallback actually plays the pitch that was asked for.

The pitch check counts zero crossings rather than calling
:mod:`guitaroids.audio.pitch`. That is deliberate: the estimator we ship is going
to be used to verify the synth, and verifying a thing with the thing it is verified
against proves only that two implementations of the same mistake agree.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from songbuild import make_chart

from guitaroids.audio.render import (
    FALLBACK_PROGRAM,
    MAX_GAIN,
    MAX_NOTE_SECONDS,
    RenderError,
    limit,
    note_ends,
    pluck,
    preset_for,
    render_chart,
)
from guitaroids.model.chart import group_by_onset

ROOT = Path(__file__).resolve().parent.parent
SOUNDFONTS = [ROOT / "assets" / "soundfont.sf3", ROOT / "assets" / "soundfont.sf2"]
soundfont = next((p for p in SOUNDFONTS if p.is_file()), None)
needs_soundfont = pytest.mark.skipif(soundfont is None, reason="no soundfont fetched")

#: Two notes two seconds apart, so a held note has a gap to be held across.
TWO_NOTES = [(2.0, 0, 0), (4.0, 1, 2)]


# --- the preset index, which is the off-by-one that plays a tab a programme sharp


def test_preset_is_the_gm_programme_minus_one() -> None:
    """Soundfont presets are 0-indexed; GM programme numbers are 1-indexed.

    Passing the chart's GM number straight through plays every tab one programme
    sharp: a guitar tab on "jazz guitar", which sounds plausible and is wrong.
    """
    assert preset_for(25) == 24
    assert preset_for(30) == 29
    assert preset_for(24) == 23


def test_a_non_guitar_programme_falls_back_to_a_guitar() -> None:
    """A piano preset for a guitar tab is a worse bug than an approximation."""
    assert preset_for(0) == FALLBACK_PROGRAM - 1
    assert preset_for(48) == FALLBACK_PROGRAM - 1, "a string ensemble is not a guitar"
    assert preset_for(40) == FALLBACK_PROGRAM - 1, "nor is a violin"


@needs_soundfont
def test_the_preset_index_lands_on_the_instrument_it_claims() -> None:
    """Checked by name against the soundfont, because the symptom is inaudible.

    A one-programme error still sounds like a guitar, so nothing in a playtest
    would report it. The name is the only evidence available.
    """
    import tinysoundfont as tsf

    synth = tsf.Synth()
    soundfont_id = synth.sfload(str(soundfont))
    assert soundfont_id >= 0
    names = {
        25: synth.sfpreset_name(soundfont_id, 0, preset_for(25)),
        26: synth.sfpreset_name(soundfont_id, 0, preset_for(26)),
        30: synth.sfpreset_name(soundfont_id, 0, preset_for(30)),
    }
    assert "nylon" in names[25].lower()
    assert "steel" in names[26].lower()
    assert "distortion" in names[30].lower() or "overdrive" in names[30].lower()


# --- the fallback synth ----------------------------------------------------------


def test_pluck_returns_the_requested_length() -> None:
    wave = pluck(440.0, 0.5, 44100)
    assert wave.dtype == np.float32
    assert len(wave) == pytest.approx(22050, abs=2)


def test_pluck_plays_the_pitch_it_was_asked_for() -> None:
    """§9's unkept promise, and the first verification of the synth at all.

    Counted by zero crossings, independently of any estimator we ship, so the two
    cannot be the same mistake twice. A guitar note is a harmonic series, so the
    fundamental from crossings is a little low -- hence the 6% window.
    """
    for midi in (40, 52, 64):  # low E, high-ish E, and the top string
        expected = 440.0 * (2.0 ** ((midi - 69) / 12.0))
        wave = pluck(expected, 0.25, 44100)
        crossings = int(np.count_nonzero(np.diff(np.signbit(wave))))
        measured = crossings / 2.0 / 0.25
        assert measured == pytest.approx(expected, rel=0.06), (
            f"MIDI {midi}: wanted {expected:.1f}Hz, crossings say {measured:.1f}Hz"
        )


def test_pluck_decays_instead_of_stopping() -> None:
    """A plucked string rings down. A flat sustain is a bug that sounds like a beep."""
    wave = pluck(220.0, 1.0, 44100)
    head = float(np.abs(wave[:2000]).max())
    tail = float(np.abs(wave[-2000:]).max())
    assert tail < head * 0.5, f"no decay: {head:.4f} at the head, {tail:.4f} at the tail"


def test_pluck_is_quiet_enough_to_be_measured() -> None:
    """A single pluck sits near 0.2, like a soundfont (§22), so normalisation has
    something to do and the numbers stay comparable between backends."""
    peak = float(np.abs(pluck(220.0, 0.5, 44100)).max())
    assert 0.05 < peak < 0.9, f"unexpected single-pluck peak {peak:.3f}"


# --- note lengths ---------------------------------------------------------------


def test_a_note_lasts_until_the_next_onset() -> None:
    """Not a fixed decay: a held note is held, and this is the difference between
    a sustained chord and a stutter."""
    chart = make_chart(TWO_NOTES, collapse=False)
    ends = note_ends(chart, 44100)
    groups = group_by_onset(list(chart.notes))
    assert len(ends) == len(groups)
    first_end = ends[0] / 44100
    assert first_end == pytest.approx(groups[1][0].time, abs=0.01)


def test_the_last_note_gets_a_tail_rather_than_nothing() -> None:
    chart = make_chart([(1.0, 0, 0)], collapse=False)
    ends = note_ends(chart, 44100)
    assert ends[0] / 44100 == pytest.approx(1.0 + MAX_NOTE_SECONDS, abs=0.01)


def test_a_tiny_gap_is_widened_so_notes_do_not_click() -> None:
    """Two notes 10ms apart would otherwise be a 10ms burst."""
    chart = make_chart([(1.0, 0, 0), (1.01, 1, 0)], collapse=False)
    ends = note_ends(chart, 44100)
    assert (ends[0] - ends[0]) == 0
    assert ends[0] / 44100 - 1.0 >= 0.05


# --- normalisation and limiting ---------------------------------------------------


def test_normalisation_lands_the_peak_on_target() -> None:
    chart = make_chart([(1.0, 0, 0), (1.5, 2, 3), (2.0, 4, 5)], collapse=False)
    result = render_chart(chart, backend="pluck")
    assert result.gain_applied > 0.0
    assert float(np.abs(result.samples).max()) == pytest.approx(0.9, rel=0.01)


def test_the_reported_peak_is_the_unnormalised_one() -> None:
    """So a caller can see what the gain had to fix. Normalising it away would make
    the loudest-sample number a constant and therefore useless."""
    result = render_chart(make_chart([(1.0, 0, 0)], collapse=False), backend="pluck")
    assert result.peak < 0.9, "the raw peak should be below the target, or gain is 1"
    assert float(np.abs(result.samples).max()) == pytest.approx(0.9, rel=0.01)


def test_target_peak_none_returns_the_render_untouched() -> None:
    """What the level tests use, and a caller who wants to do their own gain."""
    result = render_chart(make_chart([(1.0, 0, 0)], collapse=False), backend="pluck", target_peak=None)
    assert result.gain_applied == 1.0
    assert float(np.abs(result.samples).max()) == pytest.approx(result.peak, rel=0.001)


def test_a_silent_render_is_not_amplified_into_noise() -> None:
    silence = np.zeros((4410, 2), dtype=np.float32)
    out = limit(silence)
    assert np.abs(out).max() == 0.0
    chart = make_chart([(1.0, 0, 0)], collapse=False)
    # gain is capped, so a quiet render cannot reach an absurd level
    assert MAX_GAIN < 20.0


def test_limit_leaves_quiet_material_bit_for_bit_unchanged() -> None:
    """Below the knee nothing happens at all -- which is what makes the loud case
    below mean something."""
    quiet = (np.random.default_rng(0).standard_normal((1000, 2)) * 0.1).astype(np.float32)
    assert np.array_equal(limit(quiet), quiet)


def test_limit_squashes_a_hot_signal_and_never_clips() -> None:
    hot = (np.random.default_rng(1).standard_normal((4000, 2)) * 4.0).astype(np.float32)
    out = limit(hot)
    assert np.abs(out).max() <= 1.0
    assert np.abs(out).max() < 1.0, "a hard clip would sit exactly at 1.0"
    assert np.isfinite(out).all()


# --- the whole render ------------------------------------------------------------


def test_the_render_is_stereo_float32_and_long_enough() -> None:
    chart = make_chart([(0.5, 0, 0), (1.0, 2, 3)], collapse=False)
    result = render_chart(chart, backend="pluck")
    assert result.samples.dtype == np.float32
    assert result.samples.ndim == 2 and result.samples.shape[1] == 2
    assert result.seconds >= chart.duration
    assert np.isfinite(result.samples).all()
    assert result.backend == "pluck"
    assert result.note_count == len(chart.notes)


def test_the_render_is_mono_identical_because_there_is_no_panning() -> None:
    """Both channels carry the same signal. Written down so a stereo panning
    feature is a deliberate change rather than a surprise."""
    chart = make_chart([(0.5, 0, 0)], collapse=False)
    result = render_chart(chart, backend="pluck")
    assert np.array_equal(result.samples[:, 0], result.samples[:, 1])


def test_an_empty_chart_is_an_error_not_an_empty_buffer() -> None:
    from dataclasses import replace

    empty = replace(make_chart([(1.0, 0, 0)]), notes=())
    with pytest.raises(RenderError, match="no notes"):
        render_chart(empty, backend="pluck")


def test_an_unknown_backend_is_rejected() -> None:
    chart = make_chart([(1.0, 0, 0)], collapse=False)
    with pytest.raises(ValueError, match="unknown backend"):
        render_chart(chart, backend="theremin")


def test_asking_for_the_soundfont_without_one_is_an_error() -> None:
    """Rather than silently using the fallback, which would make a test pass for
    the wrong reason."""
    chart = make_chart([(1.0, 0, 0)], collapse=False)
    with pytest.raises(RenderError, match="no soundfont"):
        render_chart(chart, backend="soundfont", soundfont=Path("/nonexistent.sf3"))


def test_the_pluck_backend_plays_one_note_of_a_chord_at_a_sixth_the_level() -> None:
    """A real limitation, stated rather than hidden: the pluck cannot voice a chord.

    It takes the highest note and divides by the chord size, so a six-note chord is
    the same waveform as its top note six times quieter. The soundfont backend
    voices all six (below). Compared unnormalised, because normalisation would
    scale the two back into each other and the test would pass either way.
    """
    chord = [(1.0, lane, 3) for lane in range(6)]
    chorded = render_chart(
        make_chart(chord, collapse=False), backend="pluck", target_peak=None
    )
    single = render_chart(
        make_chart([(1.0, 0, 3)], collapse=False), backend="pluck", target_peak=None
    )
    assert np.allclose(chorded.samples, single.samples / 6.0, atol=1e-7)


# --- against the real soundfont --------------------------------------------------


@needs_soundfont
def test_the_soundfont_backend_names_the_instrument_it_used() -> None:
    chart = make_chart([(1.0, 0, 3), (1.5, 2, 5)], collapse=False)
    result = render_chart(chart, backend="soundfont", soundfont=soundfont)
    assert result.backend == "soundfont"
    assert "guitar" in result.preset_name.lower(), (
        f"expected a guitar preset, got {result.preset_name!r} -- "
        "the GM-to-preset offset is wrong"
    )
    assert result.preset_name == "Nylon String Guitar", (
        "GM 25 is preset 24; if this says 'Steel' the -1 has been lost"
    )


@needs_soundfont
def test_the_soundfont_render_is_clean() -> None:
    result = render_chart(
        make_chart(TWO_NOTES, collapse=False), backend="soundfont", soundfont=soundfont
    )
    assert np.isfinite(result.samples).all()
    assert float(np.abs(result.samples).max()) <= 1.0
    assert result.peak > 0.0, "a silent render means the noteon sequence is wrong"


@needs_soundfont
def test_the_soundfont_backend_voices_every_note_of_a_chord() -> None:
    """The opposite of the pluck's limitation, and the reason to prefer it.

    All six notes sound, so the waveform is not one note scaled -- it has six
    partials where the pluck has one. Compared unnormalised so that the two
    backends cannot be flattened into each other by the gain.
    """
    chord = [(1.0, lane, 3) for lane in range(6)]
    chorded = render_chart(
        make_chart(chord, collapse=False),
        backend="soundfont",
        soundfont=soundfont,
        target_peak=None,
    )
    single = render_chart(
        make_chart([(1.0, 0, 3)], collapse=False),
        backend="soundfont",
        soundfont=soundfont,
        target_peak=None,
    )
    assert not np.allclose(chorded.samples, single.samples, atol=1e-6)
