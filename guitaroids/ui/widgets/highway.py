"""The note highway: a horizontal timeline of the chart, with "now" at the centre.

Orientation is **time runs left to right**, lanes are stacked as rows, and a
vertical playline sits at the centre of the widget. This deliberately departs from
DESIGN.md §1.1's "scrolls toward a hit line" and from §1.4's "fretting hand
x-position -> lane"; see DESIGN.md §15.2 for why, and for what it costs.

The widget is **pure render**: it holds a chart, a position and a zoom, and
``paintEvent`` reads nothing else. No clock, no input, no audio, no widgets it does
not own. That is what makes the first real pixel assertions possible in this test
suite -- a QImage can be rendered and measured with no audio device, no camera and
no event loop.

Two consequences of being pure, both deliberate:

- The screen owns the clock and calls :meth:`set_position` each frame. When the
  audio clock of §1.5 replaces the wall clock, this widget does not change.
- The widget cannot tell "get ready" from "nothing to play". It reports what the
  chart contains and leaves the surrounding state to the screen.

Geometry::

    x(t) = width / 2 + (t - position) * zoom
    lane L occupies a horizontal band, **lane 0 at the bottom**

Lane 0 is the high E string (``lane = string - 1``), and a real fretboard diagram
draws the high string at the bottom, so the highway matches the instrument rather
than the array index. Reading top to bottom therefore gives lanes 5, 4, 3, 2, 1, 0.

Performance: ``chart.notes`` and ``chart.bar_lines`` are both time-sorted, and the
times are cached as tuples so the visible slice is a ``bisect`` on each paint. At
the default 200px/s in a 1200px widget that is +/-3 seconds, or 10-18 notes for the
real library. Iterating all 1108 notes every frame would be the slow path; this is
the fast one, and a test asserts the drawn count equals the bisect window.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right

from PySide6 import QtCore, QtGui, QtWidgets

from ...model.chart import Chart, Note
from ...session.judge import GOOD_SECONDS
from ..theme import COLORS, LANE_COLORS, LANE_COUNT, px, radius

#: Default horizontal scale. Chosen so the real library shows 10-18 notes in a
#: 1200px widget: dense enough to feel like a song, sparse enough that a fret number
#: is legible inside each note.
DEFAULT_ZOOM = 200.0

#: How long a note is drawn, in seconds, before any duration is known.
#:
#: A fixed time rather than ``Note.duration_beats``, which is 0.0 for every note in
#: the real tab. Deliberately *under* the smallest gap in that tab (197ms, a 16th at
#: 76bpm) so consecutive notes never overlap at the default zoom.
NOTE_SECONDS = 0.16


class Highway(QtWidgets.QWidget):
    """Renders a chart at a position. Owns no clock and no input."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("highway")
        # A lane needs room for a fret number; 6 x 44 is the floor before the
        # numbers are clipped rather than scaled.
        self.setMinimumHeight(px(264))
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding
        )

        self._chart: Chart | None = None
        self._note_times: tuple[float, ...] = ()
        self._bar_times: tuple[float, ...] = ()
        self._position = 0.0
        self._zoom = DEFAULT_ZOOM

    # --- inputs -------------------------------------------------------------

    def set_chart(self, chart: Chart | None) -> None:
        """Show ``chart``, or nothing if ``None``.

        Caches the time arrays so ``paintEvent`` can bisect. Rebuilt only on a chart
        change, never per frame.
        """
        self._chart = chart
        self._note_times = tuple(note.time for note in chart.notes) if chart else ()
        self._bar_times = tuple(bar.time for bar in chart.bar_lines) if chart else ()
        self.update()

    def set_position(self, seconds: float) -> None:
        """Move the playline. Called every frame by whoever owns the clock."""
        self._position = seconds
        self.update()

    def set_zoom(self, px_per_second: float) -> None:
        if px_per_second <= 0:
            raise ValueError(f"zoom must be positive, got {px_per_second}")
        self._zoom = float(px_per_second)
        self.update()

    @property
    def chart(self) -> Chart | None:
        return self._chart

    @property
    def position(self) -> float:
        return self._position

    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def first_note_time(self) -> float | None:
        """When the first note lands, or ``None`` for an empty chart.

        Exposed because the screen needs it to say "get ready" -- for the real tab
        the first note is 3.16s in, and three seconds of blank highway reads as
        broken. The widget itself stays dumb about what to do about it.
        """
        return self._note_times[0] if self._note_times else None

    # --- geometry ------------------------------------------------------------

    @property
    def lane_height(self) -> float:
        return self.height() / LANE_COUNT

    def x_for_time(self, seconds: float) -> float:
        """Screen x of a chart time. The playline is the centre by construction."""
        return self.width() / 2 + (seconds - self._position) * self._zoom

    def time_for_x(self, x: float) -> float:
        """Inverse of :meth:`x_for_time`."""
        return self._position + (x - self.width() / 2) / self._zoom

    def y_for_lane(self, lane: int) -> float:
        """Top edge of a lane's band. **Lane 0 is at the bottom.**"""
        return (LANE_COUNT - 1 - lane) * self.lane_height

    def visible_window(self) -> tuple[float, float]:
        """The chart times currently on screen, as ``(low, high)``."""
        half = (self.width() / 2) / self._zoom
        return self._position - half, self._position + half

    def visible_notes(self) -> list[Note]:
        """Notes inside the visible window, in time order.

        Public because a test asserts the drawn count matches it, and because the
        screen's "get ready" logic wants the same answer.
        """
        if self._chart is None:
            return []
        low, high = self.visible_window()
        start = bisect_left(self._note_times, low)
        stop = bisect_right(self._note_times, high)
        return list(self._chart.notes[start:stop])

    def visible_bar_lines(self) -> list[float]:
        if not self._bar_times:
            return []
        low, high = self.visible_window()
        start = bisect_left(self._bar_times, low)
        stop = bisect_right(self._bar_times, high)
        return list(self._bar_times[start:stop])

    # --- painting ------------------------------------------------------------

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt naming
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QtGui.QColor(COLORS["bg"]))
        painter.setFont(self.font())

        self._paint_count_in(painter)
        self._paint_lanes(painter)
        self._paint_bar_lines(painter)
        # Under the notes, not over them. Painted last it hides the one note that
        # matters most -- the one the player is hitting right now -- and that is
        # exactly the bug this ordering avoids.
        self._paint_hit_zone(painter)
        self._paint_notes(painter)
        self._paint_playline(painter)

    def _paint_count_in(self, painter: QtGui.QPainter) -> None:
        """Shade the region before t=0.

        The design has 0-2 bars of count-in before the song starts (DESIGN.md §3.4),
        so the player has to be able to see it coming. Only the shading lives here;
        the screen draws the words, because the widget does not know how many bars
        were configured.
        """
        if self._position <= 0:
            return
        start_x = self.x_for_time(0.0)
        if start_x >= self.width():
            return
        painter.fillRect(
            QtCore.QRectF(0, 0, max(0.0, start_x), self.height()),
            QtGui.QColor(COLORS["surface"]),
        )
        painter.setPen(QtGui.QPen(QtGui.QColor(COLORS["border_hi"]), 1, QtCore.Qt.PenStyle.DashLine))
        painter.drawLine(QtCore.QPointF(start_x, 0), QtCore.QPointF(start_x, self.height()))

    def _paint_lanes(self, painter: QtGui.QPainter) -> None:
        lane = self.lane_height
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        # Alternating bands, drawn from the top so lane 5 is the first band. With
        # lane 0 at the bottom this reads correctly either way; what matters is that
        # the shading alternates.
        for index in range(LANE_COUNT):
            if index % 2 == 0:
                continue
            top = index * lane
            painter.fillRect(
                QtCore.QRectF(0, top, self.width(), lane), QtGui.QColor(COLORS["surface"])
            )

        painter.setPen(QtGui.QPen(QtGui.QColor(COLORS["border"]), 1))
        for index in range(1, LANE_COUNT):
            y = index * lane
            painter.drawLine(QtCore.QPointF(0, y), QtCore.QPointF(self.width(), y))

    def _paint_bar_lines(self, painter: QtGui.QPainter) -> None:
        """A vertical line per measure: the cheapest useful orientation cue."""
        painter.setPen(QtGui.QPen(QtGui.QColor(COLORS["border"]), 1))
        for time in self.visible_bar_lines():
            x = self.x_for_time(time)
            painter.drawLine(QtCore.QPointF(x, 0), QtCore.QPointF(x, self.height()))

    def _paint_notes(self, painter: QtGui.QPainter) -> None:
        lane = self.lane_height
        width = NOTE_SECONDS * self._zoom
        # Fret numbers need a readable box; below about half a lane they would be
        # illegible, so the number is dropped and the note is a plain bar.
        show_fret = lane >= 44
        for note in self.visible_notes():
            x = self.x_for_time(note.time)
            top = self.y_for_lane(note.lane)
            rect = QtCore.QRectF(x, top + lane * 0.08, width, lane * 0.84)
            colour = QtGui.QColor(LANE_COLORS[note.lane % len(LANE_COLORS)])
            painter.setPen(QtGui.QPen(colour.darker(140), 1))
            painter.setBrush(colour)
            painter.drawRoundedRect(
                rect, radius(3), radius(3)
            )
            if show_fret:
                painter.setPen(QtGui.QPen(QtGui.QColor("#101216")))
                painter.drawText(
                    rect, QtCore.Qt.AlignmentFlag.AlignCenter, str(note.fret)
                )
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

    def _paint_hit_zone(self, painter: QtGui.QPainter) -> None:
        """Shade the band a note has to be hit in.

        Sized from :data:`~guitaroids.session.judge.GOOD_SECONDS` rather than a
        number of its own, so the zone drawn on screen and the window actually
        judged cannot drift apart into two different-looking tolerances.
        """
        half = GOOD_SECONDS * self._zoom
        centre = self.width() / 2
        painter.fillRect(
            QtCore.QRectF(centre - half, 0, half * 2, self.height()),
            QtGui.QColor(COLORS["accent_hi"]),
        )

    def _paint_playline(self, painter: QtGui.QPainter) -> None:
        """The vertical line the whole view is built around. Over everything."""
        centre = self.width() / 2
        painter.setPen(QtGui.QPen(QtGui.QColor(COLORS["text"]), px(2)))
        painter.drawLine(
            QtCore.QPointF(centre, 0), QtCore.QPointF(centre, self.height())
        )
