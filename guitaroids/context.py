"""Long-lived application state, shared by every screen.

Why this exists: ``ScreenBase`` originally gave a screen nothing but the shell, and
the shell is a navigator — ``ShellBase`` deliberately declares only ``navigate``,
``go_back`` and ``current``. So there was nowhere for a screen to get the song
library. Rather than widen the shell into a holder for everything, the shared
state lives here.

The shell owns one of these and hands it to each screen, so the context outlives
every widget. That is the same rule as §1.7's "screens never own game objects",
applied one level up: a screen may borrow the library, but popping it must not
close a library or lose a play request.

Pure enough to build in a test: give it a ``Library`` you constructed and it never
touches the filesystem.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .paths import SETTINGS_PATH, SONGS_DIR
from .session.play_request import PlayRequest
from .settings import Settings
from .songlib import Chart, Library, SongEntry, scan_library


@dataclass
class AppContext:
    """Shared, long-lived state. Mutable, because the library gets rescanned."""

    library: Library = field(default_factory=lambda: Library(root=SONGS_DIR))
    settings: Settings = field(default_factory=Settings)
    songs_dir: Path = field(default_factory=lambda: SONGS_DIR)
    play_request: PlayRequest | None = None
    settings_path: Path = field(default_factory=lambda: SETTINGS_PATH)

    # --- construction -------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        songs_dir: Path | None = None,
        settings_path: Path | None = None,
        settings: Settings | None = None,
    ) -> "AppContext":
        """Build a context from disk.

        Scans synchronously. At the measured ~0.2s per tab that is fine for a
        handful of songs and it means the app always has something to show
        immediately; the background loader refreshes it later without the UI
        ever blocking.

        A missing songs directory yields an empty library rather than raising, so
        first run is an empty state and not a crash.
        """
        root = Path(songs_dir) if songs_dir is not None else SONGS_DIR
        path = Path(settings_path) if settings_path is not None else SETTINGS_PATH
        return cls(
            library=scan_library(root),
            settings=settings if settings is not None else Settings.load(path),
            songs_dir=root,
            settings_path=path,
        )

    # --- library ------------------------------------------------------------

    def set_library(self, library: Library) -> None:
        """Replace the library wholesale — used when a rescan finishes.

        Whole-library rather than incremental because the background loader emits
        per-tab results while scanning, and a partially populated list is not a
        state worth showing. The caller can offer a progress count instead.
        """
        self.library = library

    def rescan(self) -> Library:
        """Synchronous rescan, for the rare case the background loader is not wanted."""
        library = scan_library(self.songs_dir)
        self.set_library(library)
        return library

    def entry_for(self, slug: str) -> SongEntry | None:
        return self.library.get(slug)

    # --- play ---------------------------------------------------------------

    def request_play(
        self,
        slug: str,
        track_number: int,
        *,
        offset_ms: float | None = None,
        bpm: float | None = None,
    ) -> PlayRequest:
        """Record what the player asked for, and return it.

        The screen then navigates; the game screen later resolves this into a
        chart. Storing it here rather than passing it down the navigation path
        means it survives the song-select screen being destroyed.

        ``bpm`` is the practice tempo chosen there, 0 meaning as written (§19.1).
        """
        request = PlayRequest.from_settings(
            slug, track_number, self.settings, offset_ms=offset_ms, bpm=bpm
        )
        self.play_request = request
        return request

    def clear_play_request(self) -> None:
        self.play_request = None

    def chart_for(self, request: PlayRequest | None = None) -> Chart | None:
        """Resolve a request into a playable chart, or ``None``.

        Returns ``None`` rather than raising for an unknown slug, a track that has
        gone away since the scan, or a tab that no longer parses. A game screen
        that cannot resolve its request should show an error, not crash.
        """
        request = request if request is not None else self.play_request
        if request is None:
            return None
        entry = self.entry_for(request.slug)
        if entry is None:
            return None
        return entry.chart_for(request.track_number)

    def entry_for_request(self, request: PlayRequest | None = None) -> SongEntry | None:
        request = request if request is not None else self.play_request
        if request is None:
            return None
        return self.entry_for(request.slug)

    # --- settings -----------------------------------------------------------

    def save_settings(self) -> None:
        self.settings.save(self.settings_path)
