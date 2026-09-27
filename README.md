# Guitaroids

A Guitar Hero-style app. You play your own guitar and it walks you through Guitar
Pro tabs at tempo, counting misses — it renders the tab into sound and times your
notes against the audio device's clock.

Hackathon project. In progress — the song import, note-reading, audio and microphone
layers are all built, and every screen is real. The input is your guitar: a note
detector (`audio/pitch.py`) works out which note was played and the judge matches on
**pitch** rather than string, because the strings' ranges overlap. **The keyboard is
gone** (§32) and **wear headphones** — see below.

## Quick start

Linux / macOS:

```bash
scripts/setup.sh          # the only install path
```

It creates `.venv` on Python 3.12, installs the dependencies, fetches a soundfont, and
runs the M0 gate. **Do not use `pip install -r requirements.txt` instead** — that file
is `pip freeze` output and it lists tinysoundfont, which cannot be installed normally:
it depends on `pyaudio`, which has no Linux wheel here and cannot be built without
`portaudio.h`. The script filters it out and installs it with `--no-deps` instead.

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
.venv/bin/python -m guitaroids --windowed           # 960x640 window instead
.venv/bin/python -m guitaroids --songs /path/tabs   # a different library
.venv/bin/python -m guitaroids --self-test          # build the UI, render, exit
```

`--self-test` reports the geometry the window manager actually gave the window,
which on a full screen is the whole screen rather than a size we chose. It waits
for the window to be exposed before reporting, because until the WM has done its
round trip the window is still sitting at its minimum size.

All six screens are real: the main menu, **song select** (pick a tab, pick a
track, tune the audio offset, set a per-song practice tempo), **import GP** (copy a tab into the library),
**preferences**, **game** — three bars of tab notation (the bar you just played,
the current one, and the one coming) with a line sweeping left to right through the
current bar, judged PERFECT/GOOD/MISS from the pitch your guitar plays — and
**results**, which shows the tally, your accuracy, the tempo you played at, and the
best accuracy recorded for that song. There are no points and no multiplier: "best"
means best accuracy.

**Wear headphones.** The app renders the tab and plays it from the same machine, so
through speakers the microphone hears the app's own music as your playing and you would
score PERFECT without touching the guitar.

The game's clock is the audio device's clock, read from the stream, so the notes you
see and the sound you hear come from one source (§23). The wall clock survives as a
fallback for a machine with no working output device, and you are told once when it
takes over.

To look at a screen without launching the app:

```bash
.venv/bin/python scripts/screenshot_ui.py --all    # PNGs in /tmp/opencode/ui
.venv/bin/python scripts/screenshot_ui.py --all --scale 1.5 --size 1440x960
```

Screenshots render at scale 1.0 into a 960x640 frame by default — the 1080p design
size — so they stay comparable run to run whatever display you are on. Pass
`--scale` to inspect the enlarged layout.


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
.venv/bin/python scripts/import_songs.py --collapse-chords   # one note per chord
```

```
scanned songs/
  3 tab(s): 3 playable, 0 problem(s)

PLAYABLE
  slug                     title                   bpm  notes    len diff     chord  audio
  eagles_the-hotel_califo  Hotel California         76   4099   6:20 Medium       6  -- metronome only --
  guns_n_roses-sweet_chil  Sweet Child O' Mine     127   2015   5:34 Medium       5  -- metronome only --
  guns_n_roses-sweet_chil  Sweet Child O' Mine (  140   3650   5:12 Medium       6  -- metronome only --

  tracks in eagles_the-hotel_california_5:
    #3   12-stg Guitar (1)        GM 25    4099 notes  <- default
    #4   Acoustic Guitar (2)      GM 24     134 notes
    #6   Solo Guitar 1            GM 29     539 notes
    #7   Solo Guitar 2            GM 29     347 notes
```

## Two constraints worth knowing up front

Both have bitten this project and both are enforced by tests:

- **`pip install -r requirements.txt` now just works** — checked in a clean venv,
  where the app installs, imports, and opens a window. `scripts/setup.sh` is still
  the supported path, for tinysoundfont, a soundfont and the test gate; what a bare
  install costs you is the numpy pluck synth instead of a sampled guitar
  (`DESIGN.md` §27). It used to be the other way round, with a long warning about
  OpenCV install order that went away when the webcam did (`DESIGN.md` §25).
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
- No package manager is needed. Soundfonts are fetched by
  `scripts/`, not installed.

## Layout

```
guitaroids/
  model/     Chart, Note, repeat unrolling      pure data, zero I/O
  devices/   (empty; the transport is in audio/)
  session/   PlayRequest, judge                  what to play, and how it scores
  ui/        screens, library loader, theme     menu screens are built
  audio/     render, click, transport            the master clock lives here
  context.py AppContext: shared state, outlives every screen
  importer.py import decisions                  pure, no Qt
  songlib.py library scan, pairing, status
  settings.py user preferences                  pure, no Qt
scripts/     setup, asset fetchers, import report, screenshots
tests/       967 tests
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
