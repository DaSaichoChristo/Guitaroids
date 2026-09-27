"""Entry point for Guitaroids:  python -m guitaroids

Deliberately thin. All real work lives in guitaroids.app.

This file used to exist for one reason: ``guitaroids.qtenv`` had to set
``QT_PLUGIN_PATH`` before anything imported PySide6, because mediapipe's GUI build
of OpenCV ships Qt plugins that hijack it and break the app with ``Could not load
the Qt platform plugin "xcb"``. That was three install caveats and an ordering rule
for a webcam tracker that was never built (DESIGN.md §25).

With no OpenCV there is nothing to bootstrap ahead of, so this is now what it always
claimed to be: four lines that call :func:`guitaroids.app.run`.
"""

from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    from guitaroids.app import run

    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
