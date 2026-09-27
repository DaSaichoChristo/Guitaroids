"""The click track, pre-rendered, with the test §3.5 asked for.

§3.5 called the click-placement test *"the highest-value test in the project"* and
it had never been written, because there was no audio to click along to. It is
here now, and it is the cheapest possible check on the whole timing chain: if the
clicks are not at ``i * 60 / bpm`` then the note clock is wrong, and nothing else
in the system can tell you as directly.

**Everything here is numpy.** No device, no Qt, no soundfont -- a click is a short
envelope over a sine, and building it by hand is what makes it assertable.

**Pre-rendered, once, before any stream is opened.** The PortAudio callback runs at
real-time priority and must not allocate, so the click cannot be synthesised per
block. It is summed into the backing track here, and the transport plays one
contiguous buffer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..model.chart import Chart

Samples = np.ndarray

#: A click is short and percussive. 40ms is long enough to hear as a transient and
#: short enough that two clicks at 300 BPM do not run into one another.
CLICK_SECONDS = 0.040

#: Click frequencies. The downbeat is an octave above the others, so a player can
#: hear the bar line -- which is the whole reason for having a click rather than a
#: metronome tone.
ACCENT_HZ = 1760.0
BEAT_HZ = 880.0

#: A click is a decaying sine, not a gate on a sine. A rectangular click has
#: broadband splatter that smears over the note being played.
DECAY = 60.0


class ClickError(Exception):
    """A click track could not be built."""


@dataclass(frozen=True, slots=True)
class ClickTrack:
    """Pre-rendered clicks, and where they landed."""

    samples: Samples
    """Stereo float32, same rate as the track it will be added to."""
    sample_rate: int
    indices: tuple[int, ...]
    """Sample index of every click, ascending.

    This is the field the placement test asserts on. Keeping it on the result means
    the test does not have to re-derive the arithmetic it is checking.
    """
    accents: tuple[bool, ...]
    downbeats: int
    beats_per_bar: int


def click_indices(
    *,
    bpm: float,
    count_in_bars: int,
    beats_per_bar: int = 4,
    sample_rate: int = 44100,
) -> tuple[int, ...]:
    """Sample index of each count-in click.

    ``i * sample_rate * 60 / bpm`` -- one click per beat, integer division last, so
    the indices are exact and a long count-in cannot accumulate float drift. The
    downbeat is every ``beats_per_bar``-th click.
    """
    if bpm <= 0:
        raise ClickError(f"bpm must be positive, got {bpm}")
    if count_in_bars < 0:
        raise ClickError(f"count_in_bars cannot be negative, got {count_in_bars}")
    if beats_per_bar < 1:
        raise ClickError(f"beats_per_bar must be at least 1, got {beats_per_bar}")
    total = count_in_bars * beats_per_bar
    seconds_per_beat = 60.0 / bpm
    return tuple(
        int(i * sample_rate * seconds_per_beat) for i in range(total)
    )


def _click(frequency: float, sample_rate: int) -> np.ndarray:
    """One click: a decaying sine, raised-cosine windowed to zero at both ends.

    The window matters. A decaying sine that starts at full amplitude begins with a
    step discontinuity, which is a click of its own -- an audible tick in front of
    the click you meant to place.
    """
    count = max(1, int(CLICK_SECONDS * sample_rate))
    t = np.arange(count, dtype=np.float32) / np.float32(sample_rate)
    body = np.sin(2.0 * np.pi * frequency * t).astype(np.float32)
    body *= np.exp(-t * np.float32(DECAY))
    # Hann-ish window over the whole click, so it starts and ends at zero.
    window = 0.5 - 0.5 * np.cos(
        2.0 * np.pi * np.arange(count, dtype=np.float32) / np.float32(max(1, count - 1))
    )
    return (body * window.astype(np.float32)).astype(np.float32)


def build_count_in(
    *,
    bpm: float,
    count_in_bars: int,
    beats_per_bar: int = 4,
    sample_rate: int = 44100,
    level: float = 0.6,
) -> ClickTrack:
    """Pre-render a count-in: one click per beat, accented on the downbeat."""
    indices = click_indices(
        bpm=bpm,
        count_in_bars=count_in_bars,
        beats_per_bar=beats_per_bar,
        sample_rate=sample_rate,
    )
    if not indices:
        return ClickTrack(
            samples=np.zeros((0, 2), dtype=np.float32),
            sample_rate=sample_rate,
            indices=(),
            accents=(),
            downbeats=0,
            beats_per_bar=beats_per_bar,
        )

    accents = tuple(i % beats_per_bar == 0 for i in range(len(indices)))
    # The buffer must cover the whole count-in, not just the clicks. The last click
    # is one beat *before* the music starts, so reserving only room for the clicks
    # put the first chord on top of the final downbeat: a count-in that is a beat
    # short and a downbeat you never hear separately. `count_in_seconds` is the
    # authority, and the transport uses the same number to know where the music is.
    needed = int(math.ceil(count_in_seconds(
        bpm=bpm, count_in_bars=count_in_bars, beats_per_bar=beats_per_bar
    ) * sample_rate))
    last = max(indices[-1] + int(CLICK_SECONDS * sample_rate), needed, 1)
    out = np.zeros((last, 2), dtype=np.float32)
    for index, accented in zip(indices, accents, strict=True):
        mono = _click(ACCENT_HZ if accented else BEAT_HZ, sample_rate) * np.float32(level)
        out[index : index + len(mono), 0] += mono
        out[index : index + len(mono), 1] += mono
    return ClickTrack(
        samples=out,
        sample_rate=sample_rate,
        indices=indices,
        accents=accents,
        downbeats=sum(accents),
        beats_per_bar=beats_per_bar,
    )


def add_count_in(
    track: Samples,
    *,
    bpm: float,
    count_in_bars: int,
    beats_per_bar: int = 4,
    sample_rate: int = 44100,
    level: float = 0.6,
) -> tuple[Samples, ClickTrack]:
    """Prepend a count-in to a rendered track, leaving room for it.

    Returns the combined buffer and the track that went in, so a caller can report
    where the music starts -- which is not zero, and which the game screen will need
    when it maps a chart position onto a sample.
    """
    clicks = build_count_in(
        bpm=bpm,
        count_in_bars=count_in_bars,
        beats_per_bar=beats_per_bar,
        sample_rate=sample_rate,
        level=level,
    )
    if clicks.samples.size == 0:
        return track, clicks
    out = np.zeros((len(track) + len(clicks.samples), 2), dtype=np.float32)
    out[: len(clicks.samples)] = clicks.samples
    out[len(clicks.samples) :] = track
    return out, clicks


def count_in_seconds(
    *, bpm: float, count_in_bars: int, beats_per_bar: int = 4
) -> float:
    """How long the count-in lasts. The offset between sample 0 and the first note.

    Zero when there is no count-in, so this is safe to subtract unconditionally --
    a common enough mistake (adding it when it is zero) to be worth not being able
    to get wrong.
    """
    if count_in_bars <= 0 or bpm <= 0:
        return 0.0
    return count_in_bars * beats_per_bar * 60.0 / bpm


def beats_per_bar(chart: Chart) -> int:
    """The chart's time signature, with a 4/4 fallback.

    Beats are not beats if the bar is in six. §16.3 noted that beat divisions are
    still drawn from the time signature, and this is the same fact arriving in the
    audio: a click per beat is only a click per beat if the beat count is right.
    """
    numerator, denominator = chart.time_signature
    if numerator < 1 or denominator != 4:
        return 4
    return numerator


def beat_positions(
    chart: Chart, *, bpm: float | None = None, sample_rate: int = 44100
) -> tuple[int, ...]:
    """Sample index of every beat in a song, from its bar lines and time signature.

    Derived from ``chart.bar_lines`` rather than from the tempo, for the same reason
    the tab view uses them (§16.3): a measure boundary in the file is a fact, and
    ``bars * beats * 60 / bpm`` is a guess that is wrong the moment a tab has a
    pickup or a repeat that unrolled unevenly.
    """
    bars = list(chart.bar_lines)
    if not bars:
        return ()
    tempo = float(bpm if bpm is not None else chart.tempo)
    if tempo <= 0:
        return ()
    per_bar = beats_per_bar(chart)
    seconds_per_beat = 60.0 / tempo
    positions: list[int] = []
    for index in range(len(bars)):
        start = bars[index].time
        if index + 1 < len(bars):
            span = max(0.0, bars[index + 1].time - start)
        elif len(bars) > 1:
            # The final bar has no bar line after it to measure against, so its
            # length is taken from the bar before it. Guessing it from the tempo
            # (per_bar * 60/bpm) is wrong for a song that ends on a held bar, which
            # is most songs' last measure, and the clicks would bunch up.
            span = max(0.0, start - bars[index - 1].time)
        else:
            span = per_bar * seconds_per_beat
        for beat in range(per_bar):
            # Evenly divides the *measured* bar, so a bar that is not exactly
            # 4 * 60/bpm long still gets its clicks in the right places.
            positions.append(int(round((start + span * beat / per_bar) * sample_rate)))
    return tuple(positions)
