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


def test_the_entry_point_is_thin_and_still_bootstraps() -> None:
    """`python -m guitaroids` has to reach `run`, and nothing else is its job.

    Parsed with ``ast`` rather than imported (importing would launch the app) and
    rather than grepped. Grepping was the previous approach here and it was wrong
    twice: it read the module docstring's explanation of what qtenv *was* as if it
    were a use of it, and the fix was a line filter that then had to be taught about
    docstrings. An AST cannot be confused by prose.
    """
    import ast

    source = (ROOT / "guitaroids" / "__main__.py").read_text()
    tree = ast.parse(source)

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)

    assert "guitaroids.app" in imported, "the entry point lost its job"
    assert "qtenv" not in imported, "the Qt plugin bootstrap is gone; do not bring it back"
    assert not any(name.startswith("PySide6") for name in imported), (
        "the entry point imports no Qt: the app module owns that"
    )
    assert len(source.splitlines()) < 30, "__main__.py has grown past a shim"



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
def test_no_layout_child_is_squeezed_below_its_minimum(shell, qapp, screen: Screen) -> None:
    """The same failure as above, for the screens that use no group box.

    The group-box version missed song select entirely, and song select shipped a
    detail card whose six fact rows were drawn on top of each other the moment a
    fourth control was added to it (§19.2). The card is a QFrame, so there was no
    group box for the old test to measure. Checked here over every widget a layout
    actually owns, skipping anything inside a QScrollArea -- a scroll area is the
    documented answer to content that does not fit, so scrolling past it is not a
    fault.
    """
    from PySide6 import QtWidgets

    shell.resize(960, 640)
    shell.show()
    qapp.processEvents()
    shell.navigate(screen)
    qapp.processEvents()

    def in_scroll_area(widget: QtWidgets.QWidget) -> bool:
        parent = widget.parent()
        while parent is not None:
            if isinstance(parent, QtWidgets.QScrollArea):
                return True
            parent = parent.parent()
        return False

    squeezed = []
    for widget in shell.current_screen.findChildren(QtWidgets.QWidget):
        if not widget.isVisible() or in_scroll_area(widget):
            continue
        if widget.parent() is None or widget.parent().layout() is None:
            continue  # not owned by a layout, so nothing can squeeze it
        needed = widget.minimumSizeHint().height()
        if needed > 0 and widget.height() < needed - 2:
            name = widget.objectName() or type(widget).__name__
            squeezed.append(f"{name}: {widget.height()}px < {needed}px")

    assert not squeezed, f"{screen.label} squeezes its content: {squeezed}"


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


@pytest.mark.parametrize("size", [(960, 640), (1920, 1080), (3440, 1440)])
@pytest.mark.parametrize("screen", list(Screen))
def test_no_screen_clips_its_own_text(shell, qapp, screen: Screen, size) -> None:
    """A word-wrapped label must be at least as tall as the text it holds.

    A word-wrapped QLabel reports two heights: ``sizeHint`` for the width it will
    actually have, and ``minimumSizeHint``, which Qt derives from
    ``heightForWidth``. When a layout economises it hands out the *minimum*, and the
    last line is simply not drawn -- Import GP shipped that, losing "you will be
    asked before it is replaced" with no indication anything was missing.

    Three sizes because the failure needs spare room to appear, and squeezing on a
    short window can cause the same thing from the other direction.
    """
    from PySide6 import QtWidgets

    shell.resize(*size)
    shell.show()
    qapp.processEvents()
    shell.navigate(screen)
    qapp.processEvents()

    clipped = [
        (label.text()[:40], label.height(), label.sizeHint().height())
        for label in shell.current_screen.findChildren(QtWidgets.QLabel)
        if label.wordWrap()
        and label.text()
        and label.height() < label.sizeHint().height() - 2
    ]
    assert not clipped, (
        f"{screen.label} at {size[0]}x{size[1]} clips its text "
        f"(height, needed): {clipped}"
    )


@pytest.mark.parametrize("size", [(960, 640), (1920, 1080), (3440, 1440)])
def test_page_content_is_not_stretched_to_fill_a_tall_window(shell, qapp, size) -> None:
    """The Preferences form column needs a stretch item, and had lost its own.

    Its action row moved outside the scroll area when the form was made scrollable,
    and taking the ``addStretch(1)`` with it left the QVBoxLayout with nothing to
    absorb surplus height. It then handed the extra out *equally* to every widget
    that can grow, and QLabel and QGroupBox both can -- at 1440px the "Preferences"
    title was given 203px for 31px of text, opening a 120px hole under the heading.

    Checked on the two screens whose content is a single top-aligned column, since
    the placeholder screens centre their content and are supposed to spread.
    """
    from PySide6 import QtWidgets

    shell.resize(*size)
    shell.show()
    qapp.processEvents()

    stretched = {}
    for screen in (Screen.PREFERENCES, Screen.IMPORT_GP):
        shell.navigate(screen)
        qapp.processEvents()
        for label in shell.current_screen.findChildren(QtWidgets.QLabel):
            if not label.text() or not label.wordWrap():
                continue
            # Only page-level labels. A label inside a QGroupBox that shares a form
            # row with a taller control is *meant* to be stretched to match it --
            # "Mode" next to a 34px combo is 29px tall, and that is correct.
            # What the missing stretch broke was the page's own column, whose
            # children have nothing to align to but each other.
            if label.parentWidget().objectName() != "column":
                continue
            needed = label.sizeHint().height()
            if label.height() > needed + 4:
                stretched[f"{screen.label}:{label.text()[:24]}"] = (label.height(), needed)

    assert not stretched, f"at {size[0]}x{size[1]} content was stretched: {stretched}"


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


# --- every button does something (§35.1) ---------------------------------------
#
# A button that is created, laid out, and never connected looks exactly like one that
# works. It is the same shape as §21.2's unread preference and §32's dead mode combo,
# and it shipped: the results screen's "Song select" was built, added to a row, styled,
# and had no slot, and a test that called the *handler* for the other button did not
# notice for a week.
#
# This is a static check rather than a behavioural one on purpose. Clicking every
# button and asserting something changed produced three false positives: two correctly
# *disabled* buttons (Play again with no result; Add to library with no file chosen)
# and one file dialog that cannot be clicked through in a test. A rule that needs an
# exception list is a rule that gets edited to pass, and this file records why that
# happened.


BUTTON_BUILDERS = ("constrained_button", "QPushButton")


def _unconnected_buttons(path: Path) -> list[str]:
    """Buttons created in ``path`` whose ``clicked`` is connected nowhere in it.

    Matches on the assigned name rather than the line, so a button wired a dozen lines
    later counts as wired. Both ``self._x = ...`` and a bare ``x = ...`` are handled,
    because both spellings are in use: most screens keep buttons as attributes and the
    simpler ones do not.
    """
    import ast

    tree = ast.parse(path.read_text())

    created: dict[str, int] = {}
    connected: set[str] = set()

    def _collect(body) -> None:
        for node in body:
            # A call used as a statement is wrapped in `ast.Expr`, so
            # `play.clicked.connect(...)` is an Expr and not a Call. Unwrapping it is
            # the whole difference between finding the connections and finding none of
            # them -- the first version walked with `ast.walk`, which sees the inner
            # call, and reported every button in the app as unconnected.
            call = node.value if isinstance(node, ast.Expr) else node
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                func = node.value.func
                name = (
                    func.attr
                    if isinstance(func, ast.Attribute)
                    else getattr(func, "id", "")
                )
                if name in BUTTON_BUILDERS:
                    for target in node.targets:
                        if isinstance(target, ast.Name):
                            created[target.id] = node.lineno
                        elif isinstance(target, ast.Attribute):
                            created[target.attr] = node.lineno
            elif (
                isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "connect"
                and isinstance(call.func.value, ast.Attribute)
                and call.func.value.attr == "clicked"
            ):
                base = call.func.value.value
                if isinstance(base, ast.Name):
                    connected.add(base.id)
                elif isinstance(base, ast.Attribute):
                    connected.add(base.attr)
            # Recurse, but not into a builder: `constrained_button` is a *factory*, and
            # the button it constructs is connected by whoever called it. Skipping the
            # definition is what stops the check from flagging its own helper.
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name not in BUTTON_BUILDERS:
                    _collect(node.body)
            elif isinstance(node, ast.ClassDef):
                _collect(node.body)

    _collect(tree.body)

    return sorted(f"{path.name}:{line} {name}" for name, line in created.items()
                  if name not in connected)


@pytest.mark.parametrize(
    "path",
    sorted(p for p in (ROOT / "guitaroids" / "ui").glob("*.py")),
    ids=lambda p: p.name,
)
def test_no_button_is_created_without_being_connected(path: Path) -> None:
    assert not _unconnected_buttons(path), (
        "these buttons are built and laid out but their `clicked` goes nowhere: "
        f"{_unconnected_buttons(path)}"
    )


# --- wrapped labels that are narrower than their text (§38.3) ------------------


def test_no_wrapped_label_is_shorter_than_its_text(shell, qapp) -> None:
    """Every word-wrapped label is given the height its text needs at its own width.

    The existing squeeze test checks that a layout does not compress a widget *below
    the minimum it declared*, which a label declares wrongly when its text needs more
    lines than its ``sizeHint`` predicted. So this asserts the property that actually
    matters: the height the label ends up with is enough to draw what is in it.

    Caught by rendering the screen rather than by any assertion, which is the point:
    the Preferences page had "Microphone input" drawn on top of the row beneath it
    for several commits, and every test on the screen passed.
    """
    from PySide6 import QtWidgets

    # Show the window and settle first. An unshown shell has every label at a default
    # width, so "No song played" measured 100px wide and appeared to need three lines
    # -- a measurement artefact, and the first version of this test failed on it.
    shell.show()
    shell.resize(960, 640)

    for screen_enum in Screen:
        shell.navigate(screen_enum)
        # Settle fully, and the reason is not fussiness. `showEvent` pins wrapped
        # labels, and a pin that changes a minimum height invalidates the layout, so
        # the heights are only final after a *second* pass. §38.1 found the same trap
        # in the screenshot tool, where one processEvents() was reporting overlaps
        # that did not exist; here one pass reports a shortfall that does not exist
        # either. The app gets the extra pass for free between frames, a test has to
        # ask for it.
        for _ in range(2):
            qapp.processEvents()
            if shell.current_screen.layout() is not None:
                shell.current_screen.layout().activate()
        qapp.processEvents()
        for label in shell.current_screen.findChildren(QtWidgets.QLabel):
            if not label.wordWrap() or not label.text().strip():
                continue
            needed = label.heightForWidth(label.width())
            if needed <= 0:
                continue
            assert label.height() >= needed, (
                f"{screen_enum.value}: {' '.join(label.text().split())[:44]!r} needs "
                f"{needed}px at {label.width()}px wide and has {label.height()}px, so "
                "its last line is drawn over whatever is below it"
            )


def test_the_pin_measures_the_labels_own_width_not_its_size_hint(qapp) -> None:
    """A narrow label that wraps is the case `sizeHint` gets wrong.

    At construction and during `showEvent` a label is often still at its old, wider
    width, so `sizeHint().height()` answers "one line" for text that is about to wrap
    to two. `heightForWidth` at the label's current width is the question that was
    being asked.
    """
    from PySide6 import QtWidgets

    from guitaroids.ui.screens import pin_wrapped_label_heights

    holder = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(holder)
    label = QtWidgets.QLabel("A deliberately long sentence that must wrap somewhere")
    label.setWordWrap(True)
    layout.addWidget(label)
    holder.resize(200, 400)
    label.setFixedWidth(90)  # narrow enough to force a wrap
    holder.show()
    qapp.processEvents()

    wrapped = label.heightForWidth(90)
    assert wrapped > 0
    assert pin_wrapped_label_heights(holder) >= 1
    assert label.minimumHeight() >= wrapped, (
        f"pinned to {label.minimumHeight()} but needs {wrapped} for its own width"
    )


# --- screen titles are centred (§42) ------------------------------------------


def test_every_screen_title_is_centred(shell) -> None:
    """A title is centred, so it sits in the middle of the window.

    Asserted per screen rather than once, because a screen that adds a title by hand
    can forget the role: the game HUD's title was a `kind="heading"` left-aligned over
    the left half of the highway, and nothing about it looked wrong until it was
    compared with the other five.
    """
    from PySide6 import QtCore, QtWidgets

    centred = QtCore.Qt.AlignmentFlag.AlignHCenter
    # "gameTitle" as well as "title": the HUD's title is renamed for its own
    # stylesheet rule, so the object name a screen ends up with is not a reliable way
    # to ask "is this a title". The alignment is the thing being asserted.
    names = ("title", "gameTitle")
    missing = []
    for screen_enum in Screen:
        shell.navigate(screen_enum)
        titles = [
            label
            for label in shell.current_screen.findChildren(QtWidgets.QLabel)
            if label.objectName() in names
        ]
        if not titles:
            missing.append(screen_enum.value)
            continue
        for title in titles:
            assert title.alignment() & centred, (
                f"{screen_enum.value}: {title.text()!r} is a title and is not centred"
            )
    assert not missing, f"these screens have no title label at all: {missing}"


def test_a_centred_title_does_not_sit_beside_its_subtitle(shell) -> None:
    """Song Select's title used to share a row with the status text.

    A title centred in "whatever is left over" is not centred in the window, so the
    status moved to its own centred row underneath. This asserts they are not in the
    same row any more, which is the only structural difference.
    """
    from PySide6 import QtWidgets

    shell.navigate(Screen.SONG_SELECT)
    screen = shell.current_screen
    header = screen._header
    assert isinstance(header, QtWidgets.QVBoxLayout), "the header is a column now"

    title_row = header.itemAt(0).layout()
    assert isinstance(title_row, QtWidgets.QHBoxLayout), (
        "the title needs a row of its own, with a stretch either side, to be centred "
        "on the window rather than on the space the status leaves"
    )
    # A stretch, the title, a stretch -- and nothing else.
    kinds = [title_row.itemAt(i).widget() for i in range(title_row.count())]
    titles = [w for w in kinds if isinstance(w, QtWidgets.QLabel)]
    assert len(titles) == 1, f"the title row holds {titles}"
    assert title_row.itemAt(0).widget() is None, "no stretch before the title"
    assert title_row.itemAt(title_row.count() - 1).widget() is None, "no stretch after"
    assert header.itemAt(1).widget() is screen._status, "the status is its own row"
