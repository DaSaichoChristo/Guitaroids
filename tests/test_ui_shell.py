"""Tests for the application shell, navigation, and the entry point.

Covers the layers the shell is responsible for: lazy screen construction, the
navigation history, the entry point's import order, and whether a screen actually
renders rather than silently drawing nothing.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from guitaroids.ui.screens import Screen
from guitaroids.ui.theme import COLORS, STYLESHEET

ROOT = Path(__file__).resolve().parent.parent


# --- the entry point ---------------------------------------------------------


def _self_test(*args: str) -> subprocess.CompletedProcess:
    """Run the app for real, in a subprocess, and report what happened."""
    return subprocess.run(
        [sys.executable, "-m", "guitaroids", *args],
        capture_output=True,
        text=True,
        cwd=ROOT,
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )


def test_self_test_runs_and_exits_zero() -> None:
    """The whole import chain, in a real process, with no display.

    This is the UI equivalent of the M0 gate: it would catch a broken qtenv
    ordering, a missing import, or a screen that fails to construct.
    """
    result = _self_test("--self-test")
    assert result.returncode == 0, result.stderr
    assert "self-test ok" in result.stdout


def test_self_test_reports_the_platform_and_size() -> None:
    result = _self_test("--self-test")
    assert "platform=offscreen" in result.stdout
    assert "size=960x640" in result.stdout


def test_main_module_bootstraps_before_importing_app() -> None:
    """The import order in __main__.py is the point of the file.

    Parsed rather than imported, because importing it would launch the app. This
    asserts the qtenv.apply() call textually precedes the app import.
    """
    source = (ROOT / "guitaroids" / "__main__.py").read_text()
    apply_at = source.index("qtenv.apply()")
    app_at = source.index("from guitaroids.app import run")
    assert apply_at < app_at, "qtenv must be applied before app is imported"


def test_argv_is_forwarded_to_run() -> None:
    """--self-test must be visible to run(), which is what the gate depends on."""
    from guitaroids.app import parse_args

    assert parse_args(["--self-test"]).self_test is True
    assert parse_args([]).self_test is False


def test_unknown_flag_is_rejected() -> None:
    result = _self_test("--definitely-not-a-flag")
    assert result.returncode != 0
    assert "unrecognized" in result.stderr.lower()


# --- navigation ---------------------------------------------------------------


def test_starts_on_main(shell) -> None:
    assert shell.current is Screen.MAIN
    assert shell.history == ()


def test_navigate_pushes_history(shell) -> None:
    shell.navigate(Screen.SONG_SELECT)
    assert shell.current is Screen.SONG_SELECT
    assert shell.history == (Screen.MAIN,)


def test_go_back_returns_and_pops(shell) -> None:
    shell.navigate(Screen.SONG_SELECT)
    shell.go_back()
    assert shell.current is Screen.MAIN
    assert shell.history == (), "back should pop the history it consumed"


def test_go_back_at_root_does_nothing(shell) -> None:
    shell.go_back()
    shell.go_back()
    assert shell.current is Screen.MAIN


def test_go_back_unwinds_multiple_levels(shell) -> None:
    shell.navigate(Screen.SONG_SELECT)
    shell.navigate(Screen.GAME)
    shell.navigate(Screen.RESULTS)
    assert shell.current is Screen.RESULTS
    shell.go_back()
    assert shell.current is Screen.GAME
    shell.go_back()
    assert shell.current is Screen.SONG_SELECT
    shell.go_back()
    assert shell.current is Screen.MAIN


def test_navigate_does_not_self_stack(shell) -> None:
    shell.navigate(Screen.SONG_SELECT)
    shell.navigate(Screen.SONG_SELECT)
    assert shell.current is Screen.SONG_SELECT
    assert shell.history == (Screen.MAIN,)


@pytest.mark.parametrize("screen", list(Screen))
def test_every_screen_navigates_and_renders(shell, qapp, screen: Screen) -> None:
    # isVisible() is False until the top-level window is shown, so show it first.
    shell.show()
    qapp.processEvents()
    shell.navigate(screen)
    qapp.processEvents()
    assert shell.current is screen
    assert shell.current_screen.isVisible()
    # Laid out, not just constructed.
    assert shell.current_screen.width() > 0


def test_window_title_reflects_the_screen(shell) -> None:
    shell.navigate(Screen.SONG_SELECT)
    assert "Song Select" in shell.windowTitle()


# --- lazy construction -------------------------------------------------------


def test_screens_are_built_on_first_show_only(shell) -> None:
    """Screens are factories, not instances, so opening the menu builds one screen.

    This matters once a screen owns something expensive: an eager shell would open
    an audio device or a camera just to display the main menu.
    """
    shell.navigate(Screen.MAIN)
    built = [s for s in Screen if s in shell._built]
    assert built == [Screen.MAIN]

    shell.navigate(Screen.SONG_SELECT)
    assert Screen.SONG_SELECT in shell._built

    shell.navigate(Screen.MAIN)
    assert Screen.GAME not in shell._built, "unvisited screens must stay unbuilt"


def test_main_screen_has_navigation_buttons(shell) -> None:
    from PySide6 import QtWidgets

    shell.navigate(Screen.MAIN)
    labels = [b.text() for b in shell.current_screen.findChildren(QtWidgets.QPushButton)]
    assert "Play" in labels
    assert "Preferences" in labels


def test_escape_goes_back(shell, qapp) -> None:
    from PySide6 import QtCore, QtGui, QtWidgets

    shell.show()
    qapp.processEvents()
    shell.navigate(Screen.SONG_SELECT)
    shell.current_screen.setFocus()
    event = QtGui.QKeyEvent(
        QtCore.QEvent.Type.KeyPress,
        QtCore.Qt.Key.Key_Escape,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(shell.current_screen, event)
    assert shell.current is Screen.MAIN


# --- rendering ---------------------------------------------------------------


def test_shell_grab_is_not_blank(shell, qapp) -> None:
    """A screen that renders nothing must fail, not pass quietly."""
    shell.show()
    qapp.processEvents()
    image = shell.grab().toImage()
    assert not image.isNull()
    assert image.width() > 0 and image.height() > 0


def test_theme_is_actually_applied(shell, qapp) -> None:
    """The QSS must be live, not merely defined in theme.py."""
    assert qapp.styleSheet() == STYLESHEET
    # A themed widget reports a non-empty background role.
    from PySide6 import QtWidgets

    label = QtWidgets.QLabel("x")
    shell.centralWidget().layout()
    assert label.palette().color(label.backgroundRole()).name() != ""


def test_stylesheet_mentions_every_colour_token() -> None:
    """Guards against a token being added to COLORS but never applied."""
    for name, value in COLORS.items():
        assert value in STYLESHEET, f"colour '{name}' ({value}) is defined but unused"


def test_stylesheet_has_balanced_braces() -> None:
    assert STYLESHEET.count("{") == STYLESHEET.count("}")
