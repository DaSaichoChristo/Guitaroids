"""Keeps requirements.txt an accurate inventory rather than a wish list.

The file is hand-maintained on purpose (§6.2): a `pip freeze` in disguise would
overwrite the one comment documenting the OpenCV workaround, and an aspirational
list would pin packages for features nobody has built. Both failure modes are
silent — nothing fails when a pin stops being imported, the list just starts
lying about what the app needs.

So the facts are pinned here:

  * every pin is `==` and parses as a real requirement line
  * no **transitive** is pinned without a stated reason -- one deliberate pin
    (`attrs`, a PyGuitarPro dependency) is legitimate, and several is a freeze
  * every pin is either imported by the code today or has a milestone named in
    the file's own comments -- nothing is pinned on principle alone
  * the pins match the lock file, so the curated list and the frozen snapshot
    cannot describe two different projects
  * the three install caveats (OpenCV ordering, tinysoundfont --no-deps,
    fetched assets) are still all present, because a silent deletion of one of
    them reintroduces §2.2
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
OPTIONAL = ROOT / "requirements-optional.txt"
DEV = ROOT / "requirements-dev.txt"
LOCK = ROOT / "requirements-lock.txt"

PIN = re.compile(r"^([A-Za-z0-9_.\-]+)==([^\s#]+)")

#: Distributions whose *module* name differs from the distribution name. Recorded
#: rather than worked around with a fuzzy match, because the mismatch is a real
#: fact about these packages and a reader of requirements.txt benefits from it.
MODULE_NAME = {
    "opencv-contrib-python-headless": "cv2",
    "pyguitarpro": "guitarpro",
    "py-side6": "PySide6",
    "tinysoundfont": "tinysoundfont",
}

#: Pinned because another pin needs it, with nothing importing it directly.
#: `shiboken6` is what turns PySide6's Python bindings into C++ calls, so PySide6
#: declares it; the project gets it transitively and pins it to say so.
COMPANION = {"shiboken6": "PySide6"}


def parse(path: Path) -> dict[str, str]:
    """The `name==version` pins in a requirements file, comments ignored."""
    found: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-r ")):
            continue
        match = PIN.match(stripped)
        if match:
            found[match.group(1).lower().replace("_", "-")] = match.group(2)
    return found


@pytest.fixture(scope="module")
def requirements() -> dict[str, str]:
    return parse(REQUIREMENTS)


@pytest.fixture(scope="module")
def lock() -> dict[str, str]:
    return parse(LOCK)


def _pin_lines(path: Path) -> list[str]:
    """The lines of a requirements file that actually declare something.

    Comments are excluded because two of this file's tests assert an *absence* --
    of OpenCV, of transitives -- and both files explain in prose what used to be
    there. A test that read the prose would forbid the explanation.
    """
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith(("#", "-r "))
    ]


def justified(name: str) -> bool:
    """Is there a written reason for pinning ``name``?

    Two things count, and they are the two legitimate reasons a curated list has
    for a pin: a ``§`` section reference, or -- for a transitive -- a note saying
    why it is pinned anyway.

    The search is over the *whole* file rather than the few lines above the pin,
    because the milestone notes live in a block well above the pins they describe
    (the audio pair is a dozen lines below its own heading). Scoping it to the
    neighbouring lines was the first version and it flagged two correctly-pinned
    packages, which is how a guard learns to be ignored.

    One definition, used by both tests below, because two implementations of
    "explained" is how they start disagreeing.
    """
    for line in REQUIREMENTS.read_text().splitlines():
        if name not in line:
            continue
        lowered = line.lower()
        if "§" in line or "transitive" in lowered or "dependency of" in lowered:
            return True
    return False


# --- shape ---------------------------------------------------------------------


def test_every_pin_is_exact(requirements: dict[str, str]) -> None:
    """A `>=` or a bare name makes the curated list a suggestion."""
    loose = [
        line.strip()
        for line in REQUIREMENTS.read_text().splitlines()
        if line.strip() and not line.strip().startswith(("#", "-r ")) and "==" not in line
    ]
    assert not loose, f"unpinned lines in requirements.txt: {loose}"


def test_the_file_is_not_empty(requirements: dict[str, str]) -> None:
    assert len(requirements) >= 5, "the curated list has lost its contents"


def test_the_install_caveats_are_still_there() -> None:
    """Each one, deleted, reintroduces a real failure.

    There are **two** now, not three. The OpenCV ordering caveat was the longest one
    in this file and it cost an afternoon; it existed only because mediapipe insists
    on the GUI build, and §25 removed mediapipe. Asserting it is still present would
    pin a step we deleted on purpose.
    """
    text = REQUIREMENTS.read_text()
    assert "--no-deps" in text, "tinysoundfont's install caveat is gone"
    assert "pyaudio" in text, "and the reason for it"
    assert "fetch_soundfont.sh" in text, (
        "a soundfont is fetched, not installed; say so"
    )


def test_the_opencv_caveat_stays_gone() -> None:
    """The absence is the assertion.

    The caveat lived in a comment, so a reader could be forgiven for keeping it "just
    in case". Keeping it would be worse than useless: it tells the next person that
    a bare `pip install -r requirements.txt` is dangerous, which is no longer true.
    """
    declared = _pin_lines(REQUIREMENTS)
    assert not any("opencv" in line.lower() for line in declared), (
        "OpenCV is a dependency again"
    )
    assert "opencv-contrib-python-headless" not in declared
    # ...and the historical note stays, so nobody re-adds it without reading why.
    assert "OpenCV" in REQUIREMENTS.read_text(), (
        "the file should still say the trap existed and why it is gone"
    )


# --- no transitives -------------------------------------------------------------


#: Dependencies of our dependencies. Listing one here is not wrong in itself -- a
#: deliberate pin with a stated reason is defensible, and `attrs` is one -- it is
#: wrong when it turns up with nothing said about it, which is how a curated list
#: becomes a freeze one package at a time.
TRANSITIVE = {"attrs", "matplotlib", "pillow", "contourpy", "cycler", "fonttools",
              "kiwisolver", "pyparsing", "absl-py", "flatbuffers", "certifi", "cffi",
              "six", "packaging", "pluggy", "iniconfig", "typing-extensions",
              "python-dateutil", "pyside6-addons", "pyside6-essentials", "pycparser"}


def test_no_transitive_is_pinned_without_saying_why(requirements: dict[str, str]) -> None:
    """The lock file exists for these, so a pin here must justify itself.

    This replaced a hard ban on `attrs`, which was the wrong shape: pinning a
    transitive on purpose is a legitimate call, and a test that forbids the
    package instead of the *silence* cannot be satisfied honestly. The test now
    asks the useful question -- is the reason written down next to the pin?
    """
    unjustified = [
        name for name in set(requirements) & TRANSITIVE if not justified(name)
    ]
    assert not unjustified, (
        f"{unjustified} are transitive and pinned with no reason next to them; "
        "add one or drop the pin"
    )


def test_the_transitive_pins_are_few(requirements: dict[str, str]) -> None:
    """One is a decision. Five is a freeze that has not admitted it yet."""
    assert len(set(requirements) & TRANSITIVE) <= 2, (
        "more than two transitive pins: at this point requirements.txt is a lock "
        "file with comments, and requirements-lock.txt already exists"
    )


# --- every pin is justified -----------------------------------------------------


def test_every_pin_is_imported_or_has_a_milestone(requirements: dict[str, str]) -> None:
    """A pin with neither a use nor a plan is a version nobody chose.

    The check is deliberately weak in one direction: a package whose *only* mention
    is in a comment passes if that comment is a milestone heading or a
    section reference, which is the weakest thing that still counts as a reason.
    """
    sources = "\n".join(
        path.read_text()
        for path in ROOT.rglob("*.py")
        if ".venv" not in path.parts
    )
    unjustified = []
    for name in requirements:
        needed_by = COMPANION.get(name)
        if needed_by and any(
            re.fullmatch(re.escape(needed_by), other, re.I) for other in requirements
        ):
            continue
        # Case-insensitive, because a distribution name and a module name differ:
        # `PyGuitarPro` in the file, `import guitarpro` in the code, and the pin
        # compares lowercased against the lock.
        module = re.escape(MODULE_NAME.get(name, name))
        imported = re.search(rf"^\s*(?:import|from)\s+{module}\b", sources, re.M | re.I)
        if imported or justified(name):
            continue
        unjustified.append(name)
    assert not unjustified, (
        f"{unjustified} are pinned but neither imported nor explained; "
        "add a milestone reference or drop the pin"
    )


def test_every_module_name_alias_is_real() -> None:
    """The alias table is documentation, so it has to be true.

    A distribution renamed or replaced, and the map goes stale -- and then
    `test_every_pin_is_imported_or_has_a_milestone` starts passing for the wrong
    reason, which is the failure mode this whole file exists to prevent.
    """
    sources = "\n".join(
        path.read_text() for path in ROOT.rglob("*.py") if ".venv" not in path.parts
    )
    for distribution, module in MODULE_NAME.items():
        if distribution not in parse(REQUIREMENTS):
            continue
        assert re.search(
            rf"^\s*(?:import|from)\s+{re.escape(module)}\b", sources, re.M
        ), f"{distribution} is pinned as {module}, but nothing imports {module}"


def test_the_audio_pins_are_marked_as_unbuilt(requirements: dict[str, str]) -> None:
    """`sounddevice` and `soundfile` are for §1.5, and nothing plays audio yet.

    Recorded because "the dependency is there" and "the feature is there" are
    different claims, and only one of them is true.
    """
    text = REQUIREMENTS.read_text()
    for name in ("sounddevice", "soundfile"):
        if name in requirements:
            assert f"audio milestone" in text, "the audio pins lost their milestone note"


# --- the curated list and the lock agree ----------------------------------------


def test_the_pins_are_all_in_the_lock(requirements: dict[str, str], lock: dict[str, str]) -> None:
    """Otherwise the curated list and the frozen snapshot describe two projects."""
    missing = sorted(set(requirements) - set(lock))
    assert not missing, f"pinned here but absent from the lock: {missing}"


def test_every_pin_matches_the_lock(requirements: dict[str, str], lock: dict[str, str]) -> None:
    wrong = {
        name: (version, lock[name])
        for name, version in requirements.items()
        if name in lock and lock[name] != version
    }
    assert not wrong, f"version drift between requirements.txt and the lock: {wrong}"


def test_the_optional_and_dev_files_are_small_and_deliberate() -> None:
    """One package each, both explained, both installed differently."""
    assert set(parse(OPTIONAL)) == {"tinysoundfont"}
    assert set(parse(DEV)) == {"pytest"}
    assert "no-deps" in OPTIONAL.read_text()
    assert "requirements.txt" in DEV.read_text(), "dev installs the runtime too"


def test_the_obsolete_bare_install_warning_is_gone() -> None:
    """**The absence is the assertion**, and it is the whole point of this update.

    A bare `pip install -r requirements.txt` now installs everything and the app
    runs: verified in a clean venv, where the modules import and a window opens. The
    file used to say it could not, which was true once and stopped being true when
    mediapipe went (§25).

    Leaving the warning in place is worse than leaving it out. People would avoid a
    path that works, and the next person to hit a real install problem would discount
    everything else in the file too.
    """
    text = REQUIREMENTS.read_text()
    flat = re.sub(r"\s+", " ", text)
    assert "ONLY supported install path" not in flat
    assert "is not equivalent" not in flat, "the old claim is still in here"
    assert "A BARE INSTALL NOW WORKS" in text, "and the replacement is not"


def test_the_one_install_caveat_that_remains_is_still_stated() -> None:
    """Softer claims do not mean no claims.

    tinysoundfont genuinely cannot be installed normally, and the soundfont genuinely
    is not a package. Dropping the caveats with the obsolete one would lose two facts
    that are still true.
    """
    text = REQUIREMENTS.read_text()
    assert "pyaudio" in text and "--no-deps" in text
    assert "fetch_soundfont.sh" in text
    assert "portaudio.h" in text, "the reason it cannot be built is worth keeping"


def test_the_synth_fallback_is_named_after_what_it_is() -> None:
    """"pluck", not "Karplus-Strong".

    The fallback is additive synthesis with a plucked envelope, not a per-sample KS
    recurrence, because KS renders a five-minute chart in minutes and a fallback
    slower than the thing it stands in for is not a fallback (§24). Naming it
    Karplus-Strong in a requirements file would be the first place a reader meets a
    synth that does not exist.
    """
    assert "Karplus-Strong" not in REQUIREMENTS.read_text()
    assert "pluck" in REQUIREMENTS.read_text()
