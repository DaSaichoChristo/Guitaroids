"""Shared pytest fixtures.

Sets the Qt platform before anything imports Qt, then provides one
application-wide QApplication.

Why offscreen: the M0 gate established that a QApplication is a process-wide
singleton whose platform is fixed at construction. Widget tests therefore cannot
each pick a platform, and the suite must not need a real display. The M0 tests
spawn subprocesses that set their own ``QT_QPA_PLATFORM``, so they are unaffected
by this being set globally here.

The env var is set at *import* time, not in a fixture, because by the time a
fixture runs a test module may already have imported PySide6.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Must precede any Qt import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from guitaroids.context import AppContext  # noqa: E402 - needs the sys.path fix above
from guitaroids.settings import Settings  # noqa: E402
from guitaroids.songlib import Library  # noqa: E402


@pytest.fixture(autouse=True)
def reset_ui_scale():
    """Put the process-wide UI scale and stylesheet back to their defaults.

    Autouse because the scale is a module global in ``theme`` -- deliberately, so
    ``content_column`` and ``constrained_button`` can scale their design units
    without every screen having to thread a scale argument through. The cost of a
    global is that a test which sets it leaks into every test after it, and the
    symptom would be a screen mysteriously laid out at 1.5x with no obvious cause.

    The stylesheet is restored too, because it is the *other* half of the same
    state: ``QApplication.styleSheet`` is also process-wide and outlives the
    session-scoped ``qapp``, so a test that re-themes the app to check the scaled
    layout would otherwise leave every later test asserting against the wrong sheet.

    Restored **only if it differs**. ``QApplication.setStyleSheet`` re-polishes every
    live widget, and measuring that at ~0.7s per call: paying it unconditionally
    made the suite take minutes and looked like a hang. One test changes the sheet,
    so one test pays.
    """
    from PySide6 import QtWidgets

    from guitaroids.ui import theme

    yield
    theme.set_scale(1.0)
    app = QtWidgets.QApplication.instance()
    if app is None:
        return
    default = theme.build_stylesheet(1.0)
    if app.styleSheet() != default:
        app.setStyleSheet(default)


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole session. Qt permits exactly one."""
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([sys.argv[0]])
    if "Fusion" in QtWidgets.QStyleFactory.keys():
        app.setStyle("Fusion")
    from guitaroids.ui import theme

    # Scale 1.0 explicitly, not STYLESHEET, so the fixture states the scale rather
    # than relying on the global happening to be 1.0. build_application would derive
    # it from the offscreen platform's 800x800 screen, which is below the minimum
    # and so would clamp to 1.0 -- right answer, but by accident.
    theme.set_scale(1.0)
    app.setStyleSheet(theme.build_stylesheet(1.0))
    yield app


@pytest.fixture()
def context() -> AppContext:
    """A context with an empty library and nowhere to write.

    Deliberately does **not** call ``AppContext.create()``. That scans the real
    ``songs/`` directory at ~0.2s a tab, and every widget test would pay for it --
    on a library of fifty songs, minutes per suite run. A test that needs entries
    builds a ``Library`` in memory instead, which is also why this can assert a
    song list without depending on what tabs the machine happens to have.

    ``settings_path`` points into a directory that does not exist so a test that
    calls ``save_settings`` fails loudly rather than quietly writing the
    developer's real config.
    """
    missing = Path("/nonexistent/guitaroids-tests")
    return AppContext(
        library=Library(root=missing / "songs"),
        settings=Settings(),
        songs_dir=missing / "songs",
        settings_path=missing / "settings.json",
    )


@pytest.fixture()
def shell(qapp, context):
    """A fresh MainWindow, unloaded afterwards so tests cannot leak state."""
    from guitaroids.ui.shell import MainWindow

    window = MainWindow(context)
    yield window
    window.unload_all()
    window.close()
    window.deleteLater()
    qapp.processEvents()
