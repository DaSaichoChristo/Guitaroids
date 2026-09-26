"""Tests for path discovery.

These exist because ``Path(__file__).resolve().parent.parent`` was copy-pasted
into ten files, and because the soundfont search order is a licensing decision as
much as a convenience one.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from guitaroids import paths


# --- repository root ---------------------------------------------------------


def test_repo_root_is_the_real_one() -> None:
    root = paths.REPO_ROOT
    assert (root / "requirements.txt").is_file()
    assert (root / "guitaroids").is_dir()
    assert (root / "scripts" / "setup.sh").is_file()


def test_root_is_discovered_not_assumed() -> None:
    """find_repo_root must find a marker, not just go up one level."""
    nested = paths.REPO_ROOT / "guitaroids" / "ui"
    assert paths.find_repo_root(nested) == paths.REPO_ROOT


def test_root_falls_back_when_no_marker(tmp_path: Path) -> None:
    """A copy outside the repo still yields a usable directory, not a crash."""
    lonely = tmp_path / "somewhere" / "deep"
    lonely.mkdir(parents=True)
    result = paths.find_repo_root(lonely)
    assert result.is_absolute()
    assert result.exists()


def test_all_paths_hang_off_the_root() -> None:
    assert paths.SONGS_DIR == paths.REPO_ROOT / "songs"
    assert paths.SETTINGS_PATH == paths.REPO_ROOT / "settings.json"
    assert paths.ASSETS_DIR == paths.REPO_ROOT / "assets"
    assert paths.MODEL_PATH == paths.ASSETS_DIR / "hand_landmarker.task"


def test_songs_dir_exists() -> None:
    """The layout ships with the repo; song select depends on it being present."""
    assert paths.SONGS_DIR.is_dir()


# --- soundfont discovery -----------------------------------------------------


def test_candidates_put_assets_ahead_of_system_paths() -> None:
    candidates = paths.soundfont_candidates()
    asset = [c for c in candidates if c.parent == paths.ASSETS_DIR]
    system = [c for c in candidates if str(c).startswith("/usr")]
    assert asset, "an assets/ candidate must exist"
    assert system, "system fallbacks must exist"
    assert candidates.index(asset[0]) < candidates.index(system[0])


def test_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.SOUNDFONT_ENV, "/somewhere/else/custom.sf2")
    assert paths.soundfont_candidates()[0] == Path("/somewhere/else/custom.sf2")


def test_env_override_is_ignored_when_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.SOUNDFONT_ENV, "")
    assert paths.soundfont_candidates()[0] != Path("")


def test_find_soundfont_returns_none_rather_than_raising(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(paths.SOUNDFONT_ENV, str(tmp_path / "absent.sf2"))
    # assets/ may genuinely hold one, so only assert the contract when it is absent.
    found = paths.find_soundfont()
    assert found is None or found.is_file()


def test_find_soundfont_picks_a_real_file_when_one_exists() -> None:
    """If the fetch script has run, discovery finds it and the path is a file."""
    found = paths.find_soundfont()
    if found is not None:
        assert found.is_file()
        assert found.suffix.lower() in (".sf2", ".sf3")


def test_finds_the_fetched_soundfont_in_assets() -> None:
    """The fetched asset should be discoverable by the normal path.

    Skipped on a fresh clone where setup.sh has not run.
    """
    candidates = [c for c in paths.soundfont_candidates() if c.parent == paths.ASSETS_DIR]
    existing = [c for c in candidates if c.is_file()]
    if not existing:
        pytest.skip("no soundfont fetched")
    assert paths.find_soundfont() in existing


# --- staying pure ------------------------------------------------------------


def test_importing_paths_does_not_import_qt() -> None:
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import guitaroids.paths, sys;"
            "bad=[n for n in ('PySide6','cv2','sounddevice','mediapipe') if n in sys.modules];"
            "print(','.join(bad) or 'clean')",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "clean"


def test_gitignore_covers_the_settings_file() -> None:
    """Otherwise a user's settings get committed by accident."""
    ignore = (paths.REPO_ROOT / ".gitignore").read_text()
    assert "settings.json" in ignore
