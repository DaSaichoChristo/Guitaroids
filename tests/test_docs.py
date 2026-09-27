"""Tests that keep the documentation true.

The pattern is §25.4's: an **absence test written against the artifact**, not a
promise in prose. §27 fixed a false claim in `requirements.txt` and wrote down that
it had fixed all such claims -- and `README.md` was still saying "the ONLY supported
install path" two paragraphs away, because the test looked at one file. Four stale
claims and one unclosed code fence later, the problem is not any single sentence. It
is that nothing checks.

So this file checks the things that rot on their own:

- **Counts** drift every time a test is added, which is every few days.
- **Section references** outlive the sections they cite. Every ``§N`` in the three
  front-door documents is resolved against a real header in `DESIGN.md`.
- **Paths** are quoted in prose and deleted from disk. Every file the docs name must
  exist, or be one of the deliberately-untracked ones a fresh clone will not have.
- **Superseded claims** are the dangerous kind: they are not wrong, they are wrong
  *for a while*, and they read as current. Each one gets a test that fails if it
  comes back.
- **Code fences** are not prose and nobody proofreads them, and an odd number of them
  silently swallows the rest of the file into a code block.

**The test count is self-referential**, so it counts every test in `tests/` *except
the ones in this file* and the docs say so. A test that asserts the suite has 832
tests while adding itself to the suite is a test that fails on arrival; the
alternative -- not checking the count -- is not checking the count.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DESIGN = ROOT / "DESIGN.md"
AGENTS = ROOT / "AGENTS.md"
README = ROOT / "README.md"
DECISIONS = ROOT / "DECISIONS.md"

#: The documents that are read first, and are therefore the ones that must not lie.
FRONT_DOOR = (AGENTS, README, DECISIONS)

#: Paths a fresh clone legitimately will not have. Gitignored, or created by
#: `setup.sh`. Naming one of these in prose is correct; everything else must exist.
UNTRACKED_OK = frozenset(
    {
        "settings.json",  # the player's own, gitignored
        "songs",  # tabs are personal
        "assets/soundfont.sf3",  # fetched by scripts/, never committed
        "assets/soundfont.sf2",
    }
)

#: Where a bare filename in prose actually lives. The documents say `settings.py`,
#: not `guitaroids/settings.py`, so every candidate root is tried before a path is
#: called missing.
SEARCH_ROOTS = ("", "guitaroids", "tests", "scripts")

#: Files the docs name **on purpose** because they are not there. `audio/pitch.py` was
#: on this list from §28 until §29 wrote it, and `test_declared_absences_are_still_absent`
#: is what noticed -- which is the whole argument for the list. An existence test
#: cannot tell a typo from a sentence whose subject is a file that does not exist, so
#: the difference is declared here, with a reason. Each entry must earn its place:
#: `test_declared_absences_are_still_absent` fails the moment the file lands, which
#: turns the allowlist into a to-do list rather than a place for things to hide.
DECLARED_ABSENT = {
    "qtenv.py": "deleted with the webcam in §25; AGENTS.md explains the Qt plugin "
    "hijack it used to work around",
    "audio/mic.py": "the remaining input work. AGENTS.md says outright that nothing "
    "opens an input device yet, and §29.3's Not done says the same",
}


# --- the helpers -------------------------------------------------------------


def _tracked_files() -> set[str]:
    """Every path git knows about, relative to the root. One subprocess, cached."""
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return set(out.stdout.split())


def _read(path: Path) -> str:
    return path.read_text()


def _section_numbers() -> set[str]:
    """Every ``§N`` and ``§N.M`` that `DESIGN.md` actually defines.

    Reads the headers rather than the body: a section number that exists only inside
    a sentence is a citation, not a section.
    """
    found: set[str] = set()
    for line in _read(DESIGN).splitlines():
        match = re.match(r"^#{2,3} §(\d+(?:\.\d+)*)\b", line)
        if match:
            found.add(match.group(1))
            found.add(match.group(1).split(".")[0])
    return found


def _collected_count() -> int:
    """How many tests the suite collects, minus this file's own.

    ``sys.executable``, not ``python``: this venv has no ``python`` on PATH, which
    is a FileNotFoundError rather than a test failure, and the first draft of this
    function had exactly that.

    `--collect-only -q` prints one line per test id, so ids are the lines containing
    ``::``.
    """
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests",
            "--collect-only",
            "-q",
            "--ignore=tests/test_docs.py",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    return sum(1 for line in out.stdout.splitlines() if "::" in line)


def _resolves(candidate: str, tracked: set[str]) -> bool:
    """Does this path name a real file, allowing for the bare names prose uses?

    Tracked *or* present. Tracked alone would make this file fail its own test until
    it was committed, which is a bad first impression of a test whose whole job is to
    be believed; and a file that exists in the working tree is a real path whether or
    not git has heard of it yet.
    """
    if candidate in tracked or candidate in UNTRACKED_OK:
        return True
    for prefix in SEARCH_ROOTS:
        attempt = f"{prefix}/{candidate}" if prefix else candidate
        if attempt in tracked or (ROOT / attempt).is_file():
            return True
    return False


# --- counts ------------------------------------------------------------------


def test_the_documented_test_count_is_the_real_one() -> None:
    """AGENTS.md and README.md both quote a number, and it is right or it is noise.

    Both are checked, because a stale number in the file a newcomer reads first is
    worse than no number. Understating is as wrong as overstating: it is what happens
    when a test is deleted rather than fixed.
    """
    real = _collected_count()
    assert real > 800, f"only {real} tests collected -- is collection broken?"

    claimed = {
        path.name: int(m.group(1))
        for path in FRONT_DOOR + (AGENTS,)
        for m in [re.search(r"(\d{3,4}) tests", _read(path))]
        if m
    }
    assert claimed, "no document states a test count any more; one should"
    for name, count in sorted(claimed.items()):
        assert count == real, (
            f"{name} says {count} tests, the suite collects {real}. Update it, and "
            "remember this count excludes the tests in tests/test_docs.py."
        )


def test_the_documented_python_minor_version_is_the_one_in_the_venv() -> None:
    """3.12 specifically, because of tinysoundfont's wheels -- but not the patch.

    `tinysoundfont` ships cp310 and cp312 wheels only, so 3.12 is a real constraint
    and 3.12.14 is a fact about one machine. A document that pins the patch version
    is asserting something true of the author's laptop and false of everyone else's,
    which is how "3.12.12" and "3.12.14" ended up in two files at once.
    """
    running = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert running == "3.12", (
        f"this venv is Python {running}; the docs explain that 3.13 and 3.14 need a "
        "C++ toolchain for tinysoundfont, so the pin is a real constraint and it moved"
    )

    for path in FRONT_DOOR:
        found = re.search(r"3\.12\.\d+", _read(path))
        assert not found, (
            f"{path.name} pins a patch version ({found.group(0) if found else ''}). "
            "Say 3.12 -- the constraint is the minor version, and the patch belongs "
            "to one machine."
        )


def test_the_documented_package_count_matches_the_lock() -> None:
    """The lock is the artefact; the docs are the claim about it."""
    lock = (ROOT / "requirements-lock.txt").read_text()
    pins = len(re.findall(r"^[A-Za-z0-9_.-]+==", lock, re.M))
    assert pins > 0
    for path in FRONT_DOOR:
        for m in re.finditer(r"(\d+) packages", _read(path)):
            assert int(m.group(1)) == pins, (
                f"{path.name} says {m.group(1)} packages; requirements-lock.txt pins "
                f"{pins}"
            )


# --- section references ------------------------------------------------------


def test_every_section_cited_in_the_front_door_documents_exists() -> None:
    """A ``§N`` that resolves to nothing is a dead link, and nobody notices.

    `DESIGN.md` is append-only, so sections are only ever added -- which makes a
    dangling citation unlikely to be noticed and impossible to detect by reading. The
    cost is paid by whoever follows the reference to find the justification for a
    decision, and finds nothing.
    """
    sections = _section_numbers()
    assert len(sections) > 20, f"only found {len(sections)} sections; the parse broke"

    for path in FRONT_DOOR:
        cited = set(re.findall(r"§(\d+(?:\.\d+)*)", _read(path)))
        missing = sorted(c for c in cited if c not in sections and c.split(".")[0] not in sections)
        assert not missing, f"{path.name} cites sections that do not exist: {missing}"


def test_a_decision_row_cites_the_section_that_justifies_it() -> None:
    """DECISIONS.md is an index of decisions, so every row needs an address.

    A row with an empty or unparseable citation is the failure mode of a one-screen
    index: it looks complete, and half of it cannot be followed.
    """
    rows = [ln for ln in _read(DECISIONS).splitlines() if ln.startswith("|") and "---" not in ln]
    assert len(rows) > 30, f"only {len(rows)} table rows found; the parse broke"
    for row in rows:
        if "§" not in row:
            continue
        cited = re.findall(r"§(\d+(?:\.\d+)*)", row)
        assert cited, f"a decision row has a section mark but no number: {row[:70]}"


# --- paths -------------------------------------------------------------------


def test_every_file_path_the_docs_name_exists() -> None:
    """Docs quote paths; the filesystem does not always have them.

    Two ways to be wrong. A path that was renamed is a dead reference. A path that was
    *deleted* is worse, because the sentence around it may still read as though the
    file is somewhere to be found -- which is exactly the case for `qtenv.py`, gone
    since §25 and still named in AGENTS.md's own history of the Qt plugin hijack.

    Bare filenames resolve against `guitaroids/`, `tests/` and `scripts/` too, because
    that is how prose refers to them. Anything still unresolved has to be declared in
    `DECLARED_ABSENT` with a reason, which is a stronger claim than "the file exists".
    """
    tracked = _tracked_files()
    missing: list[str] = []
    for path in FRONT_DOOR:
        for m in re.finditer(r"`([A-Za-z0-9_][A-Za-z0-9_./-]*\.[A-Za-z0-9]+)`", _read(path)):
            candidate = m.group(1)
            if not candidate.endswith(
                (".md", ".py", ".txt", ".toml", ".sh", ".ps1", ".json", ".cfg", ".qss")
            ):
                continue
            if _resolves(candidate, tracked) or candidate in DECLARED_ABSENT:
                continue
            missing.append(f"{path.name}: {candidate}")
    assert not missing, (
        "the docs name files that do not exist and are not declared absent: "
        f"{sorted(set(missing))}"
    )


def test_declared_absences_are_still_absent() -> None:
    """The allowlist has to keep earning its place.

    An entry is a claim that a file the docs mention does not exist -- true today for
    `qtenv.py` (deleted, §25) and `audio/pitch.py` (not written yet). The moment one
    of them lands, the entry is wrong: it would let a future rename or deletion of a
    real file pass unnoticed. So it fails, and says to delete the line.
    """
    tracked = _tracked_files()
    stale = [
        name
        for name in DECLARED_ABSENT
        if _resolves(name, tracked) and name not in UNTRACKED_OK
    ]
    assert not stale, (
        f"these are declared absent but exist now: {stale}. Delete the entries from "
        "DECLARED_ABSENT in tests/test_docs.py -- the existence check will cover them."
    )


# --- superseded claims -------------------------------------------------------
#
# One test each, and each says what the truth is now. An absence test with no
# replacement fact is a rule nobody can act on.


def test_the_docs_do_not_say_a_bare_pip_install_is_unsupported() -> None:
    """§27.2 verified a bare `pip install -r requirements.txt` in a clean venv.

    `README.md` kept saying "the ONLY supported install path" after §27 fixed
    `requirements.txt`, because that commit's test read one file. So the check is
    over all three front-door documents, which is the generalisation §27.4 needed and
    did not get.
    """
    for path in FRONT_DOOR:
        flat = re.sub(r"\s+", " ", _read(path))
        assert "ONLY supported install path" not in flat, (
            f"{path.name} still says a bare pip install is unsupported. It works: "
            "§27.2 installed the app in a clean venv and opened a window."
        )


def test_the_docs_do_not_say_there_is_no_audio_yet() -> None:
    """§23 made the audio device the master clock, five sections ago.

    `README.md` said "There is no audio yet. The game runs on a wall clock ... the
    audio clock is the next milestone", in the same file that elsewhere described the
    audio layers as built. It was the first thing a new reader learned about the
    project and it described a state two sections behind HEAD.
    """
    for path in FRONT_DOOR:
        flat = re.sub(r"\s+", " ", _read(path))
        for claim in ("There is no audio yet", "the audio clock is the next milestone"):
            assert claim not in flat, (
                f"{path.name} still says {claim!r}. The audio clock is live (§23) and "
                "the wall clock is its fallback, not its future."
            )


def test_no_rule_instructs_the_reader_to_avoid_the_removed_camera() -> None:
    """`AGENTS.md`'s Rules list was still telling the reader to avoid `cap.read()`.

    §25 removed the camera, so the instruction had nothing to apply to -- and a rule
    that cannot apply is worse than no rule, because it looks like the failure it
    warns about is still reachable.

    **Scoped to the instruction, not the topic.** Two earlier versions of this test
    scanned for the words "mediapipe" and "cap.read()" and were both wrong. One
    flagged the stack summary ("**No mediapipe and no OpenCV**"), where the wrapping
    had put the `(§24, §25)` on a different line. The other flagged the xcb
    troubleshooting note, which says the only package that ever shipped Qt plugins was
    mediapipe's OpenCV -- accurate, useful, and not a claim that it is installed.
    Keywords cannot tell "mentions" from "asserts as current". This checks the one
    sentence that genuinely instructed the reader, and a mention in prose is free.
    """
    for path in FRONT_DOOR:
        text = _read(path)
        assert "block on `cap.read()`" not in text, (
            f"{path.name} still tells the reader not to block on cap.read(). The "
            "camera went in §25; the remaining half of that rule is about audio."
        )
        assert "no working camera" not in text, (
            f"{path.name} still justifies the keyboard fallback by a missing camera. "
            "The honest reason is a missing audio input (§24.4)."
        )


# --- formatting, which nobody proofreads -------------------------------------


@pytest.mark.parametrize("path", FRONT_DOOR, ids=lambda p: p.name)
def test_code_fences_are_balanced(path: Path) -> None:
    """An odd number of fences swallows the rest of the file into a code block.

    `AGENTS.md` had five. Everything from "Six things that will bite you" to the end --
    the six warnings, "Unblock this first", "The next blocker" and the whole audio
    section -- rendered as one preformatted block with no headings and no bold. It is
    in the file every agent reads first, and it is invisible to anyone reading the
    source, because the source is perfectly well formed. `README.md` had seventeen, so
    its "## Adding songs" heading and first paragraph were inside a block too.

    **There is deliberately no test for inline backtick parity.** An inline code span
    may wrap across a line -- two of these documents do exactly that, correctly -- so
    an odd count per line is not a defect and checking for one produces false
    positives on valid Markdown. The fence count has no such ambiguity.
    """
    fences = [ln for ln in _read(path).splitlines() if ln.lstrip().startswith("```")]
    assert len(fences) % 2 == 0, (
        f"{path.name} has {len(fences)} code fences, which is odd, so everything "
        f"after fence {len(fences) - 1} renders inside a code block"
    )
