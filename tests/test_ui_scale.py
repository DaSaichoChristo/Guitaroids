"""Tests for the UI scale.

The scale is what stops a 3440x1440 display rendering a 520px column in the middle
of it (DESIGN.md §12.2). Two things have to hold, and they are checked separately:

- **The policy** -- which factor a given screen height produces, and the clamps.
  Pure arithmetic, no Qt.
- **That everything actually moves together** -- the QSS, the layout helpers, and
  the raw pixel values inside the screens. A scale that grows the type but not the
  column would look worse than no scale at all, so the layout tests measure the
  whole screen at 1.0 and at 1.5.
"""

from __future__ import annotations

import pytest

from guitaroids.ui import theme


# --- the policy --------------------------------------------------------------


def test_1080p_is_the_reference_and_is_unchanged() -> None:
    """The size the design was measured at must not move.

    Everything else in the UI was judged at 1080p, so 1.0 there is what makes this
    a scale rather than a redesign.
    """
    assert theme.scale_for_height(1080) == 1.0
    assert theme.build_stylesheet(theme.scale_for_height(1080)) == theme.STYLESHEET


@pytest.mark.parametrize("height", [640, 768, 900, 1000, 1080])
def test_small_screens_never_shrink(height: int) -> None:
    """Scaling down would trade §11.6 and §13.2 for fresh clipping bugs.

    14px is already small, and a short window should get a scrollbar, not smaller
    type.
    """
    assert theme.scale_for_height(height) == theme.MIN_SCALE


@pytest.mark.parametrize("height", [2160, 2560, 3840])
def test_huge_screens_are_capped(height: int) -> None:
    """Past about 1.5x the column reads as a web page, not a menu."""
    assert theme.scale_for_height(height) == theme.MAX_SCALE


def test_the_scale_rises_monotonically() -> None:
    heights = range(700, 2200, 50)
    scales = [theme.scale_for_height(h) for h in heights]
    assert scales == sorted(scales), "a taller screen must never give a smaller scale"


def test_1440_is_the_cobserved_case() -> None:
    """The display this was built on. 1.33, not clamped."""
    assert theme.scale_for_height(1440) == pytest.approx(1440 / 1080)


# --- the helpers -------------------------------------------------------------


def test_set_scale_clamps_and_returns_what_it_stored() -> None:
    assert theme.set_scale(0.5) == theme.MIN_SCALE
    assert theme.scale() == theme.MIN_SCALE
    assert theme.set_scale(9.0) == theme.MAX_SCALE
    assert theme.scale() == theme.MAX_SCALE
    theme.set_scale(1.25)
    assert theme.scale() == 1.25


def test_px_scales_and_never_returns_zero() -> None:
    """A zero-height slider groove is a rendering artefact, not a thin groove."""
    assert theme.px(14, 1.0) == 14
    assert theme.px(14, 1.5) == 21
    assert theme.px(5, 0.01) == 1


def test_px_uses_the_process_scale_by_default() -> None:
    theme.set_scale(1.5)
    assert theme.px(100) == 150
    assert theme.px(100, 1.0) == 100, "an explicit factor must win over the global"


def test_radii_scale_more_gently_than_lengths() -> None:
    """Otherwise a 1.33x scale gives visibly pill-shaped buttons."""
    assert theme.radius(6, 1.0) == 6
    assert theme.radius(6, 4.0) < 6 * 4.0
    assert theme.radius(6, 1.5) == 7


def test_the_stylesheet_stays_balanced_at_every_scale() -> None:
    """The QSS braces are doubled for the f-string, so this is worth pinning."""
    for factor in (1.0, 1.1, 1.33, 1.5):
        sheet = theme.build_stylesheet(factor)
        assert sheet.count("{") == sheet.count("}"), factor


def test_every_colour_token_survives_scaling() -> None:
    for factor in (1.0, 1.5):
        sheet = theme.build_stylesheet(factor)
        for value in theme.COLORS.values():
            assert value in sheet, f"{value} lost at scale {factor}"


def test_the_base_font_grows_with_the_scale() -> None:
    small = theme.build_stylesheet(1.0)
    large = theme.build_stylesheet(1.5)
    assert "font-size: 14px" in small
    assert "font-size: 21px" in large


# --- the screens move together ------------------------------------------------


def _column_width(scale: float) -> int:
    """The width content_column actually caps its container at."""
    from PySide6 import QtWidgets

    from guitaroids.ui.screens import content_column

    parent = QtWidgets.QWidget()
    content_column(parent, margin=40, max_width=520, scale_factor=scale)
    container = parent.findChild(QtWidgets.QWidget, "column")
    return container.maximumWidth()


def _button_width(scale: float) -> int:
    from guitaroids.ui.screens import constrained_button

    return constrained_button("x", width=240, scale_factor=scale).maximumWidth()


def test_the_column_and_the_buttons_scale_together(qapp) -> None:
    """They must move as a pair.

    A column that grows while the buttons do not is worse than no scaling at all,
    because the content stops looking designed.
    """
    assert _column_width(1.5) == 780
    assert _button_width(1.5) == 360
    assert _column_width(1.0) == 520
    assert _button_width(1.0) == 240


def test_the_shell_minimum_size_grows_with_the_scale(qapp) -> None:
    """A minimum that does not grow would starve the enlarged UI of room.

    §11.6 records what a layout does when it is given less space than it needs:
    it does not clip, it compresses, and the widgets overlap.
    """
    from guitaroids.context import AppContext
    from guitaroids.ui.shell import MainWindow

    theme.set_scale(1.0)
    small = MainWindow(AppContext()).minimumSize()
    theme.set_scale(1.5)
    large = MainWindow(AppContext()).minimumSize()
    assert small.width() == 720 and small.height() == 480
    assert large.width() == 1080 and large.height() == 720


def test_the_screenshot_tool_pins_its_scale_to_its_frame_size() -> None:
    """A regression guard for a mistake this change introduced.

    ``screenshot_ui.py`` renders a fixed 960x640 frame. Left to itself it inherited
    the display's scale -- 1.33 on the machine this was built on -- and drew a 1.33x
    UI into a 1080p frame, which silently clipped the Quit button off the bottom of
    the main menu. These frames are the only screenshots anyone reviews, so a tool
    that quietly produces the wrong ones is worth pinning.
    """
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "screenshot_ui", root / "scripts" / "screenshot_ui.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    args = module.build_parser().parse_args([])
    assert args.scale == 1.0, "the tool must not inherit the display's scale"
    assert args.size == "960x640"

    # And the override works, which is how the enlarged layout gets inspected.
    scaled = module.build_parser().parse_args(["--scale", "1.5", "--size", "1440x960"])
    assert scaled.scale == 1.5
    assert scaled.size == "1440x960"


def test_no_screen_breaks_at_the_maximum_scale(shell, qapp) -> None:
    """The whole point: 1.5x must not reintroduce §11.6 or §13.2.

    Both of those bugs are invisible at 960x640 and appear once there is room for
    the layout to misbehave, so this is the size where they would come back.
    """
    theme.set_scale(theme.MAX_SCALE)
    qapp.setStyleSheet(theme.build_stylesheet())
    shell.resize(3440, 1440)
    shell.show()
    qapp.processEvents()

    from PySide6 import QtWidgets

    from guitaroids.ui.screens import Screen

    for screen in Screen:
        shell.navigate(screen)
        qapp.processEvents()
        for box in shell.current_screen.findChildren(QtWidgets.QGroupBox):
            layout = box.layout()
            if layout is not None:
                assert box.height() >= layout.minimumSize().height() - 2, (
                    f"{screen.label}: {box.title()!r} compressed at max scale"
                )
        for label in shell.current_screen.findChildren(QtWidgets.QLabel):
            if label.wordWrap() and label.text():
                assert label.height() >= label.sizeHint().height() - 2, (
                    f"{screen.label}: clipped {label.text()[:30]!r} at max scale"
                )


def test_a_disabled_primary_button_is_dimmed() -> None:
    """An id selector beats a pseudo-state, so this needed saying out loud.

    ``QPushButton#primary`` outranks ``QPushButton:disabled``, which left every
    disabled primary button at full accent green -- a live-looking control that
    does nothing when pressed. Found by looking at Import GP's Add button before
    a tab had been chosen.
    """
    from guitaroids.ui.theme import build_stylesheet

    sheet = build_stylesheet()
    assert "QPushButton#primary:disabled" in sheet
