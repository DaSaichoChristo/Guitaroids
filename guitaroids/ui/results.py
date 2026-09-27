"""The results screen: what you just played, and how well.

The last placeholder. `Screen.RESULTS` existed from the first commit with a
`PlaceholderScreen` behind it, so the flow could be clicked through to a dead end —
and the counts the game screen has been showing in its HUD went nowhere, which
`AGENTS.md` and `README.md` both said out loud.

**There are no points.** No 10,000 maximum, no streak multiplier, no invented weight
per verdict. The numbers a player can act on are the tally and the percentage, and
inventing a score would have meant a formula to tune and a set of edge cases to get
wrong for a hackathon project whose real subject is playing a guitar accurately.

What it shows: the song, the accuracy as the one large number, the four counts, how
many notes were hit out of how many, the tempo the run was played at — two runs of one
song at 76 and 57 BPM are not comparable on accuracy alone — and the best accuracy
recorded for that song, with a line when this run beat it.

Reads the finished attempt from `AppContext.last_result` rather than taking it as an
argument. The shell's `navigate()` takes no payload and keeps built screens in a
cache, so the context is the only carrier that survives the screen being rebuilt — and
it is the same reason the context holds `play_request`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6 import QtCore, QtWidgets

from ..session.result import Result
from .screens import Screen, ScreenBase, constrained_button, content_column, heading

if TYPE_CHECKING:  # pragma: no cover - types only
    from ..context import AppContext

_CENTRED = QtCore.Qt.AlignmentFlag.AlignHCenter


class Results(ScreenBase):
    """One finished attempt, and the best one so far."""

    TITLE = "results"

    def __init__(
        self,
        shell,
        context: "AppContext",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(shell, context, parent)
        # Wider than the 520 default. A monospace tally of four padded columns does
        # not fit in it -- the first render put "4 stray" on a second line, misaligned
        # with the other three.
        column = content_column(self, margin=44, max_width=620, vertical_centred=True)

        self._title = heading("", parent=self)
        column.addWidget(self._title)
        self._subtitle = heading("", kind="subtitle", parent=self)
        column.addWidget(self._subtitle)
        column.addSpacing(18)

        # The one number worth reading at a glance, so it is the large one. There is
        # no dedicated "big" label role, and adding one to the stylesheet for a single
        # screen is more than this needs; a heading is already the largest thing here.
        self._accuracy = heading("", parent=self)
        column.addWidget(self._accuracy)
        self._verdict = heading("", kind="subtitle", parent=self)
        column.addWidget(self._verdict)
        column.addSpacing(14)

        self._tally = heading("", kind="stat", parent=self)
        # Never wrapped. A monospace tally is data, not prose, and the three spaces
        # between its columns are break opportunities to QLabel -- which is how the
        # first render put "4 stray" on a second line, misaligned with the other
        # three, in a column 412px wide around a string that needs 352. The project's
        # own rule about wrapped labels is that they need their height pinned; the
        # answer here is not to wrap it at all.
        self._tally.setWordWrap(False)
        column.addWidget(self._tally)
        self._detail = heading("", kind="dim", parent=self)
        column.addWidget(self._detail)
        self._best = heading("", kind="dim", parent=self)
        column.addWidget(self._best)
        self._note = heading("", kind="dim", parent=self)
        column.addWidget(self._note)
        #: Every label whose text depends on a result, so the empty state can hide the
        #: ones with nothing in them rather than leaving collapsed rows behind.
        self._content_labels = (
            self._subtitle,
            self._accuracy,
            self._verdict,
            self._tally,
            self._detail,
            self._best,
        )

        # No stretch before the button row. The column is already vertically centred,
        # so the group centres as a whole; a stretch pushed the buttons into the middle
        # of the page and left a dead band beneath them. The empty state needs the same
        # treatment, and gets it by hiding its labels rather than by moving anything.
        row = QtWidgets.QHBoxLayout()
        row.setAlignment(_CENTRED)
        row.setSpacing(12)
        self._again = constrained_button("Play again", width=200, parent=self)
        self._again.clicked.connect(self._play_again)
        self._select = constrained_button("Song select", width=200, parent=self)
        row.addWidget(self._again)
        self._select.clicked.connect(self._song_select)
        row.addWidget(self._select)
        column.addLayout(row)

        self.render()

    # --- rendering -----------------------------------------------------------

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 - Qt naming
        """Re-render on every visit.

        The shell keeps built screens, so this is the same instance for the second
        song. Rendering only in `__init__` would show the first run's numbers over the
        second song's title, which is the bug §32's `showEvent` reload fixed for the
        game screen.
        """
        self.render()
        super().showEvent(event)

    def render(self) -> None:
        """Read `context.last_result` and put it on screen. Pure display logic.

        Split out of `__init__` so a test can render without a window: constructing a
        screen and showing it are different costs, and every assertion here is about
        what the labels say.
        """
        result = self.context.last_result
        if result is None:
            self._render_empty()
            return
        self._render_result(result)

    def _render_empty(self) -> None:
        """Reached by navigating here with no attempt recorded.

        Nothing in the app does that — only the game screen navigates here, and only
        with a result — but the screen is in the shell's registry, so a test can reach
        it and a future button could. Saying "no song played" is honest; showing the
        last run's numbers with no run would be a lie.
        """
        self._title.setText("No song played")
        self._note.setText("Play a song and its results will appear here.")
        self._again.setEnabled(False)
        for label in self._content_labels:
            label.setVisible(False)

    def _render_result(self, result: Result) -> None:
        self._again.setEnabled(True)
        for label in self._content_labels:
            label.setVisible(True)
        self._title.setText(result.title or "Unknown song")
        self._subtitle.setText(self._describe(result))
        self._accuracy.setText(f"{result.accuracy * 100:.1f}%")
        self._verdict.setText(self._verdict_line(result))
        self._tally.setText(
            f"{result.perfect:>5} perfect   {result.good:>5} good   "
            f"{result.miss:>5} miss   {result.stray:>5} stray"
        )
        self._detail.setText(
            f"{result.notes_hit} of {result.note_count} notes"
        )
        self._best.setText(self._best_line(result))
        self._note.setText(self._note_line(result))

    # --- the lines -----------------------------------------------------------

    def _describe(self, result: Result) -> str:
        """Artist, track, and how the song was written -- the identity, plainly.

        The track name is what the tab actually asks for, and on a tab with several
        guitar tracks it is the difference between "Sweet Child O' Mine" and which
        Sweet Child O' Mine. The tempo is here rather than only in the detail line
        because two runs of one song at different tempi are different attempts.
        """
        parts = [part for part in (result.artist, result.track_name) if part]
        if result.bpm > 0.0:
            parts.append(f"practice tempo {result.bpm:.0f} BPM")
        else:
            parts.append("as written")
        return " — ".join(parts)

    def _verdict_line(self, result: Result) -> str:
        if result.note_count <= 0:
            return "Nothing to play"
        if result.is_full_combo:
            return "Full combo"
        if not result.completed:
            return "Song not finished"
        return ""

    def _best_line(self, result: Result) -> str:
        """The best for this song, and whether this run is it.

        Both numbers come off the result rather than off the settings, because the
        settings already hold *this* run's score by the time this screen is built --
        reading them here would print the new best as the previous one. There is no
        line at all when the song has no recorded history, rather than a 0% about a
        song never played.
        """
        if result.is_new_best and result.previous_best is not None:
            return f"New best — previous {result.previous_best * 100:.1f}%"
        if result.is_new_best:
            return "First run recorded"
        stored = self.context.settings.best_accuracy_for(result.slug, default=-1.0)
        if stored < 0.0:
            return ""
        return f"Best {stored * 100:.1f}%"

    def _note_line(self, result: Result) -> str:
        """The two things that are worth a sentence rather than a number.

        A full combo is the only one that earns prose. A part-finished run is worth
        saying because the percentage on its own reads as a verdict on the whole song
        when the truth is that the song ended early.
        """
        if result.note_count <= 0:
            return ""
        if not result.completed:
            return "You left before the last note, so this is the part you played."
        return ""

    # --- navigation ----------------------------------------------------------

    def _play_again(self) -> None:
        """Straight back into the same song, same track, same practice tempo.

        `context.play_request` still holds all three, and the game screen rebuilds the
        chart from it on `showEvent` -- which it does, because leaving that screen
        stopped its timer. So this really does replay the attempt rather than sending
        the player back to choose it again, which is what the button says.
        """
        self.shell.navigate(Screen.GAME)

    def _song_select(self) -> None:
        """Straight to song select, to pick a different song.

        Navigates rather than `go_back()`: history already holds song select from the
        way in, so `go_back` would arrive there today and somewhere else after any
        navigation from here, and a button that means "song select" should mean it.
        """
        self.shell.navigate(Screen.SONG_SELECT, remember=False)
