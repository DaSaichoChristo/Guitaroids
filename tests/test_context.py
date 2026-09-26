"""Tests for AppContext.

Uses a synthetic in-memory library rather than scanning ``songs/``, for two
reasons: the suite stays fast (a real scan costs ~0.2s per tab), and these tests
must not depend on whether the machine happens to have tabs in it.

The real-tab path is covered separately at the end, skipped when absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from songbuild import make_entry, make_song

from guitaroids.context import AppContext
from guitaroids.session.play_request import PlayRequest
from guitaroids.settings import InputMode, Settings
from guitaroids.songlib import Library

REAL_SONGS = Path(__file__).resolve().parent.parent / "songs"


@pytest.fixture()
def context() -> AppContext:
    entry = make_entry(make_song(), Path("songs/synthetic.gp5"))
    return AppContext(
        library=Library(root=Path("songs"), entries=(entry,)),
        settings=Settings(),
        songs_dir=Path("songs"),
        settings_path=Path("/nonexistent/settings.json"),
    )


# --- defaults ----------------------------------------------------------------


def test_defaults_are_usable_without_touching_disk() -> None:
    ctx = AppContext()
    assert isinstance(ctx.settings, Settings)
    assert ctx.play_request is None
    assert isinstance(ctx.library, Library)


def test_create_with_a_missing_songs_dir_is_not_fatal(tmp_path: Path) -> None:
    ctx = AppContext.create(
        songs_dir=tmp_path / "absent", settings_path=tmp_path / "settings.json"
    )
    assert ctx.library.entries == ()
    assert ctx.songs_dir == tmp_path / "absent"


# --- library -----------------------------------------------------------------


def test_entry_for_finds_a_known_slug(context: AppContext) -> None:
    assert context.entry_for("synthetic") is not None
    assert context.entry_for("nope") is None


def test_set_library_replaces_wholesale(context: AppContext) -> None:
    context.set_library(Library(root=Path("songs"), entries=()))
    assert context.entry_for("synthetic") is None


def test_rescan_replaces_the_library(context: AppContext, tmp_path: Path) -> None:
    """A rescan of an empty directory empties it, and does not raise."""
    context.songs_dir = tmp_path / "empty"
    (tmp_path / "empty").mkdir()
    library = context.rescan()
    assert library.entries == ()
    assert context.entry_for("synthetic") is None


# --- play requests -----------------------------------------------------------


def test_request_play_records_and_returns(context: AppContext) -> None:
    request = context.request_play("synthetic", 1)
    assert isinstance(request, PlayRequest)
    assert context.play_request == request


def test_request_play_uses_settings(context: AppContext) -> None:
    context.settings.count_in_bars = 2
    context.settings.collapse_chords = False
    request = context.request_play("synthetic", 1)
    assert request.count_in_bars == 2
    assert request.collapse_chords is False


def test_request_play_accepts_an_offset_override(context: AppContext) -> None:
    request = context.request_play("synthetic", 1, offset_ms=-120.0)
    assert request.offset_seconds == pytest.approx(-0.12)


def test_a_later_request_replaces_the_earlier_one(context: AppContext) -> None:
    context.request_play("synthetic", 1)
    second = context.request_play("synthetic", 2)
    assert context.play_request == second


def test_clear_play_request(context: AppContext) -> None:
    context.request_play("synthetic", 1)
    context.clear_play_request()
    assert context.play_request is None


# --- resolving a request into a chart ----------------------------------------


def test_chart_for_resolves_the_default_track(context: AppContext) -> None:
    request = context.request_play("synthetic", 1)
    chart = context.chart_for(request)
    assert chart is not None
    assert chart.notes, "a synthetic song should have notes"
    assert chart.tempo == 120


def test_chart_for_falls_back_to_the_stored_request(context: AppContext) -> None:
    context.request_play("synthetic", 1)
    assert context.chart_for() is not None, "no argument means 'the current request'"


def test_chart_for_returns_none_with_no_request(context: AppContext) -> None:
    assert context.chart_for() is None


def test_chart_for_unknown_slug_is_none_not_a_crash(context: AppContext) -> None:
    context.request_play("does_not_exist", 1)
    assert context.chart_for() is None


def test_chart_for_unknown_track_is_none(context: AppContext) -> None:
    context.request_play("synthetic", 99)
    assert context.chart_for() is None


def test_entry_for_request(context: AppContext) -> None:
    context.request_play("synthetic", 1)
    assert context.entry_for_request() is not None
    context.clear_play_request()
    assert context.entry_for_request() is None


# --- settings ----------------------------------------------------------------


def test_save_settings_writes_to_the_configured_path(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    ctx = AppContext(settings=Settings(master_volume=0.25), settings_path=path)
    ctx.save_settings()
    assert path.is_file()
    assert Settings.load(path).master_volume == 0.25


def test_create_loads_existing_settings(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    Settings(master_volume=0.11, input_mode=InputMode.CAMERA).save(path)
    ctx = AppContext.create(songs_dir=tmp_path, settings_path=path)
    assert ctx.settings.master_volume == 0.11
    assert ctx.settings.input_mode is InputMode.CAMERA


def test_create_falls_back_to_defaults_for_a_corrupt_settings_file(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ broken", encoding="utf-8")
    ctx = AppContext.create(songs_dir=tmp_path, settings_path=path)
    assert ctx.settings.to_dict() == Settings().to_dict()


# --- against the real library -----------------------------------------------

needs_songs = pytest.mark.skipif(
    not any(REAL_SONGS.glob("*.gp5")), reason="no tabs in songs/"
)


@needs_songs
def test_create_scans_the_real_songs_directory() -> None:
    ctx = AppContext.create(songs_dir=REAL_SONGS)
    assert ctx.library.entries, "a real tab should be found"


@needs_songs
def test_request_then_chart_works_end_to_end_on_a_real_tab(tmp_path: Path) -> None:
    """The whole path the game screen will take, on real data."""
    ctx = AppContext.create(songs_dir=REAL_SONGS)
    playable = ctx.library.playable
    if not playable:
        pytest.skip("no playable tab")
    entry = playable[0]
    default = entry.default_track
    assert default is not None

    request = ctx.request_play(entry.slug, default.number)
    chart = ctx.chart_for(request)
    assert chart is not None
    assert chart.notes
    assert chart.track_number == default.number


@needs_songs
def test_offered_tracks_all_produce_charts(tmp_path: Path) -> None:
    """Song select offers track choices; every one must actually be playable."""
    ctx = AppContext.create(songs_dir=REAL_SONGS)
    for entry in ctx.library.playable:
        for track in entry.tracks:
            chart = entry.chart_for(track.number)
            assert chart is not None, f"{entry.slug} track {track.number} offered but unplayable"
            assert chart.notes


# --- purity ------------------------------------------------------------------


def test_importing_context_does_not_import_qt() -> None:
    import subprocess
    import sys
    from pathlib import Path as P

    root = P(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import guitaroids.context, sys;"
            "bad=[n for n in ('PySide6','cv2') if n in sys.modules];"
            "print(','.join(bad) or 'clean')",
        ],
        capture_output=True,
        text=True,
        cwd=str(root),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "clean"
