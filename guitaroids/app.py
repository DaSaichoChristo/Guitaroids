"""Application object and entry into the event loop.

Owns the QApplication lifetime, the theme, and the exit code. Screens, the song
library and game logic live elsewhere.

Module-level Qt imports are safe here, unlike in ``__main__.py``: Qt reads
``QT_PLUGIN_PATH`` when the *application is constructed*, not at import time, so
the only ordering that matters is that ``qtenv.apply()`` runs before
``QApplication(...)``.
"""

from __future__ import annotations

import argparse
import sys
import time

from PySide6 import QtWidgets

from guitaroids import qtenv
from guitaroids.context import AppContext
from guitaroids.ui.shell import MainWindow
from guitaroids.ui.theme import STYLESHEET

APPLICATION_NAME = "Guitaroids"
DEFAULT_SIZE = (960, 640)


def build_application(argv: list[str]) -> QtWidgets.QApplication:
    """Create or reuse the QApplication, with the theme applied.

    Reuse rather than always construct: Qt permits exactly one QApplication per
    process, so a test that has already built widgets and then calls ``run()``
    would abort on a second construction. Same singleton trap as the M0 gate's
    subprocess rule, on the other side of the boundary.

    Note ``argv[:1]`` -- only the program name is handed to Qt. Passing our own
    flags through invites Qt to try to parse them as its own.

    No ``setFont``: the stylesheet owns typography, and two owners for font size
    means changing one and wondering why nothing happened.
    """
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv[:1])
    app.setApplicationName(APPLICATION_NAME)
    app.setOrganizationName(APPLICATION_NAME)

    # Fusion is the only base style present on every platform, so the app looks the
    # same on Windows and Linux. Guarded: a PySide6 build without it would silently
    # keep the platform default, which is the inconsistency Fusion exists to avoid.
    if "Fusion" in QtWidgets.QStyleFactory.keys():
        app.setStyle("Fusion")
    else:  # pragma: no cover - no such build seen
        print("warning: the Fusion style is unavailable; the platform default will be used")

    app.setStyleSheet(STYLESHEET)
    return app


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="guitaroids", description=APPLICATION_NAME)
    parser.add_argument(
        "--songs",
        metavar="DIR",
        default=None,
        help=(
            "directory to scan for Guitar Pro tabs and backing audio. Defaults to "
            "the repository's songs/ directory."
        ),
    )
    parser.add_argument(
        "--windowed",
        action="store_true",
        help=(
            f"run in a {DEFAULT_SIZE[0]}x{DEFAULT_SIZE[1]} window instead of "
            "full screen. Full screen is the default; this is for development and "
            "for running beside other windows."
        ),
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help=(
            "build the UI, render one frame, and exit without entering the event "
            "loop. Used by the test suite, and a quick way to check whether a "
            "machine can run the app at all."
        ),
    )
    return parser.parse_args(argv)


def _settle(app: QtWidgets.QApplication, window: QtWidgets.QWidget, timeout_ms: int = 1_000) -> None:
    """Spin the event loop until the window manager has mapped and sized the window.

    A single ``processEvents()`` is not enough for a real full screen. Until the WM
    has done its round trip the window is still sitting at its minimum size, so
    ``--self-test`` reported ``size=720x480 fullscreen=true`` -- a self-test
    reporting a geometry that is simply wrong, which is worse than no self-test at
    all, because it looks like a passing check.

    Cheap: ~3ms on the offscreen platform, ~60ms on a real display.
    """
    deadline = time.monotonic() + timeout_ms / 1_000
    while time.monotonic() < deadline:
        app.processEvents()
        handle = window.windowHandle()
        if handle is not None and handle.isExposed():
            break
        time.sleep(0.005)
    app.processEvents()


def run(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(raw)

    # Second bootstrap, and not a redundant one: tests import guitaroids.app
    # directly and never go through __main__.py. apply() is idempotent, so calling
    # it in both places costs one dict lookup and makes both entry paths safe.
    qtenv.apply()

    app = build_application([sys.argv[0]])

    # create() rather than AppContext(): this is the one place a real scan belongs.
    # It happens before the window exists, so the ~0.2s a tab costs delays the
    # first frame rather than freezing a visible one, and it means the first
    # screen drawn already has the library. Rescans go through the background
    # loader instead.
    context = AppContext.create(songs_dir=args.songs)

    shell = MainWindow(context)
    if args.windowed:
        shell.resize(*DEFAULT_SIZE)
        shell.show()
    else:
        # showFullScreen, not showMaximized: this is a game, and a title bar and a
        # taskbar entry are chrome the player has to click past. It implies show(),
        # so there is no separate call to forget.
        shell.showFullScreen()

    # One real layout and paint pass, so construction errors surface here rather
    # than inside exec() where the traceback is much harder to read -- and the
    # settle, so the geometry we report below is the window the WM actually gave us.
    _settle(app, shell)

    if args.self_test:
        print(
            f"self-test ok: platform={app.platformName()} "
            f"size={shell.width()}x{shell.height()} "
            f"fullscreen={str(shell.isFullScreen()).lower()} "
            f"screen={shell.current.value} history={len(shell.history)} "
            f"songs={len(context.library.entries)}"
        )
        return 0

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(run())
