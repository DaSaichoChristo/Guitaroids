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


def executable_lines(text: str) -> str:
    """A script with its comments and doc-comments removed.

    The absence assertions below have to look at what a script *does*, and both
    scripts explain in prose why they no longer do the OpenCV dance. Stripping the
    comments is what lets a script say "we used to" without the test reading it as
    "we still do".

    ``#`` starts a comment in both bash and PowerShell, and PowerShell block
    comments are ``<# ... #>``.
    """
    without_blocks = re.sub(r"<#[\s\S]*?#>", "", text)
    lines = [
        line for line in without_blocks.splitlines()
        if not line.lstrip().startswith("#")
    ]
    return "\n".join(lines)


def test_both_use_requirements_optional_with_no_deps(sh: str, ps1: str) -> None:
    for text in (sh, ps1):
        assert "requirements-optional.txt" in text
        assert "--no-deps" in text, "the --no-deps install is mandatory for tinysoundfont"


def test_both_fetch_the_same_soundfont(ps1: str) -> None:
    """The .ps1 inlines the URL; setup.sh delegates to the fetch script.

    The fetch script is the source of truth, and the .ps1 is compared against it so
    a URL change in one place cannot silently miss the other.
    """
    soundfont = (ROOT / "scripts" / "fetch_soundfont.sh").read_text()

    def url_of(text: str, marker: str) -> str:
        match = re.search(rf'["\']?(https?://[^"\'\s]*{marker}[^"\'\s]*)', text)
        assert match, f"no {marker} URL found"
        return match.group(1)

    assert url_of(soundfont, "fluidr3mono") in ps1


def test_neither_script_mentions_opencv_or_mediapipe(sh: str, ps1: str) -> None:
    """**The absence is the assertion**, and it is the point of §25.

    Both scripts used to uninstall the GUI OpenCV build and install the headless one
    in a strict order, because both write to the same cv2/ directory and mediapipe
    insists on the GUI build. Dropping mediapipe dropped the whole dance -- the trap
    that cost an afternoon, the third install caveat, and the reason this file existed.

    If OpenCV comes back, it comes back with an ordering requirement, and these
    scripts have to grow it again. Until then, a mention of either package means
    something was re-added without thinking about the install.
    """
    for name, text in (("setup.sh", sh), ("setup.ps1", ps1)):
        code = executable_lines(text)
        for package in ("opencv", "mediapipe", "hand_landmarker", "fetch_model"):
            assert package not in code.lower(), (
                f"{name} still has a {package} step outside its comments"
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
    """The soundfont step is allowed to fail, so something has to take over.

    Named "pluck" rather than Karplus-Strong: the fallback really is additive
    synthesis, because a per-sample KS recurrence renders a five-minute chart in
    minutes and a fallback slower than the thing it stands in for is not a fallback
    (DESIGN.md §24). The name is asserted so the two scripts cannot drift apart
    about which synth is meant.
    """
    for name, text in (("setup.sh", sh), ("setup.ps1", ps1)):
        assert "pluck synth" in text, f"{name} does not mention the numpy fallback"


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
    # The pip show probe was the OpenCV version check, and OpenCV is gone (§25), so
    # there is no probe left to guard. Asserting it would pin a step we deleted on
    # purpose; the bare-pip assertion above still covers every install and uninstall.


def test_powershell_handles_windows_venv_layout(ps1: str) -> None:
    """Windows puts the interpreter in .venv/Scripts, not .venv/bin."""
    assert "Scripts" in ps1
