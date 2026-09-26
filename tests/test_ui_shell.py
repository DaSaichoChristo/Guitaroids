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
    assert result.returncode == 0, result.stderr
    assert "platform=offscreen" in result.stdout
    # Full screen is the default, so the size is the platform's screen size rather
    # than anything we choose. Asserting a literal here would only pin the
    # offscreen platform's default; the windowed case below pins our own size.
    assert "fullscreen=true" in result.stdout
    assert "size=" in result.stdout


def test_windowed_mode_uses_the_declared_default_size() -> None:
    """--windowed must land on DEFAULT_SIZE, and must not be full screen."""
    from guitaroids.app import DEFAULT_SIZE

    result = _self_test("--self-test", "--windowed")
    assert result.returncode == 0, result.stderr
    assert f"size={DEFAULT_SIZE[0]}x{DEFAULT_SIZE[1]}" in result.stdout
    assert "fullscreen=false" in result.stdout


def test_fullscreen_is_the_default() -> None:
    """A flag is only meaningful if the other branch is what happens without it."""
    from guitaroids.app import parse_args

    assert parse_args([]).windowed is False
    assert parse_args(["--windowed"]).windowed is True


def test_the_app_asks_for_full_screen_not_maximized() -> None:
    """Maximized keeps the title bar and a taskbar entry.

    This is a game; chrome the player has to click past is the thing being
    removed. Asserted on the source text because Qt's showMaximized() would look
    correct in a screenshot and would still be the wrong choice.
    """
    source = (ROOT / "guitaroids" / "app.py").read_text()
    assert "showFullScreen()" in source
    assert "showMaximized()" not in source, "maximized is not full screen"


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


def test_songs_flag_is_forwarded() -> None:
    """--songs must reach AppContext.create, or it silently does nothing."""
    from guitaroids.app import parse_args

    assert parse_args(["--songs", "/tmp/tabs"]).songs == "/tmp/tabs"
    assert parse_args([]).songs is None, "absent means 'use the default', not an empty path"


def test_self_test_reports_the_song_count() -> None:
    """The count is the only evidence in the self-test that the scan ran.

    Without it, a context wired to the wrong directory would still print a
    cheerful "self-test ok" and look fine.
    """
    result = _self_test("--self-test")
    assert result.returncode == 0, result.stderr
    assert "songs=" in result.stdout


def test_self_test_with_an_empty_songs_dir_finds_nothing() -> None:
    """An empty library is a valid first run, not a failure."""
    import tempfile

    with tempfile.TemporaryDirectory() as empty:
        result = _self_test("--self-test", "--songs", empty)
    assert result.returncode == 0, result.stderr
    assert "songs=0" in result.stdout


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


@pytest.mark.parametrize("screen", list(Screen))
def test_window_title_uses_the_declared_label(shell, screen: Screen) -> None:
    """Every screen, not just one.

    The previous version asserted only SONG_SELECT -- the single case str.title()
    gets right -- so it passed while IMPORT_GP rendered as "Import Gp". An exact
    match against screen.label is what makes a derived-from-the-enum-value title
    impossible to reintroduce silently.
    """
    shell.navigate(screen)
    assert shell.windowTitle() == f"Guitaroids - {screen.label}"


def test_every_screen_has_a_label() -> None:
    """Guards the mapping against a new screen being added without one."""
    from guitaroids.ui.screens import _SCREEN_LABELS

    assert set(_SCREEN_LABELS) == {screen.value for screen in Screen}
    missing = [s for s in Screen if not s.label.strip()]
    assert missing == [], f"screens with an empty label: {missing}"


def test_labels_are_not_derived_by_title_casing() -> None:
    """A label must survive being title-cased, or it holds an acronym.

    Not a style rule: str.title() would turn "Import GP" into "Import Gp", which
    is precisely the bug this mapping exists to prevent.
    """
    for screen in Screen:
        assert screen.label == screen.label.title() or any(
            part.isupper() for part in screen.label.split()
        ), f"{screen.name}: {screen.label!r} looks like it was title-cased"


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


def test_main_menu_has_exactly_the_expected_buttons(shell) -> None:
    """An exact set, so a forgotten button is a failure rather than nothing.

    Was two membership checks, which could not detect a missing button and did
    not know about Quit or Import GP.
    """
    from PySide6 import QtWidgets

    shell.navigate(Screen.MAIN)
    labels = {b.text() for b in shell.current_screen.findChildren(QtWidgets.QPushButton)}
    assert labels == {"Play", "Import GP", "Preferences", "Quit"}


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


# --- context wiring -----------------------------------------------------------


@pytest.mark.parametrize("screen", list(Screen))
def test_every_screen_receives_the_shells_context(shell, context, screen: Screen) -> None:
    """The whole point of the refactor: a screen can reach the shared state.

    Identity, not equality: a screen that quietly built its own context would have
    an empty library while the shell had fifty songs, and every read would return
    nothing without any error.
    """
    shell.navigate(screen)
    assert shell.current_screen.context is context
    assert shell.context is context


def test_the_context_outlives_every_screen(shell, context) -> None:
    """Popping screens must not take the library or the play request with them.

    This is "screens never own game objects" (DESIGN.md §1.7) applied to the
    context: navigate through everything, unload it all, and the shared state is
    still there. A screen holding the only reference would fail here.
    """
    context.request_play("some_song", 3)
    for screen in Screen:
        shell.navigate(screen)

    shell.unload_all()

    assert shell._built == {}, "unload_all should have dropped every screen"
    assert shell.context is context
    assert context.play_request is not None
    assert context.play_request.slug == "some_song"
    assert context.play_request.track_number == 3


def test_a_screen_can_be_built_without_a_window(shell, context) -> None:
    """The reason context is a constructor argument and not shell.context.

    If a screen could only get the context by reaching through the shell, every
    screen test would need a whole MainWindow; this is the cheaper shape.
    """
    from guitaroids.ui.main_menu import MainMenu

    menu = MainMenu(shell, context)
    try:
        assert menu.context is context
        assert menu.shell is shell
    finally:
        menu.deleteLater()


def test_screen_base_requires_a_context() -> None:
    """Guards the signature.

    A screen constructed without one would fail deep inside its own __init__ with
    an AttributeError on a library access, a long way from the mistake.
    """
    import inspect

    from guitaroids.ui.screens import ScreenBase

    parameters = list(inspect.signature(ScreenBase.__init__).parameters)
    assert parameters == ["self", "shell", "context", "parent"]


@pytest.mark.parametrize("screen", list(Screen))
def test_no_screen_compresses_its_content(shell, qapp, screen: Screen) -> None:
    """Nothing on any screen may be drawn on top of anything else.

    A QVBoxLayout that cannot fit its children does not clip them -- it compresses
    them below their minimum height, and the widgets overlap. Preferences did
    exactly this on first build: three combo boxes stacked 19px apart when each
    needed 34. A screenshot is how that was found, so the check belongs here
    rather than in one screen's tests where a new screen would not hit it.
    """
    from PySide6 import QtWidgets

    shell.resize(960, 640)  # the app's default, and the tightest realistic case
    shell.show()
    qapp.processEvents()
    shell.navigate(screen)
    qapp.processEvents()

    squeezed = []
    for box in shell.current_screen.findChildren(QtWidgets.QGroupBox):
        layout = box.layout()
        if layout is None:
            continue
        needed = layout.minimumSize().height()
        if box.height() < needed - 2:
            squeezed.append(f"{box.title()!r}: {box.height()}px < {needed}px")

    assert not squeezed, f"{screen.label} overlaps its contents: {squeezed}"


@pytest.mark.parametrize("screen", list(Screen))
def test_every_screen_fits_inside_the_window(shell, qapp, screen: Screen) -> None:
    """A child wider than the screen is clipped, i.e. invisible.

    Catches a fixed-width panel that stops fitting when the window is narrow --
    the failure mode a fixed-width detail pane invites.
    """
    from PySide6 import QtWidgets

    shell.resize(960, 640)
    shell.show()
    qapp.processEvents()
    shell.navigate(screen)
    qapp.processEvents()

    current = shell.current_screen
    too_wide = [
        c.objectName() or type(c).__name__
        for c in current.findChildren(QtWidgets.QWidget)
        if c.parent() is current and c.width() > current.width() + 1
    ]
    assert not too_wide, f"{screen.label} has children wider than the screen: {too_wide}"


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
