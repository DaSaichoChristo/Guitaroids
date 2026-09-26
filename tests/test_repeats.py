"""Tests for repeat unrolling.

These run with no ``.gp5`` file and no hardware, which is the point: the unroller is
the least verifiable part of the import path (DESIGN.md §6.5), so it is exercised
against hand-built ``MeasureHeader``-shaped objects.

Fixture values follow the *stored* encoding, where guitarpro has already subtracted
one from the file's pass count::

    stored  1 -> 2 passes   (the standard double repeat)
    stored  2 -> 3 passes
    stored -1 -> no repeat
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from guitaroids.model.repeats import (
    MAX_EMITTED_MEASURES,
    MAX_REPEAT_DEPTH,
    RepeatStructureError,
    has_repeats,
    unroll_repeats,
)


@dataclass
class Header:
    """Minimal stand-in for guitarpro.models.MeasureHeader."""

    number: int
    isRepeatOpen: bool = False
    repeatClose: int = -1
    repeatAlternative: int = 0


def h(number: int, **kwargs) -> Header:
    return Header(number, **kwargs)


def numbered(order: list[int]) -> list[int]:
    """Zero-based indices to 1-based measure numbers, for readable assertions."""
    return [index + 1 for index in order]


# --- the stored-value interpretation itself ------------------------------------


@pytest.mark.parametrize(
    "stored,passes",
    [(-1, 1), (0, 1), (1, 2), (2, 3), (4, 5)],
)
def test_stored_to_pass_mapping(stored: int, passes: int) -> None:
    """Pin the stored -> passes mapping.

    Derived from ``gp5.py:327-328``, not yet confirmed against a real tab. If this
    test needs changing, the real tabs disagree with us -- see
    ``repeats._repeat_passes``. Note ``-1`` still plays the measure once: it means
    "not a close", not "emit nothing".
    """
    headers = [h(1, isRepeatOpen=True, repeatClose=stored)]
    assert numbered(unroll_repeats(headers)) == [1] * passes


# --- no repeats ---------------------------------------------------------------


def test_plain_sequence_passes_through() -> None:
    assert numbered(unroll_repeats([h(1), h(2), h(3), h(4)])) == [1, 2, 3, 4]


def test_has_repeats_false_for_plain() -> None:
    assert has_repeats([h(1), h(2)]) is False


def test_empty_sequence() -> None:
    assert unroll_repeats([]) == []


# --- basic repeats ------------------------------------------------------------


def test_single_repeat_plays_section_twice() -> None:
    # |: a :| b c
    assert numbered(unroll_repeats([h(1, isRepeatOpen=True, repeatClose=1), h(2), h(3)])) == [
        1,
        1,
        2,
        3,
    ]


def test_three_pass_repeat() -> None:
    headers = [h(1, isRepeatOpen=True, repeatClose=2), h(2), h(3)]
    assert numbered(unroll_repeats(headers)) == [1, 1, 1, 2, 3]


def test_repeat_covering_two_measures() -> None:
    # |: a b :| c  -- the close barline ends measure b
    headers = [h(1, isRepeatOpen=True), h(2, repeatClose=1), h(3)]
    assert numbered(unroll_repeats(headers)) == [1, 2, 1, 2, 3]


def test_two_independent_sections() -> None:
    """Regression: the second section must not bind to the first section's open.

    This is the case that a backward scan without a floor gets wrong, producing
    a b a b c c d instead of a a b c c d.
    """
    headers = [
        h(1, isRepeatOpen=True, repeatClose=1),
        h(2),
        h(3, isRepeatOpen=True, repeatClose=1),
        h(4),
    ]
    assert numbered(unroll_repeats(headers)) == [1, 1, 2, 3, 3, 4]


def test_three_independent_sections() -> None:
    headers = [
        h(1, isRepeatOpen=True, repeatClose=1),
        h(2),
        h(3, isRepeatOpen=True, repeatClose=1),
        h(4),
        h(5, isRepeatOpen=True, repeatClose=1),
        h(6),
    ]
    assert numbered(unroll_repeats(headers)) == [1, 1, 2, 3, 3, 4, 5, 5, 6]


# --- the -1 sentinel ----------------------------------------------------------


def test_repeat_close_minus_one_is_not_a_close() -> None:
    headers = [h(1), h(2, repeatClose=-1), h(3)]
    assert numbered(unroll_repeats(headers)) == [1, 2, 3]


def test_has_repeats_ignores_sentinel() -> None:
    assert has_repeats([h(1, repeatClose=-1)]) is False


# --- alternative endings ------------------------------------------------------


def test_single_ending_taken_on_final_pass() -> None:
    # |: a :| b1 c
    headers = [
        h(1, isRepeatOpen=True, repeatClose=1),
        h(2, repeatAlternative=1),
        h(3),
    ]
    assert numbered(unroll_repeats(headers)) == [1, 1, 2, 3]


def test_two_endings_last_one_wins() -> None:
    """Regression: both endings must not play in sequence.

    Straight-through playback of |: a :| b1 b2 c is a a b2 c, not a a b1 b2 c.
    """
    headers = [
        h(1, isRepeatOpen=True, repeatClose=1),
        h(2, repeatAlternative=1),
        h(3, repeatAlternative=2),
        h(4),
    ]
    assert numbered(unroll_repeats(headers)) == [1, 1, 3, 4]


def test_three_endings_last_one_wins() -> None:
    headers = [
        h(1, isRepeatOpen=True, repeatClose=1),
        h(2, repeatAlternative=1),
        h(3, repeatAlternative=2),
        h(4, repeatAlternative=3),
        h(5),
    ]
    assert numbered(unroll_repeats(headers)) == [1, 1, 4, 5]


def test_ending_after_close_is_reached_once() -> None:
    headers = [
        h(1, isRepeatOpen=True, repeatClose=1),
        h(2, repeatAlternative=1),
        h(3, repeatAlternative=2),
    ]
    assert numbered(unroll_repeats(headers)) == [1, 1, 3]


def test_ending_with_multi_measure_body() -> None:
    # |: a b :| c1 c2 d  ->  a b a b c2 d
    headers = [
        h(1, isRepeatOpen=True),
        h(2, repeatClose=1),
        h(3, repeatAlternative=1),
        h(4, repeatAlternative=2),
        h(5),
    ]
    assert numbered(unroll_repeats(headers)) == [1, 2, 1, 2, 4, 5]


# --- malformed input ----------------------------------------------------------


def test_unpaired_close_plays_once() -> None:
    """Hand-edited tabs close a section they never opened.

    There is nothing to repeat back to, so the measure plays once. Inventing a
    repeat here would be more surprising than ignoring the stray barline.
    """
    assert numbered(unroll_repeats([h(1), h(2), h(3, repeatClose=1)])) == [1, 2, 3]


def test_unpaired_open_plays_once() -> None:
    """|: with no close anywhere -- the tail plays through normally."""
    headers = [h(1, isRepeatOpen=True), h(2), h(3)]
    assert numbered(unroll_repeats(headers)) == [1, 2, 3]


def test_close_at_very_first_measure() -> None:
    assert numbered(unroll_repeats([h(1, repeatClose=1), h(2)])) == [1, 2]


def test_runaway_sequence_is_rejected() -> None:
    """A wide section with a big repeat count must raise, not exhaust memory."""
    headers = [h(i + 1, isRepeatOpen=(i == 0)) for i in range(100)]
    headers[-1] = h(100, repeatClose=126)  # 127 passes x 100 measures = 12,700
    with pytest.raises(RepeatStructureError, match="exceeded"):
        unroll_repeats(headers)


def test_realistic_long_tab_completes() -> None:
    """A long but sane tab must survive the guards."""
    headers = [h(i + 1, isRepeatOpen=(i % 8 == 0)) for i in range(200)]
    headers[7] = h(8, repeatClose=2)
    result = unroll_repeats(headers)
    assert len(result) < MAX_EMITTED_MEASURES
    assert result.count(0) >= 2


def test_guards_are_bounded_sensibly() -> None:
    assert 1_000 < MAX_EMITTED_MEASURES < 1_000_000
    assert 1 <= MAX_REPEAT_DEPTH <= 64


def test_only_the_chosen_ending_is_kept() -> None:
    """Sanity invariant over a mixed tab.

    Every body measure, and the *highest-numbered* ending, must appear. The
    lower-numbered endings are intentionally dropped, so they are excluded here.
    """
    headers = [
        h(1, isRepeatOpen=True, repeatClose=1),
        h(2),
        h(3, isRepeatOpen=True, repeatClose=1),
        h(4, repeatAlternative=1),
        h(5, repeatAlternative=2),
        h(6),
    ]
    result = set(unroll_repeats(headers))
    # Everything survives except ending 1, which straight-through playback discards.
    assert {0, 1, 2, 3, 4, 5} - result == {3}
    assert 4 in result, "the highest-numbered ending must be the one kept"
