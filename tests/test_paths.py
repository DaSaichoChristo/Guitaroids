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
    # MODEL_PATH went with the mediapipe hand model (§25). Asserted as an absence so
    # a path constant for a file nothing fetches cannot creep back in.
    assert not hasattr(paths, "MODEL_PATH"), (
        "MODEL_PATH is the mediapipe model, which is no longer fetched"
    )


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


# --- one policy, and the one that runs ---------------------------------------


def test_the_renderer_uses_this_policy_and_not_a_private_copy() -> None:
    """There were two soundfont searches, and only one of them was tested.

    ``render.py`` had its own ``find_soundfont`` that overrode this one, so the
    renderer never saw a system soundfont -- a user with one installed got the
    pluck synth without being told, while every test in this file passed.

    Asserting the *identity* is the point: a second copy cannot drift back in
    without this failing, which a behavioural test could not catch.
    """
    from guitaroids.audio import render

    assert render.find_soundfont is paths.find_soundfont


def test_the_renderer_module_defines_no_soundfont_search_of_its_own() -> None:
    """An absence test against the source, the way §25.4 insists.

    The identity check above catches a *different function* being used. This
    catches the same function being written a second time -- which is how the
    duplicate arrived, and which would be invisible to any behavioural test.
    """
    source = (paths.REPO_ROOT / "guitaroids" / "audio" / "render.py").read_text()
    assert "def find_soundfont" not in source
    assert "SOUNDFONT_ENV" not in source, "the override is paths' business too"


def test_a_tilde_in_the_override_is_expanded(monkeypatch: pytest.MonkeyPatch) -> None:
    """``$GUITAROIDS_SOUNDFONT=~/guitar.sf2`` is what a person actually types.

    The private copy in ``render.py`` compared the unexpanded string with
    ``is_file()``, so a tilde silently found nothing.
    """
    monkeypatch.setenv(paths.SOUNDFONT_ENV, "~/guitar.sf2")
    assert paths.soundfont_candidates()[0] == Path.home() / "guitar.sf2"


def test_an_override_pointing_nowhere_falls_through_instead_of_giving_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """A bad override should cost you the override, not the soundfont.

    The private copy returned ``None`` the moment the override failed to resolve,
    so a typo in the environment variable silently downgraded the whole app to the
    pluck synth. Here the search carries on to the next candidate.
    """
    monkeypatch.setenv(paths.SOUNDFONT_ENV, str(tmp_path / "typo.sf2"))
    found = paths.find_soundfont()
    assert found != tmp_path / "typo.sf2"
    assert found is None or found.is_file()


def test_the_fluid_r3_filename_is_still_searched() -> None:
    """Moving render's name into this list must not lose it.

    ``Guitarramelodica.sf2`` is what the FluidR3 download is called, and the
    private copy was the only thing that knew that.
    """
    names = [c.name for c in paths.soundfont_candidates() if c.parent == paths.ASSETS_DIR]
    assert "Guitarramelodica.sf2" in names
    assert "soundfont.sf3" in names


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
