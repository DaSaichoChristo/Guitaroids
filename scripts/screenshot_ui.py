#!/usr/bin/env python
"""Screenshot a screen of the app, for looking at menus while iterating.

Builds the real shell on a real display (or offscreen with --offscreen), renders
one frame, and writes a PNG. Much faster feedback than launching the app and
squinting at it, and it is how the render tests know a screen is not blank.

Usage::

    .venv/bin/python scripts/screenshot_ui.py                     # main menu
    .venv/bin/python scripts/screenshot_ui.py song_select         # a named screen
    .venv/bin/python scripts/screenshot_ui.py --all               # every screen
    .venv/bin/python scripts/screenshot_ui.py --offscreen main    # no display needed
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = Path("/tmp/opencode/ui")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screen", nargs="?", default="main")
    parser.add_argument("--all", action="store_true", help="capture every screen")
    parser.add_argument("--offscreen", action="store_true", help="render without a display")
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args(argv)

    if args.offscreen:
        import os

        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from guitaroids import qtenv

    qtenv.apply()

    from guitaroids.app import build_application
    from guitaroids.ui.screens import Screen
    from guitaroids.ui.shell import MainWindow

    app = build_application([sys.argv[0]])
    shell = MainWindow()
    shell.resize(960, 640)
    shell.show()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    targets = list(Screen) if args.all else [Screen(args.screen)]
    written = []
    for screen in targets:
        shell.navigate(screen)
        app.processEvents()
        path = out / f"{screen.value}.png"
        if not shell.grab().save(str(path)):
            print(f"failed to write {path}", file=sys.stderr)
            return 1
        written.append(path)
        print(f"{screen.value:14} -> {path}")

    print(f"platform: {app.platformName()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
