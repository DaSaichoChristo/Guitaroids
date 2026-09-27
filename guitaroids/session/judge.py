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
tracked (:attr:`GameState.strays`) but never penalised. The original justification
was that the real tab is 68% one lane *after chord collapse*; chords are kept by
default now (§21), so that number no longer describes the shipped chart -- but the
rule stands on its own, because counting faking-through-a-solo as a miss punishes
the exact behaviour a practice tool is for, at any density.
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

#: How far :meth:`GameState.timing_delta` walks either side of the insertion point
#: looking for an unjudged note. Bounded so a long song's per-press cost does not
#: grow with the number of notes at that pitch; 32 is far more than the handful a
#: player can be plausibly away from.
TIMING_SCAN = 32


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
    """The **chart note's** lane, whichever way the note was hit.

    For :meth:`GameState.press_pitch` that is not the string the detected pitch could
    have come from -- MIDI 55 is D-string-5 *or* open G -- and it is deliberately not
    reported, because the playfield draws what the song asked for and a hit drawn on the
    wrong string would be a lie about the tab. ``-1`` means the pitch matched no note at
    all, which is the stray case.
    """

    note_time: float
    delta_seconds: float = 0.0
    pitch: int = 0
    """The detected MIDI pitch, when the judgement came from the microphone. 0 for a
    keypress, and for a note matched by lane."""

    pressed: bool = True
    """Whether anything was actually played for this note.

    ``False`` only for a note that timed out with no press at all, from
    :meth:`GameState.update`. The distinction matters because
    :attr:`delta_seconds` means two different things: for a press it is how late or
    early *the player* was, and for an expiry it is merely how far past the MISS
    window the clock had travelled when the screen noticed -- which is always a
    little over :data:`MISS_SECONDS` and is not a measurement of anything.

    So anything that reads the deltas to work out a player's timing -- a
    calibration readout, an average -- has to skip these. Averaging them in drags
    the answer toward a permanent +140ms and makes the offset look larger than it
    is, which is a calibration that cannot be trusted.
    """

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
    #: The same, keyed by MIDI pitch instead of by string. §24.2: the six strings'
    #: ranges overlap, so a detected fundamental does not identify a lane -- measured on
    #: the real library, 13 of 24 distinct pitches are reachable on more than one
    #: string, and MIDI 49 is on three. Both indices share :attr:`by_note`, so a note
    #: cannot be judged twice whichever way it is hit.
    _pending_pitch: dict[int, list[int]] = field(default_factory=dict)
    _pitch_times: dict[int, list[float]] = field(default_factory=dict)
    #: Notes with no pitch at all (the tab carried no usable tuning). They cannot be
    #: matched by pitch, so they are absent from the pitch index and can only be hit
    #: by lane. Counted rather than assumed: a chart that is entirely unmatchable by
    #: pitch is a state worth being able to report.
    unpitched: int = 0
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
        self._pending_pitch = {}
        self._pitch_times = {}
        self.unpitched = 0
        for index, note in enumerate(self.chart.notes):
            self._pending[note.lane].append(index)
            self._lane_times[note.lane].append(note.time)
            if note.pitch <= 0:
                self.unpitched += 1
                continue
            self._pending_pitch.setdefault(note.pitch, []).append(index)
            self._pitch_times.setdefault(note.pitch, []).append(note.time)

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

    def press_pitch(self, pitch: int, position: float) -> Judgement | None:
        """Resolve a **detected** pitch at ``position``, or ``None``.

        The same algorithm as :meth:`press` over a pending list keyed by MIDI pitch
        instead of by string, sharing :attr:`by_note` so a note cannot be judged twice
        whichever way it is hit. The returned :attr:`Judgement.lane` is the **chart
        note's** lane, not the string the pitch could have come from: the display stays
        truthful about what the song asked for.

        A pitch the song never uses is a stray, counted. A pitch the song *does* use
        with nothing resolvable right now returns ``None`` **silently**, and that
        asymmetry is the whole subtlety of playing a real instrument:

        - A held note is detected on every analysis window -- at the 512-sample hop, a
          note lasting 400ms is heard about **35 times**. If each re-detection were a
          stray, a single held note would add 35 to the count and the tally would say
          nothing about how the player played.
        - So "already judged, or outside the window" is treated as *the same note still
          sounding*, which is what it is.

        Returns ``None`` rather than a STRAY for those, and the caller has nothing to
        do about it, which is what a microphone needs: most of what it hears is a note
        that is already being held.
        """
        if pitch in self._pitch_times:
            indices = self._pending_pitch[pitch]
            times = self._pitch_times[pitch]
        else:
            # A pitch the chart never asks for. This is the one case that is genuinely
            # the player playing something else, so it counts.
            self.strays += 1
            stray = Judgement(Verdict.STRAY, lane=-1, note_time=position, pitch=pitch)
            self.judgements.append(stray)
            return stray

        low = bisect_left(times, position - MISS_SECONDS)
        high = bisect_right(times, position + MISS_SECONDS)

        best: Judgement | None = None
        best_index: int | None = None
        for slot in range(low, high):
            index = indices[slot]
            if index in self.by_note:
                continue
            note = self.chart.notes[index]
            delta = position - note.time
            if best is None or abs(delta) < abs(best.delta_seconds):
                best_index = index
                best = Judgement(
                    verdict=verdict_for(delta),
                    lane=note.lane,
                    note_time=note.time,
                    delta_seconds=delta,
                    pitch=pitch,
                )

        if best is None or best_index is None:
            return None
        self.by_note[best_index] = best
        self.judgements.append(best)
        if best.is_penalised:
            self.misses += 1
        return best

    def timing_delta(self, pitch: int, position: float) -> float | None:
        """Signed distance to the nearest **unjudged** note of ``pitch``, ignoring the window.

        ``None`` when the chart has no such pitch, or every note of it is resolved.

        This exists because :meth:`press_pitch` is deliberately silent when a press
        lands outside the MISS window -- a press 300ms late resolves nothing, because
        there is no note left to resolve by then. So the judgement carries no
        information about *how* late the player was, and a timing readout fed only by
        judgements can never see an offset larger than the window.

        That is backwards: the larger the player's error, the less they could see of
        it. This asks the question the judgement cannot -- how far is the nearest note
        this pitch *would* have hit -- so the offset is measurable at any size.

        Signed the same way as :attr:`Judgement.delta_seconds`: positive is late.
        Uses the sorted per-pitch time list and walks outward from the insertion
        point, capped, so a long song costs a bounded amount per press rather than a
        scan of every note of that pitch.
        """
        times = self._pitch_times.get(pitch)
        if not times:
            return None
        indices = self._pending_pitch[pitch]
        slot = bisect_left(times, position)
        best: float | None = None
        for offset in range(TIMING_SCAN):
            for candidate in (slot - 1 - offset, slot + offset):
                if not 0 <= candidate < len(times):
                    continue
                if indices[candidate] in self.by_note:
                    continue
                delta = position - times[candidate]
                if best is None or abs(delta) < abs(best):
                    best = delta
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
                    pressed=False,
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
