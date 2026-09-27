# DECISIONS.md — what we decided

The one-screen answer to *"what is the design?"* Full reasoning, measurements and
dated work entries live in [`DESIGN.md`](DESIGN.md); every `§` reference points
there so any row can be traced to the evidence behind it.

| Status | Meaning |
|---|---|
| **live** | decided and implemented |
| **planned** | decided, not yet built |
| **assumed** | believed true, **not** verified against reality — read the note |

## How this file relates to `DESIGN.md`

The two have deliberately **opposite** policies:

- `DESIGN.md` is **append-only**. New work adds a numbered, dated section; earlier
  sections are never edited, and a later section that contradicts an earlier one
  supersedes it by number.
- `DECISIONS.md` is **edited in place and always current**. Rows change as
  decisions get built or corrected.

So `DECISIONS.md` only ever summarises, and `DESIGN.md` only ever adds. Neither
should ever be cited as the authority for the other: if the two disagree, this file
is describing something `DESIGN.md` has moved past, and the fix is to update this
file. **Rows flip `planned` → `live` as milestones land**, so this needs a
deliberate refresh at the end of each one.

Anything decided *not* to do — rejected libraries, superseded reasoning, the
open questions — is deliberately **not** here. That all lives in `DESIGN.md`, which
is where the "why" belongs.

**What keeps this file honest:** `tests/test_docs.py` checks the rows above against
the repository, not against anyone's memory. Counts, `§N` references resolving to
real sections, file paths that exist, and the superseded claims staying superseded.
A row that goes stale fails a test rather than being found six months later. Rows
marked `planned` are **not** checked — the point of this file is that a `live` row
is true today, and `planned` is a statement of intent.

---

## Stack & dependencies

| Decision | Status | Ref |
|---|---|---|
| PySide6 6.11, Qt **Widgets + QPainter**, not QML | live | §1.2 |
| A bare `pip install -r requirements.txt` **works** — verified in a clean venv, and the scripts say what they add instead of forbidding it | live | §27.2 |
| The synth fallback is the numpy **pluck**, not Karplus-Strong, in every file that names it | live | §27.4 |
| **One** soundfont search — `paths.find_soundfont`, the one with tests. The renderer used a private copy that could not see a system soundfont | live | §28.1 |
| `start_playback(volume=, device=)` are **required with no default**, read from `Settings` by the caller, so a call site cannot inherit a default and silently ignore a preference | live | §28.2 |
| `master_volume` scales the mix **once, in `Transport.__init__`**, above §22's peak normalisation and below the callback; 0.0–1.0, and above 1.0 raises | live | §28.2 |
| `click_volume` is applied at the **mix** step, which is the only moment music and click are still separable | live | §28.2 |
| Python **3.12** in `.venv` — the minor version, because that is the constraint | live | §7.1 |
| ~~mediapipe 1.0.1, Tasks API only~~ — **dropped**; the input is a microphone now | live | §25 |
| ~~opencv-contrib-python-headless, installed in a strict order after the GUI build~~ — **dropped with it**, and the install trap with that | live | §25.2 |
| **No webcam, no OpenCV, no cv2** — nothing imports them, and the lock is 18 packages instead of 32 | live | §25.1 |
| numpy 2.2.6 — the 3.12 ceiling (2.5.3 needs `>=3.12`) | live | §7.1 |
| soundfile 0.14.0 decodes audio; **no resampler needed** — the stream opens at the file's own rate | live | §3.1 |
| ~~Never `pip install -r requirements.txt`; use `scripts/setup.sh`~~ — **withdrawn**: a bare install works, verified in a clean venv. `setup.sh` is still the supported path, for tinysoundfont, a soundfont and the M0 gate | superseded | §27.2, supersedes §2.2 |
| Requirements stay **curated**, with `pip freeze` kept separately as a lock file | live | §6.2 |
| tinysoundfont 0.3.7, installed **`--no-deps`** from `requirements-optional.txt` | live | §7.5, §9 |
| ~~mido~~ — **taken off the table**; no MIDI round trip, the synth reads the `Chart` | live | §9 |
| FluidR3 mono soundfont, fetched rather than committed (MIT) | live | §7.5 |
| Soundfonts **do not clip** — a six-note chord peaks at 0.22; `sfload(gain=…)` is not a level control, so boost the **rendered buffer** | live | §22.2 |

## Architecture

| Decision | Status | Ref |
|---|---|---|
| Four layers, `ui/` → `session/` → `devices/` → `model/`, strictly one-directional | live | §1.3 |
| `model/` is **pure**: no Qt, no sounddevice, no audio I/O of any kind | live | §1.3 |
| `devices/` never imports `session/` or `ui/` | live | §1.3 |
| Audio is fed by **PortAudio's callback**, so nothing of ours is inside the driver and there is no feeder thread to join — the GUI thread never blocks on `write()` or inference | live | §26.3, supersedes §1.4 |
| ~~mediapipe `VIDEO` mode, not `LIVE_STREAM`~~ — superseded, there is no capture thread | live | §25 |
| `QApplication` is a process-wide singleton with a fixed platform — test platforms in **subprocesses** | live | §5.3 |
| Screens never own game objects; `GameSession` outlives them | live | §1.7 |
| `AppContext` holds shared state and **outlives every screen**; the shell owns one | live | §11 |
| Screens take the `context` as a constructor argument, not `self.shell.context` | live | §11 |
| Screens are registered as **factories, not instances** — built on first show | live | §11 |
| One malformed file must not break the library — a status, never an exception | live | §6.6 |
| The library is **rescanned in a background thread**; the GUI thread never blocks | live | §11 |
| `find_tabs()` is shared by the sync and async scans so they cannot disagree | live | §11 |

## Screens

| Decision | Status | Ref |
|---|---|---|
| Playable and unplayable tabs are in **separate lists**; problems only appear when there are some | live | §11 |
| A `SongEntry` is stashed whole in `UserRole`, not its slug — one object, no second lookup | live | §11 |
| The **offset writes settings on Play**, not on every slider move (a save is an fsync) | live | §11 |
| A screen **cancels its scan on `hideEvent`**, not `closeEvent` — the shell only ever hides | live | §11 |
| Preferences edits a **draft** and commits on Save; live-apply would leave half-changes in effect | live | §11 |
| Preferences **never writes `song_offsets_ms`** — song select owns that field | live | §11 |
| A form too tall for the window **scrolls**; a layout that cannot fit compresses instead, and overlaps | live | §11 |
| The compression guard covers **every widget a layout owns**, not just group boxes — the narrow version missed a `QFrame` card entirely | live | §19.2 |
| Disabled controls **say they are not ready** rather than looking live | live | §11 |
| Import is a **copy**, and **never overwrites without asking** | live | §11 |
| Import copies via temp-file-and-rename, so a failure cannot leave a half-written tab | live | §11 |
| `.gpx` is refused **at import** with a reason, rather than copied in to fail at scan time | live | §11 |
| Import is **two steps** — Choose a tab, then Add to library — and the chosen file is named on screen | live | §20.1 |
| The replace question is asked when **Add** is pressed, not when the file is selected | live | §20.1 |
| The button says **"Add to library"**, not "Save" — the action is a copy | live | §20.1 |
| **A tempo-changing track is not offered** in song select: it cannot be charted, so it is a dead end | live | §20.3 |
| A song with **no usable tempo** is playable, not a tempo change | live | §20.3 |
| `MixTableChange.tempo` is a **`MixTableItem`, not an int**, and comes in two shapes; both are read | live | §20.2 |
| A disabled `QPushButton#primary` is **dimmed** — an id selector outranks `:disabled` | live | §20.4 |
| `AppContext` stays **free of Qt**; a screen owns its own loader | live | §11 |
| The app opens **full screen** (`showFullScreen`, not maximized); `--windowed` opts out | live | §12 |
| Before reporting geometry, the entry point **waits for the window to be exposed** | live | §12 |
| ~~Nothing scales with the screen~~ — **reversed**: every design-unit length goes through `theme.px()` and the factor is `clamp(height/1080, 1.0, 1.5)` | superseded | §14, supersedes §12 |
| A vertical layout filling a variable-height container **needs a stretch item**, or it shares surplus height equally | live | §13 |
| `ScreenBase.showEvent` **pins every wrapped label's height to its size hint**, so a layout cannot clip the last line | live | §13 |
| UI scale is `clamp(screen_height / 1080, 1.0, 1.5)`; 1080p is 1.0 and never changes | live | §14 |
| Every design-unit length goes through `theme.px()`; the scale is a module global | live | §14 |
| Corner radii scale as `sqrt(f)` — full scaling makes buttons read as pills | live | §14 |
| The window's **minimum size scales too**, or the enlarged UI is starved of room | live | §14 |

## The clock

The most important part of the design. Getting this wrong makes the game feel
broken in a way that is hard to diagnose.

| Decision | Status | Ref |
|---|---|---|
| `song_pos = (stream.time - t0) - stream.latency - count_in - offset` | live | §1.5, §3.3 |
| Judge against the **audio** clock; never drive note timing from a GUI timer | live | §1.5 |
| sounddevice owns the audio device; **Qt plays no audio at all** | live | §1.2, §2.3 |
| One buffer — count-in clicks, then music with clicks overlaid — and one stream | live | §3.3, §23.2 |
| `blocksize=0`; never the default `latency='high'` | live | §2.4 |
| Pre-render the click track before opening the stream; the RT callback must not allocate | live | §2.4 |
| Hit windows: Perfect ±35ms, Good ±80ms, Miss past 140ms | live | §1.6 |
| Apply gain **after** rendering — `sfload(gain=...)` is a no-op — and **raise** it, because a chord peaks at 0.22 and a chart at 0.67 | live | §7.5, §22.2, §23.3 |
| The gain is **measured from the render**, not a fixed multiple, and reported as `gain_applied` | live | §23.3 |
| The soundfont preset is the **GM programme minus one** — presets are 0-indexed | live | §23.3 |
| `t0` is **read from the device** in `play()`; `stream.time` is ~1.8e9, not a count since open | live | §23.3 |
| The stream is fed by PortAudio's **callback**, not a feeder thread: nothing of ours is ever inside the driver | live | §26.3 |
| `stop()` has **no timeout** — there is no other thread to wait for | live | §26.3 |
| The callback **never raises**; a failure inside it degrades to silence | live | §26.3 |
| A spent buffer **feeds silence** and leaves the stream running, so the clock never stops | live | §26.3 |
| The wall clock is started **as soon as the audio is**, so it can always take over | live | §26.2 |
| Losing the audio **carries on from where it stopped** and says so once | live | §26.2 |
| "Is the audio usable" is tested by **liveness, not the sign** of the position — a count-in is negative | live | §26.5 |
| The **transport handle lives on `AppContext`**, because §1.7 says screens never own devices | live | §23.2 |
| Rendering happens on a **worker**, and the clock does not start until it lands | live | §23.2 |
| `AppContext.audio_enabled` — playing silently is supported, and it is what the tests turn off | live | §23.5 |
| Count-in configurable, default 1 bar | live | §3.4, §11 |
| Per-song audio offset via a manual slider | live | §3.4, §11 |

Omitting `- stream.latency` biases every note 10–20ms early, systematically, which
inside a ±35ms window reads as "the app is broken" rather than as an off-by-10ms.

## Tabs & chart

| Decision | Status | Ref |
|---|---|---|
| `lane = string number − 1` over 6 lanes — no mapping table | live | §1.6 |
| `seconds = tick / 960 × (60 / tempo)` | live | §1.6 |
| `Beat.start` is an **absolute** tick, so note times are recomputed against a running offset when repeats unroll | live | §6.5 |
| Tempo changes are rejected outright rather than silently misplayed | live | §6.5 |
| ...and a track that changes tempo is **dropped from the track list** rather than offered and refused | live | §20.3 |
| Missing audio is **metronome-only**, a supported state rather than an error | live | §3.4 |
| Track is chosen by the user; the **General MIDI program** (24–31 guitar, 32–39 bass) classifies and filters the list | live | §7.3, §8 |
| ~~Chords collapsed to one note per onset, by default~~ — **full chords are now the default**; collapsing is opt-in | live | §21.3 |
| A chord is **playable**: each lane resolves independently, so six notes are six presses and six PERFECTs | live | §21.4 |
| The chord setting is read from the **`PlayRequest`**, so it changes the next song with no rescan | live | §21.2 |
| `LibraryLoader.start` takes `collapse` as a **required** argument — no default for a call site to inherit | live | §21.2 |
| Every `collapse` default in the project is **False**, so none of them can disagree | live | §21.2 |
| Settings **version 2** drops a stale `collapse_chords` so the new default reaches an existing install | live | §21.3 |
| Loading a file **upgrades its version**, so a migration does not re-run on every launch | live | §21.3 |
| `.gpx` is unreadable — PyGuitarPro 0.11 handles GP3/GP4/GP5 only | assumed | §6.3 |
| Repeat barlines are unrolled with `passes = stored + 1` | assumed | §6.4 |
| Alternative endings resolve to the **last** ending (straight-through playback) | assumed | §6.4 |

The three `assumed` rows have never been seen hold:

- **`.gpx`** — read from guitarpro's version-dispatch table, never actually hit.
  A user's real tab would be needed to see this fire.
- **`passes = stored + 1`** — derived from `gp5.py:327-328`, which decrements the
  stored value. The tabs in `songs/` are written out linearly and use **no
  repeat barlines at all**, so the unroller has still never run on real notation.
  `repeats._repeat_passes` names itself as the line to flip if a real file disagrees.
- **Alternative endings** — covered only by synthetic fixtures, for the same reason.

## Input & gameplay

| Decision | Status | Ref |
|---|---|---|
| Keyboard input **always** works as a fallback — a demo laptop may have no audio input at all, which is the same failure as having had no camera | live | §1.1, restated §24.4 |
| ~~Strumming hand's downward wrist velocity fires all active lanes~~ — **retired with the hand-tracking input model**; a concept that appears in no section and has no device | rejected | §24, supersedes §1.4 |
| The input is a **microphone**, not a webcam: 23ms of block against 30–100ms of camera buffering | live | §24.1 |
| A detected note is judged on **pitch, not string** — the strings' ranges overlap, and the same note is the same note. Decided, and the **judge's pitch-keyed index does not exist yet** (`press_pitch` is nowhere in the tree) | planned | §24.2 |
| The keyboard **stays the default** input; a machine with no audio input is a real case | live | §24.4 |
| `InputMode.CAMERA` is read as **`MICROPHONE`**, so an old settings file keeps its meaning | live | §25.5 |
| An **absence** test must be written against code, not prose — a comment explaining the history defeats it | live | §25.4 |
| ~~Camera feed mirroring direction~~ — **retired with the camera**; no device to build it on | rejected | §25, supersedes §1.6 |
| Hit windows: Perfect ±35ms, Good ±80ms, **expire past 140ms** | live | §1.6, §15.7 |
| The 80–140ms band resolves the note as a **MISS**, not a stray — one hit, one outcome | live | §15.7 |
| **Strays are counted but never penalised** — faking through a solo is practice, not cheating | live | §15.7 |
| **Difficulty bands on onsets per second, not notes** — a checkbox must not re-band a whole library | live | §21.5 |
| `accuracy` is hits over notes **judged so far**; `song_accuracy` is the whole-chart figure | live | §15.7 |
| Keys **1–6** for lanes 0–5, hard-coded; the only mapping there is | live | §15 |
| The game **does not depend on Qt focus** — an app-wide filter catches the lane keys while visible | live | §15.5 |
| ~~The game clock is a wall clock for now~~ — **it is the audio device's clock**, with the wall clock as the no-audio fallback | live | §23.1 |

**⚠ The whole hand-tracking input model is SUPERSEDED by §24.** §1.4 wanted lane
from a fretting hand's **x**-position; §15.2 corrected that to **y**; §24 replaces
both with a **microphone and a pitch estimate**. Each correction was cheap because
the pipeline was never built, and this one retires the error term §4.1 called the
largest in the product.

**Camera latency no longer dominates anything, because there is no camera.** The
chain §4.1 worried about was `camera buffer (~30–100ms) → capture thread → inference
→ lane decision → judge`. A microphone block is 23ms and we choose its size, and
PortAudio reports the rest (§24.1).

## The playfield

**§15's scrolling highway was replaced by §16's three-bar tab view.** The rows below
are current; §15.2's "lane 0 at the bottom" and §15.3's "bisect per paint" are
superseded or gone with the widget.

| Decision | Status | Ref |
|---|---|---|
| **Three bars stacked vertically** — previous, current, next | live | §16.1 |
| Progression **down the page**, one bar at a time — discrete, not a continuous scroll | live | §16.1 |
| Within a bar the beat line sweeps **left to right**, reset at each bar line | live | §16.1 |
| Each bar is a **six-line staff**; **lane 0 is the top line**, as in printed tab | live | §16.1 |
| The string line is the **primary read**; the fret number is confirmation inside the mark | live | §17.1 |
| Fret numbers appear in **all three bars**, sized from `marker_radius`, never the stylesheet | live | §17.2 |
| Numbers are **dropped below a 9px derived size** rather than drawn as a smudge | live | §17.5 |
| `marker_font()` calls `ensurePolished()` — `QWidget.font()` is the app default until then | live | §17.3 |
| The previous and next bars are **dimmed**; the current one is full weight | live | §16.4 |
| The widget is **pure render**: `(chart, position)` → pixels, no clock | live | §15.1, §16.5 |
| The judgement tolerance is drawn as **two window edges**, from `GOOD_SECONDS` itself | live | §16.4 |
| The beat line is drawn **over** the notes — it points at the one being played | live | §16.3 |
| Measure spans come from `chart.bar_lines`, not interpolated from the tempo | live | §16.3 |
| Lane colours stay, though printed tab is monochrome — it is a game | live | §16.2 |
| The HUD is an **overlay** — the playfield keeps the whole window | live | §15.4 |
| Strings are **named `E A D G B E`** down the left margin, where printed tab puts them | live | §18.1 |
| The bottom **key legend is deleted** — one answer to "which key is which", not two | live | §18.1 |
| Each painter method sets its **own font**; the number and the letters are sized to different boxes | live | §18.1 |

## Practice tempo

| Decision | Status | Ref |
|---|---|---|
| **Per-song absolute BPM**, remembered per song in `Settings.song_bpm` | live | §18.3 |
| A **percentage** is rejected: a rate is song-relative, and an absolute BPM is also the value a transport needs | live | §18.3 |
| Rate is **never above 1.0**; the spin box is clamped to the tab's own tempo | live | §18.3 |
| The rate is a **single multiplication at the clock**; notes keep their times, expiry slows down for free | live | §18.2 |
| The **audio offset is applied after the rate** — it is a property of the song, not of the practice speed | live | §18.2 |
| Judgement windows stay in **milliseconds**, so scores are comparable across speeds | live | §18.3 |
| The control lives on **song select**, with the track and the offset — it is a choice about the next attempt, not during one | live | §19.1 |
| The tempo travels in **`PlayRequest.bpm`**, where `0` means "as written" | live | §19.1 |
| The rate is **fixed for the run**; nothing can re-time a song that is playing | live | §19.1 |
| ~~Changing tempo re-anchors the clock origin~~ — **deleted with the mid-song control**. (The row also claimed the game reverted to a plain `QElapsedTimer`; that was true for one commit in §19 and was refuted by §23 — the clock is the audio device's) | live | §19.1, §23 |
| **0 is stored** for "as written", not the written tempo, so a re-exported tab is not held at the old one | live | §19.1 |
| Both song-select controls save on **Play** — one write per attempt | live | §19.1 |
| A **generated** backing track that follows the practice tempo | planned | §1.5 |
