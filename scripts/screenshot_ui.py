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


def build_parser() -> argparse.ArgumentParser:
    """The argument parser, exposed so a test can check the defaults.

    Separate from :func:`main` because the scale default is load-bearing: this tool
    renders a fixed-size frame, and inheriting the display's scale would draw a
    1.33x UI into a 1080p window and clip the bottom of every screen.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screen", nargs="?", default="main")
    parser.add_argument("--all", action="store_true", help="capture every screen")
    parser.add_argument("--offscreen", action="store_true", help="render without a display")
    parser.add_argument("--out", default=str(OUT_DIR))
    parser.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help=(
            "UI scale to render at. Defaults to 1.0, the 1080p design size, which "
            "is what --size below assumes. Pass --scale 1.5 with a matching larger "
            "--size to look at the enlarged layout."
        ),
    )
    parser.add_argument(
        "--size",
        default="960x640",
        help="window size, WxH. Should match --scale.",
    )
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)

    if args.offscreen:
        import os

        os.environ["QT_QPA_PLATFORM"] = "offscreen"

    from guitaroids.app import build_application
    from guitaroids.context import AppContext
    from guitaroids.ui import theme
    from guitaroids.ui.screens import Screen
    from guitaroids.ui.shell import MainWindow

    app = build_application([sys.argv[0]], scale_factor=args.scale)
    # A real context, unlike the test suite's. The point of this script is to look
    # at what the user will actually see, and a real song list is most of it.
    context = AppContext.create()
    print(f"songs: {len(context.library.entries)} found in {context.songs_dir}")

    width, _, height = args.size.partition("x")
    width, height = int(width), int(height)
    shell = MainWindow(context)
    shell.resize(width, height)
    shell.show()
    print(f"scale: {theme.scale():.3f}  window: {width}x{height}")

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
