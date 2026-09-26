"""The tab view: three measures of Guitar Pro-style notation, with a moving beat line.

This replaces the scrolling highway of §15.2, and is a different idea rather than a
tuned version of the same one. The player reads notation the way they would read a
printed tab, and the screen advances **one measure at a time** instead of scrolling
continuously.

Layout::

    ┌─────────────────────────────────────────┐
    │ previous measure   (dimmed)             │  six horizontal string lines
    ├─────────────────────────────────────────┤
    │ CURRENT measure                          │  the beat line sweeps this one
    ├─────────────────────────────────────────┤
    │ next measure        (dimmed)             │
    └─────────────────────────────────────────┘

Two axes, and the split is the point:

- **Down the page is time.** One measure per block; crossing a bar line shifts the
  whole thing down by one and brings in a new bottom measure. Discrete, not
  continuous -- DESIGN.md §15.2's "scrolling highway" was rejected partly *because*
  it was continuous.
- **Across a measure is the beat.** A vertical line at ``x(position)`` sweeps
  left to right, reaching the right edge exactly at the bar line, then the view
  advances and it resets to the left.

**Lane 0 is the top line.** In printed tab notation the top line is the high E
string, and ``lane = string - 1``, so lane 0 is the top line. (The scrolling
highway had lanes as rows and put lane 0 at the bottom, which is why that is
recorded as a change rather than silently flipped.)

**No fret numbers.** §15.4 drew the fret inside every note and the reaction was
that the number system was hard to make sense of. In real tab notation there are no
numbers on the staff at all -- the string line *is* the information, and a number
only appears in a chord diagram. A note is a mark on a line here, and which line it
sits on says which string. ``Note.fret`` is still carried, for display *if* someone
later wants it.

Like the widget it replaces, this is **pure render**: it holds a chart, a position
and nothing else, so it rasterises to a ``QImage`` with no audio device, no camera
and no event loop.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

from ...model.chart import Chart, Note
from ...session.judge import GOOD_SECONDS
from ..theme import COLORS, LANE_COLORS, LANE_COUNT, px, radius

#: Measures on screen: the one just played, the current one, and the one coming.
#: One of each, as asked for -- not a scrolling window.
MEASURES_SHOWN = 3


@dataclass(frozen=True, slots=True)
class MeasureSpan:
    """One measure's extent in seconds."""

    index: int
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def is_complete(self) -> bool:
        return self.end > self.start


class TabView(QtWidgets.QWidget):
    """Renders three measures of tab notation at a position. Owns no clock."""

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("highway")  # keeps the QSS rule name; it is the playfield

        self._chart: Chart | None = None
        self._spans: tuple[MeasureSpan, ...] = ()
        self._starts: tuple[float, ...] = ()
        self._by_measure: list[list[Note]] = []
        self._position = 0.0

        # Room for three staves of six lines plus the gaps between them.
        self.setMinimumHeight(px(300))
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding
        )

    # --- inputs -------------------------------------------------------------

    def set_chart(self, chart: Chart | None) -> None:
        """Show ``chart``, or nothing if ``None``.

        The measure spans and the per-measure note lists are built here rather than
        per paint: both are derived from the chart, and rebuilding them 60 times a
        second to answer "which measure is this" would be absurd.
        """
        self._chart = chart
        self._rebuild()
        self.update()

    def set_position(self, seconds: float) -> None:
        self._position = seconds
        self.update()

    @property
    def chart(self) -> Chart | None:
        return self._chart

    @property
    def position(self) -> float:
        return self._position

    @property
    def first_note_time(self) -> float | None:
        if self._chart is None or not self._chart.notes:
            return None
        return self._chart.notes[0].time

    def _rebuild(self) -> None:
        self._spans = ()
        self._starts = ()
        self._by_measure = []
        chart = self._chart
        if chart is None or not chart.notes:
            return

        self._spans = _measure_spans(chart)
        self._starts = tuple(span.start for span in self._spans)
        self._by_measure = [[] for _ in self._spans]
        for note in chart.notes:
            index = self.measure_index_at(note.time)
            if 0 <= index < len(self._by_measure):
                self._by_measure[index].append(note)

    # --- which measure --------------------------------------------------------

    def measure_index_at(self, seconds: float) -> int:
        """The measure containing ``seconds``.

        Bisect, so this is O(log n) rather than a scan -- and it is called once per
        paint, not once per note.
        """
        if not self._starts:
            return 0
        index = bisect_right(self._starts, seconds) - 1
        return max(0, min(index, len(self._spans) - 1))

    @property
    def current_measure(self) -> int:
        return self.measure_index_at(self._position)

    def visible_measures(self) -> list[int]:
        """``[previous, current, next]``, with ``None`` where there is no such bar.

        The first slot is the measure just played. Before the first measure there is
        none, and the player gets a gap rather than a fabricated bar.
        """
        if not self._spans:
            return []
        current = self.current_measure
        return [
            current - 1 if current - 1 >= 0 else None,
            current,
            current + 1 if current + 1 < len(self._spans) else None,
        ]

    def notes_in_measure(self, index: int | None) -> list[Note]:
        if index is None or not 0 <= index < len(self._by_measure):
            return []
        return self._by_measure[index]

    # --- geometry ------------------------------------------------------------

    @property
    def block_height(self) -> float:
        return self.height() / MEASURES_SHOWN

    @property
    def line_spacing(self) -> float:
        """Distance between the six string lines, within one staff.

        60% of the block, leaving room above and below for the measure gap and for a
        note marker centred on a line to overhang without colliding.
        """
        return self.block_height * 0.6 / (LANE_COUNT - 1)

    def block_top(self, slot: int) -> float:
        """Top edge of one of the three staff blocks."""
        return slot * self.block_height

    def y_for_lane(self, lane: int, slot: int = 1) -> float:
        """Y of a string line. **Lane 0 is the top line**, as in printed tab."""
        return self.block_top(slot) + (self.block_height - self.line_spacing * (LANE_COUNT - 1)) / 2 + lane * self.line_spacing

    def measure_left(self) -> float:
        """Left edge of a staff, leaving a margin so the bar line is not on the edge."""
        return px(56)

    def measure_width(self) -> float:
        return max(1.0, self.width() - self.measure_left() - px(24))

    def x_for_time(self, seconds: float, measure_index: int | None = None) -> float:
        """Screen x of a time, inside the measure that contains it.

        Outside the measure -- which is every note not on screen -- the result is
        clamped to the staff's edges rather than extrapolated. Extrapolating would
        draw notes from the next bar spread across this one, which is the one way a
        notation view can lie about the music.
        """
        if measure_index is None:
            measure_index = self.measure_index_at(seconds)
        if not 0 <= measure_index < len(self._spans):
            return self.measure_left()
        span = self._spans[measure_index]
        if span.duration <= 0:
            return self.measure_left()
        fraction = (seconds - span.start) / span.duration
        fraction = max(0.0, min(1.0, fraction))
        return self.measure_left() + fraction * self.measure_width()

    def beat_line_x(self) -> float:
        """X of the moving line: the current time, in the current measure."""
        return self.x_for_time(self._position, self.current_measure)

    def marker_radius(self) -> float:
        """Note marker size, from the line spacing.

        Derived rather than fixed so a note stays the same visual weight relative to
        its staff at any window size -- which is the whole reason the UI scale exists
        (§14) and the reason this does not hardcode a pixel.
        """
        return max(4.0, self.line_spacing * 0.34)

    # --- painting ------------------------------------------------------------

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:  # noqa: N802 - Qt naming
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QtGui.QColor(COLORS["bg"]))
        painter.setFont(self.font())

        if not self._spans:
            return

        for slot, index in enumerate(self.visible_measures()):
            if index is None:
                continue
            current = slot == 1
            self._paint_staff(painter, slot, index, current)
            self._paint_measure_notes(painter, slot, index, current)

        self._paint_beat_line(painter)

    def _paint_staff(
        self, painter: QtGui.QPainter, slot: int, index: int, current: bool
    ) -> None:
        """Six string lines, the bar lines at each end, and the beat grid.

        Dimmed unless current: the previous and next measures are context, and
        treating all three at the same weight is what made the scrolling version read
        as a wall rather than as a page.
        """
        left = self.measure_left()
        width = self.measure_width()
        span = self._spans[index]

        lines = QtGui.QColor(COLORS["text"] if current else COLORS["border_hi"])
        # Always a QColor, never a hex str: QPen(str, width) is not a valid overload
        # -- it takes a QBrush, a QColor, *or* a str, but not a str plus a width.
        staff_colour = QtGui.QColor(COLORS["text_dim"] if current else COLORS["border_hi"])
        if not current:
            lines.setAlpha(110)

        # Faint beat divisions, only on the current measure. They are what makes
        # "left to right" legible as beats rather than as an arbitrary sweep.
        if current:
            beats = _beats_per_measure(self._chart, index)
            painter.setPen(QtGui.QPen(QtGui.QColor(COLORS["border"]), 1, QtCore.Qt.PenStyle.DotLine))
            for beat in range(1, max(beats, 1)):
                x = left + width * beat / max(beats, 1)
                painter.drawLine(
                    QtCore.QPointF(x, self.y_for_lane(0, slot)),
                    QtCore.QPointF(x, self.y_for_lane(LANE_COUNT - 1, slot)),
                )

        painter.setPen(QtGui.QPen(staff_colour, 1))
        for lane in range(LANE_COUNT):
            y = self.y_for_lane(lane, slot)
            painter.drawLine(QtCore.QPointF(left, y), QtCore.QPointF(left + width, y))

        # Bar lines: a light one at the head of the measure, a heavier one at its
        # tail. This is the feature that makes the whole thing read as tab.
        painter.setPen(QtGui.QPen(lines, 1))
        painter.drawLine(
            QtCore.QPointF(left, self.y_for_lane(0, slot) - self.line_spacing / 2),
            QtCore.QPointF(left, self.y_for_lane(LANE_COUNT - 1, slot) + self.line_spacing / 2),
        )
        painter.setPen(QtGui.QPen(lines, 2 if current else 1))
        painter.drawLine(
            QtCore.QPointF(left + width, self.y_for_lane(0, slot) - self.line_spacing / 2),
            QtCore.QPointF(left + width, self.y_for_lane(LANE_COUNT - 1, slot) + self.line_spacing / 2),
        )

    def _paint_measure_notes(
        self, painter: QtGui.QPainter, slot: int, index: int, current: bool
    ) -> None:
        """Note markers on their string lines.

        A mark, not a numbered block: which line a note sits on *is* the pitch, the
        same way it is in printed tab. A note at the very start or end of a measure
        would be half-clipped by the bar line, so it is nudged inward.
        """
        span = self._spans[index]
        left = self.measure_left()
        width = self.measure_width()
        radius = self.marker_radius()
        nudge = radius + px(2)

        for note in self.notes_in_measure(index):
            if not span.is_complete:
                continue
            x = left + (note.time - span.start) / span.duration * width
            if x < left + nudge - 1e-6 or x > left + width - nudge + 1e-6:
                x = min(max(x, left + nudge), left + width - nudge)
            y = self.y_for_lane(note.lane, slot)

            colour = QtGui.QColor(LANE_COLORS[note.lane % len(LANE_COLORS)])
            if not current:
                colour.setAlpha(150)
            painter.setPen(QtGui.QPen(colour.darker(130), 1))
            painter.setBrush(colour)
            painter.drawEllipse(QtCore.QPointF(x, y), radius, radius)
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

    def _paint_beat_line(self, painter: QtGui.QPainter) -> None:
        """The line that goes left to right.

        Only across the current measure: drawn over the previous and next it would
        read as a playhead through the whole page, which is the scrolling idea this
        replaced.

        The tolerance is drawn as **two thin lines at the window edges**, not as a
        filled band. A bar of 4/4 is 3.16s and the measure spans the full window
        width, so +/-80ms is a 5% slice -- as a solid fill that is a 77px green wall
        with a note hidden inside it, which reads as a bug. As edges it says the same
        thing and leaves the notes visible.
        """
        x = self.beat_line_x()
        top = self.y_for_lane(0, 1) - self.line_spacing
        bottom = self.y_for_lane(LANE_COUNT - 1, 1) + self.line_spacing
        half = (
            GOOD_SECONDS
            / max(1e-6, self._current_measure_duration())
            * self.measure_width()
        )

        painter.setPen(QtGui.QPen(QtGui.QColor(COLORS["accent_hi"]), 1))
        for edge in (x - half, x + half):
            painter.drawLine(QtCore.QPointF(edge, top), QtCore.QPointF(edge, bottom))

        painter.setPen(QtGui.QPen(QtGui.QColor(COLORS["text"]), px(2)))
        painter.drawLine(QtCore.QPointF(x, top), QtCore.QPointF(x, bottom))

    def _current_measure_duration(self) -> float:
        if not self._spans:
            return 1.0
        span = self._spans[self.current_measure]
        return span.duration if span.duration > 0 else 1.0


def _measure_spans(chart: Chart) -> tuple[MeasureSpan, ...]:
    """Every measure in the chart, as ``(start, end)``.

    Taken from ``chart.bar_lines`` when they exist, because that is what the notation
    actually says, and the time signature is only a fallback. Interpolating from the
    tempo would silently mis-draw a chart whose bars are not evenly spaced.
    """
    starts = [bar.time for bar in chart.bar_lines]
    if not starts:
        return ()

    nominal = _nominal_measure_seconds(chart)
    last_note = max(note.time for note in chart.notes)
    spans = []
    for index, start in enumerate(starts):
        if index + 1 < len(starts):
            end = starts[index + 1]
        else:
            # The final measure has no following bar line. Extend it far enough to
            # hold the last note, so a note written after the last bar does not get
            # clamped to the right edge.
            end = max(start + nominal, last_note)
        spans.append(MeasureSpan(index=index, start=start, end=max(end, start + 1e-6)))
    return tuple(spans)


def _nominal_measure_seconds(chart: Chart) -> float:
    numerator = chart.time_signature[0] if chart.time_signature else 4
    beats = max(1, numerator)
    return beats * 60.0 / max(1, chart.tempo)


def _beats_per_measure(chart: Chart | None, index: int) -> int:
    numerator = chart.time_signature[0] if chart and chart.time_signature else 4
    return max(1, numerator)
