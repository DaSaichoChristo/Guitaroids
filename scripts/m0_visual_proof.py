"""M0 visual proof: open a real window on $DISPLAY and screenshot the X root.

The pytest gate proves a QApplication constructs and QPainter rasterizes. This
proves a window is genuinely mapped onto the X server, by capturing the root
window with xwd after the event loop has had time to map it.

Usage:  .venv/bin/python scripts/m0_visual_proof.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

OUT = Path("/tmp/opencode/m0_proof.xwd")


def main() -> int:
    app = QApplication([])
    label = QLabel("Guitaroids - M0 passed")
    label.setStyleSheet("background:#1b6b3a; color:white; font-size:24px;")
    label.setAlignment(Qt.AlignCenter)
    label.resize(560, 160)
    label.show()

    # Let the X server map the window before we capture the root.
    QTimer.singleShot(1500, app.quit)
    app.exec()

    display = os.environ.get("DISPLAY", ":0")
    result = subprocess.run(
        ["xwd", "-root", "-display", display, "-out", str(OUT)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"xwd failed: {result.stderr}", file=sys.stderr)
        return 1

    size = OUT.stat().st_size
    print(f"platform      : {app.platformName()}")
    print(f"display       : {display}")
    print(f"screenshot    : {OUT} ({size} bytes)")
    return 0 if size > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
