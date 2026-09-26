"""Tests for the settings store.

Pure, so these run in milliseconds with no display and no Qt. The behaviour under
test is mostly about *not* surprising the user: a missing file, a corrupt file, a
hand-edited value out of range, or a file from a newer version must all leave a
working app.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from guitaroids.settings import (
    SETTINGS_VERSION,
    InputMode,
    Settings,
)


# --- defaults ----------------------------------------------------------------


def test_defaults_are_sane() -> None:
    s = Settings()
    assert 0.0 <= s.master_volume <= 1.0
    assert 0.0 <= s.click_volume <= 1.0
    assert s.input_mode is InputMode.KEYBOARD, "keyboard must be the default, not camera"
    assert s.count_in_bars == 1
    assert s.collapse_chords is True
    assert s.input_latency_ms == 0.0
    assert s.audio_device is None
    assert s.song_offsets_ms == {}
    assert s.version == SETTINGS_VERSION


def test_defaults_are_independent_between_instances() -> None:
    """A mutable default must not be shared between Settings objects."""
    a = Settings()
    b = Settings()
    a.song_offsets_ms["song"] = 12.0
    assert b.song_offsets_ms == {}


# --- round trip --------------------------------------------------------------


def test_round_trip_preserves_everything(tmp_path: Path) -> None:
    original = Settings(
        master_volume=0.42,
        click_volume=0.77,
        input_mode=InputMode.CAMERA,
        input_latency_ms=85.0,
        audio_device="USB Audio",
        camera_device=2,
        count_in_bars=2,
        collapse_chords=False,
        song_offsets_ms={"hotel_california": -120.0},
    )
    path = tmp_path / "settings.json"
    original.save(path)
    loaded = Settings.load(path)
    assert loaded.to_dict() == original.to_dict()


def test_save_creates_missing_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "settings.json"
    Settings().save(path)
    assert path.is_file()


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    Settings().save(path)
    assert [p.name for p in tmp_path.iterdir()] == ["settings.json"]


def test_save_writes_readable_json(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    Settings(master_volume=0.5).save(path)
    raw = json.loads(path.read_text())
    assert raw["master_volume"] == 0.5
    assert raw["input_mode"] == "keyboard", "enums serialise as their value"


# --- missing and corrupt -----------------------------------------------------


def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert Settings.load(tmp_path / "nope.json").to_dict() == Settings().to_dict()


def test_corrupt_json_gives_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ this is not json", encoding="utf-8")
    assert Settings.load(path).to_dict() == Settings().to_dict()


def test_empty_file_gives_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("", encoding="utf-8")
    assert Settings.load(path).to_dict() == Settings().to_dict()


def test_json_that_is_not_an_object_gives_defaults(tmp_path: Path) -> None:
    for payload in ("[1, 2, 3]", '"a string"', "42", "null"):
        path = tmp_path / "settings.json"
        path.write_text(payload, encoding="utf-8")
        assert Settings.load(path).to_dict() == Settings().to_dict(), payload


def test_directory_instead_of_file_gives_defaults(tmp_path: Path) -> None:
    """A path that exists but is a directory must not raise."""
    assert Settings.load(tmp_path).to_dict() == Settings().to_dict()


def test_unreadable_bytes_give_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_bytes(b"\xff\xfe\x00binary garbage")
    assert Settings.load(path).to_dict() == Settings().to_dict()


# --- per-field tolerance -----------------------------------------------------


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    """A file from a newer version must not break an older one."""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"master_volume": 0.3, "holographic_lanes": True, "future": [1, 2]}),
        encoding="utf-8",
    )
    loaded = Settings.load(path)
    assert loaded.master_volume == 0.3
    assert not hasattr(loaded, "future")


@pytest.mark.parametrize(
    "value,expected",
    [
        (5.0, 1.0),
        (-5.0, 0.0),
        (0.5, 0.5),
        ("0.25", 0.25),
        ("loud", 0.8),      # unparseable -> field default
        (None, 0.8),
        (True, 1.0),        # bool is an int in Python; clamped, not rejected
    ],
)
def test_volume_is_clamped_or_defaulted(value: object, expected: float) -> None:
    assert Settings.from_dict({"master_volume": value}).master_volume == expected


def test_nan_volume_falls_back_to_default() -> None:
    assert Settings.from_dict({"master_volume": float("nan")}).master_volume == 0.8


def test_count_in_bars_is_clamped() -> None:
    assert Settings.from_dict({"count_in_bars": -3}).count_in_bars == 0
    assert Settings.from_dict({"count_in_bars": 99}).count_in_bars == 2
    assert Settings.from_dict({"count_in_bars": "two"}).count_in_bars == 1


def test_camera_device_is_clamped() -> None:
    assert Settings.from_dict({"camera_device": -1}).camera_device == 0
    assert Settings.from_dict({"camera_device": 9999}).camera_device == 64


def test_latency_is_clamped_to_a_plausible_range() -> None:
    assert Settings.from_dict({"input_latency_ms": -99999}).input_latency_ms == -500.0
    assert Settings.from_dict({"input_latency_ms": 99999}).input_latency_ms == 2000.0


def test_one_bad_field_does_not_discard_the_others(tmp_path: Path) -> None:
    """Per-field recovery, not all-or-nothing."""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps({"master_volume": "garbage", "count_in_bars": 2}), encoding="utf-8"
    )
    loaded = Settings.load(path)
    assert loaded.master_volume == 0.8, "bad field falls back"
    assert loaded.count_in_bars == 2, "good field survives"


# --- input mode --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("camera", InputMode.CAMERA),
        ("CAMERA", InputMode.CAMERA),
        ("  Keyboard  ", InputMode.KEYBOARD),
        ("telepathy", InputMode.KEYBOARD),
        (None, InputMode.KEYBOARD),
        (7, InputMode.KEYBOARD),
        (InputMode.CAMERA, InputMode.CAMERA),
    ],
)
def test_input_mode_parses_tolerantly(raw: object, expected: InputMode) -> None:
    assert InputMode.parse(raw) is expected


def test_input_mode_survives_a_round_trip() -> None:
    s = Settings(input_mode=InputMode.CAMERA)
    assert s.to_dict()["input_mode"] == "camera"
    assert Settings.from_dict(s.to_dict()).input_mode is InputMode.CAMERA


# --- audio device ------------------------------------------------------------


def test_audio_device_accepts_none_and_string() -> None:
    assert Settings.from_dict({"audio_device": None}).audio_device is None
    assert Settings.from_dict({"audio_device": ""}).audio_device is None
    assert Settings.from_dict({"audio_device": "HDA Intel"}).audio_device == "HDA Intel"
    assert Settings.from_dict({"audio_device": 42}).audio_device is None


# --- per-song offsets --------------------------------------------------------


def test_offset_helpers() -> None:
    s = Settings()
    assert s.offset_for("unknown") == 0.0
    s.set_offset_for("hotel", -135.0)
    assert s.offset_for("hotel") == 135.0 * -1
    assert s.song_offsets_ms["hotel"] == -135.0


def test_offset_is_clamped_on_set() -> None:
    s = Settings()
    s.set_offset_for("song", 99999.0)
    assert s.song_offsets_ms["song"] == 5000.0


def test_garbage_offsets_are_dropped_not_fatal() -> None:
    s = Settings.from_dict({"song_offsets_ms": {"ok": -50.0, "bad": "loud", 7: 1.0}})
    assert s.song_offsets_ms == {"ok": -50.0}, "non-string keys and bad values dropped"


def test_offsets_survive_a_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    Settings(song_offsets_ms={"a": 1.5, "b": -2.5}).save(path)
    assert Settings.load(path).song_offsets_ms == {"a": 1.5, "b": -2.5}


# --- staying pure ------------------------------------------------------------


def test_module_imports_nothing_heavy() -> None:
    """This module is L1-ish: no Qt, no cv2, no audio.

    Verified by importing it in a clean subprocess and checking sys.modules. Worth
    pinning, because a stray `import sounddevice` here would make every settings
    test need an audio device.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import guitaroids.settings as m, sys;"
            "bad=[n for n in ('PySide6','cv2','sounddevice','mediapipe') if n in sys.modules];"
            "print(','.join(bad) or 'clean')",
        ],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "clean", f"settings.py pulled in {result.stdout.strip()}"
