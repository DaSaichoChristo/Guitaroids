"""Keeps `scripts/setup.sh` honest about what it installs.

This file used to be about **two** scripts. `scripts/setup.ps1` was a hand-maintained
PowerShell duplicate of the same install procedure -- the worst-case arrangement, since
nothing fails when one is updated and the other is not, and the install quietly does
the wrong thing on one platform. The agreement was pinned by eleven tests here, and
§31 made that pinning necessary rather than fussy: `requirements.txt` became one
`pip freeze` file, so both scripts had to learn to filter tinysoundfont out of the bulk
install and install it separately with `--no-deps`.

Then `setup.ps1` was deleted, and so was the reason for those eleven tests. There is
no second script to drift from, so there is nothing left to compare against -- and a
test that compares a file to itself is a test that cannot fail, which §25.4 calls out by
name.

What replaces them is the set of things the one script can be held to on its own:
that it does the tinysoundfont dance in the right order, that it says the things a
reader needs before running it, and that its claims about the environment are the ones
§31 verified. The pins it used to keep in step with the PowerShell copy are now
ordinary assertions about `setup.sh`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SH = ROOT / "scripts" / "setup.sh"


@pytest.fixture()
def sh() -> str:
    assert SH.is_file(), "scripts/setup.sh is the install path; it must exist"
    return SH.read_text()


# --- what it installs ----------------------------------------------------------


def test_it_installs_the_requirements_file(sh: str) -> None:
    assert "requirements.txt" in sh, "the install reads nothing at all"


def test_it_filters_tinysoundfont_out_of_the_bulk_install(sh: str) -> None:
    """Installing the whole list first would fail on pyaudio and never reach step two.

    `requirements.txt` is `pip freeze` output, so tinysoundfont is in it, and it cannot
    be installed normally. So the order is load-bearing: bulk install with the package
    excluded, then the package itself with `--no-deps`.
    """
    exclude = re.search(r"grep -v '\^tinysoundfont==' requirements\.txt", sh)
    bulk = sh.index("pip install -r") if "pip install -r" in sh else -1
    alone = sh.index("install tinysoundfont==0.3.7 --no-deps")
    assert exclude, "the bulk install does not exclude tinysoundfont"
    assert exclude.start() < alone, (
        "the bulk install must come after the exclusion, or it fails on pyaudio before "
        "reaching the --no-deps step"
    )
    assert "--no-deps" in sh[alone : alone + 80], "and the separate step needs --no-deps"


def test_the_bulk_install_temp_file_is_cleaned_up(sh: str) -> None:
    """A mktemp with no trap leaves a file behind on every run of a setup script."""
    assert "mktemp" in sh
    assert re.search(r"trap .*rm -f", sh), "the temp file is never removed"


def test_it_fetches_a_soundfont_but_survives_without_one(sh: str) -> None:
    assert "fetch_soundfont.sh" in sh
    line = next(ln for ln in sh.splitlines() if "fetch_soundfont.sh" in ln and "||" in ln)
    assert "||" in line, "a soundfont fetch failure must not abort the whole script"


def test_it_runs_the_m0_gate(sh: str) -> None:
    """The gate is the one thing that proves the install worked, so it runs last."""
    assert "test_m0_window.py" in sh


def test_no_opencv_step_remains(sh: str) -> None:
    """§25 removed the webcam, and with it the install-order trap the step existed for.

    The trap was real: mediapipe pulled the GUI build, whose Qt plugins break PySide6,
    and both builds write the same `cv2/` directory. Nothing pulls OpenCV now, so a
    step that uninstalls it would be cargo cult.
    """
    body = "\n".join(
        line for line in sh.splitlines() if not line.strip().startswith("#")
    )
    for gone in ("opencv", "cv2", "mediapipe"):
        assert gone not in body.lower(), f"the script still has a {gone} step"


# --- what it tells you ---------------------------------------------------------


def test_it_says_which_interpreter_is_known_good(sh: str) -> None:
    """3.12 is a real constraint -- cp310/cp312 wheels only -- and 3.13/3.14 are not.

    A setup script that installs the wrong version and then fails on a build is a bad
    first experience, so the version check is asserted rather than assumed.
    """
    assert "PYVER" in sh and "3.12" in sh
    # 3.14 is named explicitly (no wheel at all) and every other unsupported version
    # falls through to the generic warning below it. So the assertion is the *guard*,
    # not a list of versions -- a script that only warned about 3.14 would be wrong for
    # 3.13, which has no wheel either.
    assert re.search(r'if \[ "\$PYVER" != "3\.12" \]', sh), (
        "an unlisted version is not warned about at all"
    )
    assert "3.14" in sh, "and 3.14 is not named, though it is the worst case"
    assert "C++ toolchain" in sh, "the script should say what an unsupported version costs"


def test_it_warns_when_the_soundfont_is_missing(sh: str) -> None:
    assert "pluck" in sh, "the fallback synth is not mentioned as the failure path"


def test_it_does_not_claim_a_bare_pip_install_works(sh: str) -> None:
    """§31 flipped this. The script IS the install path, and it must not imply otherwise."""
    flat = re.sub(r"\s+", " ", sh)
    assert "ONLY supported install path" not in flat
    assert "is not equivalent" not in flat
    assert "FAILS" in flat or "fails" in flat, (
        "the script does not say why a bare pip install does not work"
    )


# --- the one that reads requirements.txt ---------------------------------------


def test_the_bare_install_claim_is_verified_not_asserted() -> None:
    """The claim is that installing the file with pip FAILS. That needs evidence.

    It cannot be checked here -- it needs a venv and a network, and a test that creates
    one would be slow and flaky -- so what is pinned is that the claim is *recorded*,
    with how it was checked, so a reader can repeat it and nobody quietly deletes the
    evidence. §27's version of this test asserted the opposite claim, so it would have
    passed straight over the change that made it wrong.
    """
    text = (ROOT / "requirements.txt").read_text()
    flat = re.sub(r"\s+", " ", text)
    assert "FAILS" in flat, "the file does not say that installing it with pip fails"
    # Flattened, because the evidence is quoted across comment lines and a reader sees
    # it as one sentence.
    assert "Failed building wheel for pyaudio" in flat, "say what the failure was"
    assert "clean venv" in flat, "say how it was checked"
    assert "dry-run" in flat, (
        "and say that a dry run is not evidence, or the next person will use one"
    )


# --- what is gone --------------------------------------------------------------


def test_there_is_only_one_setup_script() -> None:
    """The PowerShell copy is deleted, and nothing has quietly replaced it.

    Recorded as a test because the deletion is a decision: §31 had just changed both
    scripts, and `setup.ps1` had still never been run on a machine that has PowerShell.
    A test that cannot fail is worse than no test (§25.4), so there is no point
    comparing one script to a second one that is not there.
    """
    assert not (ROOT / "scripts" / "setup.ps1").exists(), (
        "setup.ps1 was deleted. If a Windows install path is wanted again, it needs a "
        "machine with PowerShell to be verified on -- §31's version of it never had one."
    )
