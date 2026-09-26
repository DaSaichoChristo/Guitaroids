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


@pytest.fixture(scope="session")
def qapp():
    """One QApplication for the whole session. Qt permits exactly one."""
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication([sys.argv[0]])
    if "Fusion" in QtWidgets.QStyleFactory.keys():
        app.setStyle("Fusion")
    from guitaroids.ui.theme import STYLESHEET

    app.setStyleSheet(STYLESHEET)
    yield app


@pytest.fixture()
def shell(qapp):
    """A fresh MainWindow, unloaded afterwards so tests cannot leak state."""
    from guitaroids.ui.shell import MainWindow

    window = MainWindow()
    yield window
    window.unload_all()
    window.close()
    window.deleteLater()
    qapp.processEvents()
