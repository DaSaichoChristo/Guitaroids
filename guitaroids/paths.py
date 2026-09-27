"""Where things live on disk.

Consolidates what was ten copies of ``Path(__file__).resolve().parent.parent``.
Two jobs:

1. **Find the repository root** by walking up for a marker file, rather than
   assuming the package sits exactly one level below it. That assumption is true
   today and is the kind of thing that breaks the first time the package is
   installed, vendored, or moved.
2. **Name every path the app reads or writes** in one place, so the song
   directory, the settings file, and the fetched assets cannot drift apart.

Pure: no Qt, no I/O at import time beyond a few stat calls.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Files that only exist at the repository root. Used to recognise it while
#: walking upwards, so a ``site-packages`` copy cannot masquerade as the repo.
_ROOT_MARKERS = ("requirements.txt", "scripts/setup.sh")

#: Environment variable that overrides soundfont discovery entirely.
SOUNDFONT_ENV = "GUITAROIDS_SOUNDFONT"


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from ``start`` looking for a repository marker.

    Falls back to the package's parent directory if no marker is found, which is
    what happens if the package is installed as a library. That is a best guess
    rather than a guarantee, so callers that genuinely need a real path should
    check ``.exists()`` rather than trust the return value.
    """
    here = (start or Path(__file__).resolve()).parent
    for candidate in (here, *here.parents):
        if all((candidate / marker).exists() for marker in _ROOT_MARKERS):
            return candidate
    return here.parent


REPO_ROOT: Path = find_repo_root()

#: Where tabs and their backing audio live. Gitignored, personal to the user.
SONGS_DIR: Path = REPO_ROOT / "songs"

#: Runtime settings. Gitignored; see settings.example.json for the committed copy.
SETTINGS_PATH: Path = REPO_ROOT / "settings.json"

#: Fetched assets: an optional soundfont.
ASSETS_DIR: Path = REPO_ROOT / "assets"


def soundfont_candidates() -> tuple[Path, ...]:
    """Every place a soundfont might be, most specific first.

    The first that exists wins. Nothing here is required: with no soundfont the
    numpy Karplus-Strong synth is used instead (DESIGN.md §9.5).
    """
    candidates: list[Path] = []

    override = os.environ.get(SOUNDFONT_ENV)
    if override:
        candidates.append(Path(override).expanduser())

    candidates += [ASSETS_DIR / "soundfont.sf2", ASSETS_DIR / "soundfont.sf3"]

    # The only soundfont most Linux desktops ship is TimGM6mb, which is GPL-2, so
    # the fetch script installs a permissively licensed one instead. These paths
    # are a convenience, never a commitment to a licence.
    system_dirs = ("/usr/share/sounds/sf2", "/usr/share/sounds/sf3", "/usr/share/soundfonts")
    candidates += [Path(d) / "default-GM.sf2" for d in system_dirs]
    candidates += [Path(d) / "TimGM6mb.sf2" for d in system_dirs]
    candidates += [Path(d) / "FluidR3_GM.sf2" for d in system_dirs]

    return tuple(candidates)


def find_soundfont() -> Path | None:
    """The first existing soundfont, or ``None``. Never raises."""
    for candidate in soundfont_candidates():
        if candidate.is_file():
            return candidate
    return None
