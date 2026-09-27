"""What note is that? A fundamental-frequency estimate, with no device in it.

This is the module §24 has been pointing at since it was written, and the one piece of
the input half that can be built and **verified** before a microphone is ever opened.
The verification is the point: §24.3 asks for the estimator to be checked against a
real rendered guitar note rather than a synthetic sine, because the failure mode that
matters is not "does it find 440 Hz" but "does it find the note this project actually
renders".

Pure with respect to Qt and to sounddevice: numpy in, numpy or ``None`` out. The
microphone that will feed it lives in ``audio/mic.py`` and owns the device; nothing here
knows a stream exists.

**Method: normalised autocorrelation (McLeod's NSDF).** §24.3 rejected the cheap
version -- six bandpass filters at the open string pitches, envelope followers, loudest
wins -- because it is twenty lines of numpy and only correct for low frets. Measured on
the real library, frets 0-5 are most of the tab, so the filter bank would work on the
first song tried and fail on the next one. A fundamental estimate handles every fret.

**The numbers that set the design**, measured over all three tabs in ``songs/``
(9764 notes):

- MIDI 39-69, which is **77.8-440 Hz**. The bottom is below the open low E: the Sweet
  Child tabs are in a dropped tuning, and 77.8 Hz is B1.
- 24 distinct pitches at most, and **13 of them are reachable on more than one
  string** -- MIDI 49 on three. So a detected fundamental does not identify a lane,
  which is why the judge needs a pitch-keyed index beside its lane-keyed one
  (§24.2), and why nothing here tries to guess a string.

**Window length is latency, and 2048 spends 46ms of the 280ms miss window.** NSDF
needs a few periods to be trustworthy, and the lowest note in the library is 567
samples a period at 44.1kHz -- so 2048 samples holds **3.6 periods**, which is
comfortable rather than marginal. The first draft of this docstring said 82 Hz and
1.9 periods, having divided the window by the wrong period; the corrected figures are
why 2048 is the default and not 1024. Going to 4096 would hold 7.2 periods and cost
another 46ms of latency for accuracy the library does not need.

**Octave errors are the failure that matters**, because a guitar's harmonics are at 2f
and 3f and an estimate that reports 2f matches a *different chart note*, so the player
is marked wrong for playing the right one. The peak-picking below therefore prefers the
*highest* frequency among peaks that are near-equal in clarity, which is the direction
that turns a doubled estimate back into the fundamental.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

#: Samples per analysis window. See the module docstring: 1.9 periods at the library's
#: lowest note, and 46ms of the 140ms miss window spent.
WINDOW = 2048

#: How far the window advances between estimates. Independent of the window length --
#: this is how *often* the player is offered a note, not how old it is.
HOP = 512

#: A peak must be at least this fraction of the best peak's clarity to be considered
#: an octave candidate. Below 0.8 the 3rd harmonic starts winning; at 1.0 an octave
#: error is never corrected.
OCTAVE_CLARITY = 0.85

#: Below this the block is not a note. Judged on RMS rather than clarity because a
#: room's hum has excellent periodicity and no pitch worth reporting.
MIN_RMS = 0.004

#: Below this the estimate is not confident enough to act on. A guitar played close to
#: the microphone clears it easily; a laptop's microphone two rooms away does not, and
#: a wrong note is worse than no note.
MIN_CLARITY = 0.55

#: A guitar's lowest plausible note, as a sanity bound. Below this a "fundamental" is
#: almost certainly a rumble, a DC-ish artefact, or the estimator failing.
MIN_HZ = 60.0

#: ...and its highest, for the same reason. 24th fret on the high E is 1319 Hz, and the
#: library never goes near it.
MAX_HZ = 1400.0


def midi_to_hz(midi: float) -> float:
    """Equal temperament, A4 = 440 Hz = MIDI 69."""
    return 440.0 * (2.0 ** ((midi - 69.0) / 12.0))


def hz_to_midi(hz: float) -> float:
    """A **fractional** MIDI note: 57.0 is A3, 57.5 is 25 cents sharp of it.

    Fractional on purpose. Rounding before comparison throws away the difference
    between "in tune" and "a semitone out", which is most of what a judgement is.
    """
    return 69.0 + 12.0 * math.log2(hz / 440.0)


@dataclass(frozen=True, slots=True)
class PitchEstimate:
    """One estimate, with enough context to decide whether to trust it."""

    hz: float
    """The estimated fundamental."""

    midi: float
    """``hz_to_midi(hz)``, fractional."""

    clarity: float
    """Peak value of the normalised autocorrelation, 0-1. How periodic the block is.

    Not a probability and not comparable between estimators -- it is the NSDF peak,
    which is 1.0 for a clean periodic signal and near 0 for noise. Used to reject
    rather than to weight: a note is either offered to the judge or it is not.
    """

    rms: float
    """Block loudness, so a caller can threshold without recomputing it."""

    def nearest_midi(self) -> int:
        """The nearest equal-tempered note. Always returns one.

        **There is deliberately no tolerance parameter**, and the first version of this
        class had one. A note is at most half a semitone from *some* equal-tempered
        pitch, so a 0.5-semitone tolerance can never reject anything: the knob looked
        like it was guarding something and guarded nothing, which is the same disease
        as a setting nothing reads.

        The meaningful filter is not "is this near *a* note" but "is this near *a note
        in this chart*", and only the judge knows the chart. So the rejection happens
        there: :meth:`~guitaroids.session.judge.GameState.press_pitch` returns ``None``
        for a pitch the song never asked for, and that is where a false estimate goes
        to die. See §24.2.
        """
        return int(round(self.midi))


def _nsdf(window: np.ndarray) -> np.ndarray:
    """McLeod's normalised square-difference function for lags 0..len/2.

    ``n[t] = 2*r[t] / m[t]`` where ``r`` is the autocorrelation and ``m`` the summed
    squares of both windows. Normalising is what makes the peak height comparable
    between a loud note and a quiet one, and what stops a decaying pluck from
    reporting a shorter period as it fades.
    """
    size = len(window)
    # r(tau) for tau = 0, 1, ... : full[W-1:] is exactly that.
    correlation = np.correlate(window, window, mode="full")[size - 1 : size - 1 + size // 2]
    # m(tau) = sum(x[:size-tau]**2) + sum(x[tau:]**2), both from one cumulative sum.
    power = np.concatenate(([0.0], np.cumsum(window.astype(np.float64) ** 2)))
    m = power[size - np.arange(len(correlation))] + (power[size] - power[: len(correlation)])
    with np.errstate(divide="ignore", invalid="ignore"):
        nsdf = 2.0 * correlation / m
    return np.nan_to_num(nsdf, nan=0.0, posinf=0.0, neginf=0.0)


def _key_maxima(nsdf: np.ndarray) -> list[int]:
    """Positive peaks, the way McLeod picks them: one per positive excursion.

    Taking the single largest value in the whole function is not enough, because a
    periodic signal has a large peak at every multiple of its period. What identifies
    the period is the *first* maximum after each crossing of zero, so those are the
    candidates and the rest are harmonics of the answer.
    """
    peaks: list[int] = []
    lag = 1
    limit = len(nsdf)
    while lag < limit:
        if nsdf[lag] > 0 and nsdf[lag] >= nsdf[lag - 1]:
            # Walk to the top of this excursion.
            while lag + 1 < limit and nsdf[lag + 1] >= nsdf[lag]:
                lag += 1
            if lag + 1 < limit and nsdf[lag + 1] < nsdf[lag]:
                peaks.append(lag)
        lag += 1
    return peaks


def _interpolate(nsdf: np.ndarray, lag: int) -> float:
    """Sub-sample the peak, so the estimate is not quantised to whole samples.

    At 82 Hz one sample is 4.4 cents, which is audible as flatness and enough to push
    a note outside a quarter-tone tolerance near the edges of a scale.
    """
    if lag <= 0 or lag + 1 >= len(nsdf):
        return float(lag)
    left, centre, right = nsdf[lag - 1], nsdf[lag], nsdf[lag + 1]
    denominator = left - 2.0 * centre + right
    if denominator == 0.0:
        return float(lag)
    shift = 0.5 * (left - right) / denominator
    return lag + max(-0.5, min(0.5, shift))


def estimate(
    block: np.ndarray,
    sample_rate: int,
    *,
    window: int = WINDOW,
    min_clarity: float = MIN_CLARITY,
    min_rms: float = MIN_RMS,
) -> PitchEstimate | None:
    """The fundamental of ``block``, or ``None`` if there is not confidently one.

    ``None`` is a normal answer, not a failure: silence, a room, a fret buzz, a
    transient between notes. The caller is expected to treat "no note" as ordinary --
    a microphone hears a great deal that is not a note, and a game that invented one
    for every noise would be unplayable.

    Takes the **most recent** ``window`` samples of ``block`` when it is longer, so a
    caller can pass a rolling buffer of any size and get the freshest answer.
    """
    data = np.asarray(block, dtype=np.float64).reshape(-1)
    if len(data) < window:
        return None
    data = data[-window:]

    rms = float(np.sqrt(np.mean(data**2)))
    if rms < min_rms:
        return None
    data = data - float(np.mean(data))  # DC offset reads as a huge sub-bass peak

    nsdf = _nsdf(data)
    peaks = _key_maxima(nsdf)
    if not peaks:
        return None

    best = max(peaks, key=lambda lag: nsdf[lag])
    clarity = float(nsdf[best])
    if clarity < min_clarity:
        return None

    # The octave correction. If a peak *earlier* than the best one -- that is, at a
    # higher frequency, so a harmonic -- is nearly as clear, it is the real
    # fundamental and the best peak is its double. Taking the highest frequency among
    # near-equal peaks is the direction that fixes a doubled estimate, at the cost of
    # occasionally preferring a slightly weak harmonic over a slightly weak
    # fundamental; MIN_CLARITY then rejects the block if that was the wrong call.
    threshold = clarity * OCTAVE_CLARITY
    chosen = min((lag for lag in peaks if nsdf[lag] >= threshold), default=best)
    lag = _interpolate(nsdf, chosen)

    if lag <= 0:
        return None
    hz = sample_rate / lag
    if not MIN_HZ <= hz <= MAX_HZ:
        return None
    return PitchEstimate(
        hz=float(hz),
        midi=hz_to_midi(hz),
        clarity=clarity,
        rms=rms,
    )
