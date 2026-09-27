"""One finished attempt, as a number rather than a live readout.

`GameState` answers "how is it going"; this answers "how did it go". The difference
looks small and is not:

- `GameState.accuracy` is hits over notes **judged so far**, because at three seconds
  into a song almost nothing has been judged and hits/note_count would read near-zero
  for a player doing perfectly well. That is right for a HUD and wrong for a results
  screen.
- `Result.accuracy` is hits over the **whole song**, so it is the fraction of the tab
  the player actually played, and it is 1.0 for a full combo.

**Every note is judged by the time a Result exists** — the game screen only publishes
one when `outstanding == 0` — so the two agree numerically here. They are computed
separately rather than shared, because sharing them would mean importing a live
readout into a finished one, and the next reader would have no way to tell which of the
two they were looking at.

**There are no points.** No 10,000-maximum, no streak multiplier, no invented weight
per verdict. A score would need a formula to tune and a set of edge cases to test, and
the numbers the player actually acts on are the tally and the percentage. The one
thing worth comparing between runs is the percentage, so that is what `Settings`
remembers per song.

Pure: no Qt, no I/O, no clock. Frozen, so a published result cannot be edited by
anything that holds it — the results screen and the settings both do.
"""

from __future__ import annotations

from dataclasses import dataclass

from .judge import GameState, Verdict


@dataclass(frozen=True, slots=True)
class Result:
    """One completed attempt at one song."""

    title: str
    """The song's title, falling back to its track name when it has none."""

    artist: str = ""
    track_name: str = ""
    slug: str = ""
    """The file stem, which is what :class:`~guitaroids.settings.Settings` keys its
    per-song data on -- offsets, practice tempo, and now the best accuracy."""

    track_number: int = 0

    perfect: int = 0
    good: int = 0
    miss: int = 0
    stray: int = 0
    """A note the player did not play, or the chart did not ask for. Counted, never
    penalised, and **not** part of the accuracy ratio in either direction -- it is not
    a note of the song."""

    note_count: int = 0
    """Notes in the chart, chords expanded. This is the denominator."""

    notes_hit: int = 0
    """``perfect + good``. A miss is a note that came up and was not played."""

    bpm: float = 0.0
    """The practice tempo this run was played at. 0.0 means as written."""

    rate: float = 1.0
    """Playback rate in force, 1.0 being the tab's own tempo. Recorded because two runs
    of the same song at 76 and 57 BPM are not comparable on accuracy alone, and the
    results screen says which one this was."""

    is_new_best: bool = False
    """Whether this run beat the best accuracy previously recorded for the song.

    Set by the game screen, not computed here, because the answer is only knowable
    after :meth:`~guitaroids.settings.Settings.record_accuracy_for` has compared
    against the store. The screen builds a result, records it, and fills this in --
    which is why it is a field rather than a property.

    Default False so a hand-built ``Result`` in a test is not congratulating the player
    for a run nobody recorded.
    """

    previous_best: float | None = None
    """The best accuracy for this song *before* this run, or ``None`` if it was the
    first recorded attempt. Paired with :attr:`is_new_best` for the same reason: the
    two are one fact, asked and answered together, and reading the store again after
    recording would return this run's own score.
    """

    @property
    def accuracy(self) -> float:
        """Notes hit over notes in the song, 0.0-1.0. **Not** `GameState.accuracy`.

        A chart with no notes -- an import that produced nothing playable, a library
        scan mid-copy -- gives 0.0 rather than dividing by zero. Nothing was played, so
        nothing was played well.
        """
        if self.note_count <= 0:
            return 0.0
        return self.notes_hit / self.note_count

    @property
    def is_full_combo(self) -> bool:
        """Every note in the tab hit, at any timing window. A miss is a miss."""
        return self.note_count > 0 and self.miss == 0 and self.notes_hit == self.note_count

    @property
    def completed(self) -> bool:
        """Whether every note was actually judged.

        Always true for a result the game screen publishes, which only does so at
        ``outstanding == 0``. It is a field rather than an assumption because a result
        can also be built from a state that was abandoned, and a screen that renders
        one has to say which it is showing.
        """
        return self.perfect + self.good + self.miss == self.note_count

    @classmethod
    def from_state(
        cls,
        state: GameState,
        *,
        title: str = "",
        artist: str = "",
        track_name: str = "",
        slug: str = "",
        track_number: int = 0,
        bpm: float = 0.0,
        rate: float = 1.0,
    ) -> "Result":
        """The finished attempt described by ``state``.

        The identity fields are passed in rather than read off the chart because the
        chart is a pure data object that knows nothing about which file it came from,
        and `GameState` deliberately knows nothing beyond the notes.
        """
        tally = state.counts()
        perfect = tally[Verdict.PERFECT]
        good = tally[Verdict.GOOD]
        return cls(
            title=title or track_name,
            artist=artist,
            track_name=track_name,
            slug=slug,
            track_number=track_number,
            perfect=perfect,
            good=good,
            miss=tally[Verdict.MISS],
            stray=tally[Verdict.STRAY],
            note_count=state.note_count,
            notes_hit=state.hits,
            bpm=bpm,
            rate=rate,
        )
