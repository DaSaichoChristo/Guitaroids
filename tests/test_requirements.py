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

#: There used to be four files: a curated list, a lock, an optional set and a dev set.
#: There is one now -- `pip freeze` -- because knowing every dependency in one place is
#: worth more than the separation was. What that costs is this file's other job: the
#: curated list could say *why* a pin was there, and a freeze cannot. So the tests below
#: assert the properties a generated file can actually have (it matches the venv, it
#: carries no stale claims, the install caveat is stated) and the ones that needed a
#: curated file are gone rather than rewritten to pass.
LOCK = REQUIREMENTS

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
        "OpenCV is a dependency again. It was pulled in by mediapipe, which was "
        "removed in §25: the input is a microphone, not a webcam."
    )
    # The *history* of that caveat used to be asserted here, so nobody re-added the
    # package without reading why. It is not asserted any more, because this is a
    # generated file and a comment above `pip freeze` output is a lie waiting to
    # happen. The history lives in DESIGN.md §25 and the absence in test_m0_window.py.


# --- no transitives -------------------------------------------------------------


#: Dependencies of our dependencies. Listing one here is not wrong in itself -- a
#: deliberate pin with a stated reason is defensible, and `attrs` is one -- it is
#: wrong when it turns up with nothing said about it, which is how a curated list
#: becomes a freeze one package at a time.
TRANSITIVE = {"attrs", "matplotlib", "pillow", "contourpy", "cycler", "fonttools",
              "kiwisolver", "pyparsing", "absl-py", "flatbuffers", "certifi", "cffi",
              "six", "packaging", "pluggy", "iniconfig", "typing-extensions",
              "python-dateutil", "pyside6-addons", "pyside6-essentials", "pycparser"}


# --- a generated file's invariants, which are fewer ---------------------------
#
# Four tests that used to live here are gone rather than rewritten, and the loss is
# real. A curated list could demand that every pin justify itself, that no transitive
# be pinned without a reason, that at most two be, and that every pin be imported or
# carry a named milestone. `pip freeze` satisfies none of those -- it lists nine
# transitives and a test framework -- so the honest options were to keep tests that
# cannot pass, or to drop them. They were dropped, and this is the note saying so.


def test_it_matches_the_venv_it_was_generated_from() -> None:
    """The one property a generated file can be held to, and the one that matters.

    `requirements.txt` is `pip freeze` output, so its whole value is describing the
    environment. The moment it stops matching, it is a stale list that looks current --
    which is what §21.2 is about, one level up from code.
    """
    import subprocess
    import sys

    def canonical(name: str) -> str:
        """PEP 503: `PySide6-Addons`, `pyside6_addons` and `pyside6.addons` are one.

        `pip freeze` writes underscores and the file was written with hyphens, so a
        plain lowercase comparison reports three phantom missing packages. That is not a
        detail: it is why the first version of this test failed on a file that matched
        perfectly.
        """
        return re.sub(r"[-_.]+", "-", name).lower()

    out = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        capture_output=True,
        text=True,
        check=True,
    )
    frozen = {}
    for line in out.stdout.splitlines():
        if "==" in line:
            name, version = line.split("==", 1)
            frozen[canonical(name)] = version.strip()

    recorded = {canonical(k): v for k, v in parse(REQUIREMENTS).items()}
    assert recorded, "no pins parsed at all"

    missing = sorted(set(frozen) - set(recorded))
    extra = sorted(set(recorded) - set(frozen))
    wrong = {
        name: (recorded[name], frozen[name])
        for name in set(recorded) & set(frozen)
        if recorded[name] != frozen[name]
    }
    assert not missing, f"in the venv but not in requirements.txt: {missing}"
    assert not extra, f"in requirements.txt but not the venv: {extra}"
    assert not wrong, f"version drift from the venv: {wrong}"


def test_the_file_says_pip_install_cannot_work_here() -> None:
    """The one fact that makes this file an inventory rather than an install spec.

    `tinysoundfont` depends on `pyaudio`, which has no Linux wheel and cannot be built
    without `portaudio.h`. **Verified**: a clean venv plus `pip install
    tinysoundfont==0.3.7` exits with "Failed building wheel for pyaudio". So a bare
    `pip install -r requirements.txt` fails, and a file that lists 18 pins without
    saying so is a trap for whoever tries it next.

    `pip install --dry-run` reports success on this package, so the check cannot be
    automated as an install -- which is why the claim is written into the file and
    asserted here rather than proven on every run.
    """
    text = REQUIREMENTS.read_text()
    assert "pyaudio" in text
    assert "portaudio.h" in text
    assert "--no-deps" in text
    assert "FAILS" in text or "fails" in text, (
        "the file does not say that installing it with pip does not work"
    )


def test_the_pins_that_matter_are_all_there() -> None:
    """A freeze could lose a package and still look like a tidy list of eighteen.

    These are the ones the app actually imports, plus the one that cannot be installed
    normally. If any is missing the file no longer answers the question it exists for.
    """
    pins = {k.lower() for k in parse(REQUIREMENTS)}
    for required in (
        "pyside6", "shiboken6", "numpy", "soundfile", "sounddevice",
        "pyguitarpro", "tinysoundfont",
    ):
        assert required in pins, f"{required} is missing from requirements.txt"


def test_the_python_constraint_is_stated() -> None:
    """3.12 is a real constraint -- cp310/cp312 wheels only -- and a freeze hides it.

    Nothing in a list of 18 pins says which interpreter it works on, so the fact has to
    live in the header or a newcomer on 3.13 discovers it by failing to build.
    """
    text = REQUIREMENTS.read_text()
    assert "3.12" in text
    assert "cp310" in text and "cp312" in text


def test_the_obsolete_bare_install_warning_is_gone() -> None:
    """**The absence is the assertion.**

    The file used to say a bare `pip install` was "NOT equivalent" and named
    `scripts/setup.sh` the only supported path -- which was true when mediapipe pulled in
    the GUI OpenCV build (§25) and stopped being true when mediapipe went. Leaving a
    warning in place is worse than leaving it out: people avoid a path that works, and
    the next person to hit a real problem discounts everything else in the file too.
    """
    text = REQUIREMENTS.read_text()
    flat = re.sub(r"\s+", " ", text)
    assert "ONLY supported install path" not in flat
    assert "is not equivalent" not in flat, "the old claim is still in here"
    assert "INVENTORY" in text, "and it does not say what the file now is"


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
    slower than the thing it stands in for is not a fallback (§23.1, not §24 — §24
    is the microphone and implements no synth). Naming it Karplus-Strong would be the
    first place a reader meets a synth that does not exist.
    """
    assert "Karplus-Strong" not in REQUIREMENTS.read_text()
    assert "pluck" in REQUIREMENTS.read_text()


def test_every_file_that_names_the_fallback_names_it_the_same_way() -> None:
    """§27.4 said "all three" and there were four.

    That commit fixed `requirements.txt`, `requirements-optional.txt` and `paths.py`
    and then wrote down that it had fixed all of them -- while
    `assets/ATTRIBUTION-soundfont.md` still called it Karplus-Strong *and* pointed at
    `guitaroids/audio/synth_numpy.py`, a module that has never existed. The test beside
    this one could not see it, because it looked at a single file.

    **The rule is positive, not an absence**, which is the correction to my first
    attempt. A blanket "Karplus-Strong must not appear" test fails on the prose that
    *corrects* the myth -- `render.py` says "**Not** Karplus-Strong", which is the
    sentence the project most wants to keep. So instead: any file that mentions the
    fallback at all, by either name, has to use the real one too. A file that calls it
    Karplus-Strong and never says "pluck" cannot pass.

    DESIGN.md is exempt because it is append-only history; §7.5 and §9.5 describe a
    synth that was planned and never built, and correcting them in place is the one
    thing that file's convention forbids.
    """
    fallback_files = 0
    offenders: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in (".md", ".txt", ".py", ".toml"):
            continue
        if ".venv" in path.parts or ".git" in path.parts or path.name == "DESIGN.md":
            continue
        if path.name == Path(__file__).name:
            continue  # this file names the myth to assert against it
        text = path.read_text(errors="replace")
        if "Karplus" not in text and "pluck" not in text:
            continue
        fallback_files += 1
        if "pluck" not in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert fallback_files >= 5, (
        f"the sweep only found {fallback_files} files describing the fallback, so it "
        "is not searching where it thinks it is"
    )
    assert not offenders, (
        "these files name the numpy synth without ever calling it a pluck: "
        f"{offenders}"
    )


def test_the_attribution_points_at_a_module_that_exists() -> None:
    """A licence file that names a nonexistent module is worse than one that names none.

    `ATTRIBUTION-soundfont.md` is what a person reads to find out what is playing
    their music. It said the fallback was `guitaroids/audio/synth_numpy.py`, which was
    never written; the real one is the `backend="pluck"` path in `render.py`.
    """
    text = (ROOT / "assets" / "ATTRIBUTION-soundfont.md").read_text()
    assert "synth_numpy.py" not in text
    assert "render.py" in text, "name the module that actually does the fallback"
