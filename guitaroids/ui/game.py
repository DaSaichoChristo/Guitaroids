"""The game screen: a tab view, a clock, six keys, and a tally.

This is the first screen that needs a *position*, and therefore the first that needs
a clock. The clock here is a **wall clock** (``QElapsedTimer``) and that is a
deliberate limitation, not an oversight:

- It is self-consistent. The number drawn on the tab view and the number judged
  against come from the same source, so pressing a key exactly as a note crosses the
  playline scores PERFECT. That is enough to verify lane/key alignment, note
  timing, hit feedback and the whole judging path.
- It cannot tell you whether the game *feels* right. That needs the audio clock of
  DESIGN.md §1.5, with a metronome the player can hear. DESIGN.md §3.5 calls the
  click-placement test "the highest-value test in the project" and schedules it
  before the highway widget; it did not get there first, so the audio transport is
  the next milestone rather than a rewrite of this screen.

Everything below the clock is already testable without any of it: the widget is
pure render, and ``GameState`` is pure logic.

The screen owns the clock and a ``GameState``, and hands the widget a position each
frame. It owns no device, so DESIGN.md §1.7 -- screens never own game objects --
holds here for a slightly different reason than it was written for: there is no
audio stream or camera handle to leak *yet*.

Key mapping is ``1``-``6`` for lanes 0-5, chosen because it needs no legend to be
understood. It is a hard-coded table rather than a setting because there is exactly
one mapping to have.
"""

from __future__ import annotations

from collections import deque
from dataclasses import replace

from pathlib import Path
from typing import TYPE_CHECKING

from PySide6 import QtCore, QtGui, QtWidgets

from ..audio.click import add_count_in, beats_per_bar
from ..audio.mic import SAMPLE_RATE, Microphone
from ..model.chart import Chart, retime
from ..session.judge import GameState, Verdict
from ..session.result import Result
from .render_task import ChartRenderer, estimate_seconds
from .screens import Screen, ScreenBase, constrained_button, content_column, heading
from .theme import COLORS, px
from .widgets.tabview import TabView

if TYPE_CHECKING:  # pragma: no cover - types only
    from ..audio.render import RenderResult
    from ..context import AppContext

_CENTRED = QtCore.Qt.AlignmentFlag.AlignHCenter

#: Digit keys 1-6 for lanes 0-5.


#: The frame timer. 60Hz is the display; nothing here needs more, and the widget
#: draws from a position rather than integrating anything.
FRAME_MS = 16

#: How many recent played notes the timing readout summarises. The **median** of
#: this many is the player's offset. A mean would be dragged around by the one note
#: they fumbled; a median throws it away, which is the whole reason for reaching for
#: it here. 9 is enough to stop reacting to a single bad note and few enough that the
#: number still moves while a player is adjusting the trim.
TIMING_SAMPLES = 9


class Game(ScreenBase):
    """Play a chart with six keys."""

    TITLE = "game"

    def __init__(
        self,
        shell,
        context: "AppContext",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(shell, context, parent)

        self._chart: Chart | None = None
        self._state: GameState | None = None
        self._clock = QtCore.QElapsedTimer()
        self._offset = 0.0
        self._finished = False
        self._last_verdict: tuple[Verdict, float] | None = None

        #: Playback rate, fixed for the run. The practice tempo is chosen on song
        #: select and arrives in the PlayRequest (§19.1), so unlike §18 there is
        #: nothing to re-anchor: the rate never changes while the clock is running.
        self._rate = 1.0

        #: True while the chart is being turned into audio. The clock does not start
        #: until it is, because a song that starts on the wall clock and is then
        #: taken over by the audio clock jumps by however long the render took.
        self._preparing = False
        self._render_error = ""
        #: The audio position at the moment the audio stopped, so the wall clock can
        #: take over from there. ``None`` means the wall clock is not in use.
        self._wall_origin: float | None = None
        self._last_audio_position = 0.0
        self._audio_lost = False
        #: True between "the audio arrived" and "the audio stopped". The only way to
        #: tell a lost device from a machine that never had one.
        self._audio_live = False
        self._renderer = ChartRenderer(self)
        self._renderer.ready.connect(self._on_audio_ready)
        self._renderer.failed.connect(self._on_audio_failed)
        self._renderer.cancelled.connect(self._on_audio_cancelled)

        #: The input device, when the player asked for the microphone. Opened on the
        #: first run and kept for the screen's life -- opening a stream per song would
        #: put a device acquisition on the critical path of starting a run.
        self._mic: Microphone | None = None
        #: Why the microphone did not open, if it did not. Reported, not swallowed.
        self._mic_error = ""
        self._pitchDetected.connect(self._on_pitch)

        self._view = TabView(self)
        self._build_hud()

        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(FRAME_MS)
        self._timer.timeout.connect(self._tick)

        self._place_hud()
        self._load_request()

    # --- construction --------------------------------------------------------

    def _build_hud(self) -> None:
        """The score row, laid out over the top of the tab view.

        Built as children of the screen and floated by ``resizeEvent``, so the
        playfield keeps the whole window -- a rhythm game that shrinks it to
        make room for a score is worse than one with no score. Every label is
        **parented to the screen**: a parentless QLabel is a top-level window, and
        showing one opens a second window floating over the game.
        """
        self._title = heading("", kind="heading", parent=self)
        self._title.setObjectName("gameTitle")
        self._title.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)

        self._tally = heading("", kind="stat", parent=self)
        self._tally.setObjectName("gameTally")
        self._tally.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        # The player's timing, in one number. Exists because "MISS, 0%" cannot be
        # acted on: it looks identical whether you are 180ms late or 1800ms early,
        # and those need opposite corrections. §39.
        self._timing = heading("", kind="stat", parent=self)
        self._timing.setObjectName("gameTiming")
        self._timing.setAlignment(QtCore.Qt.AlignmentFlag.AlignRight)

        self._flash = heading("", kind="heading", parent=self)
        self._flash.setObjectName("gameFlash")
        self._flash.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

        self._banner = heading("", kind="subtitle", parent=self)
        self._banner.setObjectName("gameBanner")
        self._banner.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._banner.setWordWrap(True)

        # There is deliberately no tempo control here. It was on this screen in §18
        # and moved to song select in §19.1: the tempo is a choice about the *next*
        # attempt, made alongside the track and the alignment, and a control you have
        # to reach by leaving the song is a control for next time -- which is what
        # practice tempo is actually for.
        self._quit = constrained_button("Back to menu", width=200, parent=self)
        self._quit.clicked.connect(self._leave)
        # Deliberately no setFocus here. The shell focuses the screen after building it,
        # and Escape already navigates back through ScreenBase, so the button does not
        # need the keyboard either. Nothing on this screen reads a key now: the input
        # is a microphone (§32).

    def _place_hud(self) -> None:
        """Float the HUD, and make the tab view fill the window.

        Manual geometry rather than a layout, because the HUD is an overlay: the
        view is the screen, and a QVBoxLayout holding both would shrink the
        playfield to make room for a score.

        Called from ``resizeEvent`` *and* once at the end of ``__init__``, because a
        widget that has never been resized has not received a ``resizeEvent`` and
        the view would otherwise sit at its default 640x480 on first show.
        """
        self._view.setGeometry(self.rect())
        width, height = self.width(), self.height()
        pad = px(20)
        self._title.setGeometry(pad, px(12), width // 2, px(40))
        self._tally.setGeometry(width // 2, px(12), width // 2 - pad, px(40))
        # Under the tally, not beside it: the tally is already the full half-width
        # and is monospaced, so anything appended to it would break its columns.
        self._timing.setGeometry(width // 2, px(12) + px(40), width // 2 - pad, px(30))
        self._flash.setGeometry(0, height // 5, width, px(80))
        self._banner.setGeometry(pad, height // 2 - px(40), width - pad * 2, px(80))
        # The bottom row holds Back and nothing else. The key legend used to sit here
        # and went when the string names moved onto the cords (§18.1); the tempo
        # control sat here until §19.1 moved it to song select.
        row_y = height - px(34) - px(28)
        self._quit.setGeometry(pad, row_y, px(200), px(28))

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._place_hud()

    # --- loading -------------------------------------------------------------

    def _load_request(self) -> None:
        """Resolve the context's play request into a chart and start a run.

        A missing request, or one whose chart will not resolve, is an **empty
        state**, not a crash: ``context.chart_for()`` returns ``None`` by design and
        the player has a way back.
        """
        chart = self.context.chart_for()
        request = self.context.play_request
        if chart is None or not chart.notes:
            self._chart = None
            self._state = None
            self._view.set_chart(None)
            self._title.setText("")
            self._tally.setText("")
            self._banner.setText(
                "No song selected.\n\nGo back and choose a song to play."
            )
            self._flash.setText("")
            return

        # The rate is resolved from the tab's *written* tempo, so the chart has to be
        # in place first -- and the rate has to be resolved *before* the chart is
        # retimed. Both orderings are load-bearing: ask a chart already scaled to 0.5
        # for a half-speed request and it reports 1.0, and three existing tempo tests
        # did exactly that until they were run. 0.0 in the request means "as written",
        # which is a rate of 1.0.
        self._chart = chart
        self._rate = self.rate_for(request.bpm if request else 0.0)
        # One insertion point for the practice tempo. The chart is slowed here rather
        # than the clock being divided, so the *rendered audio* is slower too: the
        # music, the bar and the judge all speak the same slowed time. Until §29 this
        # did nothing on the audio path, which is the path a playing song takes -- the
        # rate was applied only to the wall-clock fallback, so a practice tempo
        # changed nothing at all.
        chart = retime(chart, self._rate)

        self._chart = chart
        self._state = GameState(chart)
        self._offset = float(request.offset_seconds) if request else 0.0
        self._finished = False
        #: Set once a result has been handed to the results screen, so the end of a
        #: song navigates exactly once however many frames it takes to notice.
        self._result_published = False
        self._last_verdict = None
        #: Recent *played* notes' signed timing errors, newest last. Only pressed
        #: judgements: an expired note's delta is the clock's overshoot, not the
        #: player's, and mixing the two in would bias the answer toward +140ms.
        self._recent_timing: deque[float] = deque(maxlen=TIMING_SAMPLES)
        self._refresh_timing()
        self._view.set_chart(chart)
        self._title.setText(chart.title or chart.track_name)
        self._banner.setText("")
        self._flash.setText("")
        self._start_microphone()
        self._render_audio(chart, request)
        self._timer.start()
        self._refresh_tally()

    def _render_audio(self, chart: Chart, request) -> None:
        """Start turning the chart into audio, off the GUI thread.

        The clock does not start here. It starts when the audio is ready, in
        :meth:`_on_audio_ready`, because a song that begins on the wall clock and is
        then handed to the audio clock jumps by however long the render took -- and
        it jumps forwards, which is the direction that makes a rhythm game feel
        broken rather than merely wrong.
        """
        self.context.stop_playback()
        self._render_error = ""
        if not self.context.audio_enabled:
            # Playing silently is a supported state, not a degraded one. The song
            # runs on the wall clock, exactly as it always did.
            self._start_timer_clock()
            return
        if not self._renderer.start(chart):
            # A render is already in flight, which can only happen if the player
            # re-entered the screen faster than a seven-second render. Fall back to
            # the wall clock rather than refusing to play the song at all.
            self._start_timer_clock()
            return
        self._preparing = True
        self._show_preparing(chart)

    def _show_preparing(self, chart: Chart) -> None:
        """Say what is happening, and roughly how long.

        A frozen screen with no explanation reads as a hang, and this genuinely
        takes seconds -- a five-minute tab is 130MB of float32 stereo.
        """
        seconds = estimate_seconds(chart)
        self._banner.setText(
            f"Preparing audio...\n\nAbout {seconds:.0f}s. "
            "Rendering the tab into sound."
        )

    def _note_audio_lost(self) -> None:
        """Notice that the sound stopped. The banner text is set at the end of the tick.

        Split in two because the banner is one label with several writers: the
        "Get ready" and "Song complete" messages are refreshed after this, and a
        notice set here would be overwritten by them on the very same frame.
        """
        if self._wall_origin is not None or self._finished:
            return  # already noted, or the song is over and it does not matter
        self._audio_lost = True

    def _show_audio_lost(self) -> None:
        """Written last in the tick, so nothing overwrites it."""
        if not self._audio_lost:
            return
        self._banner.setText(
            "Audio stopped.\n\nThe song keeps going on a wall clock, which cannot "
            "tell whether the timing feels right."
        )

    def _start_timer_clock(self) -> None:
        """The no-audio fallback: a wall clock, which cannot say whether it feels right.

        Used when there is no device, no soundfont, or the render failed. §1.5 says
        never drive note timing from a GUI timer, and the honest reading of that is
        "unless there is no audio to drive it from" -- a game that will not start
        without a sound card is worse than one that starts slightly wrong.
        """
        self._preparing = False
        self._clock.start()
        # The wall clock is the *primary* clock in this path, so its origin is the
        # chart's own zero, which the offset moves: the song starts `offset` seconds
        # behind the audio, not at zero.
        self._wall_origin = -self._offset
        self._last_audio_position = 0.0
        self._banner.setText("")

    # --- the render, arriving -------------------------------------------------

    def _on_audio_ready(self, result: "RenderResult") -> None:
        """The audio exists. Count it in, open the stream, start the audio clock."""
        if self._chart is None:
            return
        if not self.isVisible():
            # The render finished after the player left. Opening a stream now would
            # start the song on a screen nobody is looking at -- the audible half of
            # the bug `hideEvent` fixes, for the narrow case where the render landed
            # in the event queue between the cancel and this slot running.
            self.context.stop_playback()
            return
        count_in = int(self.context.settings.count_in_bars)
        settings = self.context.settings
        mixed, _clicks = add_count_in(
            result.samples,
            bpm=float(self._chart.tempo),
            count_in_bars=count_in,
            beats_per_bar=beats_per_bar(self._chart),
            sample_rate=result.sample_rate,
            # The click is mixed here, before the transport sees the buffer, which is
            # the only moment the two volumes are still separable. `click_volume` is
            # documented as the metronome relative to the master, so it replaces the
            # 0.6 this would otherwise default to.
            level=float(settings.click_volume),
        )
        try:
            self.context.start_playback(
                mixed,
                sample_rate=result.sample_rate,
                volume=float(settings.master_volume),
                device=settings.audio_device,
                song_start=self._count_in_seconds(),
                offset=self._offset,
            )
        except Exception as exc:  # noqa: BLE001 - any device failure falls back
            # No output device, or PortAudio refused it. The song is still playable
            # on the wall clock, so say what happened and play it.
            self._render_error = str(exc)
            self._start_timer_clock()
            return
        self._preparing = False
        self._audio_lost = False
        self._audio_live = True
        self._banner.setText("")
        # Started here even though the audio clock is what will be read: a wall clock
        # that has never been started cannot take over if the audio goes away, and
        # the recovery path is exactly the case where a frozen bar is worst.
        self._clock.start()
        self._wall_origin = None
        self._last_audio_position = 0.0

    def _count_in_seconds(self) -> float:
        """Seconds between sample zero and the chart's time zero.

        Must agree exactly with what :func:`add_count_in` reserved, or the first
        note arrives offset from the music by the difference.
        """
        from ..audio.click import count_in_seconds as _seconds

        if self._chart is None:
            return 0.0
        return _seconds(
            bpm=float(self._chart.tempo),
            count_in_bars=int(self.context.settings.count_in_bars),
            beats_per_bar=beats_per_bar(self._chart),
        )

    def _on_audio_failed(self, message: str) -> None:
        """The audio could not be made. Play the song anyway, and say why not."""
        self._render_error = message
        self._start_timer_clock()

    def _on_audio_cancelled(self) -> None:
        """The player moved on. Nothing to show and nothing to start."""
        self._preparing = False

    # --- the clock -----------------------------------------------------------

    def position(self) -> float:
        """Chart position, in seconds. The one number everything else reads.

        **The audio device's clock when there is one, the wall clock when there is
        not.** §1.5 is explicit that note timing must come from
        ``(stream.time - t0) - stream.latency`` and never from a GUI timer, because
        a timer is self-consistent and cannot say whether the game feels right. The
        wall clock survives as a fallback for a machine with no output device --
        starting late on such a machine is better than not starting.

        The rate is read once, at load (§19.1): the practice tempo is chosen on song
        select and travels in the PlayRequest, so there is no origin to move and no
        re-anchor. Everything downstream works in chart time, which is why the rate
        is a single multiplication here rather than a rescale of the chart.

        Returns ``-1.0`` while the audio is still being rendered, so nothing
        downstream can mistake "not started" for "at the first note".

        **Falls back to the wall clock mid-song, and says so.** The audio device can
        go away while a song is playing, and there are two wrong answers: freeze
        (the bar stops and it looks like a hang) or restart from zero (the song jumps
        back to the top). So the last audio position is remembered and the wall clock
        carries on from there.
        """
        if self._preparing:
            return -1.0
        # Liveness, not the *sign* of the position. A position is legitimately
        # negative for the whole count-in and for the first `latency` of any song,
        # and -1.0 is also what the transport says when it is not playing, so testing
        # `audio >= 0` confuses "before the music starts" with "there is no music" --
        # which handed the song to the wall clock for its first 46ms every time.
        if self.context.is_playing:
            audio = self.context.song_position()
            self._wall_origin = None
            self._last_audio_position = audio
            return audio
        if self._wall_origin is None:
            # The audio has stopped without telling us -- an unplugged interface, a
            # device that stalled, a stream that failed. Freeze here and the bar
            # simply stops moving, which is the worst thing this screen can do: it
            # looks like a hang rather than like a song with no sound. So the wall
            # clock takes over *from where the audio left off*.
            #
            # The origin is derived from the last audio position, not set to it: the
            # wall clock has been running since the audio started, so its `elapsed`
            # is not zero here. `origin + elapsed * rate` then reproduces the audio
            # position exactly at the moment of the switch, which is the same re-anchor
            # shape §18.4 needed and the reason the song does not jump.
            now = self._clock.elapsed() / 1000.0 * self._rate
            self._wall_origin = self._last_audio_position - now
        return self._wall_origin + self._clock.elapsed() / 1000.0 * self._rate

    @property
    def rate(self) -> float:
        """The playback rate in force, for the HUD and for tests."""
        return self._rate

    def written_bpm(self) -> int:
        """The tempo the tab is written at, or 0 when it has no chart."""
        return self._chart.tempo if self._chart is not None else 0

    def rate_for(self, bpm: float) -> float:
        """The playback rate for a requested tempo.

        Never above 1.0: a tab cannot be practised *faster* than it is written
        without breaking the music, and "slower, for beginners" is the whole point of
        the control. A chart with no usable tempo plays at 1.0 rather than raising --
        a missing tempo should not stop a song being playable. 0.0 means the request
        asked for the written tempo, which is 1.0.
        """
        written = self.written_bpm()
        if written <= 0 or bpm <= 0:
            return 1.0
        return min(1.0, bpm / written)

    def _tick(self) -> None:
        # Notice the audio *transitioning* from playing to not, before anything
        # reads a position, so the frame it happens is the frame the player is told.
        # A transition, not a state: `context.playback` is None both before the audio
        # starts and after it stops, so testing it says nothing. A flag set when the
        # audio arrived is what distinguishes "never had audio" from "lost it".
        #
        # **This runs whether or not the song is finished**, which it did not until
        # §34. The guard was `not self._finished`, and that is exactly the moment the
        # results screen cares about: the audio does not stop when the last note is
        # judged, it stops twelve seconds later when the render's decay runs out, and
        # the transition that announces the song is over is the one this guard was
        # hiding. `_maybe_publish` decides what that transition means -- a lost device
        # mid-song, or a finished song reaching its end.
        audio_ended = self._audio_live and not self.context.is_playing
        if audio_ended:
            self._audio_live = False
            if not self._finished:
                self._note_audio_lost()
        position = self.position()
        if self._state is not None:
            self._state.update(position)
        self._view.set_position(position)
        self._refresh_tally()
        if self._state is not None and not self._finished:
            if self._state.outstanding == 0:
                self._finished = True
        self._refresh_banner(position)
        self._show_audio_lost()
        # Unconditional, and that was not the first draft: this was called only when
        # the audio had just stopped, which meant a run on the **wall clock** never
        # published at all -- there is no "ended" transition when the audio never
        # started, and `audio_ended` is False forever. A test caught it. Every
        # condition that matters is re-checked inside.
        self._maybe_publish()

    def _maybe_publish(self) -> None:
        """Hand the finished attempt to the results screen, once, and go there.

        **Only when the sound has stopped.** The last note of Hotel California is at
        380.5s and its rendered buffer runs to 392.5s, so "every note is judged" is
        about twelve seconds before the music is over. Leaving on the judgement would
        cut the tail off, and the player would hear a song stop mid-phrase because a
        counter reached zero.

        **And immediately when there is no audio at all.** A machine with no output
        device runs the wall clock, where there is no tail and no sound to wait for --
        so `outstanding == 0` is itself the end of the song. The caller has already
        established the song is finished before reaching here.

        `remember=False` is what makes Back land on song select rather than on this
        screen: song select is already on the history stack from the way in, and
        pushing GAME would put the finished attempt behind the results for it.
        """
        if self._state is None or self._result_published:
            return
        if not self._finished:
            return
        if self.context.is_playing:
            return  # still sounding: the tail is not over
        self._result_published = True
        self.context.last_result = self._build_result()
        self.shell.navigate(Screen.RESULTS, remember=False)

    def _build_result(self) -> "Result":
        """The finished attempt, with the best-score comparison already made.

        Built, then recorded, then rebuilt with the comparison filled in -- because the
        accuracy to record is the result's, and the answer is only knowable after
        recording. Computing the ratio here instead would put the accuracy formula in
        two places, and the two would drift.

        The song's *written* tempo is reported when the run was at the written tempo,
        because "played at 76 BPM" and "played at 0 BPM" are not the same statement to
        somebody comparing two attempts.
        """
        request = self.context.play_request
        slug = request.slug if request else ""
        title = self._chart.title if self._chart else ""
        track_name = self._chart.track_name if self._chart else ""
        result = Result.from_state(
            self._state,
            title=title,
            artist=getattr(self._chart, "artist", "") if self._chart else "",
            track_name=track_name,
            slug=slug,
            track_number=request.track_number if request else 0,
            bpm=self._played_bpm(),
            rate=self._rate,
        )
        is_new_best, previous = self.context.settings.record_accuracy_for(
            slug, result.accuracy
        )
        return replace(result, is_new_best=is_new_best, previous_best=previous)

    def _played_bpm(self) -> float:
        """The tempo this run was actually played at, in BPM, or 0.0 for "as written".

        **The retimed chart's own tempo, with no further multiplication.** §29.2 already
        scaled `chart.tempo` by the rate when it retimed the chart, so a 60 BPM tab at
        0.75 *is* a 45 BPM chart -- and multiplying again reported 33.75, which is a
        tempo nobody played. A test at rate 1.0 could not see it, because 60 x 1.0 is
        60.

        **0.0 when the rate is 1.0**, so the results screen can say "as written". A run
        at the tab's own tempo is not a practice tempo, and printing "practice tempo
        60 BPM" for a song written at 60 BPM would be a small lie in the one line a
        player uses to compare two attempts.
        """
        if self._chart is None or self._chart.tempo <= 0:
            return 0.0
        if self._rate >= 1.0:
            return 0.0
        return float(self._chart.tempo)

    def _refresh_banner(self, position: float) -> None:
        """Three states, one label: empty library, get ready, complete.

        The get-ready state exists because the real tab's first note is 3.16s in.
        Three seconds of empty notation looks like a broken game rather than like a
        song about to start, and there is nothing else on screen to explain it.
        """
        if self._chart is None or self._state is None:
            return
        if self._finished:
            self._banner.setText("Song complete.")
            return
        first = self._chart.notes[0].time
        if position < first and self._state.outstanding == self._state.note_count:
            self._banner.setText("Get ready")
        elif self._banner.text() == "Get ready":
            self._banner.setText("")

    # --- input ---------------------------------------------------------------

    # --- the microphone -------------------------------------------------------

    #: Emitted on the GUI thread by the worker, carrying an integer MIDI note.
    #: A signal rather than a direct call because the estimate is produced on the
    #: microphone's thread, and Qt's queued connection is what puts it back here.
    _pitchDetected = QtCore.Signal(int)

    def _start_microphone(self) -> None:
        """Open the input device, if the player asked for the microphone.

        A device that will not open is **not** an error the player is shown: the
        keyboard is still there (§24.4) and a game that cannot listen is still a game
        you can play. The failure is recorded so it can be reported, and the run
        continues.

        Nothing here is a fallback path that was never designed: §23 established that a
        silent device should be announced rather than papered over, and the same applies
        to a silent *input*.
        """
        if self._mic is not None:
            return
        settings = self.context.settings
        self._mic = Microphone(
            device=settings.input_device,
            sample_rate=SAMPLE_RATE,
            on_pitch=self._on_estimate,
        )
        try:
            self._mic.start()
        except Exception as exc:  # noqa: BLE001 - any device failure keeps the game
            self._mic = None
            self._mic_error = str(exc)

    def _on_estimate(self, estimate) -> None:  # noqa: ANN001 - PitchEstimate
        """Called on the microphone's worker thread. Emit, and get off it."""
        self._pitchDetected.emit(int(estimate.nearest_midi()))

    def _on_pitch(self, midi: int) -> None:
        """Judge a detected note. The microphone's way in, beside :meth:`_press`.

        **The position is corrected for the analysis latency before judging.** An
        estimate describes the middle of its 2048-sample window, so it is ~23ms behind
        the moment it was emitted, before PortAudio's own buffering. Uncorrected, that
        is a systematic lateness on every note -- a third of the Perfect window, and
        enough to walk a good player into MISS. `Settings.input_latency_ms` is the
        manual trim on top of it, and it is the first thing this setting has ever been
        read for.
        """
        if self._state is None or self._finished:
            return
        when = self.position() - self._input_latency()
        judgement = self._state.press_pitch(midi, when)
        if judgement is None:
            # Either a note still sounding, or a press outside the window -- and those
            # two need different handling. The readout has to be told about the
            # second, because `press_pitch` is silent out there by design: the bigger
            # the player's error, the less a judgement-based readout could see of it.
            self._note_timing(midi, when)
            return
        self._last_verdict = (judgement.verdict, judgement.delta_seconds)
        self._flash.setText(_verdict_text(judgement))
        self._note_timing(midi, when, delta=judgement.delta_seconds)

    def _note_timing(
        self, pitch: int, position: float, *, delta: float | None = None
    ) -> None:
        """Record one observation of the player's timing.

        Prefers the judgement's own delta, and falls back to asking the judge how far
        the nearest unjudged note of this pitch was -- so a press that resolved
        nothing is still measured. See :meth:`GameState.timing_delta` for why the
        window cannot be the limit on what is visible.
        """
        if delta is None:
            delta = self._state.timing_delta(pitch, position)
        if delta is None:
            return
        self._recent_timing.append(delta)
        self._refresh_timing()

    def _refresh_timing(self) -> None:
        """Say how far off the player's playing is, in the direction that helps.

        The median of the last few *played* notes, as a signed number with the
        direction spelled out. "180ms late" is actionable -- it is the value to put
        in the input-latency trim -- and it is the difference between a player
        converging on a correct setting in a handful of notes and one who cannot
        tell a small lateness from a large earliness, because the tally shows
        "MISS 0%" for both.
        """
        if not self._recent_timing:
            self._timing.setText("")
            return
        ordered = sorted(self._recent_timing)
        middle = len(ordered) // 2
        if len(ordered) % 2:
            median = ordered[middle]
        else:
            median = (ordered[middle - 1] + ordered[middle]) / 2.0
        milliseconds = median * 1000.0
        if abs(milliseconds) < 5.0:
            self._timing.setText("timing  on time")
        elif milliseconds > 0:
            self._timing.setText(f"timing  {milliseconds:.0f}ms late")
        else:
            self._timing.setText(f"timing  {-milliseconds:.0f}ms early")

    def _input_latency(self) -> float:
        """Seconds to subtract from every detected note's position."""
        detector = self._mic.detector.latency_seconds if self._mic else 0.0
        return detector + self.context.settings.input_latency_ms / 1000.0

    def _refresh_tally(self) -> None:
        if self._state is None:
            return
        tally = self._state.counts()
        self._tally.setText(
            f"{tally[Verdict.PERFECT]} perfect   "
            f"{tally[Verdict.GOOD]} good   "
            f"{tally[Verdict.MISS]} miss   "
            f"{self._state.accuracy * 100:.0f}%"
        )

    # --- lifecycle ------------------------------------------------------------

    def _leave(self) -> None:
        self._stop()
        self.shell.go_back()

    def _stop(self) -> None:
        self._timer.stop()
        self._clock.invalidate()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:  # noqa: N802 - Qt naming
        """Stop everything this screen is responsible for when navigated away from.

        A QTimer left running would keep calling ``_tick`` -- and keep mutating the
        tally -- on a screen the player is no longer looking at. The filter has to go
        with it or the keys keep driving the game from the menu.

        **The audio stream has to stop here too, and did not.** The song carried on
        playing into the main menu, because the only `stop_playback()` in the UI was in
        `_render_audio`, which runs when a *new* song starts. Nothing stopped what was
        already playing. `Transport.stop()` closes PortAudio's stream and waits for it
        to stop; there is no join and no other thread, so it is safe on the GUI thread.

        **And the render is cancelled**, which is the worse half. `ChartRenderer` has
        had a `cancel()` since it was written, and `should_stop` is polled at every
        onset -- but nothing called it. So leaving during a six-second render let it
        finish, and `_on_audio_ready` started the song *in the menu*. Cancelling here
        closes that window; the visibility check in `_on_audio_ready` closes the rest
        of it, for a render that finished in the moment between the two.
        """
        self._stop()
        self.context.stop_playback()
        self._renderer.cancel()
        self._audio_live = False
        if self._mic is not None:
            # The input device too, for the same reason as the output: a stream left
            # open on a screen nobody is looking at is a device held for nothing.
            self._mic.stop()
        super().hideEvent(event)

    def showEvent(self, event: QtGui.QShowEvent) -> None:  # noqa: N802 - Qt naming
        """Install the key filter, and reload on every visit.

        The shell keeps built screens, so this is not constructed afresh the second
        time. Re-entering GAME therefore needs an explicit reload, or the player
        would resume a finished song.
        """
        super().showEvent(event)
        self.setFocus()
        if self._chart is not None and not self._timer.isActive():
            self._load_request()

    # --- accessors for tests --------------------------------------------------

    @property
    def view(self) -> TabView:
        return self._view

    @property
    def state(self) -> GameState | None:
        return self._state

    @property
    def chart(self) -> Chart | None:
        return self._chart


def _verdict_text(judgement) -> str:  # noqa: ANN001 - Judgement
    """The flash shown when a note resolves.

    **A played note carries its signed error**, because a bare "MISS" says nothing
    about what to change. `delta_seconds` is positive when the note was late, so the
    wording is derived from the sign rather than from the magnitude: a player who is
    consistently 200ms late needs a *larger* input-latency trim, and one who is 200ms
    early needs a smaller one -- possibly a negative one -- and "MISS" does not
    distinguish them.

    A note that expired with no press shows no error, because there is no playing to
    report: its delta is how far past the window the clock had moved, not an
    attempt. See `Judgement.pressed`.
    """
    if judgement.verdict is Verdict.STRAY:
        return ""
    if not judgement.pressed:
        return "MISS"
    milliseconds = judgement.delta_seconds * 1000.0
    if judgement.verdict is Verdict.PERFECT and abs(milliseconds) < 5.0:
        return "PERFECT"
    if abs(milliseconds) < 5.0:
        name = "PERFECT" if judgement.verdict is Verdict.PERFECT else "GOOD"
        return name
    direction = "late" if milliseconds > 0 else "early"
    return f"{judgement.verdict.value.upper()}  {abs(milliseconds):.0f}ms {direction}"
