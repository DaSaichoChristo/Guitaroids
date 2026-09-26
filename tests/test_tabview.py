"""Tests for the tab view.

Replaces the highway's tests rather than supplementing them: §16 replaced the
scrolling highway outright, and keeping tests for a widget that no longer exists
would only preserve the shape of an idea that was rejected.

The interesting properties here are different from the highway's, because the
question changed. The highway's tests were about *pixels* ("does a note appear in
its lane"); the tab view's are about **which measure is on screen and where in it
the beat is** -- plus the pixel tests, because that is still the only way this
suite can catch a widget that draws the wrong thing.
"""

from __future__ import annotations

import pytest
from songbuild import make_chart

from guitaroids.ui.theme import COLORS, LANE_COLORS, LANE_COUNT
from guitaroids.ui.widgets.tabview import MEASURES_SHOWN, TabView

WIDTH = 1400
HEIGHT = 800

#: 4/4 at tempo 60: a beat is a second and a bar is four. Fixed bars are what make
#: the expected x positions in these tests arithmetic rather than measurement.
BEAT = 1.0
BAR = 4.0


@pytest.fixture()
def view(qapp) -> TabView:
    widget = TabView()
    widget.resize(WIDTH, HEIGHT)
    return widget


@pytest.fixture()
def chart():
    """One note in each of the first four bars, on four different strings."""
    # Five bars, so the last bar under test has a neighbour on each side.
    return make_chart(
        [
            (0.5 * BEAT, 0, 0),
            (0.5 * BEAT + BAR, 1, 1),
            (0.5 * BEAT + 2 * BAR, 2, 2),
            (0.5 * BEAT + 3 * BAR, 3, 3),
        ],
        tempo=60,
        beats=20,
    )


def render(widget: TabView):
    return widget.grab().toImage()


def _rgb(image, x: int, y: int) -> tuple[int, int, int]:
    value = int(image.pixel(x, y))
    return (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF


# --- which measure ------------------------------------------------------------


def test_three_measures_are_shown(view: TabView, chart) -> None:
    view.set_chart(chart)
    view.set_position(BAR + 0.5 * BEAT)
    assert view.visible_measures() == [0, 1, 2], "previous, current, next"
    assert MEASURES_SHOWN == 3


def test_the_middle_slot_is_the_current_measure(view: TabView, chart) -> None:
    view.set_chart(chart)
    for position, expected in [(0.5, 0), (BAR, 1), (2 * BAR, 2), (3 * BAR, 3)]:
        view.set_position(position)
        previous, current, following = view.visible_measures()
        assert current == expected, position
        assert (previous, following) == (expected - 1 if expected else None, expected + 1)


def test_before_the_first_bar_there_is_no_previous_measure(view: TabView, chart) -> None:
    """The player gets a gap, not a fabricated bar."""
    view.set_chart(chart)
    view.set_position(0.1)
    assert view.visible_measures() == [None, 0, 1]


def test_after_the_last_bar_there_is_no_next_measure(view: TabView, chart) -> None:
    """The fixture has five bars, so the last one is index 4."""
    view.set_chart(chart)
    view.set_position(4 * BAR + 1.0)
    assert view.current_measure == 4
    assert view.visible_measures() == [3, 4, None]


def test_the_view_advances_one_measure_at_a_bar_line(view: TabView, chart) -> None:
    """Discrete, not continuous -- the thing §15.2 got wrong."""
    view.set_chart(chart)
    view.set_position(BAR - 0.001)
    assert view.current_measure == 0
    view.set_position(BAR)
    assert view.current_measure == 1
    view.set_position(2 * BAR - 0.001)
    assert view.current_measure == 1
    view.set_position(2 * BAR)
    assert view.current_measure == 2


def test_measures_do_not_all_stretch_to_the_end_of_the_song(view: TabView, chart) -> None:
    """A regression that piled every note at the left edge.

    The span builder once took ``max(next_bar, last_note_time)`` for *every*
    measure, so each bar claimed to be 12 seconds long and every note in the chart
    landed at fraction 0.0.
    """
    view.set_chart(chart)
    assert view.measure_index_at(0.5) == 0
    assert view.measure_index_at(0.5 + BAR) == 1
    assert view.measure_index_at(0.5 + 3 * BAR) == 3
    spans = view._spans
    assert spans[0].end == pytest.approx(BAR), "bar 0 must end at the next bar line"
    assert spans[1].end == pytest.approx(2 * BAR)


def test_notes_are_grouped_into_their_measure(view: TabView, chart) -> None:
    view.set_chart(chart)
    for index in range(4):
        assert len(view.notes_in_measure(index)) == 1
    assert view.notes_in_measure(4) == []


# --- geometry -----------------------------------------------------------------


def test_lane_zero_is_the_top_line(view: TabView, chart) -> None:
    """Printed tab: the top line is the high E string, and lane 0 is the high E."""
    view.set_chart(chart)
    view.set_position(BAR)
    assert view.y_for_lane(0) < view.y_for_lane(LANE_COUNT - 1)
    assert view.y_for_lane(LANE_COUNT - 1) > view.block_top(1), "inset in its block"


def test_the_six_lines_span_one_block(view: TabView) -> None:
    assert view.line_spacing > 0
    top = view.y_for_lane(0, 1)
    bottom = view.y_for_lane(LANE_COUNT - 1, 1)
    assert bottom - top == pytest.approx(view.line_spacing * (LANE_COUNT - 1))
    assert top > view.block_top(1), "the staff sits inside its block, not at its edge"
    assert bottom < view.block_top(2)


def test_the_measure_start_is_at_the_left_and_its_end_at_the_right(
    view: TabView, chart
) -> None:
    view.set_chart(chart)
    view.set_position(BAR)
    index = view.current_measure
    span = view._spans[index]
    assert view.x_for_time(span.start, index) == pytest.approx(view.measure_left())
    assert view.x_for_time(span.end, index) == pytest.approx(
        view.measure_left() + view.measure_width()
    )


def test_the_middle_of_a_measure_is_the_middle_of_the_width(view: TabView, chart) -> None:
    view.set_chart(chart)
    view.set_position(2 * BAR)
    index = view.current_measure
    span = view._spans[index]
    middle = view.x_for_time((span.start + span.end) / 2, index)
    assert middle == pytest.approx(view.measure_left() + view.measure_width() / 2)


def test_the_beat_line_sweeps_left_to_right(view: TabView, chart) -> None:
    """The whole point of the redesign: progression across, measures down."""
    view.set_chart(chart)
    view.set_position(BAR)
    start = view.beat_line_x()
    view.set_position(BAR + BAR / 2)
    middle = view.beat_line_x()
    view.set_position(2 * BAR - 0.001)
    end = view.beat_line_x()

    assert start == pytest.approx(view.measure_left())
    assert start < middle < end
    assert middle == pytest.approx(view.measure_left() + view.measure_width() / 2)
    assert end == pytest.approx(view.measure_left() + view.measure_width(), abs=1.0)

    # And the reset: at the bar line the view advances, so the line is back at the left.
    view.set_position(2 * BAR)
    assert view.current_measure == 2
    assert view.beat_line_x() == pytest.approx(view.measure_left())


def test_a_time_outside_its_measure_is_clamped_not_extrapolated(view: TabView, chart) -> None:
    """Extrapolating would draw the next bar's notes spread across this one."""
    view.set_chart(chart)
    view.set_position(BAR)
    index = view.current_measure
    left, width = view.measure_left(), view.measure_width()
    assert view.x_for_time(-99.0, index) == pytest.approx(left)
    assert view.x_for_time(99.0, index) == pytest.approx(left + width)


def test_the_beat_line_uses_the_current_measure(view: TabView, chart) -> None:
    view.set_chart(chart)
    for position in (0.5 * BEAT, BAR + 1.25, 3 * BAR + 2.0):
        view.set_position(position)
        assert view.beat_line_x() == pytest.approx(
            view.x_for_time(position, view.current_measure)
        )


def test_the_marker_scales_with_the_staff(view: TabView) -> None:
    """A fixed pixel size would look wrong next to a rescaled staff (§14)."""
    view.set_chart(make_chart([(0.5, 0, 0)]))
    small = view.marker_radius()
    view.resize(WIDTH, HEIGHT * 2)
    assert view.marker_radius() > small


# --- rendering -----------------------------------------------------------------


def test_it_renders_something(view: TabView, chart) -> None:
    view.set_chart(chart)
    view.set_position(BAR)
    image = render(view)
    assert not image.isNull()
    assert image.width() == WIDTH and image.height() == HEIGHT
    sampled = {
        image.pixel(x, y)
        for x in range(0, WIDTH, 11)
        for y in range(0, HEIGHT, 11)
    }
    assert len(sampled) > 4, "a flat fill is not a tab"


def test_six_string_lines_are_drawn(view: TabView, chart) -> None:
    """One per string, at the y positions the geometry reports."""
    view.set_chart(chart)
    view.set_position(BAR)
    image = render(view)
    left = int(view.measure_left() + view.measure_width() * 0.1)
    found = 0
    previous = None
    for y in range(int(view.y_for_lane(0)) - 4, int(view.y_for_lane(5)) + 5):
        pixel = _rgb(image, left, y)
        lit = sum(pixel) > 150
        if lit and previous is not True:
            found += 1
        previous = lit
    assert found == LANE_COUNT, f"expected {LANE_COUNT} lines, found {found}"


def test_a_note_is_drawn_on_its_string_line(view: TabView, chart) -> None:
    view.set_chart(chart)
    view.set_position(0.5 * BEAT)
    note = chart.notes[0]
    image = render(view)
    # Clear of the beat line, which is drawn over the note's centre.
    x = int(view.x_for_time(note.time, 0)) - 5
    y = int(view.y_for_lane(note.lane))
    got = _rgb(image, x, y)
    want = tuple(int(LANE_COLORS[note.lane][i : i + 2], 16) for i in (1, 3, 5))
    assert max(abs(a - b) for a, b in zip(got, want)) < 90, (got, want)


def test_a_note_stays_visible_under_the_beat_line(view: TabView, chart) -> None:
    """The note being played must not disappear under the moving line.

    Same failure class as the §15.4 hit zone. The line is only a few pixels wide
    against a marker of about twenty, so the note must still show either side of it.
    """
    view.set_chart(chart)
    view.set_position(0.5 * BEAT)
    note = chart.notes[0]
    image = render(view)
    centre = int(view.x_for_time(note.time, 0))
    y = int(view.y_for_lane(note.lane))
    around = {_rgb(image, centre + offset, y) for offset in (-6, 6)}
    want = tuple(int(LANE_COLORS[note.lane][i : i + 2], 16) for i in (1, 3, 5))
    for got in around:
        assert max(abs(a - b) for a, b in zip(got, want)) < 90, (got, want)


def test_a_note_is_not_drawn_in_another_measure(view: TabView, chart) -> None:
    """The bar-3 note must not appear in bar 0, which is what a broken span did."""
    view.set_chart(chart)
    view.set_position(0.5 * BEAT)
    image = render(view)
    x = int(view.x_for_time(0.5 * BEAT, 0)) - 5
    later = chart.notes[3]
    want = tuple(int(LANE_COLORS[later.lane][i : i + 2], 16) for i in (1, 3, 5))
    got = _rgb(image, x, int(view.y_for_lane(later.lane)))
    assert max(abs(a - b) for a, b in zip(got, want)) > 90, (
        f"a bar-3 note was drawn in bar 0: {got} is the note colour {want}"
    )


def test_the_beat_line_is_visible(view: TabView, chart) -> None:
    view.set_chart(chart)
    view.set_position(BAR + 1.0)
    image = render(view)
    x = int(view.beat_line_x())
    y = int(view.y_for_lane(2))
    lit = sum(_rgb(image, x, y))
    assert lit > 250, "the moving line is not drawn"
    assert _rgb(image, x - 30, y) != _rgb(image, x, y), "it is a line, not a fill"


def test_a_chart_with_no_notes_renders_blank_but_alive(view: TabView) -> None:
    view.set_chart(None)
    view.set_position(0.0)
    image = render(view)
    assert not image.isNull()
    assert view.visible_measures() == []
    assert view.notes_in_measure(0) == []


# --- inputs -------------------------------------------------------------------


def test_setting_a_chart_rebuilds_the_measures(view: TabView, chart) -> None:
    view.set_chart(chart)
    assert view._spans
    view.set_chart(None)
    assert view._spans == ()
    assert view.visible_measures() == []
    assert view.first_note_time is None


def test_position_is_reported_back(view: TabView, chart) -> None:
    view.set_chart(chart)
    view.set_position(7.25)
    assert view.position == 7.25
    assert view.chart is chart


def test_first_note_time_is_reported(view: TabView, chart) -> None:
    """The screen needs it for "get ready"; here the first note is at 0.5s."""
    view.set_chart(chart)
    assert view.first_note_time == pytest.approx(0.5)


# --- measure spans ------------------------------------------------------------


def test_spans_come_from_the_bar_lines_not_the_tempo() -> None:
    """A chart whose bars are not evenly spaced must be drawn as written.

    Interpolating from the tempo would silently mis-draw it, and nothing else in the
    suite would notice.
    """
    from dataclasses import replace

    from guitaroids.model.chart import BarLine

    chart = make_chart([(0.5, 0, 0), (5.0, 0, 1)], tempo=60, beats=16)
    assert [bar.time for bar in chart.bar_lines] == [0.0, BAR, 2 * BAR, 3 * BAR]
    view = TabView()
    view.resize(WIDTH, HEIGHT)
    view.set_chart(chart)
    assert view._spans[0].end == pytest.approx(BAR)
    assert view._spans[1].end == pytest.approx(2 * BAR)


def test_the_final_measure_reaches_the_last_note() -> None:
    """A note written after the last bar line must not be clamped to the right edge."""
    chart = make_chart([(0.5, 0, 0), (3 * BAR + 2.0, 0, 1)], tempo=60, beats=16)
    view = TabView()
    view.resize(WIDTH, HEIGHT)
    view.set_chart(chart)
    last = view._spans[-1]
    assert last.end >= 3 * BAR + 2.0
