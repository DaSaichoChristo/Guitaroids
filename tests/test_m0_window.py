"""M0 gate: can this stack open a window?

DESIGN.md §4.2 makes M0 a hard gate — nothing else starts until it passes, because
if the §1.2 framework choice cannot open a window then every later section needs
revisiting.

Two things are being asserted:

1. The §2.2 OpenCV/Qt plugin hijack is genuinely gone (``cv2/qt`` does not exist),
   and Qt is pointed at PySide6's own plugins.
2. A real ``QApplication`` starts, a real ``QPainter`` pass rasterizes pixels, and
   a real widget is mapped onto the display.

The platform-plugin checks run in **subprocesses**. ``QApplication`` is a
process-wide singleton whose platform is fixed at construction, so two platforms
cannot be exercised in one process — the second call silently reuses the first
application and reports the wrong platform.

Run with:  .venv/bin/python -m pytest tests/test_m0_window.py -v
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guitaroids import qtenv  # noqa: E402

qtenv.apply()


# --- static checks: no QApplication needed -------------------------------------


def test_plugin_dir_resolves() -> None:
    plugins = qtenv.plugin_dir()
    assert plugins is not None, "PySide6 Qt/plugins not found"
    assert (plugins / "platforms" / "libqxcb.so").is_file(), "xcb platform plugin missing"


def test_opencv_has_no_qt_plugins() -> None:
    """The §2.2 hazard, asserted rather than assumed.

    If this fails, mediapipe pulled the GUI OpenCV build back in and its bundled
    plugins will fight PySide6 for QT_PLUGIN_PATH.
    """
    import cv2

    assert not (Path(cv2.__file__).parent / "qt").exists(), (
        "cv2/qt exists: the GUI OpenCV build is installed. "
        "Run: pip uninstall -y opencv-contrib-python && "
        "pip install opencv-contrib-python-headless==<same version>"
    )


def test_all_dependencies_import() -> None:
    for module in ("cv2", "mediapipe", "numpy", "soundfile", "sounddevice", "guitarpro"):
        __import__(module)


def test_qt_plugin_path_points_at_pyside6() -> None:
    plugins = str(qtenv.plugin_dir())
    assert os.environ.get("QT_PLUGIN_PATH", "").split(os.pathsep)[0] == plugins


# --- window checks, one platform per subprocess --------------------------------

CHILD = """
import sys
sys.path.insert(0, {root!r})
from guitaroids import qtenv
qtenv.apply()

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QPixmap, QImage
from PySide6.QtWidgets import QApplication, QLabel

app = QApplication([])

label = QLabel("Guitaroids M0")
label.setStyleSheet("background:#1b6b3a; color:white; font-size:20px;")
label.setAlignment(Qt.AlignCenter)
label.resize(480, 140)
label.show()

# Real QPainter pass, then verify it actually rasterized something.
pixmap = QPixmap(label.size())
pixmap.fill()
painter = QPainter(pixmap)
painter.drawText(pixmap.rect(), Qt.AlignCenter, "render ok")
painter.end()

image = pixmap.toImage().convertToFormat(QImage.Format_RGB32)
colours = {{
    image.pixel(x, y)
    for x in range(0, image.width(), 4)
    for y in range(0, image.height(), 4)
}}
assert len(colours) > 1, "QPainter produced a uniform image - nothing was drawn"

label.repaint()
app.processEvents()
assert label.isVisible(), "widget never became visible"

print(app.platformName())
"""


def _run_platform(platform: str) -> str:
    result = subprocess.run(
        [sys.executable, "-c", CHILD.format(root=str(ROOT))],
        capture_output=True,
        text=True,
        env={**os.environ, "QT_QPA_PLATFORM": platform},
    )
    assert result.returncode == 0, f"platform={platform} failed:\n{result.stderr}"
    return result.stdout.strip().splitlines()[-1]


def test_window_opens_offscreen() -> None:
    """A real QApplication + QPainter pass works, no display needed."""
    assert _run_platform("offscreen") == "offscreen"


@pytest.mark.skipif(
    not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"),
    reason="no display available",
)
def test_window_opens_on_real_display() -> None:
    assert _run_platform("xcb") == "xcb"
