"""Tests for the background renderer.

The shape under test is the same one :mod:`tests.test_library_loader` checks for a
scan, because it is the same problem: CPU-heavy work off the GUI thread, and the
promise that **exactly one** terminal signal arrives, so a screen waiting on "the
audio is ready" is never left waiting forever.

These tests never open a sound device. Rendering is pure numpy.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from qtsupport import SignalSpy, wait_until
from songbuild import make_chart

from guitaroids.audio.render import RenderResult
from guitaroids.model.chart import Chart
from guitaroids.ui.render_task import ChartRenderer, estimate_seconds

ROOT = Path(__file__).resolve().parent.parent
soundfont = next(
    (p for p in (ROOT / "assets/soundfont.sf3", ROOT / "assets/soundfont.sf2") if p.is_file()),
    None,
)


@pytest.fixture()
def renderer(qapp) -> ChartRenderer:
    """A renderer, with a QApplication.

    ``qapp`` is not optional and not used directly: the render finishes on a pool
    thread and its signal is delivered by Qt's queued connection, which needs an
    event loop to be pumped. Without it the signal is never delivered and every
    test here waits for its full timeout.
    """
    instance = ChartRenderer()
    yield instance
    instance.cancel()
    wait_until(lambda: not instance.is_running, timeout_ms=15_000)


def a_chart(notes: int = 8) -> Chart:
    return make_chart(
        [(0.5 + i * 0.25, i % 6, 0) for i in range(notes)], collapse=False, tempo=120
    )


# --- the happy path ---------------------------------------------------------------


def test_a_render_arrives_as_a_result(renderer: ChartRenderer) -> None:
    ready = SignalSpy(renderer.ready)
    assert renderer.start(a_chart(), backend="pluck") is True
    assert ready.wait(timeout_ms=20_000), ready.describe()
    result = ready.first[0]
    assert isinstance(result, RenderResult)
    assert result.backend == "pluck"
    assert result.samples.dtype.name == "float32"
    assert np_finite(result)


def np_finite(result: RenderResult) -> bool:
    import numpy as np

    return bool(np.isfinite(result.samples).all())


def test_the_render_reports_the_notes_it_rendered(renderer: ChartRenderer) -> None:
    """So the screen can say "4099 notes" without re-counting the chart."""
    ready = SignalSpy(renderer.ready)
    renderer.start(a_chart(12), backend="pluck")
    assert ready.wait(timeout_ms=20_000)
    assert ready.first[0].note_count == 12


# --- exactly one terminal signal --------------------------------------------------


def test_a_render_clears_the_guard(renderer: ChartRenderer) -> None:
    ready = SignalSpy(renderer.ready)
    renderer.start(a_chart(), backend="pluck")
    assert ready.wait(timeout_ms=20_000)
    assert wait_until(lambda: not renderer.is_running)
    assert renderer.is_running is False


def test_only_one_terminal_signal_arrives(renderer: ChartRenderer) -> None:
    """The guarantee a screen waiting on this state depends on.

    A render that emitted both `ready` and `failed`, or `cancelled` after `ready`,
    would leave a screen that started playback for a song it has also given up on.
    """
    ready = SignalSpy(renderer.ready)
    failed = SignalSpy(renderer.failed)
    cancelled = SignalSpy(renderer.cancelled)
    renderer.start(a_chart(), backend="pluck")
    assert ready.wait(timeout_ms=20_000)
    time.sleep(0.3)  # long enough for a stray signal to arrive
    assert ready.count == 1
    assert failed.count == 0
    assert cancelled.count == 0


@pytest.mark.parametrize("backend", ["soundfont", "pluck"])
def test_an_unrenderable_chart_fails_rather_than_hanging(renderer: ChartRenderer, backend) -> None:
    """No notes, or a soundfont that is not there: a message, not a hang."""
    from dataclasses import replace

    empty = replace(make_chart([(1.0, 0, 0)]), notes=())
    failed = SignalSpy(renderer.failed)
    ready = SignalSpy(renderer.ready)
    assert renderer.start(empty, backend=backend) is True
    assert failed.wait(timeout_ms=20_000), f"nothing arrived: {ready.describe()}"
    assert "no notes" in failed.first[0]
    assert ready.count == 0


def test_asking_for_a_soundfont_that_is_absent_fails_clearly(renderer: ChartRenderer) -> None:
    """The message has to name the problem, or the player cannot act on it."""
    failed = SignalSpy(renderer.failed)
    renderer.start(a_chart(), backend="soundfont", soundfont=Path("/nonexistent.sf3"))
    assert failed.wait(timeout_ms=20_000), failed.describe()
    assert "no soundfont" in failed.first[0]


# --- cancellation ------------------------------------------------------------------


def test_a_second_render_is_refused_while_one_is_in_flight(renderer: ChartRenderer) -> None:
    """Not queued. Two renders of the same chart would race to deliver two buffers
    and the loser's would start playing at the wrong moment."""
    slow = a_chart(400)
    assert renderer.start(slow, backend="pluck") is True
    assert renderer.start(slow, backend="pluck") is False
    wait_until(lambda: not renderer.is_running, timeout_ms=30_000)


def test_cancelling_gives_up_and_says_so(renderer: ChartRenderer) -> None:
    """The player has moved on: no buffer, and a terminal signal either way."""
    cancelled = SignalSpy(renderer.cancelled)
    ready = SignalSpy(renderer.ready)
    renderer.start(a_chart(2000), backend="pluck")
    renderer.cancel()
    assert cancelled.wait(timeout_ms=30_000), f"nothing arrived: {ready.describe()}"
    assert ready.count == 0, "a cancelled render must not deliver a buffer"


def test_a_cancelled_render_leaves_the_renderer_usable(renderer: ChartRenderer) -> None:
    """Cancelling must not wedge it -- the next song has to be able to start."""
    cancelled = SignalSpy(renderer.cancelled)
    renderer.start(a_chart(2000), backend="pluck")
    renderer.cancel()
    assert cancelled.wait(timeout_ms=30_000)
    assert wait_until(lambda: not renderer.is_running, timeout_ms=15_000)

    ready = SignalSpy(renderer.ready)
    assert renderer.start(a_chart(4), backend="pluck") is True
    assert ready.wait(timeout_ms=20_000), ready.describe()


def test_cancelling_before_the_task_starts_still_terminates(renderer: ChartRenderer) -> None:
    """A cancel that lands first must not be lost, leaving the guard set forever."""
    cancelled = SignalSpy(renderer.cancelled)
    renderer.start(a_chart(2000), backend="pluck")
    renderer.cancel()
    renderer.cancel()
    assert cancelled.wait(timeout_ms=30_000)
    assert wait_until(lambda: not renderer.is_running, timeout_ms=15_000)


# --- the estimate, which is shown to a waiting player -------------------------------


def test_the_estimate_is_positive_and_proportional() -> None:
    """A player staring at a frozen screen needs a number, and a longer song needs a
    bigger one.

    Proportional to the *audio*, so the fixture has to have longer notes rather than
    more bars: `beats` extends the bar lines but the last note is what
    ``chart.duration`` reports, and a chart of one note at 1.0s is one second long
    however many empty bars follow it.
    """
    short = [(0.5 + i * 0.5, i % 6, 0) for i in range(8)]      # ~3.5s
    long = [(0.5 + i * 0.5, i % 6, 0) for i in range(80)]     # ~40s
    small = estimate_seconds(make_chart(short, collapse=False, tempo=120))
    big = estimate_seconds(make_chart(long, collapse=False, tempo=120))
    assert small > 0.0
    assert big > small * 5, f"{small:.3f}s vs {big:.3f}s -- not proportional"


def test_the_estimate_is_deliberately_pessimistic() -> None:
    """Wrong in the direction of "longer" beats wrong in the direction of "two
    seconds", which the player experiences as a lie.

    Measured here: the pluck backend renders about 650x real time, so the estimate's
    40x divisor is an order of magnitude conservative for the fallback and about 25%
    conservative for the soundfont.
    """
    import time

    from guitaroids.audio.render import render_chart

    chart = make_chart([(0.1 * i, i % 6, 0) for i in range(40)], collapse=False, tempo=60)
    started = time.perf_counter()
    render_chart(chart, backend="pluck")
    actual = time.perf_counter() - started
    assert estimate_seconds(chart) >= actual, (
        f"promised {estimate_seconds(chart):.2f}s, took {actual:.2f}s"
    )
