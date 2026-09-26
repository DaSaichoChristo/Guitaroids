"""Colour tokens, the UI scale, and the application stylesheet.

Three rules keep this maintainable:

1. **The stylesheet owns typography.** Font family and size live in the QSS and
   nowhere else. `app.py` deliberately does not call `setFont`, because two owners
   for font size means changing one and wondering why nothing happened.
2. **Colours are defined once, here.** Everything else interpolates from
   :data:`COLORS`, so a palette change is a one-place edit.
3. **Every length is a 1080p design unit, multiplied by the scale.** See
   :func:`scale_for_height`.

The base style is Fusion -- the only one present on every platform -- so the app
looks the same on Windows and Linux. That is set in `app.py`, not here.

On QComboBox and QScrollBar: the QSS below sets colours, borders and padding but
deliberately does not try to style the drop-down arrow or the scrollbar groove.
Both need image assets to look right, and a half-styled arrow looks worse than
Fusion's default. This is the known fiddly part of QSS; see DESIGN.md §11.
"""

from __future__ import annotations

import math

#: Screen height the design-unit numbers below were written for. 1080p is the
#: overwhelmingly common laptop panel, and it is the size at which the UI was
#: measured to look right, so the scale is 1.0 there by definition.
REFERENCE_HEIGHT = 1080

#: Never shrink below 1.0. A short window should get a scrollbar or a compressed
#: layout, not smaller type -- 14px is already small, and DESIGN.md §11.6 records
#: what happens when a layout cannot fit.
MIN_SCALE = 1.0

#: Never grow past this. Past roughly 1.5x the column stops reading as a menu and
#: starts reading as a web page, and a 4K panel is usually viewed from further away
#: than a laptop, so it needs less enlargement than the arithmetic suggests.
MAX_SCALE = 1.5


def scale_for_height(height: int) -> float:
    """The scale factor for a screen this many pixels tall.

    Height, not width and not area: this is a full screen app, and what makes the
    UI look small is being measured against the vertical extent of the display. A
    3440x1440 ultrawide is tall enough to read comfortably from a desk, and at 1.0
    it rendered a 520px column marooned in the middle of it.

    Clamped at both ends. The lower clamp is the important one -- scaling *down* on
    a small screen would trade the clipping bugs in DESIGN.md §11.6 and §13.2 for
    fresh ones.
    """
    return max(MIN_SCALE, min(MAX_SCALE, height / REFERENCE_HEIGHT))


#: Process-wide scale, set once by `app.py` before any screen is built. A module
#: global rather than a parameter threaded through every helper, because the
#: alternative is `content_column` and `constrained_button` growing a `scale`
#: argument that all four screens have to remember to pass.
_scale: float = 1.0


def set_scale(value: float) -> float:
    """Set the process-wide scale. Returns the clamped value actually stored."""
    global _scale
    _scale = max(MIN_SCALE, min(MAX_SCALE, float(value)))
    return _scale


def scale() -> float:
    return _scale


def px(value: float, factor: float | None = None) -> int:
    """Scale a design-unit length to a pixel value.

    Rounded, and never below 1 for a positive request: a zero-height slider groove
    is a rendering artefact rather than a very thin groove.
    """
    return max(1, round(value * (_scale if factor is None else factor)))


def radius(value: float, factor: float | None = None) -> int:
    """Scale a corner radius, damped.

    Radii do not want the full factor. A 6px radius becomes 8px at 1.33, which is a
    noticeably rounder button, and past about 12px it reads as a pill. The square
    root keeps the proportion of curvature steady while the box grows.
    """
    return max(1, round(value * math.sqrt(_scale if factor is None else factor)))


#: The palette. Dark, low-saturation, so the note highway's colours stand out later.
COLORS: dict[str, str] = {
    "bg": "#14161c",          # window background
    "surface": "#1b1e26",     # panels, list backgrounds
    "surface_hi": "#2a2f3a",  # inputs, hover
    "border": "#2f3542",
    "border_hi": "#3f4757",
    "text": "#e8eaed",
    "text_dim": "#9aa0aa",
    "accent": "#1f7a4d",      # primary action
    "accent_hi": "#2a9c68",
    "danger": "#8c3a3a",
    "focus": "#2a9c68",
}

#: One colour per highway lane, indexed by ``Note.lane``.
#:
#: Separate from :data:`COLORS` because these are not used in the stylesheet, and
#: ``test_stylesheet_mentions_every_colour_token`` asserts the reverse for everything
#: in there.
#:
#: A blue-to-red hue ramp: adjacent lanes are maximally far apart at the bottom of
#: the highway, where a hand-tracking player's error is most likely to be a
#: neighbouring-lane slip. Deliberately *not* monotonic in brightness, because two
#: lanes of similar luminance are exactly the pair a player confuses.
LANE_COLORS: tuple[str, ...] = (
    "#4aa3df",  # lane 0 - high E, the busiest lane once chords collapse
    "#35b8a0",
    "#7bc043",
    "#d9c33b",
    "#e08a3c",
    "#d9534f",  # lane 5 - low E
)

#: The highway's lane count is a property of the data model (lane = string - 1 over
#: six strings), not a theme choice. Asserted against the model by a test.
LANE_COUNT = 6

def build_stylesheet(f: float | None = None) -> str:
    """The stylesheet, with every length scaled from its 1080p design value.

    ``f`` defaults to the process-wide scale set by :func:`set_scale`.

    The lengths below are deliberately written as literal design units with ``px()``
    around them rather than pre-computed, so a reader can see what the UI looks like
    at 1.0 without doing arithmetic. QSS rule braces are doubled for the f-string;
    that is the whole cost of doing this in Python rather than in a .qss file, and
    the alternative -- a template plus a substitution pass -- is a lot of machinery
    to avoid two braces.
    """
    f = _scale if f is None else f
    return f"""
/* Typography lives here, and only here. */
QWidget {{
    background: {COLORS["bg"]};
    color: {COLORS["text"]};
    font-family: "Sans Serif";
    font-size: {px(14, f)}px;
}}

/* --- headings ------------------------------------------------------------- */
QLabel#heading {{
    font-size: {px(26, f)}px;
    font-weight: 600;
    color: {COLORS["text"]};
    background: transparent;
}}
QLabel#subtitle {{
    font-size: {px(15, f)}px;
    color: {COLORS["text_dim"]};
    background: transparent;
}}
QLabel#dim {{
    color: {COLORS["text_dim"]};
    background: transparent;
}}
QLabel#stat {{
    font-family: "Monospace";
    font-size: {px(15, f)}px;
    color: {COLORS["text"]};
    background: transparent;
}}

/* --- buttons -------------------------------------------------------------- */
QPushButton {{
    background: {COLORS["surface_hi"]};
    border: 1px solid {COLORS["border"]};
    border-radius: {radius(6, f)}px;
    padding: {px(8, f)}px {px(20, f)}px;
    min-height: {px(20, f)}px;
    color: {COLORS["text"]};
}}
QPushButton:hover  {{ background: {COLORS["border_hi"]}; }}
QPushButton:pressed{{ background: {COLORS["border"]}; }}
QPushButton:disabled {{
    color: {COLORS["text_dim"]};
    background: {COLORS["surface"]};
    border-color: {COLORS["border"]};
}}
QPushButton#primary {{
    background: {COLORS["accent"]};
    border-color: {COLORS["accent_hi"]};
    font-weight: 600;
}}
QPushButton#primary:hover   {{ background: {COLORS["accent_hi"]}; }}
QPushButton#danger {{ background: {COLORS["danger"]}; border-color: {COLORS["danger"]}; }}

/* --- lists ---------------------------------------------------------------- */
QListWidget, QTreeWidget, QTableWidget {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["border"]};
    border-radius: {radius(6, f)}px;
    padding: {px(4, f)}px;
    outline: none;
}}
QListWidget::item {{
    padding: {px(8, f)}px {px(6, f)}px;
    border-radius: {radius(4, f)}px;
}}
QListWidget::item:selected   {{ background: {COLORS["accent"]}; color: #ffffff; }}
QListWidget::item:hover      {{ background: {COLORS["surface_hi"]}; }}
QListWidget::item:selected:hover {{ background: {COLORS["accent_hi"]}; }}

/* --- inputs --------------------------------------------------------------- */
/* Frame only. Fusion still draws the arrow, which needs an image asset to
   replace and looks worse when half-styled. */
QComboBox, QSpinBox, QLineEdit, QDoubleSpinBox {{
    background: {COLORS["surface_hi"]};
    border: 1px solid {COLORS["border"]};
    border-radius: {radius(6, f)}px;
    padding: {px(6, f)}px {px(10, f)}px;
    min-height: {px(20, f)}px;
}}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover {{ border-color: {COLORS["border_hi"]}; }}
QComboBox:disabled, QSpinBox:disabled {{ color: {COLORS["text_dim"]}; }}

QSlider::groove:horizontal {{
    background: {COLORS["surface"]};
    height: {px(5, f)}px;
    border-radius: {radius(2, f)}px;
}}
QSlider::sub-page:horizontal {{ background: {COLORS["accent"]}; border-radius: {radius(2, f)}px; }}
QSlider::handle:horizontal {{
    background: {COLORS["text"]};
    width: {px(14, f)}px;
    margin: -{px(5, f)}px 0;
    border-radius: {radius(7, f)}px;
}}
QSlider:disabled::sub-page:horizontal {{ background: {COLORS["border"]}; }}

/* --- structure ------------------------------------------------------------ */
QGroupBox {{
    border: 1px solid {COLORS["border"]};
    border-radius: {radius(6, f)}px;
    margin-top: {px(14, f)}px;
    padding-top: {px(10, f)}px;
    background: {COLORS["surface"]};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: {px(10, f)}px;
    padding: 0 {px(4, f)}px;
    color: {COLORS["text_dim"]};
}}

QScrollBar:vertical {{
    background: transparent;
    width: {px(10, f)}px;
    margin: {px(2, f)}px;
}}
QScrollBar::handle:vertical {{
    background: {COLORS["border_hi"]};
    border-radius: {radius(5, f)}px;
    min-height: {px(30, f)}px;
}}
QScrollBar::handle:vertical:hover {{ background: {COLORS["text_dim"]}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QFrame#card {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["border"]};
    border-radius: {radius(8, f)}px;
}}
QFrame#divider {{ background: {COLORS["border"]}; max-height: 1px; border: none; }}

/* --- the highway ---------------------------------------------------------- */
/* Painted in QPainter rather than assembled from widgets, so its text cannot be
   styled by the stylesheet -- it is styled here instead, which keeps typography in
   one place per the rule above. */
QWidget#highway {{
    font-family: "Monospace";
    font-size: {px(14, f)}px;
    background: {COLORS["bg"]};
}}

/* --- the game HUD ---------------------------------------------------------- */
/* An overlay floated over the highway, not a layout above it: the playfield keeps
   the whole window, because a rhythm game that shrinks its highway to make room for
   a score is worse than one with no score.

   Every one of these needs `background: transparent`. They float over the highway,
   and the default QWidget background would paint an opaque `bg` rectangle over the
   alternating lane bands -- which reads as a rendering glitch, not as a panel. */
QLabel#gameTitle   {{ font-size: {px(22, f)}px; font-weight: 600; background: transparent; }}
QLabel#gameTally   {{ font-family: "Monospace"; font-size: {px(15, f)}px; background: transparent; }}
QLabel#gameBanner  {{ color: {COLORS["text_dim"]}; background: transparent; }}
QLabel#gameFlash   {{ font-size: {px(30, f)}px; font-weight: 700; color: {COLORS["text"]}; background: transparent; }}
"""


#: The stylesheet at scale 1.0. A module constant because the tests compare against
#: it, and because 1.0 is the honest default on a platform with no screen to measure
#: -- the offscreen platform the suite runs on.
STYLESHEET = build_stylesheet(1.0)
