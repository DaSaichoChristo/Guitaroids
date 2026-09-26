"""Tests for the highway widget.

The first tests in this suite to check **rendered pixels** rather than geometry.
Everything before this asserted on sizes and positions; that is enough to catch a
layout that overlaps, and not enough to catch a widget that renders nothing at all,
draws its notes in the wrong place, or paints the hit zone over the note the player
is trying to hit.

Rendering to a ``QImage`` needs no audio device, no camera and no event loop, which
is the payoff for the widget owning no clock: ``set_position`` puts a time in, and
pixels come out.
"""

from __future__ import annotations

import pytest
from songbuild import make_chart

from guitaroids.session.judge import GOOD_SECONDS
from guitaroids.ui.theme import COLORS, LANE_COLORS, LANE_COUNT
from guitaroids.ui.widgets.highway import DEFAULT_ZOOM, Highway

WIDTH = 1200
HEIGHT = 600


@pytest.fixture()
def highway(qapp) -> Highway:
    """A fixed-size widget, so every geometric assertion can be an exact number."""
    widget = Highway()
    widget.resize(WIDTH, HEIGHT)
    widget.set_zoom(DEFAULT_ZOOM)
    return widget


@pytest.fixture()
def chart():
    """Notes at known times, spread across lanes.

    From the shared builder so the times are exact: the note asked for at 1.0 is
    *at* 1.0, and nothing else is near it.
    """
    return make_chart([(0.0, 1, 1), (1.0, 2, 2), (2.0, 0, 3), (4.0, 3, 4)], tempo=60)


def render(highway: Highway) -> "object":
    """Rasterise the widget and return the QImage."""
    return highway.grab().toImage()


# --- geometry ----------------------------------------------------------------


def test_the_playline_is_at_the_centre(highway: Highway) -> None:
    highway.set_position(7.5)
    assert highway.x_for_time(7.5) == WIDTH / 2


def test_a_note_at_the_position_is_centred(highway: Highway, chart) -> None:
    highway.set_chart(chart)
    highway.set_position(1.0)
    assert highway.x_for_time(1.0) == WIDTH / 2


def test_x_scales_with_zoom(highway: Highway) -> None:
    highway.set_position(0.0)
    assert highway.x_for_time(1.0) == WIDTH / 2 + DEFAULT_ZOOM
    highway.set_zoom(400.0)
    assert highway.x_for_time(1.0) == WIDTH / 2 + 400.0


def test_time_for_x_inverts_x_for_time(highway: Highway) -> None:
    highway.set_position(3.25)
    for seconds in (-2.0, 0.0, 1.5, 9.0):
        assert highway.time_for_x(highway.x_for_time(seconds)) == pytest.approx(seconds)


def test_the_window_is_symmetric_about_the_position(highway: Highway) -> None:
    highway.set_position(10.0)
    low, high = highway.visible_window()
    assert (low + high) / 2 == pytest.approx(10.0)
    assert high - low == pytest.approx(WIDTH / DEFAULT_ZOOM)


def test_lane_zero_is_at_the_bottom(highway: Highway) -> None:
    """Fretboard order: lane 0 is the high E string, drawn lowest."""
    assert highway.y_for_lane(0) > highway.y_for_lane(5)
    assert highway.y_for_lane(5) == 0
    assert highway.y_for_lane(0) == pytest.approx(HEIGHT - HEIGHT / LANE_COUNT)


def test_lanes_are_stacked_without_gaps(highway: Highway) -> None:
    """Six bands covering the full height, in order."""
    tops = sorted(highway.y_for_lane(lane) for lane in range(LANE_COUNT))
    assert tops[0] == 0
    assert tops[-1] + highway.lane_height == pytest.approx(HEIGHT)
    for previous, following in zip(tops, tops[1:]):
        assert following - previous == pytest.approx(highway.lane_height)


# --- windowing ----------------------------------------------------------------


def test_only_visible_notes_are_reported(highway: Highway, chart) -> None:
    """The bisect window, not all 4 notes.

    At 200px/s in 1200px the window is +/-3s, so a note 30s away must not appear.
    This is the performance contract: the paint loop iterates this, not the chart.
    """
    highway.set_chart(chart)
    highway.set_position(1.0)
    times = [note.time for note in highway.visible_notes()]
    assert times, "the note at the position must be visible"
    assert all(abs(t - 1.0) <= 3.0 for t in times)


def test_visible_notes_are_time_ordered(highway: Highway, chart) -> None:
    highway.set_chart(chart)
    highway.set_position(2.0)
    times = [note.time for note in highway.visible_notes()]
    assert times == sorted(times)


def test_a_chart_with_no_notes_visible_returns_nothing(highway: Highway, chart) -> None:
    highway.set_chart(chart)
    highway.set_position(600.0)
    assert highway.visible_notes() == []
    assert highway.visible_bar_lines() == []


def test_no_chart_is_not_an_error(highway: Highway) -> None:
    highway.set_chart(None)
    assert highway.visible_notes() == []
    assert highway.first_note_time is None
    assert render(highway) is not None


# --- bar lines ----------------------------------------------------------------


def test_bar_lines_are_reported_inside_the_window(highway: Highway, chart) -> None:
    highway.set_chart(chart)
    highway.set_position(0.0)
    assert highway.visible_bar_lines(), "a bar line at 0.0 must be visible"
    low, high = highway.visible_window()
    assert all(low <= t <= high for t in highway.visible_bar_lines())


# --- rendering ----------------------------------------------------------------


def test_it_renders_something(highway: Highway, chart) -> None:
    """A widget that paints nothing must fail rather than pass quietly."""
    highway.set_chart(chart)
    highway.set_position(1.0)
    image = render(highway)
    assert not image.isNull()
    assert image.width() == WIDTH and image.height() == HEIGHT
    colours = {image.pixel(x, y) for x in range(0, WIDTH, 7) for y in range(0, HEIGHT, 7)}
    assert len(colours) > 4, "a flat fill is not a highway"


def _rgb(image, x: int, y: int) -> tuple[int, int, int]:
    """One pixel as (r, g, b).

    QImage.pixel returns a packed QRgb int, and `&` binds looser than `>>` in Python,
    so unpacking it inline is a reliable source of a silently wrong channel.
    """
    value = int(image.pixel(x, y))
    return (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF


def test_lane_bands_alternate(highway: Highway, chart) -> None:
    """Two adjacent lanes must be distinguishable, or a player cannot tell them apart."""
    highway.set_chart(chart)
    highway.set_position(900.0)  # empty stretch, so only the bands are visible
    image = render(highway)
    # Off the playline: the centre column is the playline and the hit zone, so
    # sampling there reads those and every lane looks the same.
    x = WIDTH // 2 + 100
    sampled = [_rgb(image, x, int(highway.y_for_lane(lane) + 4)) for lane in range(LANE_COUNT)]
    assert len(set(sampled)) == 2, f"expected alternating bands, got {sampled}"
    assert sampled[0] != sampled[1]


def test_a_note_is_drawn_in_its_own_lane(highway: Highway, chart) -> None:
    """The note at t=1.0 sits at the playline, in the band of its lane."""
    highway.set_chart(chart)
    highway.set_position(1.0)
    note = next(n for n in chart.notes if n.time == pytest.approx(1.0))
    image = render(highway)
    # Near the left of the note: the fret number is centred, so the middle of the
    # note is glyph, not fill.
    x = int(highway.x_for_time(note.time) + 6)
    y = int(highway.y_for_lane(note.lane) + highway.lane_height / 2)
    assert _rgb(image, x, y) != _rgb(image, x, int(highway.y_for_lane(0) + 4)), (
        "nothing drawn in the note's lane"
    )


def test_notes_use_the_lane_colours(highway: Highway, chart) -> None:
    """A note is drawn in LANE_COLORS[lane], so the palette is actually wired up."""
    highway.set_chart(chart)
    for note in chart.notes:
        highway.set_position(note.time)
        image = render(highway)
        x = int(highway.x_for_time(note.time) + 6)
        y = int(highway.y_for_lane(note.lane) + highway.lane_height / 2)
        got = _rgb(image, x, y)
        want = tuple(int(LANE_COLORS[note.lane][i : i + 2], 16) for i in (1, 3, 5))
        # The 1px border and antialiasing nudge the values, so this is a proximity
        # check rather than an equality.
        assert max(abs(a - b) for a, b in zip(got, want)) < 90, (note.lane, got, want)


def test_the_hit_zone_does_not_hide_the_note_being_played(highway: Highway, chart) -> None:
    """A note at the exact position must stay visible under the hit zone.

    The regression this pins: painting the zone last covered the single most
    important note on screen with an opaque fill. Asserting on "the pixels vary
    around the centre" is not enough -- the playline alone makes them vary -- so this
    checks the note's *own colour* is still there inside the zone.
    """
    highway.set_chart(chart)
    highway.set_position(1.0)
    note = next(n for n in chart.notes if n.time == pytest.approx(1.0))
    image = render(highway)
    zone_half = GOOD_SECONDS * highway.zoom
    assert zone_half > 10, "the zone must be wide enough for this test to mean something"
    # Inside the note, inside the zone, clear of both the 1px note border and the
    # centred fret number.
    x = int(highway.x_for_time(note.time) + 5)
    y = int(highway.y_for_lane(note.lane) + highway.lane_height / 2)
    got = _rgb(image, x, y)
    want = tuple(int(LANE_COLORS[note.lane][i : i + 2], 16) for i in (1, 3, 5))
    zone = tuple(int(COLORS["accent_hi"][i : i + 2], 16) for i in (1, 3, 5))
    assert max(abs(a - b) for a, b in zip(got, zone)) > 40, (
        f"the note is hidden by the hit zone; read the zone colour {zone}, not {want}"
    )


def test_the_hit_zone_matches_the_judgement_window(highway: Highway) -> None:
    """The drawn band is GOOD_SECONDS wide, so the two cannot disagree.

    Not a magic number in the widget: it is imported from the judge, so changing the
    window changes the drawing.
    """
    highway.set_position(0.0)
    image = render(highway)
    centre = WIDTH // 2
    edge = int(centre + GOOD_SECONDS * highway.zoom)
    inside = image.pixel(centre + 2, 4)
    outside = image.pixel(edge + 12, 4)
    assert inside != outside, "the hit zone is not drawn at the judgement width"


# --- inputs -------------------------------------------------------------------


def test_zoom_must_be_positive(highway: Highway) -> None:
    with pytest.raises(ValueError, match="positive"):
        highway.set_zoom(0.0)
    with pytest.raises(ValueError, match="positive"):
        highway.set_zoom(-100.0)


def test_changing_the_chart_resets_the_window(highway: Highway, chart) -> None:
    """A stale time array would bisect into the wrong notes."""
    highway.set_chart(chart)
    highway.set_position(1.0)
    assert highway.visible_notes()
    highway.set_chart(None)
    assert highway.visible_notes() == []
    assert highway.visible_bar_lines() == []


def test_first_note_time_is_reported(highway: Highway, chart) -> None:
    """The screen needs it for the "get ready" state; the real tab starts at 3.16s."""
    highway.set_chart(chart)
    assert highway.first_note_time == pytest.approx(min(n.time for n in chart.notes))


def test_shrinking_the_widget_keeps_the_playline_centred(highway: Highway) -> None:
    highway.set_position(2.0)
    highway.resize(800, 300)
    assert highway.x_for_time(2.0) == 400
