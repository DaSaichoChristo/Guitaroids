"""Tests for the song library scanner.

Exercises pairing, status classification, and the "one bad file must not stop the
library" rule. Writes real (tiny, invalid) files to a tmp_path so the filesystem
walk is genuinely tested rather than mocked.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from guitarpro.models import MixTableItem
from songbuild import make_song

from guitaroids.songlib import (
    AUDIO_EXTENSIONS,
    EASY_BELOW,
    HARD_BELOW,
    MEDIUM_BELOW,
    Status,
    classify_error,
    describe_tracks,
    difficulty_band,
    find_audio,
    format_duration,
    load_tab,
    scan_library,
)
from guitaroids.context import AppContext
from guitaroids.model.chart import ChartError, changes_tempo

ROOT = Path(__file__).resolve().parent.parent
REAL_SONGS = ROOT / "songs"
needs_songs = pytest.mark.skipif(
    not any(REAL_SONGS.glob("*.gp*")), reason="no tabs in songs/"
)


def write_junk(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"NOT-A-GUITAR-PRO-FILE" * 8)
    return path


# --- missing directories ------------------------------------------------------


def test_missing_directory_is_empty_not_an_error() -> None:
    library = scan_library("/definitely/not/here")
    assert library.entries == ()
    assert library.playable == ()


def test_empty_directory_is_empty() -> None:
    library = scan_library(ROOT / "songs")
    assert isinstance(library.entries, tuple)


# --- bad files never raise ----------------------------------------------------


def test_junk_file_becomes_a_status_not_an_exception(tmp_path: Path) -> None:
    tab = write_junk(tmp_path / "broken.gp5")
    entry = load_tab(tab)
    assert entry.status in (Status.PARSE_ERROR, Status.UNSUPPORTED_VERSION)
    assert entry.detail


def test_one_bad_file_does_not_hide_the_others(tmp_path: Path) -> None:
    write_junk(tmp_path / "a_broken.gp5")
    write_junk(tmp_path / "b_also_broken.gp5")
    library = scan_library(tmp_path)
    assert len(library.entries) == 2
    assert len(library.problems) == 2
    assert library.playable == ()


def test_non_gp5_files_are_ignored(tmp_path: Path) -> None:
    write_junk(tmp_path / "notes.txt")
    write_junk(tmp_path / "song.gpx")
    (tmp_path / "backing.mp3").write_bytes(b"fake")
    library = scan_library(tmp_path)
    assert library.entries == ()


# --- audio pairing ------------------------------------------------------------


@pytest.mark.parametrize("extension", AUDIO_EXTENSIONS)
def test_audio_paired_by_extension(tmp_path: Path, extension: str) -> None:
    (tmp_path / f"song{extension}").write_bytes(b"fake audio")
    found = find_audio(tmp_path, "song")
    assert found is not None
    assert found.suffix == extension


def test_missing_audio_is_none(tmp_path: Path) -> None:
    assert find_audio(tmp_path, "song") is None


def test_extension_priority_is_deterministic(tmp_path: Path) -> None:
    """ogg wins over mp3 because AUDIO_EXTENSIONS order is the priority order."""
    for extension in AUDIO_EXTENSIONS:
        (tmp_path / f"song{extension}").write_bytes(b"x")
    assert find_audio(tmp_path, "song").suffix == ".ogg"


def test_audio_must_share_the_tab_basename(tmp_path: Path) -> None:
    (tmp_path / "different.mp3").write_bytes(b"x")
    assert find_audio(tmp_path, "song") is None


# --- classification -----------------------------------------------------------


@pytest.mark.parametrize(
    "message,expected",
    [
        ("unsupported version 'BCFZ'", Status.UNSUPPORTED_VERSION),
        ("this tab changes tempo partway through", Status.TEMPO_CHANGE),
        ("no playable notes found in the selected track", Status.EMPTY),
        ("no 6-string guitar track (found [4] strings)", Status.NOT_GUITAR),
        ("no non-percussion track in this tab", Status.NOT_GUITAR),
        ("something entirely unexpected", Status.PARSE_ERROR),
    ],
)
def test_classify(message: str, expected: Status) -> None:
    """Pins the message-matching. If these drift, this is where it shows up."""
    assert classify_error(ChartError(message))[0] is expected


def test_playable_statuses() -> None:
    assert Status.OK.is_playable
    assert Status.NO_AUDIO.is_playable
    assert not Status.PARSE_ERROR.is_playable
    assert not Status.TEMPO_CHANGE.is_playable


# --- entry derived properties -------------------------------------------------


def test_entry_falls_back_to_filename_when_unparsed(tmp_path: Path) -> None:
    tab = write_junk(tmp_path / "my_song.gp5")
    entry = load_tab(tab)
    assert entry.slug == "my_song"
    assert entry.title == "my_song", "falls back to the stem when no chart"
    assert entry.tempo == 0
    assert entry.duration == 0.0
    assert entry.difficulty == "?"


@pytest.mark.parametrize(
    "seconds,expected",
    [(0, "0:00"), (-5, "0:00"), (1, "0:01"), (59, "0:59"), (60, "1:00"), (185, "3:05")],
)
def test_format_duration(seconds: float, expected: str) -> None:
    assert format_duration(seconds) == expected


# --- library queries ----------------------------------------------------------


def test_library_get_by_slug(tmp_path: Path) -> None:
    write_junk(tmp_path / "alpha.gp5")
    write_junk(tmp_path / "beta.gp5")
    library = scan_library(tmp_path)
    assert library.get("alpha") is not None
    assert library.get("beta") is not None
    assert library.get("gamma") is None


def test_library_by_status(tmp_path: Path) -> None:
    write_junk(tmp_path / "one.gp5")
    write_junk(tmp_path / "two.gp5")
    library = scan_library(tmp_path)
    first_status = library.entries[0].status
    assert len(library.by_status(first_status)) == 2


def test_scan_is_recursive(tmp_path: Path) -> None:
    write_junk(tmp_path / "top.gp5")
    write_junk(tmp_path / "sub" / "nested.gp5")
    library = scan_library(tmp_path)
    assert len(library.entries) == 2


# --- the CLI ------------------------------------------------------------------


def _run_cli(target: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "import_songs.py"), str(target), "--json"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )


def test_cli_json_output_is_valid(tmp_path: Path) -> None:
    write_junk(tmp_path / "broken.gp5")
    result = _run_cli(tmp_path)
    assert result.returncode == 1, "problems present -> exit 1"
    payload = json.loads(result.stdout)
    assert payload["root"] == str(tmp_path)
    assert len(payload["entries"]) == 1
    assert payload["entries"][0]["slug"] == "broken"


def test_cli_on_empty_library_exits_zero(tmp_path: Path) -> None:
    result = _run_cli(tmp_path)
    assert result.returncode == 0
    assert json.loads(result.stdout)["entries"] == []


def test_cli_human_output_mentions_counts(tmp_path: Path) -> None:
    write_junk(tmp_path / "x.gp5")
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "import_songs.py"), str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert "1 tab(s)" in result.stdout
    assert "PROBLEMS" in result.stdout


# --- tempo changes -----------------------------------------------------------
#
# These use hand-built stand-ins rather than a real tab. PyGuitarPro will not
# produce a tempo change from `make_song`, and the point is the *shape* of the API:
# `MixTableChange.tempo` is a MixTableItem, not a number, and None when the change
# only touches volume. Both were found by a real .gp4 (Sweet Child O' Mine).


class _Fake:
    """The smallest object the tempo check reads from."""

    def __init__(self, **fields) -> None:
        self.__dict__.update(fields)


def _track_with_mix_tempo(tempo, *, song_tempo: int = 127):
    """A track carrying one beat whose mix table sets ``tempo``."""
    change = _Fake(tempo=tempo)
    beat = _Fake(effect=_Fake(mixTableChange=change))
    voice = _Fake(beats=[beat])
    measure = _Fake(voices=[voice])
    return _Fake(measures=[measure]), _Fake(tempo=song_tempo)


def test_a_mix_table_tempo_is_a_value_not_a_number() -> None:
    """The bug: `int(change.tempo)` raised TypeError on a real tab.

    It took out the whole library scan, not one tab, because the crash happened
    inside the loader's per-file guard and the guard did not catch a TypeError.
    """
    from guitarpro.models import MixTableItem

    track, song = _track_with_mix_tempo(MixTableItem(value=96, duration=0))
    assert changes_tempo(song, track) is True


def test_a_mix_table_change_that_keeps_the_tempo_is_not_a_change() -> None:
    from guitarpro.models import MixTableItem

    track, song = _track_with_mix_tempo(MixTableItem(value=127, duration=0))
    assert changes_tempo(song, track) is False


def test_a_mix_table_change_that_never_touches_the_tempo_is_not_a_change() -> None:
    """`tempo` is None when the change only adjusts volume or reverb."""
    track, song = _track_with_mix_tempo(None)
    assert changes_tempo(song, track) is False


def test_a_song_with_no_tempo_is_not_treated_as_a_change() -> None:
    """Otherwise every track in such a tab would be silently unplayable."""
    track, song = _track_with_mix_tempo(96, song_tempo=0)
    assert changes_tempo(song, track) is False


def test_a_tempo_changing_track_is_not_offered(tmp_path: Path) -> None:
    """Song select must not list a track the game will refuse to chart.

    The tab is playable; one of its tracks is not. Offering it made the combo box
    a list of dead ends, which is what the real .gp4's track 3 was.
    """
    from guitarpro.models import MixTableChange

    song = make_song(tempo=127, tracks=2, notes=4)
    song.tracks[1].measures[0].voices[0].beats[0].effect = MixTableChange(
        tempo=MixTableItem(value=96, duration=0)
    )

    offered = describe_tracks(song, None)

    assert [t.number for t in offered] == [1], "only the constant-tempo track"


def test_a_mix_table_that_is_the_effect_itself_is_read_too() -> None:
    """The other real shape: no ``mixTableChange`` wrapper at all.

    Same file library, different PyGuitarPro effect type, and the tab that has it
    is the one a guitarist would actually want to play.
    """
    from guitarpro.models import MixTableChange

    effect = MixTableChange(tempo=MixTableItem(value=96, duration=0))
    beat = _Fake(effect=effect)
    track = _Fake(measures=[_Fake(voices=[_Fake(beats=[beat])])])
    song = _Fake(tempo=127)

    assert changes_tempo(song, track) is True
    assert changes_tempo(_Fake(tempo=96), track) is False


def test_an_effect_with_no_mix_table_at_all_is_not_a_tempo_change() -> None:
    """Slides, bends and the rest must not be mistaken for mix tables.

    Checked on a real tab: every synthetic note in ``make_song`` carries one, and a
    bare ``effect.mixTableChange`` raised AttributeError on the second one. An
    exception here is not one bad tab, it is a dead library scan.
    """
    song = make_song(tracks=1, notes=4)
    assert changes_tempo(song, song.tracks[0]) is False


@needs_songs
def test_difficulty_is_unchanged_by_the_chord_setting() -> None:
    """The band is a property of the song, not of a checkbox (§21).

    On the real tab, notes go from 1108 to 4099 when chords are kept -- density
    2.91 nps to 10.77 -- which would put every rock tab in "Expert" and leave the
    column saying nothing. Banding on onsets keeps the rating honest.
    """
    from guitaroids.settings import Settings

    collapsed = AppContext.create(
        songs_dir=REAL_SONGS, settings_path=Path("/nonexistent/s.json"),
        settings=Settings(collapse_chords=True),
    )
    full = AppContext.create(
        songs_dir=REAL_SONGS, settings_path=Path("/nonexistent/s.json"),
        settings=Settings(collapse_chords=False),
    )
    bands = {e.slug: (e.difficulty, e.notes_per_second) for e in collapsed.library.playable}
    for entry in full.library.playable:
        assert entry.difficulty == bands[entry.slug][0], (
            f"{entry.slug}: {entry.difficulty} vs {bands[entry.slug][0]}"
        )
    # ...and the density really did move, so the test is not passing because
    # nothing changed.
    assert any(
        entry.notes_per_second != bands[entry.slug][1] for entry in full.library.playable
    ), "full chords must change the note density; otherwise this proves nothing"


@needs_songs
def test_the_real_tab_keeps_every_note_by_default() -> None:
    """The flip, on the data that prompted it.

    Hotel California is 4099 notes over 1108 onsets. Collapsing it lost 2991 notes
    and put 68% of the rest on the high E, which is what stopped the play view
    looking like the tab.
    """
    ctx = AppContext.create(
        songs_dir=REAL_SONGS, settings_path=Path("/nonexistent/s.json")
    )
    for entry in ctx.library.playable:
        if entry.chart is None:
            continue
        assert entry.chart.note_count >= entry.chart.onset_count
        assert "collapsed" not in " ".join(entry.chart.warnings), (
            f"{entry.slug} was collapsed: {entry.chart.warnings}"
        )


# --- difficulty bands (§45) ----------------------------------------------------
#
# The bands were 2.0 / 4.5 / 7.0, which put the entire library in one 2.5-wide band:
# four genuinely different rock tabs, all labelled "Medium". These rates are the
# measured `onsets_per_second` of the four real tabs, recorded as numbers rather than
# read from `songs/` -- a test that depends on the player's disk is a test that fails
# when they import a song, and §37 already flagged that shape.
#: ``(song, measured onsets per second, the band it should land in)``.
REAL_LIBRARY: tuple[tuple[str, float, str], ...] = (
    ("Sweet Child O' Mine", 2.02, "Easy"),
    ("Hotel California", 2.91, "Medium"),
    ("Sweet Child O' Mine (Live)", 3.37, "Medium"),
    ("Afterlife", 3.90, "Hard"),
)


def test_the_bands_separate_the_real_library() -> None:
    """**The assertion that would have caught it.**

    A library where every song is "Medium" is not a library of similar songs; it is a
    band that is too wide to say anything. If the boundaries are ever widened again,
    this fails before anyone imports a tab and wonders whether the label is broken.
    """
    bands = {difficulty_band(rate) for _name, rate, _band in REAL_LIBRARY}
    assert len(bands) >= 3, (
        f"the real library spans {bands}, so the bands are not discriminating. "
        "A beginner riff and a modern metal track are not the same difficulty."
    )
    assert "?" not in bands, "a real tab banded as unknown"


@pytest.mark.parametrize(
    "name,rate,expected", REAL_LIBRARY, ids=[row[0] for row in REAL_LIBRARY]
)
def test_each_real_tab_lands_where_it_belongs(name: str, rate: float, expected: str) -> None:
    """Named, so a failure says which song moved and in which direction.

    The expectations are judgements about the songs -- Sweet Child O' Mine is the riff
    everyone learns first, Afterlife is not -- written down so that a boundary move
    has to be argued for rather than noticed.
    """
    assert difficulty_band(rate) == expected, (
        f"{name} at {rate} onsets/s banded as {difficulty_band(rate)}, expected {expected}"
    )


def test_no_real_tab_claims_expert() -> None:
    """Expert is left unclaimed, and that is deliberate.

    Nothing in the library is that fast, and a band nothing reaches is the same defect
    as a band everything lands in. It should start claiming songs as soon as some are
    actually shred-fast -- and this is where that will be noticed.
    """
    assert "Expert" not in {difficulty_band(r) for _n, r, _b in REAL_LIBRARY}


def test_the_band_boundaries_are_ordered_and_distinct() -> None:
    """A band that is not strictly increasing is a band that cannot be reasoned about."""
    bounds = (EASY_BELOW, MEDIUM_BELOW, HARD_BELOW)
    assert list(bounds) == sorted(bounds), bounds
    assert len(set(bounds)) == 3, "two boundaries share a value, so a band is empty"
    assert 0 < EASY_BELOW, "a tab with onsets would be banded below zero"


@pytest.mark.parametrize(
    "rate,expected",
    [
        (0.0, "?"),
        (-1.0, "?"),
        (0.01, "Easy"),
        (EASY_BELOW - 0.01, "Easy"),
        (EASY_BELOW, "Medium"),
        (MEDIUM_BELOW - 0.01, "Medium"),
        (MEDIUM_BELOW, "Hard"),
        (HARD_BELOW - 0.01, "Hard"),
        (HARD_BELOW, "Expert"),
        (99.0, "Expert"),
    ],
)
def test_the_boundaries_are_inclusive_on_the_lower_band(rate, expected) -> None:
    assert difficulty_band(rate) == expected


def test_the_band_is_a_pure_function_of_the_rate() -> None:
    """No chart, no entry, no disk -- which is what makes the table above possible."""
    from guitaroids.songlib import difficulty_band as direct

    for _name, rate, _band in REAL_LIBRARY:
        assert direct(rate) == difficulty_band(rate)
