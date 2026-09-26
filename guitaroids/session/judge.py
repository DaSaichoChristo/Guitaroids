"""Judging a keypress against the chart. Pure, and knows nothing about Qt.

Two things live here and they are deliberately separate:

- **:func:`verdict_for`** -- the windows from DESIGN.md §1.6. Trivial arithmetic, and
  the thing most worth pinning at its boundaries.
- **:class:`GameState`** -- the mutable "which notes are still pending" bookkeeping
  that judging actually requires.

The split exists because §1.7 says screens never own game objects. If judgement
state lived in the screen, leaving and re-entering GAME would silently reset a
run, and there would be nowhere to test the windows without building a widget.

**A stray does not count as a miss.** Pressing a lane with no note in range is
tracked (:attr:`GameState.strays`) but never penalised. DESIGN.md's real tab is
68% one lane after chord collapse, and counting faking-through-a-solo as a miss
would punish the exact behaviour a practice tool is for.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from enum import Enum

from ..model.chart import Chart, Note

#: Hit windows, in seconds. DESIGN.md §1.6.
PERFECT_SECONDS = 0.035
GOOD_SECONDS = 0.080
#: A note later than this past its time is missed whether or not anything is pressed.
MISS_SECONDS = 0.140


class Verdict(Enum):
    PERFECT = "perfect"
    GOOD = "good"
    MISS = "miss"
    STRAY = "stray"
    """A lane pressed with no note in range. Counted, never penalised."""


def verdict_for(delta_seconds: float) -> Verdict:
    """The verdict for a note hit ``delta_seconds`` from its true time.

    ``delta`` is signed: negative means early. The verdict is symmetric, so the sign
    does not matter here, but it is kept on the judgement for display.
    """
    magnitude = abs(delta_seconds)
    if magnitude <= PERFECT_SECONDS:
        return Verdict.PERFECT
    if magnitude <= GOOD_SECONDS:
        return Verdict.GOOD
    return Verdict.MISS


@dataclass(frozen=True, slots=True)
class Judgement:
    """One note resolved. ``lane`` and ``note_time`` say which."""

    verdict: Verdict
    lane: int
    note_time: float
    delta_seconds: float = 0.0

    @property
    def is_penalised(self) -> bool:
        return self.verdict is Verdict.MISS

    @property
    def is_hit(self) -> bool:
        return self.verdict in (Verdict.PERFECT, Verdict.GOOD)


@dataclass
class GameState:
    """Mutable judging state for one attempt. No Qt, no clock, no I/O.

    The screen owns one of these and feeds it positions and keypresses; it is not
    told what the positions mean, so the same state drives a wall clock now and the
    audio clock of §1.5 later.
    """

    chart: Chart
    #: Indices into ``chart.notes``, per lane, still awaiting a verdict.
    _pending: list[list[int]] = field(default_factory=list)
    _lane_times: list[list[float]] = field(default_factory=list)
    judgements: list[Judgement] = field(default_factory=list)
    #: Judgements keyed by note index, for the hit feedback layer.
    by_note: dict[int, Judgement] = field(default_factory=dict)
    misses: int = 0
    strays: int = 0

    def __post_init__(self) -> None:
        self._rebuild()

    def _rebuild(self) -> None:
        self._pending = [[] for _ in range(6)]
        self._lane_times = [[] for _ in range(6)]
        for index, note in enumerate(self.chart.notes):
            self._pending[note.lane].append(index)
            self._lane_times[note.lane].append(note.time)

    # --- queries -------------------------------------------------------------

    @property
    def note_count(self) -> int:
        return len(self.chart.notes)

    @property
    def resolved(self) -> int:
        return len(self.by_note)

    @property
    def outstanding(self) -> int:
        return self.note_count - self.resolved

    def counts(self) -> dict[Verdict, int]:
        tally = {verdict: 0 for verdict in Verdict}
        for judgement in self.judgements:
            tally[judgement.verdict] += 1
        return tally

    @property
    def hits(self) -> int:
        return sum(1 for judgement in self.judgements if judgement.is_hit)

    @property
    def accuracy(self) -> float:
        """Hits over notes **judged so far** -- a live readout, 0.0-1.0.

        Deliberately not hits over the whole song. Three seconds in, almost nothing
        has been judged, so hits/note_count reports a near-zero accuracy for a player
        who is doing perfectly well, and the number on the HUD looks broken. This is
        "of the notes that have come up, how many did I get".

        Strays never appear in either side: they are not notes.
        """
        if self.resolved == 0:
            return 0.0
        return self.hits / self.resolved

    @property
    def song_accuracy(self) -> float:
        """Hits over every note in the chart. Meaningful only once the song is over.

        This is the figure for the results screen. The live HUD wants
        :attr:`accuracy` instead, for the reason given there.
        """
        if self.note_count == 0:
            return 0.0
        return self.hits / self.note_count

    # --- inputs --------------------------------------------------------------

    def press(self, lane: int, position: float) -> Judgement | None:
        """Resolve ``lane`` at ``position``, or ``None`` if the lane was empty.

        Searches the **MISS** window, not the GOOD window. A press 100ms off is
        within the 140ms a note lives for, so it resolves that note as a MISS
        rather than falling through to become a stray *and* leaving the note to
        expire separately -- which would count one missed note as a miss plus a
        stray press, for what the player did as a single mistimed hit.

        Picks the *nearest* pending note in the window, so a late press still lands
        on its own note rather than the next one along the lane. A note cannot be
        judged twice.
        """
        if not 0 <= lane < len(self._pending):
            raise ValueError(f"lane {lane} out of range")

        times = self._lane_times[lane]
        low = bisect_left(times, position - MISS_SECONDS)
        high = bisect_right(times, position + MISS_SECONDS)

        best: Judgement | None = None
        best_index: int | None = None
        for slot in range(low, high):
            index = self._pending[lane][slot]
            if index in self.by_note:
                continue
            note = self.chart.notes[index]
            delta = position - note.time
            if best is None or abs(delta) < abs(best.delta_seconds):
                best_index = index
                best = Judgement(
                    verdict=verdict_for(delta),
                    lane=lane,
                    note_time=note.time,
                    delta_seconds=delta,
                )

        if best is None or best_index is None:
            self.strays += 1
            stray = Judgement(Verdict.STRAY, lane, note_time=position)
            self.judgements.append(stray)
            return stray

        self.by_note[best_index] = best
        self.judgements.append(best)
        if best.is_penalised:
            self.misses += 1
        return best

    def update(self, position: float) -> list[Judgement]:
        """Resolve every note now past its MISS window. Returns the new misses.

        The screen calls this as the clock advances; it is the only way a note is
        missed, so a song that ends mid-run reports its misses honestly rather than
        waiting for a keypress that will never come.
        """
        fresh: list[Judgement] = []
        for lane, times in enumerate(self._lane_times):
            stop = bisect_right(times, position - MISS_SECONDS)
            for slot in range(stop):
                index = self._pending[lane][slot]
                if index in self.by_note:
                    continue
                note = self.chart.notes[index]
                judgement = Judgement(
                    verdict=Verdict.MISS,
                    lane=lane,
                    note_time=note.time,
                    delta_seconds=position - note.time,
                )
                self.by_note[index] = judgement
                self.judgements.append(judgement)
                fresh.append(judgement)
        self.misses += len(fresh)
        return fresh

    # --- helpers for the widget's hit feedback --------------------------------

    def verdict_at(self, note: Note, index: int) -> Verdict | None:
        judged = self.by_note.get(index)
        return judged.verdict if judged is not None else None
