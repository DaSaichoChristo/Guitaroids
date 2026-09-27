"""Tests for the click track, including the one §3.5 asked for.

DESIGN.md §3.5 called this *"the highest-value test in the project"* and it could
not be written until there was audio to click along to. If the clicks are not at
``i * 60 / bpm`` then the note clock is wrong, and nothing else in the system
reports that as directly: a wrong clock produces a song that feels slightly off
rather than one that fails.

Pure numpy, so all of it runs with no sound card, no display and no event loop.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest
from songbuild import make_chart

from guitaroids.model.chart import BarLine

from guitaroids.audio.click import (
    ACCENT_HZ,
    BEAT_HZ,
    CLICK_SECONDS,
    ClickError,
    add_count_in,
    beat_positions,
    beats_per_bar,
    build_count_in,
    click_indices,
    count_in_seconds,
)

#: 120 BPM is two beats per second, so every index is a whole number of seconds
#: and the arithmetic is checkable by eye as well as by assertion.
BPM = 120.0
RATE = 44100


# --- the placement test §3.5 wanted ----------------------------------------------


def test_clicks_land_on_the_beat_grid() -> None:
    """The assertion, verbatim: one click per beat at ``i * 60 / bpm``.

    Integer division last, so this holds for any tempo and any length. A float
    accumulation would drift a few samples over a long count-in, which is exactly
    the kind of error this exists to catch.
    """
    for bpm in (60.0, 76.0, 90.0, 120.0, 127.0, 180.0, 300.0):
        for bars in (1, 2, 4):
            got = click_indices(bpm=bpm, count_in_bars=bars, sample_rate=RATE)
            want = [int(i * RATE * 60.0 / bpm) for i in range(bars * 4)]
            assert list(got) == want, f"{bpm}bpm x {bars} bars"


def test_clicks_are_evenly_spaced_to_within_a_sample() -> None:
    """The gap between clicks is the beat, whatever the tempo.

    Within *one sample*, not exactly: each index is truncated independently by the
    integer division, so at a tempo that does not divide the sample rate the gaps
    alternate by one. The placement test above asserts the exact indices, which is
    the stronger claim, and this one is about the spacing being a beat rather than
    about the truncation being invisible.
    """
    indices = click_indices(bpm=127.0, count_in_bars=3, sample_rate=RATE)
    gaps = [b - a for a, b in zip(indices, indices[1:], strict=False)]
    ideal = RATE * 60.0 / 127.0
    assert all(abs(gap - ideal) <= 1.0 for gap in gaps), f"gaps {gaps} vs {ideal}"
    assert max(gaps) - min(gaps) <= 1, f"gaps wander by more than a sample: {gaps}"


def test_the_first_click_is_at_sample_zero() -> None:
    """The count-in starts now, not one beat from now."""
    assert click_indices(bpm=90.0, count_in_bars=1, sample_rate=RATE)[0] == 0


def test_a_zero_count_in_has_no_clicks() -> None:
    """``count_in_bars`` is 0-2 and 0 is a supported state, not an edge case."""
    assert click_indices(bpm=120.0, count_in_bars=0, sample_rate=RATE) == ()


# --- accents ---------------------------------------------------------------------


def test_the_downbeat_is_accented() -> None:
    track = build_count_in(bpm=BPM, count_in_bars=2, sample_rate=RATE)
    assert track.accents == (True, False, False, False, True, False, False, False)
    assert track.downbeats == 2


def test_the_accent_is_audible() -> None:
    """An accent nobody can hear is not an accent."""
    track = build_count_in(bpm=BPM, count_in_bars=2, sample_rate=RATE)
    downbeat = track.samples[track.indices[0] : track.indices[0] + 200]
    other = track.samples[track.indices[1] : track.indices[1] + 200]
    assert not np.allclose(downbeat, other, atol=1e-6)
    assert ACCENT_HZ == pytest.approx(2 * BEAT_HZ), "the downbeat is an octave up"


def test_an_unusual_time_signature_accents_every_sixth_beat() -> None:
    """Beats are not beats if the bar is in six."""
    track = build_count_in(bpm=BPM, count_in_bars=1, beats_per_bar=6, sample_rate=RATE)
    assert track.accents == (True, False, False, False, False, False)


# --- the sound of it --------------------------------------------------------------


def test_a_click_starts_and_ends_at_zero() -> None:
    """A decaying sine that starts at full amplitude is a step, and a step is a
    click in front of the click."""
    track = build_count_in(bpm=BPM, count_in_bars=1, sample_rate=RATE)
    assert np.abs(track.samples[0]).max() < 1e-6, "the click starts with a step"
    assert np.abs(track.samples[-1]).max() < 1e-6, "and ends with one"


def test_a_click_is_shorter_than_a_fast_beat() -> None:
    """So two clicks at 300 BPM do not run into one another."""
    track = build_count_in(bpm=300.0, count_in_bars=1, sample_rate=RATE)
    gap = track.indices[1] - track.indices[0]
    assert CLICK_SECONDS * RATE < gap


def test_the_click_track_is_stereo_float32_and_not_silent() -> None:
    track = build_count_in(bpm=BPM, count_in_bars=1, sample_rate=RATE)
    assert track.samples.dtype == np.float32
    assert track.samples.shape[1] == 2
    assert np.isfinite(track.samples).all()
    assert np.abs(track.samples).max() > 0.05


# --- adding it to a track --------------------------------------------------------


def test_the_music_starts_after_the_count_in_and_is_untouched() -> None:
    """The buffer is longer by exactly the count-in, and the tail is bit-identical.

    Every chart time is measured from the first sample, so a count-in that shifted
    the music by one sample would put the whole tab one sample out -- which is the
    sort of error that reads as "the timing feels slightly off".
    """
    track = np.ones((RATE, 2), dtype=np.float32) * 0.5
    combined, clicks = add_count_in(track, bpm=BPM, count_in_bars=1, sample_rate=RATE)
    assert len(combined) == len(track) + len(clicks.samples)
    assert np.array_equal(combined[len(clicks.samples) :], track), "the music moved"


def test_adding_nothing_leaves_the_track_alone() -> None:
    track = np.ones((100, 2), dtype=np.float32)
    combined, clicks = add_count_in(track, bpm=BPM, count_in_bars=0, sample_rate=RATE)
    assert combined is track
    assert clicks.indices == ()


def test_the_count_in_length_is_the_offset_from_sample_zero_to_the_music() -> None:
    """The number the game screen needs to map chart time onto samples."""
    for bpm in (60.0, 76.0, 127.0):
        for bars in (1, 2):
            track = np.zeros((RATE, 2), dtype=np.float32)
            combined, clicks = add_count_in(
                track, bpm=bpm, count_in_bars=bars, sample_rate=RATE
            )
            seconds = count_in_seconds(bpm=bpm, count_in_bars=bars)
            # The buffer grew by the count-in plus the tail of the final click.
            assert (len(combined) - RATE) / RATE == pytest.approx(seconds, abs=0.2)


def test_a_zero_count_in_has_no_offset() -> None:
    assert count_in_seconds(bpm=120.0, count_in_bars=0) == 0.0


# --- refusals ---------------------------------------------------------------------


def test_a_zero_tempo_is_refused() -> None:
    for bpm in (0.0, -120.0):
        with pytest.raises(ClickError, match="positive"):
            click_indices(bpm=bpm, count_in_bars=1)


def test_a_negative_count_in_is_refused() -> None:
    with pytest.raises(ClickError, match="negative"):
        click_indices(bpm=120.0, count_in_bars=-1)


def test_a_zero_time_signature_is_refused() -> None:
    with pytest.raises(ClickError, match="at least 1"):
        click_indices(bpm=120.0, count_in_bars=1, beats_per_bar=0)


# --- beat positions from the chart ----------------------------------------------


def test_beat_positions_come_from_the_bar_lines() -> None:
    """Not from ``bars * beats * 60 / bpm``, which is a guess (§16.3).

    At 120 BPM with a 4/4 chart of 16 beats, bar lines are 2s apart and each carries
    four clicks. The count is the check: a tempo-derived version would give the same
    answer here, which is why the uneven-bar test below exists.
    """
    chart = make_chart([(4.0, 0, 0)], tempo=120, beats=16)
    positions = beat_positions(chart)
    assert len(positions) == 4 * 4, "four bars of four beats"
    assert positions[0] == 0
    assert positions[1] == pytest.approx(22050, abs=2), "one beat into a 2s bar"
    assert positions[4] == pytest.approx(88200, abs=2), "the next bar starts at 2s"


def test_beats_land_evenly_inside_a_bar_that_is_not_exactly_four_beats() -> None:
    """Each bar is divided by its own measured span, not by the tempo.

    Bars of 2s, 4s, 2s: the middle one gets beats 1s apart while its neighbours get
    0.5s. A tempo-derived version would put four clicks in the first two seconds of
    the long bar and none in the second half.
    """
    chart = make_chart([(4.0, 0, 0)], tempo=120, beats=16)
    stretched = replace(
        chart,
        bar_lines=(BarLine(0.0), BarLine(2.0), BarLine(6.0), BarLine(8.0)),
    )
    positions = beat_positions(stretched)
    middle = positions[4:8]
    assert middle[0] == pytest.approx(2.0 * RATE, abs=2), "the long bar starts at 2s"
    assert middle[1] - middle[0] == pytest.approx(1.0 * RATE, abs=2)
    assert middle[3] - middle[2] == pytest.approx(1.0 * RATE, abs=2)
    assert middle[3] == pytest.approx(5.0 * RATE, abs=2), "and ends at 6s"
    # The stretch did not leak: the next bar is back to 0.5s beats. (The gap from
    # the long bar's last beat to the next bar's first is 1.0s, because that bar
    # starts at 6.0s -- which is the point of measuring, not of the tempo.)
    assert positions[9] - positions[8] == pytest.approx(0.5 * RATE, abs=2)


def test_the_final_bar_borrows_the_previous_bars_length() -> None:
    """There is no bar line after the last one, so its length is a guess.

    Guessing it from the tempo puts the final bar's four clicks bunched into the
    first half of the bar -- audible as a fast triplet at the end of a song.
    Borrowing the previous bar's measured span is right for the ordinary case,
    which is the one that has to work.
    """
    chart = make_chart([(4.0, 0, 0)], tempo=120, beats=16)
    # Two bars: 0 to 4s. The second borrows 4s, so its beats are 1s apart rather
    # than the 0.5s the tempo implies.
    held = replace(chart, bar_lines=(BarLine(0.0), BarLine(4.0)))
    final = beat_positions(held)[-4:]
    assert final[0] == pytest.approx(4.0 * RATE, abs=2)
    assert final[1] - final[0] == pytest.approx(1.0 * RATE, abs=2), (
        "the final bar's beats were guessed from the tempo"
    )


def test_a_one_bar_chart_still_gets_beats() -> None:
    """The previous-bar fallback needs a previous bar; with none, the tempo is all
    there is, and that is better than silence."""
    chart = replace(make_chart([(1.0, 0, 0)], tempo=120), bar_lines=(BarLine(0.0),))
    assert len(beat_positions(chart)) == 4


def test_beats_per_bar_follows_the_time_signature() -> None:
    chart = make_chart([(1.0, 0, 0)])
    assert beats_per_bar(chart) == 4
    assert beats_per_bar(replace(chart, time_signature=(6, 4))) == 6


def test_a_non_quarter_time_signature_falls_back_to_four() -> None:
    """Six-eight is six beats, but the beat *unit* is different; until that is
    modelled, four is the honest answer and this says so."""
    chart = make_chart([(1.0, 0, 0)])
    assert beats_per_bar(replace(chart, time_signature=(6, 8))) == 4
    assert beats_per_bar(replace(chart, time_signature=(4, 0))) == 4


def test_a_chart_with_no_tempo_has_no_beats() -> None:
    """Rather than dividing by zero."""
    chart = replace(make_chart([(1.0, 0, 0)], tempo=120), tempo=0)
    assert beat_positions(chart) == ()


def test_a_chart_with_no_bar_lines_has_no_beats() -> None:
    chart = replace(make_chart([(1.0, 0, 0)]), bar_lines=())
    assert beat_positions(chart) == ()
