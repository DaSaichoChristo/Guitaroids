"""Tests for the import decision logic.

Pure, so no Qt and no message boxes: the screen's only job is to ask the user the
question this module reports. Every destructive path is pinned here, because
"silently overwrote someone's tab" is the failure this module exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from songbuild import write_tab

from guitaroids.importer import Outcome, import_tab, plan_import


@pytest.fixture()
def tab(tmp_path: Path) -> Path:
    """A real .gp5 outside the library, as if picked from somewhere else."""
    return write_tab(tmp_path / "incoming" / "song.gp5")


@pytest.fixture()
def songs_dir(tmp_path: Path) -> Path:
    path = tmp_path / "songs"
    path.mkdir()
    return path


# --- the happy path ----------------------------------------------------------


def test_a_new_tab_is_copied_in(tab: Path, songs_dir: Path) -> None:
    plan = plan_import(tab, songs_dir)
    assert plan.outcome is Outcome.COPY
    assert plan.destination == songs_dir / "song.gp5"
    assert plan.ok


def test_the_copy_lands_in_the_library(tab: Path, songs_dir: Path) -> None:
    destination = import_tab(plan_import(tab, songs_dir))
    assert destination.is_file()
    assert destination.read_bytes() == tab.read_bytes(), "the tab must arrive intact"


def test_the_source_is_left_where_it_was(tab: Path, songs_dir: Path) -> None:
    """A copy, not a move. The user's own file is not ours to relocate."""
    import_tab(plan_import(tab, songs_dir))
    assert tab.is_file()


def test_a_missing_songs_directory_is_created(tab: Path, tmp_path: Path) -> None:
    target = tmp_path / "not-created-yet"
    plan = plan_import(tab, target)
    assert plan.outcome is Outcome.COPY
    assert import_tab(plan).is_file()


# --- refusals ----------------------------------------------------------------


def test_an_unsupported_extension_is_refused(tmp_path: Path, songs_dir: Path) -> None:
    """A .gpx is refused with an explanation, not copied in to fail at scan time.

    Refusing is better than copying: the library's problems list exists for files
    that were already there, and filling it with something we could have predicted
    is just clutter.
    """
    gpx = tmp_path / "modern.gpx"
    gpx.write_bytes(b"GPX bytes")
    plan = plan_import(gpx, songs_dir)
    assert plan.outcome is Outcome.REJECT
    assert "not a Guitar Pro tab" in plan.message
    assert not (songs_dir / "modern.gpx").exists()


def test_a_file_with_no_extension_is_refused(tmp_path: Path, songs_dir: Path) -> None:
    plain = tmp_path / "notes"
    plain.write_text("hello")
    plan = plan_import(plain, songs_dir)
    assert plan.outcome is Outcome.REJECT
    assert "not a Guitar Pro tab" in plan.message


def test_a_missing_file_is_refused(tmp_path: Path, songs_dir: Path) -> None:
    plan = plan_import(tmp_path / "gone.gp5", songs_dir)
    assert plan.outcome is Outcome.REJECT
    assert "no longer exists" in plan.message


def test_importing_a_file_already_in_the_library_is_refused(
    tab: Path, songs_dir: Path
) -> None:
    import_tab(plan_import(tab, songs_dir))
    plan = plan_import(songs_dir / "song.gp5", songs_dir)
    assert plan.outcome is Outcome.REJECT
    assert "already in the library" in plan.message


def test_a_refusal_cannot_be_forced_into_a_copy(tab: Path, songs_dir: Path) -> None:
    """import_tab is the write path, so it must refuse a non-COPY plan itself.

    The screen checks first, but this is the guarantee that matters: nothing can
    write a file the planner did not approve.
    """
    plan = plan_import(tmp_path_missing := Path("/nonexistent/x.gp5"), songs_dir)
    assert plan.outcome is Outcome.REJECT
    with pytest.raises(ValueError, match="reject"):
        import_tab(plan)
    assert not tmp_path_missing.exists()


# --- overwriting -------------------------------------------------------------


def test_an_existing_destination_asks_first(tab: Path, songs_dir: Path) -> None:
    import_tab(plan_import(tab, songs_dir))
    plan = plan_import(tab, songs_dir)
    assert plan.outcome is Outcome.CONFIRM
    assert "Replace it?" in plan.message


def test_confirming_replaces_the_file(tab: Path, songs_dir: Path) -> None:
    destination = import_tab(plan_import(tab, songs_dir))
    destination.write_bytes(b"an older version")

    plan = plan_import(tab, songs_dir, replace=True)
    assert plan.outcome is Outcome.COPY
    import_tab(plan)
    assert destination.read_bytes() == tab.read_bytes()


def test_declining_leaves_the_original_alone(tab: Path, songs_dir: Path) -> None:
    destination = import_tab(plan_import(tab, songs_dir))
    destination.write_bytes(b"an older version")

    plan = plan_import(tab, songs_dir)
    assert plan.outcome is Outcome.CONFIRM
    assert plan.ok is False, "a question is not permission"
    assert destination.read_bytes() == b"an older version", "asking must not overwrite"


# --- naming ------------------------------------------------------------------


def test_an_uppercase_extension_is_accepted(tab: Path, songs_dir: Path) -> None:
    """songlib matches with suffix.lower(), so .GP5 must not be refused here.

    The two would otherwise disagree: the screen would say no to a file the
    library would have listed.
    """
    shouty = tab.with_name("SONG.GP5")
    tab.rename(shouty)
    plan = plan_import(shouty, songs_dir)
    assert plan.outcome is Outcome.COPY


def test_the_destination_extension_is_normalised_to_lower_case(
    tab: Path, songs_dir: Path
) -> None:
    shouty = tab.with_name("SONG.GP5")
    tab.rename(shouty)
    destination = import_tab(plan_import(shouty, songs_dir))
    assert destination.name == "SONG.gp5", "no mixed-case extensions in the library"


def test_the_destination_keeps_the_source_stem(tab: Path, songs_dir: Path) -> None:
    assert plan_import(tab, songs_dir).destination.name == "song.gp5"


def test_different_tabs_with_the_same_stem_collide(
    tmp_path: Path, songs_dir: Path
) -> None:
    """Two different folders, same filename. The second must ask, not clobber."""
    first = write_tab(tmp_path / "a" / "song.gp5", notes=2)
    second = write_tab(tmp_path / "b" / "song.gp5", notes=8)
    assert first.read_bytes() != second.read_bytes()

    destination = import_tab(plan_import(first, songs_dir))
    original = destination.read_bytes()
    assert plan_import(second, songs_dir).outcome is Outcome.CONFIRM
    assert destination.read_bytes() == original


# --- a failed copy leaves nothing behind --------------------------------------


def test_a_failed_copy_leaves_no_partial_file(
    songs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The temp-and-rename means the library never holds a half-written tab.

    A truncated .gp5 would scan as corrupt and sit in the problems list forever,
    blaming the user for a failure that was ours.
    """
    import guitaroids.importer as importer

    tab = write_tab(songs_dir.parent / "incoming.gp5")
    plan = plan_import(tab, songs_dir)

    def explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(importer.shutil, "copyfile", explode)
    with pytest.raises(OSError):
        import_tab(plan)

    assert list(songs_dir.iterdir()) == [], "no destination and no temp file"


def test_a_failed_copy_does_not_touch_an_existing_destination(
    songs_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import guitaroids.importer as importer

    first = write_tab(songs_dir.parent / "a.gp5", notes=2)
    second = write_tab(songs_dir.parent / "b.gp5", notes=8)
    destination = import_tab(plan_import(first, songs_dir))
    keep = destination.read_bytes()

    def explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(importer.shutil, "copyfile", explode)
    with pytest.raises(OSError):
        import_tab(plan_import(second, songs_dir, replace=True))

    assert destination.read_bytes() == keep, "a failed replace must not truncate"


# --- against the real library ------------------------------------------------


def test_an_imported_tab_then_scans_as_playable(tab: Path, songs_dir: Path) -> None:
    """The whole point: after importing, the song is in the library."""
    from guitaroids.songlib import scan_library

    import_tab(plan_import(tab, songs_dir))
    library = scan_library(songs_dir)
    assert [e.slug for e in library.playable] == ["song"]
