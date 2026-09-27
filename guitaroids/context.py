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

**It also owns the audio transport**, which is the one device handle in the app.
§1.7 says screens never own game objects, precisely so that popping one cannot leak
an audio stream -- and a rule you satisfy by leaking it somewhere else is not a rule
satisfied. The shell holds one context for the whole session, so the handle lives
here and is closed when a new song starts or the app exits.

The context stays **Qt-free**, which is the constraint that is actually tested: the
transport is numpy and sounddevice, and sounddevice is imported inside
``audio.transport`` rather than here, so importing this module still pulls in no
Qt and no audio. What the context stores is a pre-rendered numpy array and an
optional ``Transport``; the expensive part -- turning a chart into samples -- happens
on a worker thread, because rendering a six-minute tab takes about seven seconds and
the GUI thread may not block (§1.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .audio.transport import Transport
from .paths import SETTINGS_PATH, SONGS_DIR
from .session.play_request import PlayRequest
from .session.result import Result
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

    #: The most recent finished attempt, for `Screen.RESULTS` to show.
    #:
    #: Here rather than passed to `navigate()`, which takes no payload, and because the
    #: shell keeps built screens -- a results screen constructed on the second visit
    #: has to find the second song's result, and a constructor argument would have
    #: been the first one. ``None`` until a song has been played, which the results
    #: screen renders as an empty state rather than as zeroes.
    last_result: "Result | None" = field(default=None, repr=False, compare=False)

    #: The open output stream, if one is playing. Not a dataclass field: it is a
    #: device handle with a destructor, so it must be excluded from repr and
    #: equality, and only ever replaced through :meth:`start_playback`.
    playback: "Transport | None" = field(default=None, repr=False, compare=False)

    #: Whether to render and play audio at all.
    #:
    #: A user-facing switch, because playing silently has to be possible: a machine
    #: with no output device, headphones unplugged, or a player who just wants the
    #: tab. It is also what the test suite turns off, and that is a second use worth
    #: being explicit about -- a screen that renders a chart on construction would
    #: make every widget test pay for a background render, and would leave the timer
    #: and audio clocks racing in the tests rather than one of them.
    audio_enabled: bool = True

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

        The settings are loaded **before** the scan, not after, so the first
        library is built with the player's chord preference rather than with a
        default that then quietly disagrees with it. This was the same bug one
        layer down from §21's: a scan that did not know what the player asked
        for.
        """
        root = Path(songs_dir) if songs_dir is not None else SONGS_DIR
        path = Path(settings_path) if settings_path is not None else SETTINGS_PATH
        loaded = settings if settings is not None else Settings.load(path)
        return cls(
            library=scan_library(root, collapse=loaded.collapse_chords),
            settings=loaded,
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

    # --- playback ------------------------------------------------------------

    def start_playback(
        self,
        samples,
        *,
        sample_rate: int,
        volume: float,
        device: str | None,
        song_start: float = 0.0,
        offset: float = 0.0,
    ) -> "Transport":
        """Open an output stream for ``samples`` and start it. Returns the transport.

        Takes **already-rendered audio**, not a chart, on purpose: rendering is
        several seconds of CPU and must not happen here, on whatever thread is
        holding a button. The caller renders on a worker and calls this with the
        result.

        Any stream already open is closed first, so starting a second song cannot
        leave the first one playing underneath it -- which is not a subtle bug, it
        is two songs at once.

        ``volume`` and ``device`` are **required and have no default**, both of them
        read from `Settings` by the caller. That is §21.2's prescription applied to a
        parameter: `device` existed here, defaulted to None, and was omitted at every
        call site, so the default silently won and `Settings.audio_device` did nothing
        while looking exactly like a setting that worked. A required argument cannot
        be forgotten quietly.
        """
        from .audio.transport import Transport

        self.stop_playback()
        transport = Transport(
            samples,
            sample_rate=sample_rate,
            volume=volume,
            song_start=song_start,
            offset=offset,
            device=device,
        )
        transport.play()
        self.playback = transport
        return transport

    def stop_playback(self) -> None:
        """Close the output stream, if one is open. Safe to call twice."""
        if self.playback is None:
            return
        try:
            self.playback.stop()
        finally:
            self.playback = None

    def song_position(self) -> float:
        """Seconds into the chart, or ``-1.0`` when nothing is playing.

        The one number the game screen reads for its clock. -1.0 rather than 0.0 so
        "not playing" cannot be mistaken for "at the first note" -- and for the real
        tab the first note is three seconds in, so the two are visibly different.

        A caller that wants a usable position either way (the game's timer fallback,
        tests) should not use this; it is the audio clock's answer, including when
        the answer is "there isn't one".
        """
        if self.playback is None:
            return -1.0
        return self.playback.position()

    @property
    def is_playing(self) -> bool:
        return self.playback is not None and self.playback.is_running

    def chart_for(self, request: PlayRequest | None = None) -> Chart | None:
        """Resolve a request into a playable chart, or ``None``.

        Returns ``None`` rather than raising for an unknown slug, a track that has
        gone away since the scan, or a tab that no longer parses. A game screen
        that cannot resolve its request should show an error, not crash.

        The chord setting comes from the **request**, not from the library (§21).
        The request is frozen per attempt, so unticking "Collapse chords" in
        Preferences changes the next song without touching a song that is already
        playing -- and without a rescan, which is what the setting used to need.
        """
        request = request if request is not None else self.play_request
        if request is None:
            return None
        entry = self.entry_for(request.slug)
        if entry is None:
            return None
        return entry.chart_for(
            request.track_number, collapse=request.collapse_chords
        )

    def entry_for_request(self, request: PlayRequest | None = None) -> SongEntry | None:
        request = request if request is not None else self.play_request
        if request is None:
            return None
        return self.entry_for(request.slug)

    # --- settings -----------------------------------------------------------

    def save_settings(self) -> None:
        self.settings.save(self.settings_path)
