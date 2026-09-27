# AGENTS.md

Guitaroids — a Guitar Hero-style app. You play your **own guitar** while it reads
Guitar Pro `.gp5` tabs at tempo, three bars of notation at a time. The app hears what
you play through a microphone; the six on-screen keys still work for a machine with
no audio input. Hackathon project.

**Looking for the current design?** Read [`DECISIONS.md`](DECISIONS.md) first — one
screen, every decision with status and a link to the section that justifies it.
`DESIGN.md` below is the historical record behind those links.

## Read `DESIGN.md` before non-trivial work

`DESIGN.md` is the project's memory. §1 is the original plan; everything after is a
dated work entry.

**The convention is append-only — later sections supersede earlier ones rather than
editing them.** Earlier sections may contain claims that later ones refuted. When
something looks contradictory, **the higher-numbered section wins.**

New work appends a numbered, dated section at the end, with evidence attached to
claims and an explicit **Not done** list. Never edit an earlier section; append a
correction that names it by number.

Every section ends with a "Not done" list. Read it — it is where the unverified
assumptions live. Do not treat anything in `DESIGN.md` as tested unless the section
says what ran.

## The facts you need before reading anything else

- **Stack:** PySide6 6.11 (Qt Widgets + QPainter, *not* QML) · PyGuitarPro 0.11 ·
  numpy 2.2.6 · sounddevice owns audio playback, Qt plays no audio · tinysoundfont
  renders a tab into sound. **No mediapipe and no OpenCV** — the input is a
  microphone, not a webcam (§24, §25).
- **Python is 3.12 in `.venv`.** 3.12 specifically: `tinysoundfont` has wheels
  for cp310/cp312 only, so 3.13 and 3.14 compile from source and need a C++
  toolchain plus Python dev headers. Check `.venv/bin/python --version`, not the
  system `python3`. `DESIGN.md` §5.1 records a wrong analysis caused by exactly
  this; §7.1 records the version hunt.
- **A bare `pip install -r requirements.txt` FAILS** — and `scripts/setup.sh` is the
  install path, not a convenience (§31.2). `requirements.txt` is one `pip freeze`
  file, so tinysoundfont is in it, and tinysoundfont cannot be installed normally: it
  depends on `pyaudio`, which has no Linux wheel and cannot be built without
  `portaudio.h`. Verified in a clean venv — `pip install tinysoundfont==0.3.7` exits
  with *"Failed building wheel for pyaudio"*. `pip install --dry-run` reports success
  on it, so a dry run is not evidence. setup.sh filters the package out of the bulk
  install and installs it with `--no-deps`, then fetches a soundfont and runs the M0
  gate. §27 verified the opposite of all this, when the curated list left
  tinysoundfont out; there used to be an even older warning here about mediapipe's
  OpenCV install order, which went with the webcam (§25). `tests/test_requirements.py`
  asserts the file matches the venv it was generated from, and
  `tests/test_setup_scripts.py` asserts the install dance is in the right order.
- **Layers:** `ui/` → `session/` → `devices/` → `model/`, one-directional. `model/`
  is pure data with zero I/O. `devices/` never imports `session/` or `ui/`.
- **The clock is the crux.** `song_pos = (stream.time - t0) - stream.latency`, and
  `t0` is read from the device at `play()` — `stream.time` is not a count of seconds
  since you opened the stream (§23.3). Never drive note timing from a GUI timer.
  Omitting `latency` biases every note 10–20ms early.
- **Never hold a device handle across a blocking call.** Two core dumps came from
  it: closing a stream while a thread was inside `write()`, and a `join(timeout=)`
  that timed out and let the stream be collected under a live write (§26.3). Feed
  audio with PortAudio's *callback* — the device calls us, nothing of ours is inside
  the driver, and there is no handle to free from under anyone.
- **Lane = the tab's string number − 1.** No mapping table.
- **Chords are kept whole.** `Settings.collapse_chords` is `False` by default, so the
  chart is every note in the tab and a chord is several simultaneous presses —
  playable, because the judge resolves one lane at a time. Collapsing to one note
  per onset is an opt-in in Preferences, and it drops three quarters of a real tab
  (§21.3).
- **Timing:** `seconds = beat.start / 960 * (60 / song.tempo)`. `Beat.start` is an
  **absolute** tick, so note times are recomputed against a running offset when
  repeats are unrolled.
- **`.gpx` is unreadable.** PyGuitarPro 0.11 handles GP3/GP4/GP5 only; GP7/8's
  default `.gpx` raises `unsupported version`. Surfaced as
  `Status.UNSUPPORTED_VERSION`.
- **Track selection uses `track.channel.instrument`** (General MIDI program), not
  note count or track order. Every track defaults to 6 strings, so "6 strings"
  does not discriminate, and "lowest number" picks the Vocals track.

## Current state

`tests/` is 1016 tests, all passing (not counting `tests/test_docs.py` itself --
that file checks this number, and a test that counts itself never matches). **Five of the six screens are real:** the main
menu, song select (tab + track + audio offset + **per-song practice tempo**), import
GP (choose a file, then Add to library), preferences, **game** — three bars
of tab notation with a left-to-right beat line, `E A D G B E` down the left, judged
PERFECT/GOOD/MISS off the pitch a microphone hears — and **results**. The practice tempo
is chosen on song select and travels in `PlayRequest`, so a run cannot be re-timed
while it plays, and it slows the music rather than just the tab (§29.2).
**Results is real**: the tally, the accuracy, the tempo you played at, and the best
accuracy recorded for that song. **There are no points** — no 10,000 maximum, no streak
multiplier — so "best" means best accuracy, which is the only number worth comparing
between attempts (§34.1).

`model/chart.py`, `model/repeats.py`, `songlib.py`, `settings.py`, `importer.py`,
`session/play_request.py` and `session/judge.py` are pure. `context.py` holds the
shared state and is also pinned Qt-free. `ui/` holds the screens, a background
library loader, and `ui/widgets/tabview.py` — which is **pure render** and owns no
clock, so it can be rasterised to a `QImage` and asserted on with no audio device and
no event loop. That is the only way this suite checks rendered output.

Check the song library without launching the GUI:

```
.venv/bin/python scripts/import_songs.py          # human report
.venv/bin/python scripts/import_songs.py --json   # exits 1 if anything is unplayable
```

Look at a screen without launching the app:

```
.venv/bin/python scripts/screenshot_ui.py --all  # PNGs in /tmp/opencode/ui
```

Screenshots pin scale 1.0 and a 960x640 frame, so they stay comparable run to run
whatever display you are on. `--scale 1.5 --size 1440x960` renders the enlarged
layout.

## Six things that will bite you

All six cost real time, and all six are now enforced by tests.

- **A layout that does not fit does not clip — it compresses.** Children get squeezed
  below their minimum height and end up drawn on top of each other. This shipped a
  preferences screen with three combo boxes overlapping, and later a song-select
  detail card whose six fact rows were drawn on top of each other (§19.2). Tall forms
  go in a `QScrollArea`, and `test_ui_shell.py` fails if any screen squeezes a widget
  a layout owns — **not** just a group box, which is all the guard used to check and
  which is why the card got through. `QScrollArea.setWidget()` also takes ownership:
  return the *container*, never the scroll area's widget, or it is deleted out from
  under the layout.
- **A vertical layout with no stretch item shares surplus height *equally*.** Both
  `QLabel` and `QGroupBox` can grow, so on a tall window every one of them gets the
  same slice of the extra and the page opens holes between its own paragraphs. A
  `QScrollArea` with `setWidgetResizable(True)` guarantees a surplus exists, so this
  shows up on big screens and not small ones.
- **A word-wrapped `QLabel`'s minimum is smaller than its text needs.** A layout that
  economises hands out `minimumSizeHint` and the last line is silently not drawn —
  Import GP shipped a sentence ending mid-thought. `ScreenBase.showEvent` pins every
  wrapped label's `minimumHeight` to its `sizeHint`; do not remove that, and do not
  add a wrapped label without a test that its height covers its text.
- **`AppContext` must stay free of Qt.** It is pinned by a subprocess test, and it is
  why the background library loader is owned by a screen rather than by the context.
  Put a `QObject` on the context and every context test becomes a Qt test.
- **The app opens full screen, and the UI scales with the screen.** The factor is
  `clamp(height / 1080, 1.0, 1.5)`, set once in `build_application`. Every length
  must go through `theme.px(n)` — in the QSS, in `content_column`,
  `constrained_button`, and in raw `setContentsMargins`/`setSpacing` calls. A
  hardcoded pixel will not move and will look wrong next to everything that does.
  `theme.radius()` is for corners, damped by the square root. The scale is a module
  global, so `conftest.py` resets it and the stylesheet after every test.

- **A setting that nothing reads looks exactly like a setting that works.** "Collapse
  chords" was saved, persisted, carried in `PlayRequest`, printed by `describe()` and
  asserted by six tests — and read by nothing, because both `loader.start()` call
  sites omitted the argument and the loader's own default won (§21.2). Every test
  covered the field or the loader; none covered the *seam*. When a preference matters,
  test it through the real path — settings file → context → screen — and give any
  argument a call site could forget a **required** no-default parameter.

## Unblock this first

**M0 has passed** (`DESIGN.md` §5): a window opens on `xcb`, and OpenCV is no longer
installed at all, so the plugin-hijack that used to need `qtenv.py` cannot happen
(§25). Re-verify after any dependency change with
`.venv/bin/python -m pytest tests/test_m0_window.py -q`.

If that ever fails with `Could not load the Qt platform plugin "xcb"`, something has
reintroduced a package that ships Qt plugins — and the only one that ever did was
mediapipe's OpenCV. Check `pip list` for `opencv` before anything else.

## The next blocker

**Everything is built except one thing the user has to do.** The clock is the audio
device's (§23), a real tab plays through a real sound card, the practice tempo slows
the music (§29.2), and the input half is wired end to end: `audio/pitch.py` finds a
note, `audio/mic.py` opens a microphone, and the judge matches a **detected pitch**
rather than a lane, because the strings' ranges overlap (§24.2, §30).

What does not exist is a *verified* note detector. §29.3 checks the estimator against
this project's own synthesis, which is cleaner than a real guitar through a laptop
microphone — no fret buzz, no room, no sympathetic resonance. The next step is playing
an actual guitar and seeing what `MIN_CLARITY` and the analysis window need.

**The keyboard is gone** (§32). The microphone is the only input, so a machine with no
working microphone cannot play the game at all -- which is what §24.4's fallback existed
to prevent, and it is gone with it.

## Audio

`sounddevice` owns playback and is the master clock (§1.5), and the game screen
reads its position (§23). For *generating* the backing track, `tinysoundfont`
renders offline from an SF2/SF3 soundfont, reading the `Chart` directly — **no MIDI
round trip, and `mido` is deliberately not a dependency** (§9). The numpy pluck synth
is the zero-dependency fallback when `tinysoundfont` or a soundfont is absent. The
soundfont is fetched by `scripts/`, not committed.

Three things about this path that are measured rather than assumed, and each has a
way of being got wrong that looks fine:

**Soundfonts do not clip — §7.5 got that backwards.** A six-note chord peaks at
**0.22**, not 1.0, because the buffer was read as int16 instead of float32 (§22).
`sfload(gain=...)` really is useless, so gain is applied to the rendered buffer — and
**raised**, because the render is quiet, not hot. The gain is computed from the
measured peak (`target_peak / peak`, capped at 12x) rather than fixed, and reported as
`gain_applied`; the limiter's ceiling is 0.95. `generate()` returns a `memoryview` of
stereo float32; read it as anything else and you get NaN or a fake clip.

`tinysoundfont` must be installed with `--no-deps`: its `pyaudio` dependency has no
Linux wheel and cannot be built (no `portaudio.h`). `pyaudio` is a lazy import used
only for real-time playback, so offline rendering never needs it. Locked in by
`tests/test_audio_deps.py`. Note `pip install --dry-run` gives a false positive here
— it exits 0 on a package that will not actually build.

## Rules

- Never let the GUI thread block on audio `write()` or on inference. (The webcam's
  `cap.read()` went with the camera in §25; the rule is unchanged, the device is
  not there.)
- `QApplication` is a process-wide singleton whose platform is fixed at
  construction — test platforms in subprocesses, not sequentially.
- Pre-render the click track into a numpy buffer before opening the stream — the
  PortAudio callback runs at real-time priority and must not allocate.
- Keep the keyboard input path working as a fallback. A demo on an unfamiliar laptop
  may have no audio input at all, which is the same failure as having had no camera
  (§24.4), and must not crash because of it.
