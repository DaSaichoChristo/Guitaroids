"""What the player asked for when they pressed Play.

Deliberately tiny and frozen. A PlayRequest is an *intent* — "this tab, that
track, aligned like this" — and nothing more. It is not a game session and it owns
no resources, which is the whole point: screens are torn down and rebuilt as the
player navigates, and anything long-lived must survive that without travelling
through a widget (DESIGN.md §1.7).

The session, once it exists, turns one of these into an actual playable chart.

Kept free of Qt, audio and files so it can be constructed in a test and compared by
value.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Matched to settings.count_in_bars; a count-in outside this is not a thing.
MIN_COUNT_IN_BARS = 0
MAX_COUNT_IN_BARS = 2

#: Beyond this the audio and the tab are not the same performance.
MAX_OFFSET_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class PlayRequest:
    """One attempt at one song."""

    slug: str
    """Identifies the tab, as ``SongEntry.slug`` — the file stem."""

    track_number: int
    """1-based track within the tab, as ``TrackInfo.number``."""

    offset_seconds: float = 0.0
    """Manual audio alignment. Positive means the audio is *ahead* of the tab."""

    count_in_bars: int = 1
    """0-2 bars of clicks before the music starts."""

    collapse_chords: bool = True
    """One note per onset, or every note. Mirrors the setting, frozen per attempt
    so changing the preference mid-song cannot alter a running session."""

    def __post_init__(self) -> None:
        # Validate rather than silently coerce. Every value here comes from our own
        # code or from a control with a bounded range, so an out-of-range value is a
        # bug and should say so. The *user-facing* clamping lives in from_settings,
        # where a slider or a hand-edited settings file can actually produce one.
        if not self.slug:
            raise ValueError("PlayRequest.slug must not be empty")
        if self.track_number < 1:
            raise ValueError(f"track_number must be 1 or more, got {self.track_number}")
        if not MIN_COUNT_IN_BARS <= self.count_in_bars <= MAX_COUNT_IN_BARS:
            raise ValueError(
                f"count_in_bars must be {MIN_COUNT_IN_BARS}-{MAX_COUNT_IN_BARS}, "
                f"got {self.count_in_bars}"
            )
        if abs(self.offset_seconds) > MAX_OFFSET_SECONDS:
            raise ValueError(
                f"offset_seconds must be within +/-{MAX_OFFSET_SECONDS}s, "
                f"got {self.offset_seconds}"
            )

    # --- construction -------------------------------------------------------

    @classmethod
    def from_settings(
        cls,
        slug: str,
        track_number: int,
        settings,
        *,
        offset_ms: float | None = None,
    ) -> "PlayRequest":
        """Build a request from the user's preferences.

        ``offset_ms`` overrides the remembered per-song offset for this attempt,
        which is what dragging the slider in song select does. Omit it and the
        stored value for that slug is used, so a tweak persists.

        Clamps here rather than in ``__post_init__``, because these values come
        from a slider and from a hand-editable settings file, both of which can
        legitimately be out of range. Nothing is written back: a clamped value
        affects only this request, not the live settings.
        """
        count_in_bars = _clamp(
            settings.count_in_bars, MIN_COUNT_IN_BARS, MAX_COUNT_IN_BARS
        )
        raw_offset = settings.offset_for(slug) if offset_ms is None else offset_ms
        offset_seconds = _clamp(float(raw_offset), -1000.0, 1000.0) / 1000.0

        return cls(
            slug=slug,
            track_number=track_number,
            offset_seconds=offset_seconds,
            count_in_bars=count_in_bars,
            collapse_chords=bool(settings.collapse_chords),
        )

    # --- helpers ------------------------------------------------------------

    def with_offset_ms(self, offset_ms: float) -> "PlayRequest":
        """A copy with a different alignment, for live dragging in song select."""
        return PlayRequest(
            slug=self.slug,
            track_number=self.track_number,
            offset_seconds=_clamp(float(offset_ms), -1000.0, 1000.0) / 1000.0,
            count_in_bars=self.count_in_bars,
            collapse_chords=self.collapse_chords,
        )

    def describe(self) -> str:
        """One line, for a status bar or a log."""
        sign = "+" if self.offset_seconds >= 0 else ""
        return (
            f"{self.slug} track {self.track_number}, "
            f"offset {sign}{self.offset_seconds * 1000:.0f}ms, "
            f"count-in {self.count_in_bars} bar(s), "
            f"chords {'collapsed' if self.collapse_chords else 'full'}"
        )


def _clamp(value: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return max(low, min(high, number))
