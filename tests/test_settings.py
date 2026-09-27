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
    MAX_BPM,
    MIN_BPM,
    SETTINGS_VERSION,
    Settings,
)


# --- defaults ----------------------------------------------------------------


def test_defaults_are_sane() -> None:
    s = Settings()
    assert 0.0 <= s.master_volume <= 1.0
    assert 0.0 <= s.click_volume <= 1.0
    assert s.count_in_bars == 1
    # Full chords, since §21: the collapsed default dropped 2991 of the real
    # tab's 4099 notes and put 68% of the rest on one string.
    assert s.collapse_chords is False
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
        input_latency_ms=85.0,
        audio_device="USB Audio",
        input_device="Focusrite Scarlett",
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
    assert "input_mode" not in raw, "the field is gone, so the key must be too (§32)"


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


def test_input_mode_is_gone() -> None:
    """Removed, not deprecated, for the same reason `camera_device` was.

    There is one input now, so there is nothing to choose between, and a setting with
    a single legal value is a control that pretends to be a choice. This is §21.2's
    failure mode reached from the other end: the field was read by real code, and
    removing the code made it a field nothing reads.
    """
    assert not hasattr(Settings, "input_mode")
    assert not hasattr(Settings(), "input_mode")


def test_input_mode_is_not_serialised() -> None:
    """A field gone from the dataclass must be gone from the file too.

    Otherwise the next migration has to clean up a key the current code has never
    heard of, and nothing would say so.
    """
    assert "input_mode" not in Settings().to_dict()
    assert "input_mode" not in Settings().to_dict().values()


def test_a_file_that_asked_for_the_keyboard_still_loads() -> None:
    """Every existing settings file says "keyboard", and reading it must just work.

    It is the value in every file on disk, so a migration that raised would lock the
    player out of the game entirely. The key is dropped (§ version 4) and the
    microphone is what is left.
    """
    loaded = Settings.from_dict({"version": 3, "input_mode": "keyboard"})
    assert not hasattr(loaded, "input_mode")
    assert loaded.master_volume == Settings().master_volume


def test_a_file_that_asked_for_a_camera_still_loads() -> None:
    """The §25 legacy map is gone with the enum, so the key is simply dropped.

    A player who had chosen "camera" is now on the microphone, which is what the
    mapping meant in §25 and is the only input there is.
    """
    loaded = Settings.from_dict({"version": 2, "input_mode": "camera"})
    assert loaded.collapse_chords is False, "and the other migrations still apply"


# --- the camera_device removal (§25) ---------------------------------------------


def test_camera_device_is_gone() -> None:
    """Removed, not deprecated.

    It was an index into a camera enumeration that never existed, and nothing read
    it -- §21.2's failure mode exactly: saved, clamped, serialised, tested, and
    doing nothing.
    """
    assert not hasattr(Settings, "camera_device")
    assert not hasattr(Settings(), "camera_device")


def test_a_settings_file_from_before_the_change_drops_the_camera_key() -> None:
    """Migrated rather than read: an unknown key is ignored anyway, but the file
    should not claim to carry a setting the app no longer has."""
    loaded = Settings.from_dict({"version": 2, "camera_device": 3})
    assert not hasattr(loaded, "camera_device")
    assert loaded.version == SETTINGS_VERSION


def test_a_current_file_keeps_its_input_device() -> None:
    """A name, not an index, because a machine with a guitar interface, a laptop mic
    and a monitor loopback has three plausible answers and "the second one" is not a
    setting anybody can check."""
    loaded = Settings.from_dict({"version": 3, "input_device": "soundcore space"})
    assert loaded.input_device == "soundcore space"
    assert Settings.from_dict({"version": 3, "input_device": None}).input_device is None
    assert Settings.from_dict({"version": 3, "input_device": 7}).input_device is None


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


# --- per-song practice tempo -------------------------------------------------


def test_bpm_defaults_to_the_callers_default() -> None:
    """No stored value means "as written", not a fixed tempo.

    The fallback is the caller's because only the caller knows the tab's written
    tempo; a constant here would be a claim about tempo this module cannot make.
    """
    s = Settings()
    assert s.bpm_for("any_song", 76.0) == 76.0
    assert s.bpm_for("any_song") == 0.0


def test_bpm_round_trips_through_disk(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    Settings().save(path)
    raw = json.loads(path.read_text())
    raw["song_bpm"] = {"alpha": 60.0}
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = Settings.load(path)
    assert loaded.bpm_for("alpha") == 60.0
    assert loaded.bpm_for("beta") == 0.0, "one song's tempo must not leak to another"


def test_bpm_is_clamped_on_both_sides() -> None:
    s = Settings()
    s.set_bpm_for("a", 1.0)
    assert s.bpm_for("a") == MIN_BPM
    s.set_bpm_for("a", 10_000.0)
    assert s.bpm_for("a") == MAX_BPM


def test_bpm_from_a_hand_edited_file_is_clamped() -> None:
    loaded = Settings.from_dict({"song_bpm": {"a": 1, "b": 9999}})
    assert loaded.bpm_for("a") == MIN_BPM
    assert loaded.bpm_for("b") == MAX_BPM


def test_a_non_positive_stored_bpm_means_as_written() -> None:
    """Zero is a sentinel, not a tempo -- and not clamped up to MIN_BPM.

    Song select writes 0 when the control is at the written tempo (§19.1), so a
    stored 0 must resolve to the caller's default. Clamping it to 20 would turn
    "play it as written" into a 20 BPM song for every tab it happened to.
    """
    s = Settings()
    s.set_bpm_for("a", 0.0)
    assert s.bpm_for("a", default=76.0) == 76.0
    assert s.set_bpm_for("a", -5.0) is None
    assert s.bpm_for("a", default=76.0) == 76.0, "a negative is nonsense, not a tempo"


def test_a_non_positive_bpm_in_a_file_is_dropped_not_clamped() -> None:
    """A hand-typed -5 is dropped rather than stored as 20.

    An absent key already means "as written" (§19.1), so there is nothing to gain
    by inventing an entry -- and 20 BPM would be an actively wrong claim.
    """
    loaded = Settings.from_dict({"song_bpm": {"a": -5, "b": 0, "c": 62}})
    assert loaded.song_bpm == {"c": 62.0}
    assert loaded.bpm_for("a", default=76.0) == 76.0


def test_unparseable_bpm_values_are_dropped_not_invented() -> None:
    """Same rule as song_offsets_ms: do not fabricate a tempo for a song.

    Writing 0.0 would be an actively wrong claim -- "play this at zero" -- where
    dropping the entry leaves the tab at its written tempo, which is true.
    """
    loaded = Settings.from_dict(
        {"song_bpm": {"good": 62, "text": "fast", "null": None, "list": [60], "bad": True}}
    )
    assert loaded.bpm_for("good") == 62.0
    for slug in ("text", "null", "list", "bad"):
        assert slug not in loaded.song_bpm, f"{slug} should have been dropped"


def test_a_non_string_slug_is_ignored() -> None:
    loaded = Settings.from_dict({"song_bpm": {7: 60.0, "ok": 60.0}})
    assert loaded.song_bpm == {"ok": 60.0}


def test_a_song_bpm_that_is_not_a_mapping_is_ignored() -> None:
    for junk in ("nope", 60.0, [60], None):
        assert Settings.from_dict({"song_bpm": junk}).song_bpm == {}


def test_a_missing_song_bpm_key_falls_back_to_empty() -> None:
    """The migration case: a settings file written before this field existed.

    ``from_dict`` only reads keys that are present, so an older file is neither an
    error nor a reason to invent a tempo.
    """
    loaded = Settings.from_dict({"master_volume": 0.5, "version": 1})
    assert loaded.song_bpm == {}
    assert loaded.bpm_for("hotel", 76.0) == 76.0


def test_song_bpm_does_not_disturb_the_offsets() -> None:
    """Two per-song dicts on one record; one must not cost the other."""
    s = Settings()
    s.set_bpm_for("alpha", 55.0)
    s.set_offset_for("alpha", -120.0)
    assert s.bpm_for("alpha") == 55.0
    assert s.offset_for("alpha") == -120.0
    assert s.bpm_for("beta", 76.0) == 76.0
    assert s.offset_for("beta") == 0.0


def test_a_corrupt_file_still_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{ broken", encoding="utf-8")
    assert Settings.load(path).song_bpm == {}


# --- staying pure ---------------------------------------------------------------------------------------------------------------------


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


# --- the collapse migration (§21) --------------------------------------------
#
# `collapse_chords` flipped from True to False. A settings file written before the
# flip has the key present with the *old default* in it, so flipping the dataclass
# default alone would leave an existing installation exactly where it was.


def test_a_version_1_file_gets_the_new_chord_default() -> None:
    loaded = Settings.from_dict({"version": 1, "collapse_chords": True})
    assert loaded.collapse_chords is False, "the old default must not survive the flip"


def test_the_migration_does_not_invent_a_choice_the_file_already_made() -> None:
    """A version-1 file that already said False agrees with the new default."""
    assert Settings.from_dict({"version": 1, "collapse_chords": False}).collapse_chords is False


def test_a_version_2_file_keeps_the_setting_it_has() -> None:
    """The migration is a one-way door: v2 is past it, and the key is honoured."""
    assert Settings.from_dict({"version": 2, "collapse_chords": True}).collapse_chords is True
    assert Settings.from_dict({"version": 2, "collapse_chords": False}).collapse_chords is False


def test_a_file_with_no_version_is_treated_as_old() -> None:
    """Nobody wrote a version by hand, and a missing one must not be a free pass."""
    assert Settings.from_dict({"collapse_chords": True}).collapse_chords is False


def test_the_migration_leaves_every_other_setting_alone() -> None:
    """It drops one key. It does not rebuild the file."""
    loaded = Settings.from_dict(
        {
            "version": 1,
            "collapse_chords": True,
            "master_volume": 0.25,
            "count_in_bars": 2,
            "song_bpm": {"alpha": 60.0},
            "song_offsets_ms": {"alpha": -120.0},
        }
    )
    assert loaded.master_volume == 0.25
    assert loaded.count_in_bars == 2
    assert loaded.bpm_for("alpha") == 60.0
    assert loaded.offset_for("alpha") == -120.0


def test_the_migration_survives_a_round_trip(tmp_path: Path) -> None:
    """Loaded from disk, saved, loaded again: the answer must not wobble."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"version": 1, "collapse_chords": True}), encoding="utf-8")

    once = Settings.load(path)
    assert once.collapse_chords is False
    once.save(path)
    assert Settings.load(path).collapse_chords is False
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == SETTINGS_VERSION

