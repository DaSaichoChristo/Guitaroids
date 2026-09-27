# Guitaroids

A Guitar Hero-style app. You play your own guitar and it walks you through Guitar
Pro tabs at tempo — it renders the tab into sound and times your notes against the
audio device's own clock.

**Hackathon project, and every layer is built:** song import, note reading, audio
generation, playback, a microphone, and all six screens. The input is your guitar —
`audio/pitch.py` works out which note you played and the judge matches on **pitch**
rather than string, because the strings' ranges overlap (MIDI 49 is reachable on three
of them). The six on-screen keys that stood in for a microphone are gone (`DESIGN.md`
§32). **Wear headphones** — see below.

**What has not been done is the one thing that needs a human and an instrument.** The
note detector is verified against this project's own synthesised audio, which is
cleaner than a real guitar through a laptop microphone, and every layer above it is
verified against synthetic charts. Playing an actual guitar is what will tell you
whether `MIN_CLARITY` and the analysis window are right (`DESIGN.md` §29.3).

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

## The one constraint worth knowing up front

`pyaudio` cannot be installed here: no Linux wheel, and no `portaudio.h` to build one.
Everything else follows from that, and both halves are enforced by tests.

- **So `pip install -r requirements.txt` fails**, which is why `scripts/setup.sh` is
  the install path rather than a convenience. The file is `pip freeze` output, so it
  lists `tinysoundfont`, which depends on `pyaudio`. The script filters that one
  package out of the bulk install and installs it with `--no-deps`
  (`DESIGN.md` §31.2). This was the other way round once: §27 verified that a bare
  install worked, back when the requirements file was curated and left
  `tinysoundfont` out. The claim was corrected when the file became a freeze dump.
- **And `--no-deps` is safe**, because `pyaudio` is a lazy import used only for
  real-time playback, and this project renders offline with `sfload()` instead. Locked
  in by `tests/test_audio_deps.py`. Note `pip install --dry-run` reports success here
  even though a real install fails, so a dry run is not evidence either way.

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
  app.py       QApplication, stylesheet, UI scale     the process bootstrap
  ui/          six screens, theme, background loader
  audio/       render, click, transport,
               pitch, mic                              the master clock lives here
  session/     PlayRequest, judge, result             what to play, and how it scores
  model/       Chart, Note, repeat unrolling          pure data, but it reads the tab
  songlib.py   library scan, pairing, status
  importer.py  import decisions                       pure, no Qt
  settings.py  user preferences                       pure, no Qt
  paths.py     where everything lives on disk         pure, no Qt
  context.py   AppContext: shared state, outlives every screen
  devices/     (empty; the transport is in audio/)
scripts/       setup, asset fetchers, import report, screenshots
tests/       1057 tests
songs/       your tabs and audio (gitignored)
```

`model/` reads a `.gp5` and parses it — `chart_from_gp5` is the only thing in the
project that knows the Guitar Pro file format. It is free of Qt, OpenCV and
sounddevice, and it has no audio and no clock, but "zero I/O" is not true of it.

**Pinned free of Qt, OpenCV and sounddevice by subprocess tests:** `context.py`,
`importer.py`, `paths.py`, `settings.py`, `session/judge.py` and
`session/play_request.py`. That is what keeps them testable in milliseconds with no
display — and it is why the background library loader is owned by a screen rather than
by the context. `model/` and `songlib` are not in that list: they import none of those
three, but nothing asserts it.

**`songlib.py`, `importer.py` and `settings.py` sit at the package root** rather than
in folders, and there is a plan to move them into `library/`, `config/` and `app/`.
Not done — `DESIGN.md` §36.3 records what is outstanding and why the number reserved
for it (§33) was never written under.

## Attribution

- **FluidR3** soundfont (MIT) — Frank Wen. Fetched by `scripts/fetch_soundfont.sh`,
  not committed. See `assets/ATTRIBUTION-soundfont.md`.
- Tabs and audio in `songs/` are yours; record provenance in
  `songs/ATTRIBUTION.md`.
