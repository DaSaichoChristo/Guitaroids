"""Entry point for Guitaroids:  python -m guitaroids

Deliberately thin. All real work lives in guitaroids.app.

The Qt bootstrap is applied before ``app`` is imported, so that anything loading
PySide6 or cv2 picks up the correct plugin path. Note that the strict requirement
is really about *constructing* the QApplication, not about importing PySide6 --
Qt reads QT_PLUGIN_PATH at application construction. Importing app first would
therefore usually work too, but it makes the guarantee depend on something nobody
can see, and it stops being true the moment a test imports guitaroids.app without
going through here.
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    from guitaroids import qtenv

    qtenv.apply()

    from guitaroids.app import run

    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
