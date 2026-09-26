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

from guitaroids.songlib import (
    AUDIO_EXTENSIONS,
    Status,
    classify_error,
    find_audio,
    format_duration,
    load_tab,
    scan_library,
)
from guitaroids.model.chart import ChartError

ROOT = Path(__file__).resolve().parent.parent


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
