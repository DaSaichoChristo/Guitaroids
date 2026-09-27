"""M0 gate: can this stack open a window?

DESIGN.md §4.2 makes M0 a hard gate — nothing else starts until it passes, because
if the §1.2 framework choice cannot open a window then every later section needs
revisiting.

Two things are being asserted:

1. **OpenCV is not here any more.** It was the whole reason this gate had a static
   section: mediapipe pulled in the GUI build, whose bundled Qt plugins hijack
   ``QT_PLUGIN_PATH`` (§2.2), and the gate had to prove the hijack was inert. With
   mediapipe gone (§25) the hijack cannot happen, so the check is now that the
   packages are not *declared* -- which is a stronger claim than "not currently
   broken", because it survives someone reinstalling them by hand.
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


# --- static checks: no QApplication needed -------------------------------------


def test_pyside6_ships_its_own_xcb_plugin() -> None:
    """The question ``qtenv`` used to answer, asked directly.

    ``qtenv`` computed this path and exported it as ``QT_PLUGIN_PATH``, because
    OpenCV's GUI build was competing for it. There is nothing competing now, so the
    plugin only has to exist in PySide6 -- which is what this checks.
    """
    import PySide6

    plugins = Path(PySide6.__file__).parent / "Qt" / "plugins"
    assert plugins.is_dir(), f"PySide6 Qt/plugins missing at {plugins}"
    assert (plugins / "platforms" / "libqxcb.so").is_file(), "xcb platform plugin missing"


def test_opencv_and_mediapipe_are_not_declared_dependencies() -> None:
    """**Absence, not inertness.** The old check proved the hijack was not currently
    happening; this proves it cannot be *made* to happen by an install.

    Somebody reinstalling the GUI OpenCV build by hand is exactly the failure the old
    test was watching for, and it is now caught at the requirements file rather than
    at a mysterious "Could not load the Qt platform plugin".
    """
    def declared(path: Path) -> list[str]:
        # Comments excluded: requirements.txt explains at length why OpenCV used to
        # be a trap, and a test reading that prose would forbid the explanation.
        return [
            line.strip().lower()
            for line in path.read_text().splitlines()
            if line.strip() and not line.strip().startswith(("#", "-r "))
        ]

    for package in ("opencv", "mediapipe"):
        assert not any(package in line for line in declared(ROOT / "requirements.txt")), (
            f"{package} is declared in requirements.txt again"
        )
        assert not any(package in line for line in declared(ROOT / "requirements-lock.txt")), (
            f"{package} is in the lock file: the venv still has it installed"
        )


def test_all_dependencies_import() -> None:
    for module in ("numpy", "soundfile", "sounddevice", "guitarpro"):
        __import__(module)


# --- window checks, one platform per subprocess --------------------------------

CHILD = """
import os
import sys
sys.path.insert(0, {root!r})

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

# Report, then leave immediately.
#
# Without this the interpreter tears a live QApplication down at exit, and Qt
# sometimes aborts there (SIGABRT) depending on GC timing. That showed up as an
# intermittent failure of the whole gate when run from setup.sh, and was not
# reproducible by running the tests repeatedly on their own. os._exit skips static
# destructors entirely, which removes the race rather than papering over it.
sys.stdout.write(app.platformName() + "\\n")
sys.stdout.flush()
os._exit(0)
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
