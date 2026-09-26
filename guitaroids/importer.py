"""Deciding what to do with a tab the user picked to import.

Pure, and separate from the screen, because the decisions are the interesting part
and a ``QMessageBox`` is not something a test can assert against. The screen asks
the user a question; this module says whether there is a question to ask.

Import is a **copy**, not a move or a link: the user's tab stays where it is, and
the library is a self-contained directory. Nothing is ever overwritten without
``replace=True``, which the screen only passes after the user has agreed.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .songlib import TAB_EXTENSIONS


class Outcome(Enum):
    """What importing a given file would do."""

    COPY = "copy"
    """Go ahead and copy."""

    CONFIRM = "confirm"
    """The destination exists. Ask the user before replacing it."""

    REJECT = "reject"
    """Do not import, and say why."""


@dataclass(frozen=True, slots=True)
class ImportPlan:
    """The decision, before anything is written."""

    outcome: Outcome
    source: Path
    destination: Path | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is Outcome.COPY


def plan_import(source: str | Path, songs_dir: str | Path, *, replace: bool = False) -> ImportPlan:
    """Decide what importing ``source`` into ``songs_dir`` would do.

    Never touches the filesystem beyond ``is_file``: this is the "before" half, and
    ``import_tab`` is the "after".
    """
    source = Path(source)
    songs_dir = Path(songs_dir)

    if not source.is_file():
        return ImportPlan(Outcome.REJECT, source, message="that file no longer exists")

    # Case-insensitive, matching songlib's discovery, so an upper-case .GP5 is
    # accepted rather than refused for a difference the library would have ignored.
    suffix = source.suffix.lower()
    if suffix not in TAB_EXTENSIONS:
        readable = ", ".join(TAB_EXTENSIONS)
        return ImportPlan(
            Outcome.REJECT,
            source,
            message=(
                f"{source.suffix or 'that file'} is not a Guitar Pro tab. "
                f"Supported: {readable}."
            ),
        )

    # The extension is lowercased into the destination name. songlib matches with
    # suffix.lower(), so it would find song.GP5 -- but a library that accumulates
    # mixed-case extensions is a nuisance to look at and to script against.
    destination = songs_dir / f"{source.stem}{suffix}"

    if destination.resolve() == source.resolve():
        return ImportPlan(Outcome.REJECT, source, destination, "that is already in the library")

    if destination.exists() and not replace:
        return ImportPlan(
            Outcome.CONFIRM,
            source,
            destination,
            f"{destination.name} is already in the library. Replace it?",
        )

    return ImportPlan(Outcome.COPY, source, destination)


def import_tab(plan: ImportPlan) -> Path:
    """Perform a ``COPY`` plan. Returns the destination.

    Copies to a temporary name in the destination directory and renames, so a
    failure part-way through cannot leave a half-written tab that the next scan
    reports as corrupt. The library directory is created if it is missing, so
    Import GP works on a fresh checkout.
    """
    if plan.outcome is not Outcome.COPY or plan.destination is None:
        raise ValueError(f"refusing to import a {plan.outcome.value} plan")

    destination = plan.destination
    destination.parent.mkdir(parents=True, exist_ok=True)

    temp = destination.with_name(f".{destination.name}.importing")
    try:
        shutil.copyfile(plan.source, temp)
        temp.replace(destination)
    except OSError:
        temp.unlink(missing_ok=True)
        raise
    return destination
