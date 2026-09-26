"""Colour tokens and the application stylesheet.

Two rules keep this maintainable:

1. **The stylesheet owns typography.** Font family and size live in the QSS and
   nowhere else. `app.py` deliberately does not call `setFont`, because two owners
   for font size means changing one and wondering why nothing happened.
2. **Colours are defined once, here.** Everything else interpolates from
   :data:`COLORS`, so a palette change is a one-place edit.

The base style is Fusion -- the only one present on every platform -- so the app
looks the same on Windows and Linux. That is set in `app.py`, not here.

On QComboBox and QScrollBar: the QSS below sets colours, borders and padding but
deliberately does not try to style the drop-down arrow or the scrollbar groove.
Both need image assets to look right, and a half-styled arrow looks worse than
Fusion's default. This is the known fiddly part of QSS; see DESIGN.md §11.
"""

from __future__ import annotations

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

STYLESHEET = f"""
/* Typography lives here, and only here. */
QWidget {{
    background: {COLORS["bg"]};
    color: {COLORS["text"]};
    font-family: "Sans Serif";
    font-size: 14px;
}}

/* --- headings ------------------------------------------------------------- */
QLabel#heading {{
    font-size: 26px;
    font-weight: 600;
    color: {COLORS["text"]};
    background: transparent;
}}
QLabel#subtitle {{
    font-size: 15px;
    color: {COLORS["text_dim"]};
    background: transparent;
}}
QLabel#dim {{
    color: {COLORS["text_dim"]};
    background: transparent;
}}
QLabel#stat {{
    font-family: "Monospace";
    font-size: 15px;
    color: {COLORS["text"]};
    background: transparent;
}}

/* --- buttons -------------------------------------------------------------- */
QPushButton {{
    background: {COLORS["surface_hi"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    padding: 8px 20px;
    min-height: 20px;
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
    border-radius: 6px;
    padding: 4px;
    outline: none;
}}
QListWidget::item {{
    padding: 8px 6px;
    border-radius: 4px;
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
    border-radius: 6px;
    padding: 6px 10px;
    min-height: 20px;
}}
QComboBox:hover, QSpinBox:hover, QLineEdit:hover {{ border-color: {COLORS["border_hi"]}; }}
QComboBox:disabled, QSpinBox:disabled {{ color: {COLORS["text_dim"]}; }}

QSlider::groove:horizontal {{
    background: {COLORS["surface"]};
    height: 5px;
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{ background: {COLORS["accent"]}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {COLORS["text"]};
    width: 14px;
    margin: -5px 0;
    border-radius: 7px;
}}
QSlider:disabled::sub-page:horizontal {{ background: {COLORS["border"]}; }}

/* --- structure ------------------------------------------------------------ */
QGroupBox {{
    border: 1px solid {COLORS["border"]};
    border-radius: 6px;
    margin-top: 14px;
    padding-top: 10px;
    background: {COLORS["surface"]};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {COLORS["text_dim"]};
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {COLORS["border_hi"]};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: {COLORS["text_dim"]}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QFrame#card {{
    background: {COLORS["surface"]};
    border: 1px solid {COLORS["border"]};
    border-radius: 8px;
}}
QFrame#divider {{ background: {COLORS["border"]}; max-height: 1px; border: none; }}
"""
