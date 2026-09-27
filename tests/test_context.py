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
    Settings(master_volume=0.11, input_mode=InputMode.MICROPHONE).save(path)
    ctx = AppContext.create(songs_dir=tmp_path, settings_path=path)
    assert ctx.settings.master_volume == 0.11
    assert ctx.settings.input_mode is InputMode.MICROPHONE


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


# --- the chord setting actually reaches the chart (§21) ----------------------
#
# `PlayRequest.collapse_chords` was write-only: set, described, persisted, tested,
# and read by nothing. The chart came from the library scan, and both `start()`
# call sites omitted `collapse`, so the loader's own default won every time. These
# are the tests that would have caught it.


@pytest.fixture()
def chorded() -> AppContext:
    """A context whose one song is four six-note chords."""
    entry = make_entry(make_song(notes=4, chord_size=6), Path("songs/chords.gp5"))
    return AppContext(
        library=Library(root=Path("songs"), entries=(entry,)),
        settings=Settings(),
        songs_dir=Path("songs"),
        settings_path=Path("/nonexistent/settings.json"),
    )


def test_the_request_decides_whether_chords_are_collapsed(chorded: AppContext) -> None:
    """One library, two requests, two different songs -- and no rescan between them.

    The point of reading the preference off the request rather than the scan: the
    player unticks the box in Preferences, presses Play, and gets the other song.
    """
    chorded.request_play("chords", 1)
    full = chorded.chart_for(chorded.play_request)
    assert full is not None
    assert full.note_count == 24, "four chords of six"

    chorded.settings.collapse_chords = True
    collapsed_request = chorded.request_play("chords", 1)
    collapsed = chorded.chart_for(collapsed_request)
    assert collapsed is not None
    assert collapsed.note_count == 4, "one note per onset"
    assert full.onset_count == collapsed.onset_count == 4


def test_playing_a_second_song_with_a_changed_setting_needs_no_rescan(
    chorded: AppContext,
) -> None:
    """The second attempt differs from the first, with the same library object.

    Asserted on the library being untouched as much as on the two charts, because
    the old behaviour could also have been reached by rebuilding the library --
    and that needed a rescan the player was never asked for.
    """
    library = chorded.library
    chorded.settings.collapse_chords = False
    full = chorded.chart_for(chorded.request_play("chords", 1))
    chorded.settings.collapse_chords = True
    collapsed = chorded.chart_for(chorded.request_play("chords", 1))

    assert full is not None and collapsed is not None
    assert full.note_count > collapsed.note_count
    assert chorded.library is library, "charting must not have replaced the library"


def test_the_scan_uses_the_setting_too(tmp_path: Path) -> None:
    """The song list describes the library the player asked for.

    `AppContext.create` loaded its settings *after* scanning, so the first library
    was built with a default the player may not have chosen -- the same bug as the
    omitted `collapse` argument, one layer down.
    """
    from songbuild import write_tab

    songs = tmp_path / "songs"
    songs.mkdir()
    write_tab(songs / "song.gp5", notes=4, chord_size=6)
    settings_path = tmp_path / "settings.json"

    def charts(ctx: AppContext):
        return [e.chart for e in ctx.library.playable if e.chart is not None]

    # The setting has to be in place *before* create, because create scans first:
    # changing it afterwards is the very thing the test above shows has no effect.
    full = AppContext.create(
        songs_dir=songs,
        settings_path=settings_path,
        settings=Settings(collapse_chords=False),
    )
    collapsed = AppContext.create(
        songs_dir=songs,
        settings_path=settings_path,
        settings=Settings(collapse_chords=True),
    )

    full_chart = charts(full)[0]
    collapsed_chart = charts(collapsed)[0]
    # Stated as the relationship rather than as a magic number: collapsing keeps
    # every onset and drops notes. The round trip through the .gp5 format decides
    # how many onsets there are, and that is not this test's business.
    assert collapsed_chart.note_count == full_chart.onset_count
    assert full_chart.note_count > collapsed_chart.note_count


def test_a_full_chord_chart_is_what_the_app_ships(tmp_path: Path) -> None:
    """End to end from a settings file: default settings mean every note is kept.

    The regression guard for the flip itself, through the real path -- a file on
    disk, `Settings.load`, a scan, a chart -- rather than through a fixture that
    has already been told what to do.
    """
    from songbuild import write_tab

    songs = tmp_path / "songs"
    songs.mkdir()
    write_tab(songs / "song.gp5", notes=4, chord_size=6)

    ctx = AppContext.create(songs_dir=songs, settings_path=tmp_path / "settings.json")
    chart = ctx.chart_for(ctx.request_play("song", 1))
    assert chart is not None
    assert chart.note_count == 24
    assert "collapsed" not in " ".join(chart.warnings)


# --- playback (§24) ---------------------------------------------------------------
#
# The context owns the audio device handle, because §1.7 says screens never own
# devices and a rule you satisfy by leaking the handle somewhere else is not a rule
# satisfied. These are the guards; the device tests are at the bottom and skip when
# there is no sound card.


def test_a_fresh_context_is_not_playing() -> None:
    ctx = AppContext()
    assert ctx.playback is None
    assert ctx.is_playing is False
    assert ctx.song_position() == -1.0


def test_song_position_is_minus_one_rather_than_zero() -> None:
    """"Not playing" and "at the first note" are different states.

    For the real tab the first note is three seconds in, so a zero would be a lie
    the caller cannot detect.
    """
    assert AppContext().song_position() == -1.0


def test_stopping_with_nothing_open_is_safe() -> None:
    ctx = AppContext()
    ctx.stop_playback()
    ctx.stop_playback()
    assert ctx.playback is None


def test_the_playback_handle_is_excluded_from_repr_and_equality() -> None:
    """A dataclass field holding a device handle would put a C pointer in the repr
    and make two contexts unequal because of a sound card."""
    ctx = AppContext()
    assert "playback" not in repr(ctx)
    assert ctx == AppContext()


def _has_output() -> bool:
    try:
        import sounddevice as sd

        return any(d["max_output_channels"] > 0 for d in sd.query_devices())
    except Exception:  # noqa: BLE001 - no PortAudio is a legitimate state
        return False


needs_output = pytest.mark.skipif(not _has_output(), reason="no output device")


@needs_output
def test_starting_playback_opens_a_stream_and_gives_a_position() -> None:
    from guitaroids.audio.transport import silence

    ctx = AppContext()
    try:
        transport = ctx.start_playback(silence(0.4, 44100), sample_rate=44100, volume=1.0, device=None)
        assert ctx.playback is transport
        assert ctx.is_playing is True
        # Count-in of 0.25s, so the position starts negative: still in the lead-in.
        assert ctx.song_position() < 0.0
    finally:
        ctx.stop_playback()
    assert ctx.is_playing is False
    assert ctx.song_position() == -1.0


@needs_output
def test_starting_a_second_song_stops_the_first() -> None:
    """Two songs at once is not a subtle bug."""
    from guitaroids.audio.transport import silence

    ctx = AppContext()
    try:
        first = ctx.start_playback(silence(0.4, 44100), sample_rate=44100, volume=1.0, device=None)
        second = ctx.start_playback(silence(0.4, 44100), sample_rate=44100, volume=1.0, device=None)
        assert ctx.playback is second
        assert first.is_running is False, "the first stream was left open"
    finally:
        ctx.stop_playback()


@needs_output
def test_a_failed_stream_does_not_leave_a_handle_behind() -> None:
    """An unwritable buffer must not leave a half-open transport in the slot."""
    import numpy as np

    ctx = AppContext()
    with pytest.raises(Exception):
        ctx.start_playback(
        np.zeros((10, 2), dtype=np.float64), sample_rate=44100, volume=1.0, device=None
    )
    assert ctx.playback is None
