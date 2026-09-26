"""Repeat-barline unrolling.

Turns a written measure sequence into the linear order a player actually hears.

Pure: no I/O and no runtime guitarpro import (it only needs objects with the right
attributes), so it is unit-testable with hand-built headers and no ``.gp5`` file.
That matters because this is the least verifiable part of the import path -- see
DESIGN.md §6.5.

Semantics, read from guitarpro/gp5.py:317-328 and models.py:527-531:

``isRepeatOpen``
    This measure opens a repeated section.
``repeatClose``
    The file stores the **total pass count**; guitarpro then subtracts one, so the
    attribute holds ``N - 1``. The sentinel for "not a close" is ``-1``, which is
    also what a file byte of ``0`` decodes to::

        file byte 0 -> stored -1 -> no repeat at all
        file byte 1 -> stored  0 -> 1 pass  (degenerate)
        file byte 2 -> stored  1 -> 2 passes  (the standard double repeat)
        file byte 3 -> stored  2 -> 3 passes

    i.e. ``passes = stored + 1``. UNVERIFIED against a real file -- see
    ``_repeat_passes``.
``repeatAlternative``
    ``0`` for a normal measure, ``N`` for the Nth ending of a repeat. Endings sit
    *after* the close barline and belong to the section that close terminates.

The ``-1`` sentinel is a genuine trap: ``RepeatGroup.addMeasureHeader`` tests
``repeatClose > 0``, which silently disagrees with it. This module tests against
the sentinel directly.

Layouts handled, with ``a b c`` meaning measures 1..3::

    a b c                      ->  a b c
    |: a :| b c                ->  a a b c
    |: a :| b c   (3 passes)   ->  a a a b c
    |: a b :| c                 ->  a b a b c
    |: a :| b |: c :| d         ->  a a b c c d
    |: a :| b1 c                ->  a a b1 c
    |: a :| b1 b2 c             ->  a a b2 c      (straight-through takes the last)

Alternative endings resolve to the **last** ending, which is what a player hears
listening straight through.
"""

from __future__ import annotations

from typing import Protocol, Sequence

#: Backstop against pathological or hand-edited tabs. A well-formed tab nests maybe
#: two deep; anything past this is treated as malformed and we bail rather than emit
#: a sequence that would never terminate. Defence in depth -- ``_find_open``'s
#: floor already prevents unbounded reach-back in practice.
MAX_REPEAT_DEPTH = 8

#: Hard cap on emitted measures, so a bad repeat count raises instead of exhausting
#: memory. A 200-bar tab played through three times is 600; 10,000 is far beyond any
#: real song.
MAX_EMITTED_MEASURES = 10_000


class HeaderLike(Protocol):
    """The three fields this module needs from a ``MeasureHeader``."""

    isRepeatOpen: bool
    repeatClose: int
    repeatAlternative: int


class RepeatStructureError(ValueError):
    """Raised when a tab's repeat structure cannot be unrolled safely."""


def _repeat_passes(header: HeaderLike) -> int:
    """Total passes over a section ending at ``header``.

    ``repeatClose`` holds the file's pass count minus one, so passes are
    ``stored + 1``; ``-1`` means there is no close here.

    Derived from ``gp5.py:327-328`` (``if header.repeatClose > -1:
    header.repeatClose -= 1``) and the ``-1`` field default. It is **not** yet
    confirmed against a real tab. If a real file shows a standard ``|: ... :|``
    playing only once, this is the line to flip -- ``test_stored_to_pass_mapping``
    pins the current interpretation so the change is deliberate.
    """
    if header.repeatClose < 0:
        return 0
    return header.repeatClose + 1


def _find_close(headers: Sequence[HeaderLike], open_index: int) -> int | None:
    """Index of the measure closing the section opened at ``open_index``.

    Scans forward from ``open_index`` itself, because ``|: a :|`` puts the opening
    and closing barlines on the *same* measure. Returns ``None`` if the section is
    never closed.
    """
    for index in range(open_index, len(headers)):
        if headers[index].repeatClose >= 0:
            return index
    return None


def _section_end(headers: Sequence[HeaderLike], close_index: int) -> int:
    """Last measure of the section, absorbing any alternative endings.

    Endings follow the close barline and are marked ``repeatAlternative > 0``, so
    they are consumed as part of the section rather than emitted by the main walk --
    otherwise every ending plays in sequence instead of one being chosen.
    """
    end = close_index
    cursor = close_index + 1
    while cursor < len(headers) and headers[cursor].repeatAlternative > 0:
        end = cursor
        cursor += 1
    return end


def _emit_section(
    order: list[int],
    headers: Sequence[HeaderLike],
    start: int,
    end: int,
    passes: int,
) -> None:
    """Append ``passes`` traversals of ``range(start, end + 1)`` to ``order``."""
    section = range(start, end + 1)
    if sum(1 for k in section if headers[k].isRepeatOpen) > MAX_REPEAT_DEPTH:
        raise RepeatStructureError(
            f"repeat nesting deeper than {MAX_REPEAT_DEPTH} at measure {start + 1}"
        )

    body = [k for k in section if headers[k].repeatAlternative == 0]
    endings = [k for k in section if headers[k].repeatAlternative > 0]
    # Straight-through playback takes the highest-numbered ending present.
    final_ending = max(endings, key=lambda k: headers[k].repeatAlternative, default=None)

    def append(measure: int) -> None:
        order.append(measure)
        if len(order) > MAX_EMITTED_MEASURES:
            raise RepeatStructureError(
                f"unrolled sequence exceeded {MAX_EMITTED_MEASURES} measures"
            )

    for pass_no in range(passes):
        for measure in body:
            append(measure)
        # Endings are taken on the final pass only, or the same bar plays twice.
        if pass_no == passes - 1 and final_ending is not None:
            append(final_ending)


def unroll_repeats(headers: Sequence[HeaderLike]) -> list[int]:
    """Return measure indices in playback order.

    A section is handled at its **opening** measure, with the walk looking forward
    for the close. Handling it at the close instead double-emits the opening: the
    plain walk has already passed through it on the way to the close.

    A close with no matching open -- which hand-edited tabs produce -- plays its
    measure once. There is nothing to repeat back to, and inventing a repeat would
    be more surprising than ignoring the stray barline.

    Raises:
        RepeatStructureError: if nesting exceeds ``MAX_REPEAT_DEPTH`` or the
            emitted sequence exceeds ``MAX_EMITTED_MEASURES``.
    """
    total = len(headers)
    order: list[int] = []

    index = 0
    while index < total:
        if headers[index].isRepeatOpen:
            close = _find_close(headers, index)
            if close is not None:
                end = _section_end(headers, close)
                _emit_section(order, headers, index, end, _repeat_passes(headers[close]))
                index = end + 1
                continue
        order.append(index)
        index += 1

    return order


def has_repeats(headers: Sequence[HeaderLike]) -> bool:
    """True if the tab uses any repeat structure at all."""
    return any(h.repeatClose >= 0 or h.isRepeatOpen for h in headers)
