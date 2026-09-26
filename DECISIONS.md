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

---

## Stack & dependencies

| Decision | Status | Ref |
|---|---|---|
| PySide6 6.11, Qt **Widgets + QPainter**, not QML | live | §1.2 |
| Python **3.12.14** in `.venv` | live | §7.1 |
| mediapipe 1.0.1, **Tasks API only** — `mp.solutions` is gone | live | §2.1, §5.4 |
| opencv-contrib-python-**headless**; the GUI build must be removed *before* headless is installed | live | §2.2, §7.2 |
| numpy 2.2.6 — the 3.12 ceiling (2.5.3 needs `>=3.12`) | live | §7.1 |
| soundfile 0.14.0 decodes audio; **no resampler needed** — the stream opens at the file's own rate | live | §3.1 |
| Install with `scripts/setup.sh`, never a bare `pip install -r requirements.txt` | live | §2.2 |
| Requirements stay **curated**, with `pip freeze` kept separately as a lock file | live | §6.2 |
| tinysoundfont 0.3.7, installed **`--no-deps`** from `requirements-optional.txt` | live | §7.5, §9 |
| ~~mido~~ — **taken off the table**; no MIDI round trip, the synth reads the `Chart` | live | §9 |
| FluidR3 mono soundfont, fetched rather than committed (MIT) | live | §7.5 |
| Soundfonts **do not clip** — a six-note chord peaks at 0.22; `sfload(gain=…)` is not a level control, so boost the **rendered buffer** | live | §22.2 |

## Architecture

| Decision | Status | Ref |
|---|---|---|
| Four layers, `ui/` → `session/` → `devices/` → `model/`, strictly one-directional | live | §1.3 |
| `model/` is **pure**: no Qt, no OpenCV, no sounddevice, no I/O | live | §1.3 |
| `devices/` never imports `session/` or `ui/` | live | §1.3 |
| Four threads; the GUI thread never blocks on `cap.read()`, audio `write()`, or inference | live | §1.4 |
| mediapipe **`VIDEO` mode**, not `LIVE_STREAM` — the latter silently drops frames | live | §1.4 |
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
| **Nothing scales with the screen** — type and control widths are fixed pixels | live | §12 |
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
| One buffer — count-in clicks, then music with clicks overlaid — and one stream | planned | §3.3 |
| `blocksize=0`; never the default `latency='high'` | live | §2.4 |
| Pre-render the click track before opening the stream; the RT callback must not allocate | live | §2.4 |
| Hit windows: Perfect ±35ms, Good ±80ms, Miss past 140ms | live | §1.6 |
| Apply gain **after** rendering — `sfload(gain=...)` is a no-op and soundfonts clip | live | §7.5 |
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
  stored value. The one real tab in `songs/` is written out linearly and uses **no
  repeat barlines at all**, so the unroller has still never run on real notation.
  `repeats._repeat_passes` names itself as the line to flip if a real file disagrees.
- **Alternative endings** — covered only by synthetic fixtures, for the same reason.

## Input & gameplay

| Decision | Status | Ref |
|---|---|---|
| Keyboard input **always** works as a fallback — a demo on an unfamiliar laptop has no camera and must not crash | live | §1.1 |
| Strumming hand's downward wrist velocity fires all active lanes | planned | §1.4 |
| Input-latency calibration lands **before** hand tracking, not after | planned | §4.1 |
| Camera feed mirroring direction (affects handedness) | planned | §1.6 |
| Hit windows: Perfect ±35ms, Good ±80ms, **expire past 140ms** | live | §1.6, §15.7 |
| The 80–140ms band resolves the note as a **MISS**, not a stray — one hit, one outcome | live | §15.7 |
| **Strays are counted but never penalised** — faking through a solo is practice, not cheating | live | §15.7 |
| **Difficulty bands on onsets per second, not notes** — a checkbox must not re-band a whole library | live | §21.5 |
| `accuracy` is hits over notes **judged so far**; `song_accuracy` is the whole-chart figure | live | §15.7 |
| Keys **1–6** for lanes 0–5, hard-coded; the only mapping there is | live | §15 |
| The game **does not depend on Qt focus** — an app-wide filter catches the lane keys while visible | live | §15.5 |
| The game clock is a **wall clock** for now; the audio clock of §1.5 is the next milestone | live | §15.6 |

**⚠ §1.4's "fretting hand x-position → lane" is SUPERSEDED by §15.2.** The highway is
horizontal, so lanes are rows and lane must come from the player's **y**-position.
The hand-tracking pipeline is unbuilt, so nothing has to be rewritten — but the
correction has to be made *before* tracking starts, not after.

**Camera latency is expected to dominate all audio-side error.** The chain is
`camera buffer (~30–100ms) → capture thread → inference → lane decision → judge`,
and the figure scales with camera hardware rather than with our code. That is why
calibration is its own milestone rather than a setting buried in the end
(§4.1).

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
| ~~Changing tempo re-anchors the clock origin~~ — **deleted with the mid-song control**; the game is a plain `QElapsedTimer` again | live | §19.1 |
| **0 is stored** for "as written", not the written tempo, so a re-exported tab is not held at the old one | live | §19.1 |
| Both song-select controls save on **Play** — one write per attempt | live | §19.1 |
| A **generated** backing track that follows the practice tempo | planned | §1.5 |
