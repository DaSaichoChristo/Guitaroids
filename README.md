# Guitaroids

A Guitar Hero-style app that tracks your hands through the webcam (mediapipe) and
walks you through Guitar Pro tabs at tempo on a 6-lane note highway, counting
misses.

Hackathon project. In progress — the song import, audio rendering and menu layers
are built; the note highway, judging and hand tracking are not yet.

## Quick start

Linux / macOS:

```bash
scripts/setup.sh          # the ONLY supported install path
```

Windows (PowerShell):

```powershell
.\scripts\setup.ps1
```

Either script creates `.venv` on Python 3.12, installs pinned dependencies, fetches
the mediapipe model and a soundfont, and runs the M0 gate. The two are kept in
sync by `tests/test_setup_scripts.py`, which asserts they agree on the critical
pins — so a change to one is not silently missing from the other.

See [`DECISIONS.md`](DECISIONS.md) for why each choice was made, and
[`DESIGN.md`](DESIGN.md) for the reasoning and measurements behind them.

Verify at any time:

```bash
.venv/bin/python -m pytest tests/ -q
```

## Running it

The app opens **full screen** — no title bar, no taskbar entry.

```bash
.venv/bin/python -m guitaroids                      # the app, full screen
.venv/bin/python -m guitaroids --windowed           # 960x600 window instead
.venv/bin/python -m guitaroids --songs /path/tabs   # a different library
.venv/bin/python -m guitaroids --self-test          # build the UI, render, exit
```

`--self-test` reports the geometry the window manager actually gave the window,
which on a full screen is the whole screen rather than a size we chose. It waits
for the window to be exposed before reporting, because until the WM has done its
round trip the window is still sitting at its minimum size.

Four of the six screens are real: the main menu, **song select** (pick a tab, pick a
track, tune the audio offset), **import GP** (copy a tab into the library) and
**preferences**. **Game** and **Results** are placeholders — pressing Play gets you
to a screen that says so.

To look at a screen without launching the app:

```bash
.venv/bin/python scripts/screenshot_ui.py --all    # PNGs in /tmp/opencode/ui
```

## Adding songs

Drop a `.gp5` tab into `songs/`, or use **Import GP** in the app, which copies one
in from anywhere and rescans. Tabs and audio are gitignored — the library is
personal, and `.gp5` files transcribe real copyrighted songs. See
[`songs/README.md`](songs/README.md) for the format, the audio pairing convention,
and which tabs this build rejects.

Check a library without launching the GUI:

```bash
.venv/bin/python scripts/import_songs.py             # human-readable report
.venv/bin/python scripts/import_songs.py --json      # machine-readable, exits 1 on problems
.venv/bin/python scripts/import_songs.py --full-chords
```

```
scanned songs/
  1 tab(s): 1 playable, 0 problem(s)

PLAYABLE
  slug                     title                   bpm  notes    len diff     chord  audio
  eagles_the-hotel_califo  Hotel California         76   1108   6:20 Medium       6  -- metronome only --

  tracks in eagles_the-hotel_california_5:
    #3   12-stg Guitar (1)        GM 25    4099 notes  <- default
    #4   Acoustic Guitar (2)      GM 24     134 notes
    #6   Solo Guitar 1            GM 29     539 notes
    #7   Solo Guitar 2            GM 29     347 notes
```

## Two constraints worth knowing up front

Both have bitten this project and both are enforced by tests:

- **Use `scripts/setup.sh`, never `pip install -r requirements.txt`.** mediapipe
  hard-requires the *GUI* build of OpenCV, whose bundled Qt plugins break PySide6
  with `Could not load the Qt platform plugin "xcb"`. The GUI build must be removed
  *before* the headless one is installed, or pip leaves a half-removed `cv2/`
  directory. See `requirements.txt` and `DESIGN.md` §2.2, §7.2.
- **`pyaudio` cannot be installed here** — no Linux wheel, and no `portaudio.h` to
  build one. `tinysoundfont` is therefore installed with `--no-deps`, which is safe
  because `pyaudio` is a lazy import used only for real-time playback. Locked in by
  `tests/test_audio_deps.py`. Note `pip install --dry-run` reports success on this
  even though a real install fails.

## Requirements

- **Python 3.12.** 3.13 and 3.14 compile `tinysoundfont` from source, which needs a
  C++ toolchain and Python dev headers. Override the interpreter with
  `PYTHON=python3.X scripts/setup.sh`.
- A C++ compiler is **not** needed on 3.12 — every dependency has a wheel.
- No package manager is needed. Soundfonts and the mediapipe model are fetched by
  `scripts/`, not installed.

## Layout

```
guitaroids/
  model/     Chart, Note, repeat unrolling      pure data, zero I/O
  devices/   Transport, HandTracker             (not built yet)
  session/   PlayRequest                        what to play, not the game
  ui/        screens, library loader, theme     menu screens are built
  audio/     synth, soundfont discovery         (not built yet)
  context.py AppContext: shared state, outlives every screen
  importer.py import decisions                  pure, no Qt
  songlib.py library scan, pairing, status
  settings.py user preferences                  pure, no Qt
  qtenv.py   Qt plugin bootstrap
scripts/     setup, asset fetchers, import report, screenshots
tests/       446 tests
songs/       your tabs and audio (gitignored)
```

`context.py`, `importer.py`, `settings.py` and all of `model/` are pinned free of
Qt, OpenCV and sounddevice by subprocess tests. That is what keeps them testable in
milliseconds with no display — and it is why the background library loader is owned
by a screen rather than by the context.

## Attribution

- **FluidR3** soundfont (MIT) — Frank Wen. Fetched by `scripts/fetch_soundfont.sh`,
  not committed. See `assets/ATTRIBUTION-soundfont.md`.
- Tabs and audio in `songs/` are yours; record provenance in
  `songs/ATTRIBUTION.md`.
