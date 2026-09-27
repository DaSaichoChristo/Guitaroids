"""Tests for the song select screen.

Widget tests, on the offscreen platform, against a context holding synthetic
entries built in memory. Nothing here scans a real directory: the screen is
tested for what it does with a library it is handed, and the scanning is already
covered in ``test_library_loader.py``.

The two things worth most of these tests are the ones a screenshot cannot show:
that Play records a request the game screen can actually resolve, and that a
rescan cannot leave the screen showing a song that no longer exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from qtsupport import SignalSpy, wait_until
from songbuild import make_entry, make_song

from guitaroids.context import AppContext
from guitaroids.session.play_request import PlayRequest
from guitaroids.settings import Settings
from guitaroids.songlib import Library, Status
from guitaroids.ui.screens import Screen
from guitaroids.ui.song_select import SongSelect

ROLE = 0  # Qt.ItemDataRole.UserRole


def library_of(*entries) -> Library:
    return Library(root=Path("/songs"), entries=tuple(entries))


@pytest.fixture()
def context(tmp_path: Path) -> AppContext:
    """A context with three playable songs and one broken tab.

    settings_path is real and inside tmp_path, so the test that checks the offset
    is persisted can actually check it.
    """
    entries = (
        make_entry(make_song(title="Alpha", artist="One", notes=2), Path("songs/alpha.gp5")),
        make_entry(make_song(title="Beta", artist="Two", notes=6), Path("songs/beta.gp5")),
        make_entry(make_song(title="Gamma", tracks=2, notes=4), Path("songs/gamma.gp5")),
        make_entry(make_song(title="Broken"), Path("songs/broken.gp5"), status=Status.PARSE_ERROR),
    )
    return AppContext(
        library=library_of(*entries),
        settings=Settings(),
        songs_dir=tmp_path / "songs",
        settings_path=tmp_path / "settings.json",
        # These tests press Play, which navigates to GAME, and the game screen would
        # start a background render for each one. They are about song select.
        audio_enabled=False,
    )


@pytest.fixture()
def screen(shell, context) -> SongSelect:
    """The song select screen, built and populated."""
    shell.navigate(Screen.SONG_SELECT)
    widget = shell.current_screen
    assert isinstance(widget, SongSelect)
    return widget


@pytest.fixture()
def on_disk(context, tmp_path) -> Path:
    """The fixture's four songs, written where a rescan will find them.

    The in-memory library and the songs directory would otherwise disagree, and
    every rescan test would be testing a rescan of an empty directory -- which
    passes for the wrong reason.
    """
    from songbuild import broken_tab, write_tab

    songs = tmp_path / "songs"
    songs.mkdir(exist_ok=True)
    write_tab(songs / "alpha.gp5", notes=2)
    write_tab(songs / "beta.gp5", notes=6)
    write_tab(songs / "gamma.gp5", tracks=2)
    broken_tab(songs / "broken.gp5")
    assert context.songs_dir == songs
    return songs


# --- populating --------------------------------------------------------------


def test_lists_every_playable_song(screen: SongSelect) -> None:
    titles = [screen._songs.item(i).text() for i in range(screen._songs.count())]
    assert len(titles) == 3
    assert any("Alpha" in t for t in titles)
    assert any("Beta" in t for t in titles)
    assert any("Gamma" in t for t in titles)


def test_a_broken_tab_is_not_in_the_song_list(screen: SongSelect) -> None:
    """It cannot be played, so offering it as a row would be a dead end."""
    titles = [screen._songs.item(i).text() for i in range(screen._songs.count())]
    assert not any("Broken" in t for t in titles)


def test_broken_tabs_go_to_the_problems_list(screen: SongSelect) -> None:
    assert screen._problems.count() == 1
    text = screen._problems.item(0).text()
    assert "broken.gp5" in text
    assert "could not be read" in text, "a problem must say what is wrong with it"


def test_the_problems_box_is_hidden_when_there_are_none(shell, context) -> None:
    context.set_library(library_of(make_entry(make_song(), Path("songs/only.gp5"))))
    shell.navigate(Screen.SONG_SELECT)
    screen = shell.current_screen
    assert not screen._problems_box.isVisibleTo(screen)


def test_the_problems_box_appears_when_there_are(shell, context) -> None:
    shell.navigate(Screen.SONG_SELECT)
    screen = shell.current_screen
    assert screen._problems_box.isVisibleTo(screen)
    assert "1" in screen._problems_box.title()


def test_no_songs_shows_the_empty_state(shell, context) -> None:
    context.set_library(library_of())
    shell.navigate(Screen.SONG_SELECT)
    screen = shell.current_screen
    assert screen._songs.count() == 0
    assert screen._empty.isVisibleTo(screen), "first run must explain itself, not show nothing"
    assert not screen._play.isEnabled(), "there is nothing to play"


def test_the_status_line_counts_what_is_playable(screen: SongSelect) -> None:
    assert "3 of 4" in screen._status.text()


def test_an_empty_library_says_so_rather_than_showing_nothing(shell, context) -> None:
    context.set_library(library_of())
    shell.navigate(Screen.SONG_SELECT)
    assert "no tabs" in shell.current_screen._status.text()


# --- selection ---------------------------------------------------------------


def test_the_first_song_is_selected_by_default(screen: SongSelect) -> None:
    assert screen._songs.currentRow() == 0
    assert screen._play.isEnabled()


def test_the_detail_pane_shows_the_selected_song(screen: SongSelect) -> None:
    screen._songs.setCurrentRow(0)
    assert "Alpha" in screen._detail_title.text()
    assert "One" in screen._detail_artist.text()


def test_changing_the_selection_updates_the_details(screen: SongSelect) -> None:
    screen._songs.setCurrentRow(0)
    screen._songs.setCurrentRow(1)
    assert "Beta" in screen._detail_title.text()
    assert "One" not in screen._detail_title.text()


def test_the_facts_include_tempo_notes_and_difficulty(screen: SongSelect) -> None:
    """The numbers the player uses to choose, so all of them must be present."""
    from PySide6 import QtWidgets

    roles = (QtWidgets.QFormLayout.ItemRole.LabelRole, QtWidgets.QFormLayout.ItemRole.FieldRole)
    rendered = " ".join(
        screen._facts.itemAt(row, role).widget().text()
        for row in range(screen._facts.rowCount())
        for role in roles
    )
    for expected in (
        "Tempo",
        "Length",
        "Notes",
        "Note density",
        "Difficulty",
        "Audio",
    ):
        assert expected in rendered


def test_the_density_shown_is_the_one_the_band_is_computed_from(screen: SongSelect) -> None:
    """The band must be checkable against a number the player can see.

    The card showed **note** density beside a difficulty banded on **onset** density,
    and the onset figure appeared nowhere. A player reading "10.77 nps" next to
    "Medium" cannot verify the label, and the numbers really are different -- a chord
    is six notes and one onset. §45 is that this read as a bug in the band rather than
    as two measures.

    The rate travels in the Difficulty cell rather than in a row of its own: the card
    is tight enough that a row pushed Practice tempo out of view, and a number belongs
    beside the label it explains anyway.

    Asserted on the rendered rows rather than on the properties, because the thing
    that was wrong was a label on screen.
    """
    from PySide6 import QtWidgets

    roles = (QtWidgets.QFormLayout.ItemRole.LabelRole, QtWidgets.QFormLayout.ItemRole.FieldRole)
    rows = {
        screen._facts.itemAt(row, roles[0]).widget().text(): screen._facts.itemAt(
            row, roles[1]
        ).widget().text()
        for row in range(screen._facts.rowCount())
    }
    chart = screen._selected_entry().chart
    entry = screen._selected_entry()

    # The band, and the rate that produced it, in the same cell. Asserted on the
    # rendered text because the defect was a label on screen, not a number in a
    # property.
    assert rows["Difficulty"] == f"{entry.difficulty} ({chart.onsets_per_second:.2f} ops)"

    assert rows["Note density"] == f"{chart.notes_per_second:.2f} nps"

    # Note that this fixture is chordless, so the two rates coincide at 0.8 and the
    # cell cannot demonstrate that the *right* one is shown -- only that a rate is.
    # What makes the choice visible is a real tab: on Hotel California the numbers are
    # 10.77 and 2.91, and §45's first version put only the first next to the band.
    # `test_onset_count_survives_collapse` in test_chart.py covers the invariance that
    # makes the distinction matter at all.


def test_every_track_is_offered(screen: SongSelect) -> None:
    """Both tracks of a two-track song, because the user picks the part."""
    for row in range(screen._songs.count()):
        screen._songs.setCurrentRow(row)
    screen._select_slug("gamma")
    assert screen._tracks.count() == 2


def test_the_default_track_is_preselected(screen: SongSelect) -> None:
    screen._select_slug("gamma")
    default = screen._selected_entry().default_track
    assert screen._tracks.currentData() == default.number


def test_the_track_combo_stores_the_track_number(screen: SongSelect) -> None:
    """Not the index: a combo whose data is a row number breaks when rows change."""
    screen._select_slug("gamma")
    numbers = [screen._tracks.itemData(i) for i in range(screen._tracks.count())]
    assert numbers == [1, 2]


# --- playing -----------------------------------------------------------------


def test_play_records_a_request_and_navigates(screen: SongSelect, context, shell) -> None:
    screen._select_slug("alpha")
    screen._play.click()
    request = context.play_request
    assert isinstance(request, PlayRequest)
    assert request.slug == "alpha"
    assert request.track_number == 1
    assert shell.current is Screen.GAME


def test_play_uses_the_track_the_user_chose(screen: SongSelect, context) -> None:
    screen._select_slug("gamma")
    screen._tracks.setCurrentIndex(screen._tracks.findData(2))
    screen._play.click()
    assert context.play_request.track_number == 2


def test_play_carries_the_offset(screen: SongSelect, context) -> None:
    screen._select_slug("alpha")
    screen._offset.setValue(-135)
    screen._play.click()
    assert context.play_request.offset_seconds == pytest.approx(-0.135)


def test_the_played_chart_can_actually_be_resolved(screen: SongSelect, context) -> None:
    """The end-to-end promise: what Play records, the game screen can load."""
    screen._select_slug("alpha")
    screen._play.click()
    chart = context.chart_for()
    assert chart is not None, "Play offered a song that will not load"
    assert chart.notes
    assert chart.track_number == context.play_request.track_number


def test_double_click_also_plays(screen: SongSelect, context) -> None:
    screen._songs.setCurrentRow(1)
    item = screen._songs.currentItem()
    screen._songs.itemDoubleClicked.emit(item)
    assert context.play_request is not None
    assert context.play_request.slug == "beta"


def test_playing_with_nothing_selected_does_nothing(shell, context) -> None:
    context.set_library(library_of())
    shell.navigate(Screen.SONG_SELECT)
    screen = shell.current_screen
    screen._play.click()  # disabled, but a stray call must still be harmless
    assert context.play_request is None
    assert shell.current is Screen.SONG_SELECT


# --- the offset slider -------------------------------------------------------


def test_the_slider_starts_at_the_remembered_offset(shell, context) -> None:
    context.settings.set_offset_for("alpha", -80.0)
    shell.navigate(Screen.SONG_SELECT)
    assert shell.current_screen._offset.value() == -80


def test_the_slider_defaults_to_zero_for_an_unseen_song(screen: SongSelect) -> None:
    screen._select_slug("beta")
    assert screen._offset.value() == 0


def test_the_slider_label_shows_the_sign(screen: SongSelect) -> None:
    screen._offset.setValue(0)
    assert screen._offset_label.text() == "+0 ms"
    screen._offset.setValue(-135)
    assert screen._offset_label.text() == "-135 ms"
    screen._offset.setValue(40)
    assert screen._offset_label.text() == "+40 ms"


def test_moving_the_slider_does_not_write_settings(screen: SongSelect, context) -> None:
    """A settings save is an fsync, and a drag emits valueChanged continuously."""
    screen._offset.setValue(120)
    assert context.settings.offset_for("alpha") == 0.0


def test_playing_persists_the_offset(screen: SongSelect, context) -> None:
    screen._offset.setValue(-60)
    screen._play.click()
    assert context.settings.offset_for("alpha") == -60.0
    assert context.settings_path.is_file(), "the offset must reach disk, not just memory"
    assert Settings.load(context.settings_path).offset_for("alpha") == -60.0


def test_offsets_are_remembered_per_song(screen: SongSelect, context) -> None:
    screen._select_slug("alpha")
    screen._offset.setValue(25)
    screen._play.click()
    screen._select_slug("beta")
    assert screen._offset.value() == 0, "one song's offset must not leak into another's"


# --- rescan ------------------------------------------------------------------


def test_rescan_starts_a_background_scan(screen: SongSelect, qapp) -> None:
    assert screen._loader.is_running is False
    screen._rescan.click()
    assert screen._loader.is_running is True
    assert wait_until(lambda: not screen._loader.is_running)


def test_rescan_disables_the_button_while_it_runs(screen: SongSelect, qapp) -> None:
    """Two concurrent scans would race to write the library."""
    screen._rescan.click()
    assert not screen._rescan.isEnabled()


def test_rescan_replaces_the_library_and_the_list(
    screen: SongSelect, context, tmp_path, qapp
) -> None:
    from songbuild import write_tab

    songs = tmp_path / "songs"
    songs.mkdir(exist_ok=True)
    context.songs_dir = songs
    write_tab(songs / "delta.gp5")

    done = SignalSpy(screen._loader.completed)
    screen._rescan.click()
    assert done.wait(), done.describe()

    texts = [screen._songs.item(i).text() for i in range(screen._songs.count())]
    assert len(texts) == 1
    assert "delta" in texts[0].lower()
    assert [e.slug for e in context.library.entries] == ["delta"]


def test_rescan_drops_a_song_that_disappeared(
    screen: SongSelect, context, tmp_path, qapp
) -> None:
    """A song that is gone must not stay in the list, selectable."""
    from songbuild import write_tab

    songs = tmp_path / "songs"
    songs.mkdir(exist_ok=True)
    context.songs_dir = songs
    write_tab(songs / "alpha.gp5")

    done = SignalSpy(screen._loader.completed)
    screen._rescan.click()
    assert done.wait(), done.describe()

    assert screen._songs.count() == 1
    assert "Beta" not in screen._songs.item(0).text()


def test_rescan_keeps_the_selection_across_a_scan(
    screen: SongSelect, on_disk, qapp
) -> None:
    """The player's place must survive a refresh, or a rescan is a nuisance."""
    screen._select_slug("beta")
    done = SignalSpy(screen._loader.completed)
    screen._rescan.click()
    assert done.wait(), done.describe()
    assert screen._selected_slug() == "beta"
    assert screen._songs.currentRow() == 1


def test_the_button_is_re_enabled_after_a_scan(screen: SongSelect, context, tmp_path, qapp) -> None:
    done = SignalSpy(screen._loader.completed)
    screen._rescan.click()
    assert done.wait(), done.describe()
    assert screen._rescan.isEnabled()


def test_navigating_away_cancels_the_scan(
    screen: SongSelect, shell, qapp, monkeypatch, on_disk
) -> None:
    """A scan outliving the screen would emit into a destroyed widget.

    load_tab is slowed so the scan is reliably still in flight when the
    navigation happens: against four tiny tabs it would otherwise finish first,
    and the test would pass without ever exercising the cancel.
    """
    import time

    import guitaroids.ui.library_loader as loader_module

    real = loader_module.load_tab

    def slow(path, **kwargs):
        time.sleep(0.05)
        return real(path, **kwargs)

    monkeypatch.setattr(loader_module, "load_tab", slow)

    cancelled = SignalSpy(screen._loader.cancelled)
    completed = SignalSpy(screen._loader.completed)
    screen._rescan.click()
    assert screen._loader.is_running is True

    shell.navigate(Screen.MAIN)

    assert wait_until(lambda: not screen._loader.is_running)
    assert cancelled.count == 1, "leaving the screen must stop the scan"
    assert completed.count == 0, "a cancelled scan must not also report completion"


def test_a_failed_scan_is_reported_and_re_enables_the_button(
    screen: SongSelect, monkeypatch, qapp
) -> None:
    import guitaroids.ui.library_loader as loader_module

    def explode(_root):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(loader_module, "find_tabs", explode)
    screen._rescan.click()
    assert wait_until(lambda: not screen._loader.is_running)
    assert "could not read" in screen._status.text()
    assert screen._rescan.isEnabled(), "a failed scan must not leave the button dead"


# --- robustness --------------------------------------------------------------


def test_the_screen_owns_its_loader(screen: SongSelect) -> None:
    """The loader dies with the screen rather than being kept alive by the context.

    AppContext is pinned pure -- no Qt -- by a subprocess test, so it cannot be
    the loader's home.
    """
    assert screen._loader.parent() is screen


def test_the_loader_is_not_rebuilt_when_the_screen_is_revisited(shell) -> None:
    shell.navigate(Screen.SONG_SELECT)
    first = shell.current_screen
    shell.navigate(Screen.MAIN)
    shell.navigate(Screen.SONG_SELECT)
    assert shell.current_screen is first, "screens are built once and kept"


def test_no_song_row_is_clickable_when_there_is_nothing_to_play(shell, context) -> None:
    context.set_library(library_of())
    shell.navigate(Screen.SONG_SELECT)
    screen = shell.current_screen
    assert not screen._play.isEnabled()
    assert not screen._tracks.isEnabled()
    assert not screen._offset.isEnabled()


def test_a_song_with_no_tracks_does_not_crash_the_pane(shell, context) -> None:
    """Defensive: an entry with tracks=() would leave the combo empty.

    songlib filters empty tracks out, so this should be unreachable -- but the
    detail pane reading a chart off a selection it cannot fully populate is
    exactly the place an AttributeError would be least welcome.
    """
    entry = make_entry(make_song(), Path("songs/alpha.gp5"))
    stripped = type(entry)(
        tab_path=entry.tab_path,
        status=entry.status,
        chart=entry.chart,
        audio_path=None,
        tracks=(),
        _song=entry._song,
    )
    context.set_library(library_of(stripped))
    shell.navigate(Screen.SONG_SELECT)
    screen = shell.current_screen
    assert screen._tracks.count() == 0
    screen._play.click()
    assert context.play_request is None
    assert shell.current is Screen.SONG_SELECT, "must not navigate on an unplayable pick"


# --- practice tempo ----------------------------------------------------------
#
# The control moved here from the game screen in §19.1. These are the tests that
# moved with it, plus the ones the move made necessary: a value that has to
# survive a screen boundary, and a stored 0 that has to mean "as written" rather
# than than a 20 BPM song.


def test_the_tempo_starts_at_the_written_tempo(screen: SongSelect) -> None:
    """Untouched means as written, which is 120 for the fixture's synthetic tabs."""
    assert screen._written_bpm() == 120
    assert screen._bpm.value() == 120
    assert screen._bpm_label.text() == "as written"


def test_the_tempo_cannot_exceed_the_written_tempo(screen: SongSelect) -> None:
    """A tab played faster than written is a different piece of music.

    The range is clamped rather than the value being checked afterwards, so the
    control cannot *express* the tempo either -- there is no state in which the box
    reads a number the game will refuse.
    """
    assert screen._bpm.maximum() == 120
    screen._bpm.setValue(145)
    assert screen._bpm.value() == 120


def test_the_tempo_floors_at_the_minimum(screen: SongSelect) -> None:
    from guitaroids.settings import MIN_BPM

    screen._bpm.setValue(1)
    assert screen._bpm.value() == int(MIN_BPM)


def test_the_label_says_how_much_slower(screen: SongSelect) -> None:
    """An absolute number next to a "Tempo 120" fact does not say *slower*.

    The stored value is absolute (§18.3) and stays that way; the percentage is a
    display of it, not the thing being saved.
    """
    screen._bpm.setValue(60)
    assert screen._bpm_label.text() == "50% of written"


def test_play_carries_the_tempo(screen: SongSelect, context) -> None:
    screen._bpm.setValue(60)
    screen._play.click()
    assert context.play_request.bpm == pytest.approx(60.0)


def test_playing_at_the_written_tempo_sends_no_tempo(screen: SongSelect, context) -> None:
    """0 is the "as written" sentinel, and the request says so.

    Storing 120 in the file would pin the song at 120 after the tab is re-exported
    at 84, which is a stale value pretending to be a remembered choice.
    """
    screen._play.click()
    assert context.play_request.bpm == 0.0
    assert context.settings.song_bpm["alpha"] == 0.0


def test_playing_persists_the_tempo(screen: SongSelect, context) -> None:
    screen._bpm.setValue(72)
    screen._play.click()
    assert context.settings.bpm_for("alpha") == 72.0
    assert context.settings_path.is_file(), "the tempo must reach disk, not just memory"
    assert Settings.load(context.settings_path).bpm_for("alpha") == 72.0


def test_a_remembered_tempo_is_loaded_when_the_song_is_selected(shell, context) -> None:
    context.settings.set_bpm_for("alpha", 80.0)
    shell.navigate(Screen.SONG_SELECT)
    screen = shell.current_screen
    screen._select_slug("alpha")
    assert screen._bpm.value() == 80


def test_tempos_are_remembered_per_song(screen: SongSelect) -> None:
    screen._select_slug("alpha")
    screen._bpm.setValue(60)
    screen._play.click()
    screen._select_slug("beta")
    assert screen._bpm.value() == 120, "one song's tempo must not leak into another's"


def test_a_stored_tempo_above_the_written_one_is_clamped_in(shell, context) -> None:
    """A settings file is hand-editable, and a tab's tempo can change under it.

    Clamped on load, not honoured: a stored 160 on a 120 BPM tab would be a rate
    above 1.0, which the game refuses anyway (§18.3) -- so the control would show
    160 and the game would play 120.
    """
    context.settings.set_bpm_for("alpha", 160.0)
    shell.navigate(Screen.SONG_SELECT)
    screen = shell.current_screen
    screen._select_slug("alpha")
    assert screen._bpm.value() == 120
    assert screen._bpm_label.text() == "as written"


def test_the_tempo_is_disabled_with_nothing_playable(shell, context) -> None:
    context.set_library(library_of())
    shell.navigate(Screen.SONG_SELECT)
    screen = shell.current_screen
    assert not screen._bpm.isEnabled()
    assert not screen._bpm_label.isEnabled()
    assert screen._bpm_label.text() == ""


def test_the_tempo_control_is_inside_the_detail_card(screen: SongSelect) -> None:
    """A parentless widget is a top-level window and renders nowhere.

    The same bug the game screen's spin box had (§18.5), in the screen it moved
    to. Checked by walking up to the card rather than by testing the immediate
    parent, because the details now live on a scroll area's widget (§19.2) and the
    immediate parent is that, not the card.
    """
    from PySide6 import QtWidgets

    widget = screen._bpm
    while widget is not None and widget.objectName() != "card":
        widget = widget.parent()
    assert widget is not None, "the tempo control is not inside the detail card"
    assert widget is not screen, "a parentless spin box is its own top-level window"
    assert screen._bpm.isVisibleTo(screen), "present in the tree but not on screen"
