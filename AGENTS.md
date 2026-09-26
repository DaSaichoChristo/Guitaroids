# AGENTS.md

Guitaroids — a Guitar Hero-style app. Webcam hand tracking (mediapipe) walks you
through Guitar Pro `.gp5` tabs at tempo on a 6-lane note highway, counting misses.
Hackathon project.

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

- **Stack:** PySide6 6.11 (Qt Widgets + QPainter, *not* QML) · mediapipe 1.0.1
  (Tasks API only — `mp.solutions` is gone) · opencv-contrib-python-headless
  5.0.0.93 · PyGuitarPro 0.11 · numpy 2.2.6 · sounddevice owns audio playback,
  Qt plays no audio.
- **Python is 3.12.12 in `.venv`.** 3.12 specifically: `tinysoundfont` has wheels
  for cp310/cp312 only, so 3.13 and 3.14 compile from source and need a C++
  toolchain plus Python dev headers. Check `.venv/bin/python --version`, not the
  system `python3`. `DESIGN.md` §5.1 records a wrong analysis caused by exactly
  this; §7.1 records the version hunt.
- **Install with `scripts/setup.sh`, never `pip install -r requirements.txt`.** The
  bare pip install silently reinstalls the GUI OpenCV build and reintroduces §2.2.
  Both OpenCV builds write the same `cv2/` directory, so the GUI build must be
  removed *before* headless is installed — otherwise pip sees headless as satisfied
  and `import cv2` breaks. This happened and is now covered by the M0 test.
- **Layers:** `ui/` → `session/` → `devices/` → `model/`, one-directional. `model/`
  is pure data with zero I/O. `devices/` never imports `session/` or `ui/`.
- **The clock is the crux.** `song_pos = (stream.time - t0) - stream.latency`.
  Never drive note timing from a GUI timer. Omitting `latency` biases every note
  10–20ms early.
- **Lane = the tab's string number − 1.** No mapping table.
- **Timing:** `seconds = beat.start / 960 * (60 / song.tempo)`. `Beat.start` is an
  **absolute** tick, so note times are recomputed against a running offset when
  repeats are unrolled.
- **Use mediapipe `VIDEO` mode,** not `LIVE_STREAM` — the latter silently drops frames.
- **`.gpx` is unreadable.** PyGuitarPro 0.11 handles GP3/GP4/GP5 only; GP7/8's
  default `.gpx` raises `unsupported version`. Surfaced as
  `Status.UNSUPPORTED_VERSION`.
- **Track selection uses `track.channel.instrument`** (General MIDI program), not
  note count or track order. Every track defaults to 6 strings, so "6 strings"
  does not discriminate, and "lowest number" picks the Vocals track.

## Current state

`tests/` is 93 tests, all passing. `model/chart.py` and `model/repeats.py` exist and
are pure; `songlib.py` scans and validates. **No UI, no audio, no devices yet** —
`ui/`, `audio/`, `session/`, `devices/` are empty packages, awaiting the milestones
in `DESIGN.md` §4.2 (M2 for song select and audio, M3 for the highway and judging).

Check the song library without launching the GUI:

```
.venv/bin/python scripts/import_songs.py          # human report
.venv/bin/python scripts/import_songs.py --json   # exits 1 if anything is unplayable
```

## Unblock this first

`DESIGN.md` §2.2: mediapipe hard-requires `opencv-contrib-python` (the GUI build),
whose bundled Qt plugins break PySide6 with
`Could not load the Qt platform plugin "xcb"`. Fixed by installing
`opencv-contrib-python-headless` at the **identical version**; `guitaroids/qtenv.py`
points `QT_PLUGIN_PATH` at PySide6's plugins as a second line of defence.

**M0 has passed** (`DESIGN.md` §5): a window opens on `xcb` and the plugin
hijack is gone. Re-verify after any dependency change with
`.venv/bin/python -m pytest tests/test_m0_window.py -q`.

## The next blocker

`DESIGN.md` §5.4: **there is still no `.gp5` and no audio file in `songs/`.** M1
cannot be verified end to end until real files are dropped in.

## Audio

`sounddevice` owns playback and is the master clock (§1.5). For *generating* the
backing track, `tinysoundfont` renders offline from an SF2/SF3 soundfont, reading
the `Chart` directly — **no MIDI round trip, and `mido` is deliberately not a
dependency** (§9). The numpy Karplus-Strong synth is the zero-dependency fallback
when `tinysoundfont` or a soundfont is absent. Soundfonts and the model are fetched
by `scripts/`, not committed.

`tinysoundfont` must be installed with `--no-deps`: its `pyaudio` dependency has no
Linux wheel and cannot be built (no `portaudio.h`). `pyaudio` is a lazy import used
only for real-time playback, so offline rendering never needs it. Locked in by
`tests/test_audio_deps.py`. Note `pip install --dry-run` gives a false positive here
— it exits 0 on a package that will not actually build.

**Soundfonts clip.** They render hot, and `sfload(gain=...)` has no effect — apply
gain to the rendered buffer afterwards.

## Rules

- Never let the GUI thread block on `cap.read()`, audio `write()`, or inference.
- `QApplication` is a process-wide singleton whose platform is fixed at
  construction — test platforms in subprocesses, not sequentially.
- Pre-render the click track into a numpy buffer before opening the stream — the
  PortAudio callback runs at real-time priority and must not allocate.
- Keep the keyboard input path working as a fallback. A demo on an unfamiliar laptop
  has no working camera, and must not crash because of it.
