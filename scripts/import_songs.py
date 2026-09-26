#!/usr/bin/env python
"""Import report for the song library.

Parses every tab under a directory, pairs it with backing audio, and prints what is
playable and what is not. Exists so a library can be validated without launching the
GUI -- the fastest way to find out that a tab is a .gpx, has no guitar track, or
changes tempo (DESIGN.md §6.6).

Usage::

    .venv/bin/python scripts/import_songs.py [songs_dir] [--json] [--collapse-chords]

Full chords, as the app ships them (DESIGN.md §21). The flag is the other way
round from what it was: this script used to default to collapsing, which made the
report describe a song the player would never see.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from guitaroids.model.chart import CollapseRule  # noqa: E402
from guitaroids.songlib import (  # noqa: E402
    Library,
    Status,
    format_duration,
    scan_library,
)


def entry_dict(entry) -> dict:
    return {
        "slug": entry.slug,
        "tab": str(entry.tab_path),
        "status": entry.status.value,
        "detail": entry.detail,
        "title": entry.title,
        "artist": entry.artist,
        "tempo": entry.tempo,
        "note_count": entry.note_count,
        "duration": round(entry.duration, 3),
        "difficulty": entry.difficulty,
        "audio": str(entry.audio_path) if entry.audio_path else None,
        "uses_repeats": entry.chart.uses_repeats if entry.chart else False,
        "warnings": list(entry.chart.warnings) if entry.chart else [],
        "collapse": entry.collapse,
        "collapse_rule": entry.rule.value,
        "max_chord_size": max((n.chord_size for n in entry.chart.notes), default=0)
        if entry.chart
        else 0,
        "tracks": [
            {
                "number": t.number,
                "name": t.name,
                "gm_program": t.instrument,
                "kind": t.kind.value,
                "string_count": t.string_count,
                "note_count": t.note_count,
                "is_default": t.is_suggested,
            }
            for t in entry.tracks
        ],
    }


def report(library: Library) -> None:
    print(f"scanned {library.root}")
    if not library.entries:
        print("  no .gp5 files found")
        print("  drop tabs in songs/ -- see DESIGN.md §6.4")
        return

    playable = library.playable
    problems = library.problems
    print(f"  {len(library.entries)} tab(s): {len(playable)} playable, {len(problems)} problem(s)")
    print()

    if playable:
        print("PLAYABLE")
        print(
            f"  {'slug':<24} {'title':<22} {'bpm':>4} {'notes':>6} {'len':>6} "
            f"{'diff':<7} {'chord':>6}  audio"
        )
        for entry in playable:
            audio = entry.audio_path.name if entry.audio_path else "-- metronome only --"
            chord = max((n.chord_size for n in entry.chart.notes), default=0) if entry.chart else 0
            print(
                f"  {entry.slug[:23]:<24} {entry.title[:21]:<22} {entry.tempo:>4} "
                f"{entry.note_count:>6} {format_duration(entry.duration):>6} "
                f"{entry.difficulty:<7} {chord:>6}  {audio}"
            )
        print()

        # Track choices, since the user picks the track (DESIGN.md §7.3).
        for entry in playable:
            if len(entry.tracks) > 1:
                print(f"  tracks in {entry.slug}:")
                for t in entry.tracks:
                    marker = "  <- default" if t.is_suggested else ""
                    print(
                        f"    #{t.number:<3} {t.name:<24} GM {t.instrument:<4}"
                        f" {t.note_count:>5} notes{marker}"
                    )
                print()

    if problems:
        print("PROBLEMS")
        for entry in problems:
            print(f"  [{entry.status.value}] {entry.slug}")
            if entry.detail:
                print(f"      {entry.detail}")
        print()

    warnings = [e for e in playable if e.chart and e.chart.warnings]
    if warnings:
        print("WARNINGS")
        for entry in warnings:
            for warning in entry.chart.warnings:
                print(f"  {entry.slug}: {warning}")
        print()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Report on the Guitaroids song library.")
    parser.add_argument("directory", nargs="?", default=str(ROOT / "songs"))
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--collapse-chords",
        action="store_true",
        help=(
            "reduce each chord to one note, instead of the app's default of keeping "
            "every note (DESIGN.md §21)"
        ),
    )
    parser.add_argument(
        "--rule",
        choices=[r.value for r in CollapseRule],
        default=CollapseRule.HIGHEST.value,
        help="which note survives a collapse",
    )
    args = parser.parse_args(argv)

    library = scan_library(
        args.directory,
        collapse=args.collapse_chords,
        rule=CollapseRule(args.rule),
    )
    # Exit non-zero when anything is unplayable, in both output modes, so this is
    # usable as a CI check on a song library.
    exit_code = 1 if library.problems else 0

    if args.json:
        print(
            json.dumps(
                {
                    "root": str(library.root),
                    "collapse": args.collapse_chords,
                    "rule": args.rule,
                    "entries": [entry_dict(e) for e in library.entries],
                },
                indent=2,
            )
        )
        return exit_code

    report(library)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
