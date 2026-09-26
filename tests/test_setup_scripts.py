"""Keeps scripts/setup.sh and scripts/setup.ps1 from drifting apart.

They are hand-maintained duplicates of the same install procedure, which is the
worst-case arrangement: nothing fails when one is updated and the other is not,
the install just quietly does the wrong thing on one platform.

So the shared facts are pinned here rather than trusted:

  * the headless OpenCV version both scripts install
  * the model and soundfont URLs both scripts fetch
  * that both install requirements-optional.txt with --no-deps
  * that both remove BOTH OpenCV builds before installing headless (the order
    bug in DESIGN.md §7.2 -- removing only the GUI build leaves a half-deleted
    cv2/ directory that pip believes is installed)
  * that each mentions the other
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "scripts" / "setup.sh"
PS1 = ROOT / "scripts" / "setup.ps1"

pytestmark = pytest.mark.skipif(
    not SH.is_file() or not PS1.is_file(), reason="both setup scripts must exist"
)

MODEL_URL = "storage.googleapis.com/mediapipe-models/hand_landmarker"
SOUNDFONT_URL = "fluidr3mono-gm-soundfont"


@pytest.fixture(scope="module")
def sh() -> str:
    return SH.read_text()


@pytest.fixture(scope="module")
def ps1() -> str:
    return PS1.read_text()


def test_both_scripts_mention_each_other(sh: str, ps1: str) -> None:
    assert "setup.ps1" in sh
    assert "setup.sh" in ps1


def test_both_install_the_same_headless_opencv_version(sh: str, ps1: str) -> None:
    def version(text: str) -> set[str]:
        return set(re.findall(r"opencv-contrib-python-headless==([0-9][0-9.]*)", text))

    assert version(sh), "setup.sh does not pin a headless OpenCV version"
    assert version(sh) == version(ps1), "the two scripts pin different OpenCV versions"


def test_both_use_requirements_optional_with_no_deps(sh: str, ps1: str) -> None:
    for text in (sh, ps1):
        assert "requirements-optional.txt" in text
        assert "--no-deps" in text, "the --no-deps install is mandatory for tinysoundfont"


def test_both_fetch_the_same_model_and_soundfont(ps1: str) -> None:
    """The .ps1 inlines the URLs; setup.sh delegates to the fetch scripts.

    The fetch scripts are the source of truth, and the .ps1 is compared against
    them so a URL change in one place cannot silently miss the other.
    """
    model = (ROOT / "scripts" / "fetch_model.sh").read_text()
    soundfont = (ROOT / "scripts" / "fetch_soundfont.sh").read_text()

    def url_of(text: str, marker: str) -> str:
        match = re.search(rf'["\']?(https?://[^"\'\s]*{marker}[^"\'\s]*)', text)
        assert match, f"no {marker} URL found"
        return match.group(1)

    assert url_of(model, "hand_landmarker") in ps1
    assert url_of(soundfont, "fluidr3mono") in ps1


def test_both_remove_both_opencv_builds(sh: str, ps1: str) -> None:
    """Regression guard for the §7.2 ordering bug.

    Uninstalling only the GUI build deletes the shared cv2/ files while leaving
    headless's dist-info, and pip then reports "already satisfied" and restores
    nothing. Both names must appear in the same uninstall, which is why this
    allows arbitrary whitespace between them.
    """
    pattern = r"uninstall[\s\S]{0,120}?opencv-contrib-python['\"\s,\]]+[\s\S]{0,60}?opencv-contrib-python-headless"
    for name, text in (("setup.sh", sh), ("setup.ps1", ps1)):
        assert re.search(pattern, text), (
            f"{name}: the headless build must be uninstalled in the same call, "
            "or its files are left deleted while pip thinks it is installed"
        )


def test_both_run_the_m0_gate_last(sh: str, ps1: str) -> None:
    for text in (sh, ps1):
        assert "test_m0_window.py" in text


def test_both_warn_about_the_pyaudio_trap(sh: str, ps1: str) -> None:
    """pyaudio is unbuildable here, and --dry-run reports false success."""
    for text in (sh, ps1):
        assert "pyaudio" in text.lower()
    # The dry-run trap is bash-specific advice, so only the shell script must
    # mention it -- but the .ps1 must still explain the --no-deps requirement.
    assert "dry-run" in sh


def test_both_offer_the_numpy_fallback(sh: str, ps1: str) -> None:
    """The soundfont step is allowed to fail; audio must still work."""
    for text in (sh, ps1):
        assert "Karplus-Strong" in text


def test_both_warn_about_the_bare_pip_install(sh: str, ps1: str) -> None:
    for name, text in (("setup.sh", sh), ("setup.ps1", ps1)):
        assert "requirements.txt" in text, f"{name} does not reference requirements.txt"
        # Collapse whitespace: these are wrapped prose files, so the phrase can be
        # split across a line break and an indent.
        flat = re.sub(r"\s+", " ", text)
        assert re.search(r"only supported install path", flat, re.I), (
            f"{name} does not say it is the only supported install path"
        )


def test_powershell_reports_native_exit_codes(ps1: str) -> None:
    """$ErrorActionPreference does not trap a non-zero exit from a native exe.

    Without an explicit $LASTEXITCODE check, every pip failure in setup.ps1 would
    pass silently and the script would report success having installed nothing.
    """
    assert "LASTEXITCODE" in ps1
    assert re.search(r"function\s+Invoke-Native", ps1)
    # pip *install* and *uninstall* calls must go through the wrapper. `pip show`
    # is exempt: it is a probe whose exit code is the point, and it is guarded.
    bare_pip = [
        line for line in ps1.splitlines()
        if re.search(r"(?<!&)\$Py\s+-m\s+pip\s+(install|uninstall)", line)
    ]
    assert not bare_pip, f"pip invoked without exit-code checking: {bare_pip}"
    assert re.search(r"pip show.*\n.*LASTEXITCODE", ps1, re.S), (
        "the pip show probe must have its exit code checked"
    )


def test_powershell_handles_windows_venv_layout(ps1: str) -> None:
    """Windows puts the interpreter in .venv/Scripts, not .venv/bin."""
    assert "Scripts" in ps1
