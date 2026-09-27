"""A chart becomes audio. The first time this project has made a sound.

Two backends and no third:

- **SoundFont** (tinysoundfont) when a soundfont is present. This is the real one.
- **Pluck** (numpy only) when it is not, so a fresh clone with no 23MB download
  still plays something and the rest of the pipeline can be developed against.

Four things here were measured rather than assumed, and each produces a plausible
wrong answer if you skip it:

**The buffer is stereo float32.** ``generate()`` returns a ``memoryview`` of raw
bytes, so the dtype is whatever the caller assumes. Read as int16 the same render
saturates at 1.000 and looks like a clipping soundfont, which is what §7.5 believed
for a long time (§22).

**Soundfont presets are 0-indexed against a 1-based GM program.** ``preset=25`` is
GM 26, not GM 25. The chart stores ``channel.instrument`` as a 1-based GM
program, so passing it straight through plays every tab one programme sharp: a
guitar tab on "jazz guitar", which sounds plausible and is wrong. The ``- 1`` is
the reason :func:`preset_for` exists as a named function.

**The render is quiet, not hot.** A six-note chord peaks at 0.22, so this *boosts*.
``sfload(gain=...)`` does nothing at all -- gain 0.0 is as loud as 1.0 -- so gain
is applied to the rendered buffer, upwards, and :func:`limit` catches the sum with
the click track.

**A held note ends at the next onset, not at a fixed decay.** Otherwise every note
is clipped to the gap before it and a sustained chord sounds like a stutter.

Pure with respect to Qt, OpenCV and the model: numpy in, numpy out. It depends on
tinysoundfont, which is optional and behind ``--no-deps``, so importing this module
must not require it -- the import is inside the backend that uses it.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from ..model.chart import Chart, group_by_onset

#: Stereo float32 in [-1.0, 1.0], shaped (samples, 2).
Samples = np.ndarray

#: Where a rendered chart should end up, as a peak.
#:
#: **Not a fixed gain.** §22 measured one six-note chord at 0.22 and concluded a
#: 3.5x boost was right. Rendering the *whole* Hotel California tab gives 0.67 --
#: notes ring until the next onset, so a dense chart stacks six of them -- and a
#: fixed 3.5x lands that at 2.35, which the limiter then squashes back down. The
#: result is a chart that is louder but has had its dynamics flattened, which is the
#: artefact a limiter was supposed to prevent.
#:
#: So the gain is computed from what was actually rendered. One constant, measured,
#: in place of a guess, and the applied gain is reported so a caller can see it.
TARGET_PEAK = 0.9

#: Ceiling on the normalisation gain, so a near-silent render (a one-note chart, or
#: a soundfont with a very quiet patch) is not amplified into amplified noise.
MAX_GAIN = 12.0

#: Longest tail we will render for one note, in seconds. A tab with an unreadable
#: tick delta should lose its tail, not ask for minutes of audio.
MAX_NOTE_SECONDS = 12.0

#: Shortest gap between onsets that still sounds like two events, in seconds.
MIN_GAP_SECONDS = 0.05

#: General MIDI programme numbers for the six-string guitar range, 1-based.
#: §8 classifies 24-31 as guitar; songlib only ever offers 24-31.
GUITAR_PROGRAMS = range(24, 32)

#: What to ask for when a chart's instrument is not a guitar. songlib filters the
#: track list to guitar programmes, so this is unreachable from the app -- but a
#: hand-built chart could carry anything, and a piano preset for a guitar tab is a
#: worse bug than an approximation.
FALLBACK_PROGRAM = 25  # GM 25, acoustic guitar (steel)

#: generate() block size in samples. 1024 is 23ms: fine enough that a note starts
#: within a frame of its true time, coarse enough that a five-minute song is not
#: millions of calls.
BLOCK = 1024


class RenderError(Exception):
    """A chart could not be turned into audio."""


@dataclass(frozen=True, slots=True)
class RenderResult:
    """Audio, plus the facts a caller needs in order to check it."""

    samples: Samples
    sample_rate: int
    backend: str
    peak: float
    """Loudest sample *before* gain and limiting, so a caller can see what it fixed."""
    program: int
    note_count: int = 0
    preset_name: str = ""
    gain_applied: float = 1.0
    """The gain :func:`render_chart` chose. Reported, not hidden: it is the number
    that decides how a quiet soundfont and a loud one end up equally audible."""

    @property
    def seconds(self) -> float:
        return len(self.samples) / self.sample_rate if self.sample_rate else 0.0


# --- note geometry --------------------------------------------------------------


def note_ends(chart: Chart, sample_rate: int) -> list[int]:
    """Last sample of every note, one entry per onset group.

    The length of a note is the gap to the next onset, so a held note is held. The
    last onset in the song gets :data:`MAX_NOTE_SECONDS`, and a gap below
    :data:`MIN_GAP_SECONDS` is widened so a pair of fast grace notes does not click.
    """
    groups = group_by_onset(list(chart.notes))
    ends: list[int] = []
    for index, group in enumerate(groups):
        if index + 1 < len(groups):
            gap = groups[index + 1][0].time - group[0].time
        else:
            gap = MAX_NOTE_SECONDS
        tail = min(MAX_NOTE_SECONDS, max(MIN_GAP_SECONDS, gap))
        ends.append(int(round((group[0].time + tail) * sample_rate)))
    return ends


# --- the SoundFont backend ------------------------------------------------------


def preset_for(program: int) -> int:
    """The 0-indexed soundfont preset for a 1-based GM program.

    Presets in a SoundFont are 0-indexed; GM programme numbers are 1-indexed, so
    GM 25 is preset 24. Verified by name in the tests rather than trusted, because
    the failure mode is a tab that sounds slightly wrong and nobody reports it.
    """
    if int(program) not in GUITAR_PROGRAMS:
        program = FALLBACK_PROGRAM
    return int(program) - 1


def _render_soundfont(
    chart: Chart, soundfont_path: Path, sample_rate: int
) -> RenderResult:
    if not soundfont_path.is_file():
        # Checked here rather than left to sfload, which fails with a message about
        # the soundfont's *contents* when the real problem is its path.
        raise RenderError(f"no soundfont at {soundfont_path}")
    import tinysoundfont as tsf

    synth = tsf.Synth(samplerate=sample_rate)
    # sfload, not Synth.start(): start() imports pyaudio, which has no wheel here
    # and cannot be built (requirements-optional.txt). sfload does not.
    soundfont_id = synth.sfload(str(soundfont_path), gain=0.0)
    if soundfont_id < 0:
        raise RenderError(f"could not load the soundfont at {soundfont_path}")

    groups = group_by_onset(list(chart.notes))
    if not groups:
        raise RenderError("the chart has no notes to play")
    ends = note_ends(chart, sample_rate)

    program = FALLBACK_PROGRAM
    preset = preset_for(program)
    try:
        preset_name = synth.sfpreset_name(soundfont_id, 0, preset)
    except Exception:  # noqa: BLE001 - a name is a nicety, not a requirement
        preset_name = ""

    total = max(max(ends), int(chart.duration * sample_rate) + sample_rate)
    out = np.zeros((total, 2), dtype=np.float32)

    # Held notes are (chart index, end sample) pairs. The end has to travel with
    # the note: comparing a held note against the *current* onset's end releases
    # everything on the beat, which is a stutter rather than a held note.
    held: list[tuple[int, int]] = []
    cursor = 0
    for index, group in enumerate(groups):
        onset = int(round(group[0].time * sample_rate))
        if onset > cursor:
            cursor = _advance(out, synth, cursor, min(onset, total))
        # Let finished notes go before striking the next chord, so a release and a
        # new noteon at the same sample are two events rather than one.
        still: list[tuple[int, int]] = []
        for note_index, end in held:
            if end <= onset:
                synth.noteoff(0, chart.notes[note_index].pitch)
            else:
                still.append((note_index, end))
        held = still
        synth.program_select(0, soundfont_id, 0, preset)
        for note in group:
            synth.noteon(0, note.pitch, 110)
            held.append((_index_of(chart, note), ends[index]))
    cursor = _advance(out, synth, cursor, min(max(ends), total))
    synth.notes_off(0)

    peak = float(np.abs(out).max()) if out.size else 0.0
    return RenderResult(
        samples=out,
        sample_rate=sample_rate,
        backend="soundfont",
        peak=peak,
        program=program,
        preset_name=preset_name,
        note_count=len(chart.notes),
    )


def _index_of(chart: Chart, note) -> int:
    """The chart index of a note, by identity -- Notes are frozen dataclasses."""
    for index, candidate in enumerate(chart.notes):
        if candidate is note:
            return index
    raise RenderError("a note from an onset group is not in the chart")


def _advance(out: Samples, synth, start: int, stop: int) -> int:
    """Render ``[start, stop)`` into ``out``; returns the sample index reached."""
    position = start
    while position < stop:
        count = min(BLOCK, stop - position)
        # The dtype is the whole point of this line (§22).
        frame = np.frombuffer(synth.generate(count), dtype=np.float32).reshape(-1, 2)
        out[position : position + frame.shape[0]] = frame
        position += frame.shape[0]
    return position


# --- the numpy-only fallback -----------------------------------------------------


def pluck(frequency: float, seconds: float, sample_rate: int) -> np.ndarray:
    """A plucked string, additively, fully vectorised.

    **Not** Karplus-Strong, and the reason is worth recording: KS is a per-sample
    recurrence, so a pure-Python loop renders a five-minute chart in minutes rather
    than milliseconds, and a fallback slower than the thing it stands in for is not
    a fallback. This is a sum of harmonics under a plucked envelope -- six ``exp``
    calls per note -- which is enough to develop the transport and the clock
    against, and says so rather than pretending.
    """
    count = max(1, int(seconds * sample_rate))
    t = np.arange(count, dtype=np.float32) / np.float32(sample_rate)
    envelope = np.exp(-t * np.float32(3.0))
    wave = np.zeros(count, dtype=np.float32)
    for harmonic in range(1, 7):
        wave += np.float32(1.0 / harmonic) * np.sin(
            2.0 * np.pi * frequency * harmonic * t
        ).astype(np.float32)
    return (wave * envelope * np.float32(0.5)).astype(np.float32)


def _render_pluck(chart: Chart, sample_rate: int) -> RenderResult:
    groups = group_by_onset(list(chart.notes))
    if not groups:
        raise RenderError("the chart has no notes to play")
    ends = note_ends(chart, sample_rate)
    total = max(max(ends), int(chart.duration * sample_rate) + sample_rate)
    out = np.zeros((total, 2), dtype=np.float32)
    for index, group in enumerate(groups):
        start = int(round(group[0].time * sample_rate))
        midi = max(group, key=lambda n: n.pitch)
        frequency = 440.0 * (2.0 ** ((midi.pitch - 69) / 12.0))
        mono = pluck(frequency, max(1, ends[index] - start) / sample_rate, sample_rate)
        # Divide by the chord size so a six-note chord is not six times louder than
        # a single note. The soundfont does this itself; here it is arithmetic.
        mono = mono * np.float32(min(1.0, 0.8 / len(group)))
        stop = min(total, start + len(mono))
        out[start:stop, 0] += mono[: stop - start]
        out[start:stop, 1] += mono[: stop - start]
    peak = float(np.abs(out).max()) if out.size else 0.0
    return RenderResult(
        samples=out,
        sample_rate=sample_rate,
        backend="pluck",
        peak=peak,
        program=FALLBACK_PROGRAM,
        note_count=len(chart.notes),
    )


# --- finding a soundfont, and the entry point ------------------------------------


def find_soundfont() -> Path | None:
    """The first soundfont we can find, or ``None``.

    ``$GUITAROIDS_SOUNDFONT`` first, then the vendor names ``fetch_soundfont.sh``
    leaves in ``assets/``. A soundfont file is a better answer than a format we
    could parse ourselves, which is why the hand-rolled SF2 reader in
    ``play_tab_prototype.py`` is not on this path.
    """
    from ..paths import ASSETS_DIR

    override = os.environ.get("GUITAROIDS_SOUNDFONT")
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None
    for name in ("soundfont.sf3", "soundfont.sf2", "Guitarramelodica.sf2"):
        candidate = ASSETS_DIR / name
        if candidate.is_file():
            return candidate
    return None


def render_chart(
    chart: Chart,
    *,
    sample_rate: int = 44100,
    target_peak: float | None = TARGET_PEAK,
    max_gain: float = MAX_GAIN,
    soundfont: Path | None = None,
    backend: str = "auto",
) -> RenderResult:
    """Render ``chart`` to stereo float32, normalised to ``target_peak``.

    ``backend="auto"`` uses a soundfont when one is available and falls back to the
    numpy pluck otherwise. Pass it explicitly to pin one, which is what the tests
    do so that they are testing the thing they think they are.

    ``target_peak=None`` returns the render exactly as produced, which is what the
    level tests want -- they are asserting on the *unnormalised* peak, and a
    normalised one would make them pass whatever the soundfont does.
    """
    if not chart.notes:
        raise RenderError("the chart has no notes to play")
    if backend not in ("auto", "soundfont", "pluck"):
        raise ValueError(f"unknown backend {backend!r}")

    if backend == "pluck":
        result = _render_pluck(chart, sample_rate)
    else:
        found = find_soundfont() if soundfont is None else soundfont
        if found is None:
            if backend == "soundfont":
                raise RenderError("no soundfont found; fetch one, or use backend='pluck'")
            result = _render_pluck(chart, sample_rate)
        else:
            result = _render_soundfont(chart, found, sample_rate)

    if target_peak is None:
        return result
    return _normalise(result, target_peak=target_peak, max_gain=max_gain)


def _normalise(
    result: RenderResult, *, target_peak: float, max_gain: float, ceiling: float = 0.95
) -> RenderResult:
    """Scale the render so its peak lands on ``target_peak``, then limit.

    A silent render is left alone rather than multiplied by ``max_gain``: silence
    means no soundfont patch was found, and turning that into noise is the opposite
    of helpful.
    """
    if result.peak <= 1e-6:
        return _with_limited_audio(result, ceiling=ceiling)
    gain = min(max_gain, target_peak / result.peak)
    scaled = replace(result, samples=(result.samples * np.float32(gain)).astype(np.float32))
    limited = _with_limited_audio(scaled, ceiling=ceiling)
    return replace(limited, gain_applied=float(gain))


def _with_limited_audio(result: RenderResult, *, ceiling: float = 0.95) -> RenderResult:
    """The result with its samples run through :func:`limit`."""
    return replace(result, samples=limit(result.samples, ceiling=ceiling))


def limit(samples: Samples, *, ceiling: float = 0.95) -> Samples:
    """Soft-knee anything that would clip, and leave quiet material untouched.

    ``tanh`` rather than a hard clip, because a hard clip on a boosted guitar is
    audible as a crackle and this is four lines. Below the knee the signal is
    returned unchanged, so a quiet render is bit-for-bit what was rendered -- which
    is what makes the "did limiting do anything" test meaningful.
    """
    peak = float(np.abs(samples).max()) if samples.size else 0.0
    if peak <= ceiling:
        return samples
    knee = np.float32(math.tanh(1.0))  # maps 1.0 -> 0.76 before rescaling
    shaped = np.tanh(samples / np.float32(peak) / knee) * np.float32(ceiling)
    return np.clip(shaped, -1.0, 1.0).astype(np.float32)
