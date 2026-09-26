"""User settings: what the preferences screen edits.

Pure by design -- no Qt, no audio device, no camera. That keeps it testable in
milliseconds with no display, which matters because it is the first thing three
screens depend on.

Three rules, all of them about not surprising the user:

1. **A missing file is normal**, not an error. First run writes nothing.
2. **A corrupt file falls back to defaults** rather than crashing the app. Someone
   hand-editing JSON, or a half-written file from a crash, should still get a
   working app.
3. **Out-of-range values are clamped, unknown keys ignored.** A settings file
   written by a newer version must not break an older one.

Writes are atomic (temp file plus rename), so an interrupted save cannot leave a
truncated file that rule 2 then has to rescue.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from pathlib import Path

#: Bumped when a change is not backwards compatible. Older files are migrated or
#: ignored, never fatal.
SETTINGS_VERSION = 1


class InputMode(Enum):
    """How the player controls the highway."""

    KEYBOARD = "keyboard"
    CAMERA = "camera"

    @classmethod
    def parse(cls, raw: object, default: "InputMode" = None) -> "InputMode":
        """Tolerant parse: anything unrecognised falls back to the default."""
        fallback = default if default is not None else cls.KEYBOARD
        if isinstance(raw, cls):
            return raw
        if isinstance(raw, str):
            for member in cls:
                if member.value == raw.strip().lower():
                    return member
        return fallback


def _clamp_float(value: object, low: float, high: float, default: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(low, min(high, number))


def _clamp_int(value: object, low: int, high: int, default: int) -> int:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _parse_number(value: object) -> float | None:
    """Strict numeric parse: ``None`` if it is not a real number.

    Distinct from :func:`_clamp_float`, which substitutes a default. Per-song
    offsets need the difference, because 0.0 is a meaningful value ("aligned") and
    quietly rewriting garbage to 0.0 would claim alignment the user never set.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if number != number else number  # NaN


@dataclass
class Settings:
    """Everything the preferences screen can change.

    Not a per-song offset: that belongs on the PlayRequest, because it describes
    one attempt at one song. ``song_offsets_ms`` exists only to remember it for
    next time, keyed by tab slug.
    """

    master_volume: float = 0.8
    """0.0-1.0, the whole mix."""

    click_volume: float = 0.5
    """0.0-1.0, the metronome, relative to the master volume."""

    input_mode: InputMode = InputMode.KEYBOARD
    """Keyboard by default, because the keyboard path must always work (§4.1)."""

    input_latency_ms: float = 0.0
    """The user's measured camera+inference lag, subtracted before judging (§4.1)."""

    audio_device: str | None = None
    """``None`` means the system default. Matched as a substring of device names."""

    camera_device: int = 0
    """Index into the device enumeration. 0 is the usual default."""

    count_in_bars: int = 1
    """0-2 bars of clicks before the music. Many tracks open with their own."""

    collapse_chords: bool = True
    """One note per onset. Off keeps every note, for keyboard play (§7.4)."""

    song_offsets_ms: dict[str, float] = field(default_factory=dict)
    """Per-tab audio alignment, keyed by slug, so a manual tweak sticks."""

    version: int = SETTINGS_VERSION

    # --- serialisation ----------------------------------------------------

    def to_dict(self) -> dict:
        data = asdict(self)
        data["input_mode"] = self.input_mode.value
        return data

    @classmethod
    def from_dict(cls, raw: object) -> "Settings":
        """Build from a parsed mapping, tolerating anything.

        Unknown keys are dropped by only reading known fields; wrong types fall
        back per-field rather than rejecting the whole file, so one bad value does
        not discard the other twenty.
        """
        defaults = cls()
        if not isinstance(raw, dict):
            return defaults

        values: dict = {}
        for spec in fields(cls):
            name = spec.name
            if name not in raw:
                continue
            given = raw[name]

            if name == "input_mode":
                values[name] = InputMode.parse(given, defaults.input_mode)
            elif name in ("master_volume", "click_volume"):
                values[name] = _clamp_float(given, 0.0, 1.0, getattr(defaults, name))
            elif name == "input_latency_ms":
                values[name] = _clamp_float(given, -500.0, 2000.0, 0.0)
            elif name == "camera_device":
                values[name] = _clamp_int(given, 0, 64, defaults.camera_device)
            elif name == "count_in_bars":
                values[name] = _clamp_int(given, 0, 2, defaults.count_in_bars)
            elif name == "collapse_chords":
                values[name] = bool(given) if isinstance(given, bool) else defaults.collapse_chords
            elif name == "audio_device":
                if given is None or isinstance(given, str):
                    values[name] = given or None
                else:
                    values[name] = None
            elif name == "song_offsets_ms":
                offsets: dict[str, float] = {}
                if isinstance(given, dict):
                    for slug, offset in given.items():
                        if not isinstance(slug, str):
                            continue
                        number = _parse_number(offset)
                        if number is None:
                            continue  # drop, do not invent a 0.0 alignment
                        offsets[slug] = max(-5000.0, min(5000.0, number))
                values[name] = offsets
            elif name == "version":
                values[name] = _clamp_int(given, 0, 999, SETTINGS_VERSION)
            # version: keep the file's value if sane, else current

        return cls(**values)

    # --- disk -------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str) -> "Settings":
        """Read settings, never raising.

        A missing file, unreadable file, invalid JSON, or a JSON document that is
        not an object all yield defaults.
        """
        path = Path(path)
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return cls()
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return cls()
        return cls.from_dict(raw)

    def save(self, path: Path | str) -> None:
        """Write settings atomically: temp file in the same directory, then rename.

        A crash mid-write leaves the old file intact rather than a truncated one.
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2, sort_keys=True)

        handle = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=".settings-",
            suffix=".tmp",
            delete=False,
        )
        tmp = Path(handle.name)
        try:
            with handle:
                handle.write(payload + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            tmp.replace(path)
        except OSError:
            tmp.unlink(missing_ok=True)
            raise

    # --- per-song helpers --------------------------------------------------

    def offset_for(self, slug: str) -> float:
        """The remembered offset for one tab, in milliseconds."""
        return self.song_offsets_ms.get(slug, 0.0)

    def set_offset_for(self, slug: str, offset_ms: float) -> None:
        self.song_offsets_ms[slug] = _clamp_float(offset_ms, -5000.0, 5000.0, 0.0)
