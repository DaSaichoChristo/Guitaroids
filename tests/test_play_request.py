"""Tests for PlayRequest.

Frozen, so equality is meaningful and a screen can hold one without defensive
copying. The behaviour that matters is the split between *validating* in the
constructor — our own code's bugs should say so — and *clamping* in from_settings,
because a slider or a hand-edited settings file can legitimately be out of range.
"""

from __future__ import annotations

import pytest

from guitaroids.session.play_request import (
    MAX_COUNT_IN_BARS,
    MAX_OFFSET_SECONDS,
    PlayRequest,
)
from guitaroids.settings import Settings


# --- construction ------------------------------------------------------------


def test_minimal_construction() -> None:
    r = PlayRequest(slug="song", track_number=3)
    assert r.slug == "song"
    assert r.track_number == 3
    assert r.offset_seconds == 0.0
    assert r.count_in_bars == 1
    assert r.collapse_chords is True


def test_is_frozen() -> None:
    r = PlayRequest(slug="song", track_number=1)
    with pytest.raises(Exception):
        r.slug = "other"  # type: ignore[misc]


def test_equality_is_by_value() -> None:
    a = PlayRequest(slug="s", track_number=1, offset_seconds=0.25)
    b = PlayRequest(slug="s", track_number=1, offset_seconds=0.25)
    assert a == b
    assert hash(a) == hash(b), "frozen with slots must still hash"


# --- validation --------------------------------------------------------------


def test_empty_slug_is_rejected() -> None:
    with pytest.raises(ValueError, match="slug"):
        PlayRequest(slug="", track_number=1)


@pytest.mark.parametrize("track", [0, -1, -99])
def test_non_positive_track_is_rejected(track: int) -> None:
    with pytest.raises(ValueError, match="track_number"):
        PlayRequest(slug="s", track_number=track)


@pytest.mark.parametrize("bars", [MAX_COUNT_IN_BARS + 1, 5, -1])
def test_out_of_range_count_in_is_rejected(bars: int) -> None:
    with pytest.raises(ValueError, match="count_in_bars"):
        PlayRequest(slug="s", track_number=1, count_in_bars=bars)


def test_absurd_offset_is_rejected() -> None:
    with pytest.raises(ValueError, match="offset"):
        PlayRequest(slug="s", track_number=1, offset_seconds=MAX_OFFSET_SECONDS + 1)


# --- from_settings -----------------------------------------------------------


def test_from_settings_pulls_count_in_and_collapse() -> None:
    settings = Settings(count_in_bars=2, collapse_chords=False)
    r = PlayRequest.from_settings("s", 4, settings)
    assert r.count_in_bars == 2
    assert r.collapse_chords is False
    assert r.track_number == 4


def test_from_settings_uses_the_remembered_offset() -> None:
    settings = Settings()
    settings.set_offset_for("hotel", -135.0)
    assert PlayRequest.from_settings("hotel", 1, settings).offset_seconds == pytest.approx(-0.135)


def test_from_settings_ignores_another_songs_offset() -> None:
    settings = Settings()
    settings.set_offset_for("other_song", 500.0)
    assert PlayRequest.from_settings("hotel", 1, settings).offset_seconds == 0.0


def test_explicit_offset_overrides_the_remembered_one() -> None:
    """Dragging the slider in song select must not need to write to settings."""
    settings = Settings()
    settings.set_offset_for("hotel", -100.0)
    r = PlayRequest.from_settings("hotel", 1, settings, offset_ms=50.0)
    assert r.offset_seconds == pytest.approx(0.05)
    assert settings.offset_for("hotel") == -100.0, "settings must be untouched"


@pytest.mark.parametrize("bars", [-5, 7, 99])
def test_from_settings_clamps_count_in(bars: int) -> None:
    settings = Settings(count_in_bars=bars)
    request = PlayRequest.from_settings("s", 1, settings)
    assert request.count_in_bars == MAX_COUNT_IN_BARS or request.count_in_bars == 0
    assert 0 <= request.count_in_bars <= MAX_COUNT_IN_BARS


def test_from_settings_clamps_offset() -> None:
    settings = Settings()
    settings.set_offset_for("s", 99_999.0)
    assert PlayRequest.from_settings("s", 1, settings).offset_seconds == pytest.approx(1.0)


def test_from_settings_survives_a_corrupt_offset() -> None:
    settings = Settings()
    settings.song_offsets_ms["s"] = float("nan")
    assert PlayRequest.from_settings("s", 1, settings).offset_seconds == 0.0


def test_from_settings_does_not_mutate_settings() -> None:
    settings = Settings(count_in_bars=99)
    PlayRequest.from_settings("s", 1, settings)
    assert settings.count_in_bars == 99, "clamping must not write back"


# --- helpers -----------------------------------------------------------------


def test_with_offset_returns_a_copy() -> None:
    original = PlayRequest(slug="s", track_number=2, count_in_bars=0, collapse_chords=False)
    moved = original.with_offset_ms(250.0)
    assert moved.offset_seconds == pytest.approx(0.25)
    assert original.offset_seconds == 0.0, "the original is untouched"
    assert moved.count_in_bars == original.count_in_bars
    assert moved.collapse_chords == original.collapse_chords


def test_with_offset_clamps() -> None:
    assert PlayRequest(slug="s", track_number=1).with_offset_ms(1e9).offset_seconds == 1.0


def test_describe_mentions_the_essentials() -> None:
    text = PlayRequest(slug="hotel", track_number=3, offset_seconds=-0.135).describe()
    assert "hotel" in text and "track 3" in text
    assert "-135ms" in text


def test_describe_sign_of_zero_is_not_negative() -> None:
    text = PlayRequest(slug="s", track_number=1, offset_seconds=0.0).describe()
    assert "+0ms" in text


# --- purity ------------------------------------------------------------------


def test_module_imports_nothing_heavy() -> None:
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import guitaroids.session.play_request, sys;"
            "bad=[n for n in ('PySide6','cv2','sounddevice','mediapipe') if n in sys.modules];"
            "print(','.join(bad) or 'clean')",
        ],
        capture_output=True,
        text=True,
        cwd=str(root),
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "clean"


# --- practice tempo (§19.1) -------------------------------------------------


def test_bpm_defaults_to_as_written() -> None:
    """0 is the sentinel, not a tempo: it must not read as a rate of zero."""
    assert PlayRequest("a", 1).bpm == 0.0
    assert "as written" in PlayRequest("a", 1).describe()


def test_a_negative_bpm_is_a_bug_not_a_setting() -> None:
    with pytest.raises(ValueError, match="bpm"):
        PlayRequest("a", 1, bpm=-1.0)


def test_from_settings_uses_the_stored_tempo() -> None:
    settings = Settings()
    settings.set_bpm_for("a", 55.0)
    assert PlayRequest.from_settings("a", 1, settings).bpm == 55.0


def test_a_stored_zero_falls_back_to_the_written_tempo() -> None:
    """A 0 in the file is "as written", so the request says 0 too."""
    settings = Settings()
    settings.set_bpm_for("a", 0.0)
    assert PlayRequest.from_settings("a", 1, settings).bpm == 0.0


def test_an_explicit_bpm_overrides_the_stored_one() -> None:
    """Which is what the spin box in song select does."""
    settings = Settings()
    settings.set_bpm_for("a", 55.0)
    assert PlayRequest.from_settings("a", 1, settings, bpm=80.0).bpm == 80.0


def test_a_hand_edited_bpm_is_clamped_not_rejected() -> None:
    """The user-facing clamp lives here, as it does for the offset."""
    settings = Settings()
    assert PlayRequest.from_settings("a", 1, settings, bpm=99_999).bpm == 400.0
    assert PlayRequest.from_settings("a", 1, settings, bpm=-30).bpm == 0.0
    assert PlayRequest.from_settings("a", 1, settings, bpm="nonsense").bpm == 0.0


def test_with_offset_ms_keeps_the_tempo() -> None:
    """The copy helper is used while dragging the offset; the tempo must survive."""
    settings = Settings()
    request = PlayRequest.from_settings("a", 1, settings, bpm=64.0)
    assert request.with_offset_ms(-20.0).bpm == 64.0


def test_describe_names_the_tempo() -> None:
    request = PlayRequest.from_settings("a", 1, Settings(), bpm=64.0)
    assert "64 BPM" in request.describe()
