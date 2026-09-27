# ARCHITECTURE.md — how Guitaroids is put together

A walk through the whole system for someone about to work on it: what each
dependency is for, how a `.gp5` becomes pixels and sound, why the clock is the one
thing that must not be improvised, and where the project's own rules came from.

**This file summarises; it does not decide.** [`DECISIONS.md`](DECISIONS.md) is the
one-screen index of what was decided and its status. [`DESIGN.md`](DESIGN.md) is
append-only and holds the measurements, the rejected alternatives and the dated work
entries — every `§N` below points there, and the higher-numbered section wins when two
of them disagree.

Tests at the time of writing: **980 total, 967 excluding `tests/test_docs.py`.**

---

## Contents

1. [What the program is](#1-what-the-program-is)
2. [Dependencies — all 18, and what each is for](#2-dependencies--all-18-and-what-each-is-for)
3. [The layers](#3-the-layers)
4. [Architectural design and design patterns](#4-architectural-design-and-design-patterns)
5. [Data pipeline: `.gp5` → `Chart` → pixels](#5-data-pipeline-gp5--chart--pixels)
6. [The clock](#6-the-clock)
7. [Input: microphone → pitch → judge](#7-input-microphone--pitch--judge)
8. [The UI layer](#8-the-ui-layer)
9. [Settings](#9-settings)
10. [Testing strategy](#10-testing-strategy)
11. [Six traps this project has already paid for](#11-six-traps-this-project-has-already-paid-for)
12. [Known staleness in the docs](#12-known-staleness-in-the-docs)
13. [The open blocker](#13-the-open-blocker)

---

## 1. What the program is

A Guitar Hero clone where you play a **real guitar into a laptop microphone**. It
reads `.gp5` Guitar Pro tabs, renders them into audio at the tab's own tempo, shows
three bars of notation with a beat line sweeping left to right, and judges what the
microphone hears against the audio device's own clock.

Three facts drive nearly every design decision:

1. **The clock is the sound card's**, because it is the only clock that knows what the
   listener is hearing.
2. **The tab *is* the chart** — no mapping table, `lane = string - 1`, chords kept whole.
3. **The app hears itself.** It generates its own backing track, so through speakers
   the microphone hears the render and the judge awards PERFECT for notes never played.
   This is stated in Preferences rather than solved.

---

## 2. Dependencies — all 18, and what each is for

`requirements.txt` is a `pip freeze` **inventory, not an install spec**.
`tests/test_requirements.py` asserts the file matches the venv it was generated from.

### 2.1 The five that do real work

| Package | Role | Why this one |
|---|---|---|
| **PySide6 6.11.2** (+ `_Addons`, `_Essentials`, `shiboken6`) | Qt **Widgets + QPainter**, deliberately *not* QML | Five of six screens are forms, not a game canvas. `QPainter` for the playfield — see section 8. |
| **PyGuitarPro 0.11** | `.gp3` / `.gp4` / `.gp5` parsing | The only library that reads the format. **Not GP7/8** — a `.gpx` raises, surfaced as `Status.UNSUPPORTED_VERSION`. |
| **numpy 2.2.6** | pitch detection, synthesis, mixing, click track | The Python 3.12 ceiling: 2.5.3 needs `>=3.12`, and 2.2.6 is the newest with a cp312 manylinux wheel. |
| **sounddevice 0.5.6** | PortAudio — output stream *and* input stream | **Owns the audio device exclusively. Qt plays zero audio.** |
| **soundfile 0.14.0** | decodes mp3 / ogg / flac / wav / m4a for backing audio | No resampler needed — the stream opens at the file's own rate. |

### 2.2 The one that must be installed by hand

**`tinysoundfont==0.3.7`** renders a tab into sound from an SF2/SF3, reading the
`Chart` directly — **no MIDI round trip**, which is exactly why `mido` was taken off the
table (§9).

It depends on `pyaudio`, which has **no Linux wheel** and cannot be built without
`portaudio.h`. A bare `pip install -r requirements.txt` therefore **fails**, verified
in a clean venv with a real install — and `pip install --dry-run` exits 0 on it, so a
dry run is not evidence either way.

`scripts/setup.sh` filters it out of the bulk install and installs it `--no-deps`.
That is safe because `pyaudio` is a lazy import used only for real-time playback, and
this project renders offline via `sfload()`. Locked in by `tests/test_audio_deps.py`.

### 2.3 The eight you never import

`attrs`, `cffi`, `packaging`, `pluggy`, `iniconfig`, `pycparser`, `Pygments`,
`typing_extensions` — transitive. `pytest` is present because the suite is a
deliverable here, not an afterthought.

### 2.4 Explicitly removed

`mediapipe`, `opencv-contrib-python-headless`, `cv2` — gone with the webcam (§25). The
requirements file is **18 packages instead of 32**.

This removal mattered mechanically, not just tidily. OpenCV's GUI build ships its own
Qt plugins under `cv2/qt/plugins`, hijacks `QT_PLUGIN_PATH`, and produces
`Could not load the Qt platform plugin "xcb"`. `app.py` used to need a `qtenv.py` to
set `QT_PLUGIN_PATH` before any Qt import — a file that no longer exists. With no
OpenCV there is no import-order constraint at all.

### 2.5 Python version

**3.12 specifically.** `tinysoundfont` ships cp310 and cp312 wheels only; 3.13 and 3.14
compile from source and need a C++ toolchain plus dev headers. §5.1 records a wrong
version analysis caused by checking system `python3` instead of
`.venv/bin/python --version`. `tests/test_docs.py` pins the minor version and fails if
a document quotes a patch number.

### 2.6 Not a package: the soundfont

`assets/soundfont.sf3` — the **FluidR3Mono_GM** build, ~23 MB, by Frank Wen / FluidR3,
**MIT**. Fetched by `scripts/fetch_soundfont.sh`, gitignored, never committed.

MIT is the whole reason. Most Linux desktops ship a soundfont called **TimGM6mb** in
`.sf2` form, which is **GPL-2**, and vendoring it would impose GPL-2 on this project. If the fetch fails that is *not*
fatal — a numpy **pluck** synth takes over. Discovery order is `assets/` →
`$GUITAROIDS_SOUNDFONT` → system paths → fallback, and there is exactly **one**
implementation of that search, `paths.find_soundfont` (§28.1).

---

## 3. The layers

```
  ui/          4,037   widgets, QPainter, navigation, QRunnable wrappers
  session/       653   Judge, PlayRequest, Result — pure, Qt-free
  audio/       1,714   transport, render, click, pitch, mic — the only devices
  model/         772   Chart, Note, BarLine, repeats — pure data, zero I/O
                  ↑
  songlib.py · importer.py · settings.py · paths.py · context.py · app.py
```

Four layers, **strictly one-directional**:

- `ui/` → `session/` → `audio/` → `model/`
- `model/` is **pure**: no Qt, no sounddevice, no audio I/O of any kind.
- `audio/` never imports `session/` or `ui/`.

**Why this is the load-bearing rule and not tidiness:** it is what makes the chart
parser testable with no camera, no audio device and no display — which is most of the
time, on a hackathon. A quarter of the codebase is testable for that reason alone.

### 3.1 There is no `devices/` layer, and the name is still everywhere

The third layer was originally called `devices/`, and §1.3 still lists its contents as
"Transport, **HandTracker, camera**". Both of those were removed — the webcam in §25, the
keyboard that stood in for a microphone in §32 — and `guitaroids/devices/` **does not
exist**. It was an empty directory; `README.md` said so in as many words
(`devices/  (empty; the transport is in audio/)`) until that line was deleted.

What is actually there is `guitaroids/audio/`, and it holds both device handles in the
application: `audio/transport.py:413` opens a PortAudio `OutputStream` and
`audio/mic.py:228` opens an `InputStream`. The **role** is real — `audio/` is the only
code in the tree that talks to hardware. The **name** was fiction, and it has propagated
further than the empty folder:

| Where | Says |
|---|---|
| `DESIGN.md` §1.3 | `L2  devices/   Transport, HandTracker, camera` |
| `DECISIONS.md:70` | "Four layers, `ui/` → `session/` → `devices/` → `model/`" — marked **`live`** |
| `DECISIONS.md:72` | "`devices/` never imports `session/` or `ui/`" — marked **`live`** |
| `AGENTS.md:53-54` | the same two claims, in the file every agent reads first |

So the honest statement is: **the four-layer shape is current, the third layer's name is
not.** `audio/` is the correct name and it has been for a long time. Nothing in the code
depends on the name; the cost of leaving it is that a reader budgeting for a device
layer finds an empty folder, and one who trusts `DECISIONS.md`'s `live` rows is told the
wrong thing twice.

### 3.2 The layering rule is documented, not enforced

This is the more important half of the honesty. **No test parses the import graph.** The
only AST-walking tests in the suite are §35's button-connection checks in
`test_ui_shell.py`. So "strictly one-directional" is a property of the code *today*,
verified by hand — not a property anything will fail a build over.

`DESIGN.md` §36.3 records this as a known gap in its own words: the test that would catch
it — `test_every_import_is_provided_by_a_pin` — is listed as **not written**, and the
entry names the direction as "the direction no test covers". It was reserved for §33,
a number deliberately never reused, so the intent survives without the test.

That distinction matters more here than it might seem, because the six layout and
device traps in `AGENTS.md` *are* test-enforced. A reader arriving at this document from
there will reasonably assume the layering is enforced too. It is not, and a new
`audio/` → `session/` import would pass the suite silently.

### 3.3 The top-level modules

Six modules sit at the package root, outside the ladder:

- `songlib.py` — filesystem I/O over `songs/`, delegating parsing to `model/`
- `importer.py` — pure: the screen asks the user a question, this says whether there is one
- `settings.py` — pure; a versioned JSON file, no Qt
- `paths.py` — the single soundfont search
- `context.py` — shared state, **pinned Qt-free** by a subprocess test
- `app.py` — the `QApplication`, the theme, and the entry point

`context.py`'s Qt-freedom is load-bearing, not stylistic: it is *why* the background
library loader is owned by a screen rather than by the context. Put a `QObject` on the
context and every context test becomes a Qt test.

§36.3 also records a **planned** reorganisation — moving these six into `app/`,
`config/` and `library/` so that only `__init__.py` and `__main__.py` sit at the package
root, with a structural test to keep it that way. It is listed as *not done*.

---

## 4. Architectural design and design patterns

Section 3 said *what* the layers are. This one says **what shapes the code uses** —
and, just as importantly, which ones are framework-provided and which were hand-rolled
for a reason. Naming a pattern is only useful if it is the reason the code has the
shape it has.

Three of the names below are GoF or Fowler; the rest are this project's own recurring
idioms, and the most valuable ones are the last two in this section.

### 4.1 The shape in one line

> A **layered** architecture with a **pure core**, an **inversion of control at every
> boundary** (Qt owns the loop, PortAudio owns the clock, the screen owns the decision),
> and **no mutable state shared downwards** — everything crossing a boundary is an
> immutable value.

It is not MVC, and the difference is not academic: there is no "model" in the UI-layer
sense, because screens hold no domain state at all. `GameState` is owned above them, by
§1.7's rule that screens never own game objects.

### 4.2 Structural patterns

**Layered, with a strict dependency rule** (`ui/` → `session/` → `audio/` → `model/`;
there is no `devices/` — see §3.1).
The rule is one-directional and it is what makes a quarter of the codebase testable with
no device. Verified by hand, not by a test — see §3.2. `session/judge.py` pulls in
only `bisect`, `dataclasses` and `enum`; `session/result.py` only `dataclasses`;
`model/repeats.py` only `typing`.

**Functional core, imperative shell.** Everything that can be a pure function is one:
the tick→seconds conversion, the retiming, the note grouping, the NSDF, the hit-window
verdict, the clock arithmetic. Everything that touches a device is confined to
`audio/transport.py` and `audio/mic.py`.

> **One honest caveat to "pure":** `model/chart.py` imports `guitarpro`. It is pure in
> the sense that matters — no I/O, no devices, no clock, so the chart parser is
> testable anywhere — but it is not dependency-free. `model/repeats.py` *is*, which is
> why repeat unrolling is tested with hand-built headers and no `.gp5` file at all.

**Ports and adapters (dependency inversion).** `ShellBase` (`ui/screens.py:145`) is
declared as a Protocol-shaped class **rather than imported**, so `ui/screens.py` does not
depend on `ui/shell.py` and the two import in any order. It declares exactly three
members — `navigate`, `go_back`, `current` — and a screen may not assume anything else.
That narrowness is the point: `ShellBase` deliberately declares only navigation, which
is *why* `AppContext` had to be invented rather than the shell being widened into a
holder for everything.

**Facade.** `AppContext` is a facade over four things a screen would otherwise reach
for separately: the `Library`, the `Settings`, the `PlayRequest`, and the one device
handle in the app. It is constructed once, owned by the shell, and passed to every
screen as a constructor argument.

**Repository.** `songlib.py` is the repository over `songs/`: `find_tabs()`,
`scan_library()`, `Library.get(slug)`. It owns the filesystem and delegates parsing to
`model/`. Its defining guarantee is that **one malformed file cannot stop the library
loading** — `load_tab()` never raises for bad input.

**Adapter.** Two of them, both earning their place by containing a mess at an edge:
- `classify_error()` (`songlib.py:212`) maps a `ChartError` message onto a `Status`
  enum, so PyGuitarPro's single catch-all `GPException` becomes a typed, displayable
  outcome. Its own docstring admits it is fragile — it matches on message substrings —
  and explains why: the alternatives (sniffing magic bytes, duplicating guitarpro's
  version table) are worse. **This makes `model/chart.py`'s error strings load-bearing API**,
  which is a real coupling cost of the pattern.
- `SongEntry._song` retains guitarpro's parsed `Song` so a different track or a
  different chord setting can be re-charted without re-reading the file.

### 4.3 Object patterns

**Value objects and immutable DTOs, everywhere.** `Chart`, `Note`, `BarLine`,
`Judgement`, `Result`, `PlayRequest`, `Position`, `PitchEstimate`, `RenderResult` are
`frozen=True, slots=True` dataclasses. `Position` is the purest case: it is four floats
and one subtraction, and being a value is what lets the clock formula be tested with no
sound card.

`Result` is frozen *specifically* so that "a published result cannot be edited by
anything that holds it — the results screen and the settings both do."

**Command / request object.** `PlayRequest` is an immutable statement of intent — *this
tab, that track, aligned like this, at this tempo* — carrying no resources. It exists
because long-lived state has to survive a screen being destroyed without travelling
through a widget, and because it is what makes the practice tempo **fixed for a run**:
nothing can re-time a song that is already playing.

**Strategy, in two places, both parameterised rather than subclassed.**
- `CollapseRule` — `HIGHEST` / `LOWEST` / `COMMON`, dispatched in
  `collapse_chords()` (`chart.py:150-172`). Adding a rule is one `elif`.
- `backend="auto" | "soundfont" | "pluck"` in `render_chart()`, which is also
  *graceful degradation expressed as a strategy* (the ladder at the end of this section).

Both validate their input and raise on an unknown value rather than defaulting —
`ValueError` for a bad backend, `ChartError` for an unknown `CollapseRule`.

**Registry with lazy factories.** `ui/shell.py:61-68` is a `dict[Screen, Callable]`
rather than a dict of instances. A built `Game` opens an audio device; a dict of
instances would open one at startup just to show the main menu. `_widget_for()` builds
on first navigation and **caches**, which is why `Game.showEvent` and `Results.showEvent`
must both explicitly reload — the shell is a cache, not a stack that destroys pages.

**Null object and empty states, rather than exceptions and zeroes.** This is pervasive
enough to be a signature of the codebase:
- `AppContext.song_position()` returns **`-1.0`** for "nothing is playing", not `0.0` —
  so "not started" cannot be mistaken for "at the first note", and for the real tab
  those are 3.16 s apart.
- `context.chart_for()` returns `None` for an unknown slug, a vanished track, or a tab
  that no longer parses.
- `load_tab()` returns an entry with a `Status`, never raises.
- A missing `songs/` directory yields an empty `Library` — first run is an empty state,
  not a crash.
- `Results` renders an **explicit empty state** rather than zeroes, because it is
  reachable before any song has been played.

**Platform-scoped singleton.** `QApplication` is one per process with its platform
fixed at construction, so platform tests run in **subprocesses**. Note that
`AppContext` is *not* a GoF singleton — it is passed as a constructor argument, which is
what lets a test build one with a hand-built library and no window behind it.

### 4.4 Concurrency and control-flow patterns

**Inversion of control at the audio boundary — the most consequential pattern in the
project.** PortAudio calls *us*, on its own thread, with a buffer to fill. There is no
feeder thread of ours inside the driver, no `write()` that can block, no handle to join,
and no handle that can be collected while C is still writing through it. This is not
stylistic: the two core dumps (section 6.1 below) were both attempts to invert this
backwards, and the fix was to let the framework own the loop.

**Observer / publish-subscribe, framework-provided.** Every cross-thread and
screen-to-widget edge is a Qt signal, not a direct call:
`LibraryLoader` (`entry_found`, `progress`, `completed`, `cancelled`, `failed`),
`ChartRenderer` (`ready`, `failed`, `cancelled`), and `Game._pitchDetected`.

Two properties are load-bearing:
- **A queued connection is what returns work to the GUI thread.** `on_pitch` runs on
  the microphone's worker; one `emit` and the slot runs on the GUI thread. That is the
  entire mechanism by which the mic thread never touches a widget.
- **Exactly one terminal signal always arrives.** A screen waiting on "scan finished"
  is never left waiting forever, including on cancellation.

**Producer–consumer with a bounded consumer.** The microphone callback copies and
returns; a queue carries blocks to a worker that runs the estimator. The *buffer* is
bounded and drops oldest-first, because a gap in the audio is a missed note while
unbounded growth is a dead process.

**Thread-pool work queue with cooperative cancellation.** `QRunnable` on
`QThreadPool.globalInstance()`, for both the library scan and the chart render. Both
keep a Python reference to the task (a `QRunnable` with no reference can be collected
mid-`run`, and the failure is a silent hang, not an exception) and both **refuse** a
second concurrent run rather than queueing it, because two renders would race to deliver
two buffers and the loser's would start playing at the wrong moment.

**Preallocation over allocation in the real-time path.** The callback writes into a
preallocated view; the interleaved source array is built once *before* the stream opens;
the click track is rendered into a numpy buffer *before* the stream opens; volume is
applied once at construction rather than per callback. This is why the callback is a
memcpy and arithmetic in it was avoidable.

### 4.5 Behavioural patterns

**Template method.** `ScreenBase.showEvent` (`screens.py:103`) pins wrapped-label heights
and then calls `super().showEvent()`. Every screen gets the fix, and the ordering is
part of the pattern — the pin must be in place before the layout pass the event
triggers. `Results.showEvent` extends it with its own reload.

**Graceful degradation, as a ladder rather than a branch.** Every external dependency
has a defined behaviour when it is absent, and none of them crash:

| Missing | Behaviour |
|---|---|
| No soundfont | numpy **pluck** synth (`backend="auto"`) |
| No output device / `audio_enabled = False` | wall clock, still playable, banner says so once |
| Audio device dies mid-song | re-anchor and carry on from where it stopped |
| No microphone | failure recorded, the run continues |
| No backing audio file | metronome-only, a supported state with a badge |
| Corrupt `.gp5` | a `Status` and a reason; the rest of the library still loads |
| Malformed settings file | fall back to defaults |
| No `songs/` directory | an empty library |

The honesty requirement: each of these **says** it happened rather than pretending.
"Its usability is tested by liveness, not the sign of the position" (§26.5) is the same
instinct — a count-in makes the position negative, and pretending otherwise would paper
over a device that had actually died.

**Guard clauses at the risky seams.** Two visibility guards around the render handoff
(`_chart is None`, `not isVisible()`), both closing the same race from both ends.

### 4.6 The project's own recurring idiom: test the seam, not the object

Not a Gang of Four pattern, and the single most productive thing in this codebase.

The failure has occurred **four times**, always the same shape: a component is built
correctly, a test covers the component, and the defect lives in the *connection between
two correct things*.

| | What looked wired | What the test covered |
|---|---|---|
| §21.2 "Collapse chords" | saved, persisted, in `PlayRequest`, printed by `describe()`, six tests | the field, and the loader — but both `loader.start()` call sites omitted the argument, so the loader's default won |
| §35 "Song select" button | created, styled, laid out, next to a working button | the *handler* — `test_play_again_goes_back_into_the_song` passes whether or not the button is connected |
| §28.2 `audio_device` | a field on `Settings`, clamped, serialised | the transport's parameter — which defaulted to `None` and was omitted everywhere |
| §24.4 keyboard fallback | a documented promise in `AGENTS.md` | nothing; it was removed |

The two prescriptions that came out of it, both now applied mechanically:

1. **Test the path the player takes, not the object you built.** §35's fix was two
   tests that *click the buttons*; and when the behavioural version of the general check
   produced three false positives (two correctly-disabled buttons, one file dialog), it
   was replaced with a static AST check — because a rule that needs an exception list is
   a rule that gets edited to pass.
2. **Give any argument a call site could forget a required, no-default parameter.**
   `LibraryLoader.start(collapse=)`, `AppContext.start_playback(volume=, device=)`. A
   required argument cannot be forgotten quietly; a defaulted one can, and the default
   wins silently while the setting looks like it works.

### 4.7 Patterns deliberately not used, and why

| Not used | Why |
|---|---|
| **MVC** | Screens hold no domain state; `GameState` is owned above them. Calling this MVC would hide the actual invariant. |
| **A DI container** | Two seams need it (context, shell) and both are constructor arguments. A container would be more machinery than the problem. |
| **GoF Singleton for `AppContext`** | Constructor injection instead, which is what makes it testable with no window. |
| **A central event bus for app state** | Signals are used for *worker→screen* notification, not for state. Mutable state in one place (`AppContext`) is simpler than routing it through events. |
| **An ORM or database** | Settings is a versioned JSON file written atomically, with a hand-written migration chain. A schema migration framework for eleven fields is not the trade. |
| **Re-render cascades / observer on the chart** | `TabView` is a pure function of `(chart, position)`. A dirty-flag cascade would be more machinery and more state for a widget with one input. |
| **A resampler for tempo** | Time-stretching the audio buffer shifts pitch on every guitar note, and a phase vocoder is large machinery for a problem that does not exist — **because the app generates the audio**, so it re-renders from the retimed chart instead. |
| **Karplus-Strong for the fallback synth** | A per-sample recurrence in Python. A fallback slower than the thing it stands in for is not a fallback. |
| **A pattern for the patterns** | The seam rule is enforced by tests and by review, not by a framework. It is the one that keeps needing enforcement. |

---

## 5. Data pipeline: `.gp5` → `Chart` → pixels

### 5.1 Parse — `model/chart.py`

The heart is `chart_from_song()` at `chart.py:431`, and three details in its measure
loop are load-bearing.

**`Beat.start` is an absolute tick**, not a measure-local one. So
`local_tick = beat.start - header.start` and the unrolled position is
`offset + local_tick` (`chart.py:487-498`). Repeats are handled by a running
`offset += header.length`, applied once per emitted measure. This is why
`model/repeats.py` exists as a separate module.

**`lane = string - 1`, no mapping table** (`chart.py:510`). A real `.gp5`'s existing
fingering lands on the playfield unchanged, because the six lanes *are* the six
strings.

**`pitch = tuning[string] + fret`** (`chart.py:469`, `:514`) — carried for the
pitch-keyed judge and computed whether or not chord collapsing is on.

Tempo validation happens **first**, before any note is read: a tab that changes tempo
is rejected outright (§1.6). And a *track* that changes tempo is **dropped from the
track list** rather than offered and refused — it cannot be charted, so offering it is
a dead end (§20.3). A song with *no usable tempo* is still playable, because that is a
different problem.

Two facts about tracks worth knowing: every track in a GP file defaults to six
strings, so "6 strings" does not discriminate; and `clefTranspose == 12` identifies
bass *independently of the GM programme number*, which matters for hand-written tabs.

**Soundfont preset indexing** is the nastiest trap in the codebase
(`audio/render.py:17-21`): SF2 presets are 0-indexed, GM programmes are 1-indexed.
`preset=25` is GM 26. Pass a tab's `channel.instrument` through unchanged and every tab
plays one programme sharp — which sounds plausible and is wrong.

### 5.2 Chords — `Settings.collapse_chords = False`

**Full chords are the default.** Collapsing to one note per onset is opt-in, and §21.3
measured why: the real tab loses **2991 of its 4099 notes** when collapsed, and 68% of
the survivor ends up on the high E, so the play view stops resembling the tab the
player learned the song from.

A chord is genuinely playable because `GameState` resolves **each lane independently** —
a six-note chord is six simultaneous presses and six PERFECTs.

This is also the project's canonical cautionary tale, so it is worth knowing in full.
The setting was saved, persisted, carried in `PlayRequest`, printed by `describe()` and
asserted by six tests — **and read by nothing**, because both `loader.start()` call
sites omitted the argument and the loader's own default won (§21.2). Every test covered
the field or the loader; none covered the *seam*.

The fix is a shape worth copying: `LibraryLoader.start` takes `collapse` as a
**required, no-default** parameter, so a call site cannot inherit a default quietly.
Every `collapse` default in the project is `False` so none of them can disagree.

### 5.3 Render — `audio/render.py`

Two backends, numpy in and numpy out, no Qt anywhere:

- **soundfont** — `tsf.Synth(samplerate=…)` then `synth.sfload(path, gain=0.0)`. The
  loop walks onset groups: fill silence up to the onset, release finished notes, then
  `noteon` every note in the group on channel 0, bank 0, velocity 110.
- **pluck** — additive synthesis, six harmonics, `exp(-3t)` envelope. Deliberately
  **not** Karplus-Strong: KS is a per-sample recurrence, so rendering a five-minute
  chart in Python takes minutes, and a fallback slower than the thing it stands in for
  is not a fallback.

Three measured facts, each with a way of being got wrong that looks fine:

| Fact | The trap |
|---|---|
| A six-note chord peaks at **0.22**, not 1.0 | §7.5 believed the soundfont clipped for a long time. Cause: `generate()` returns a `memoryview` read as **int16** instead of **float32** (§22). |
| The whole tab peaks at **0.67** | §22 concluded "3.5× gain" from that one chord. A fixed 3.5× applied to the full tab lands at 2.35 and the limiter flattens the dynamics — the exact artefact a limiter exists to prevent. |
| `sfload(gain=…)` is **not** a level control | Gain 0.0 is as loud as 1.0. It must be applied to the **rendered buffer**, and **raised**, because the render is quiet, not hot. |

So the gain is `min(12.0, 0.9 / measured_peak)` — **computed from the render**, not a
fixed multiple — and reported as `gain_applied`. The limiter is `tanh` at a 0.95
ceiling, because a hard clip on boosted guitar is audible as crackle, and it returns
the buffer **bit-for-bit unchanged** when the peak is already under the ceiling, which
is what makes "did limiting do anything" a meaningful test rather than a vacuous one.

One more geometry rule: **a note ends at the next onset**, not at a fixed decay, and
the end travels with the note. Otherwise everything on the beat releases together and
you get a stutter.

### 5.4 The count-in — `audio/click.py`

Pre-rendered into a numpy buffer **before the stream is opened**, because the PortAudio
callback runs at real-time priority and must not allocate.

A click is a decaying sine — 40 ms, `exp(-60t)` — under a **Hann window**. A
rectangular gate is wrong: it has broadband splatter that smears over the note being
played, and a decaying sine starting at full amplitude has a step discontinuity that
sounds like a tick in front of the click. Downbeats are an octave up (1760 Hz vs
880 Hz), which is the entire reason for a click rather than a metronome tone: you can
*hear* the bar line.

`add_count_in` **prepends**, so the music's time zero is no longer sample zero. The
contract between `click.count_in_seconds()` and the game screen's call to it must agree
exactly, or the first note arrives offset from the music by the difference.

### 5.5 Playfield — `ui/widgets/tabview.py`

**Pure render**: `(chart, position) → pixels`, owns no clock. That is the *only* way
this suite checks rendered output — it rasterises to a `QImage` with no audio device
and no event loop, and roughly twenty tests in `tests/test_tabview.py` read pixels
back. Fret numbers are verified by counting coloured pixels inside a mark.

Two axes, and the split is the point:

- **Down the page is time** — one bar per block, discrete. §15's scrolling highway was
  rejected partly *because* it was continuous.
- **Across a bar is the beat** — a line at `x(position)` sweeps left to right, reaching
  the right edge exactly at the bar line, then resets.

**Lane 0 is the top line**, as in printed tab. String names `E B G D A E` go down the
left margin, where printed tab puts them; that deleted the bottom key legend (§18.1)
because one answer beats two.

Measure spans come from `chart.bar_lines`, **not** interpolated from the tempo, and the
same rule governs the click track's beat positions. `bars * beats * 60 / bpm` is a
guess that is wrong the moment a tab has a pickup bar or a repeat that unrolled
unevenly.

A fret number is drawn inside the mark in **all three bars**, sized from
`marker_radius` via `marker_font()` — **never from the stylesheet**. The marker derives
from the widget's height; the QSS font derives from the UI scale; the two are
independent. At scale 1.33 in a 960×640 window the stylesheet's 19px lands in a 17.4px
circle — fine on a large display, broken on a small one, which is the worst way for it
to fail. Below 9px the number is dropped rather than drawn as a smudge.

---

## 6. The clock

The single most important part of the design. Getting it wrong makes the game feel
broken in a way that is hard to diagnose.

```
song_pos = (stream_time - t0) - latency - offset
```

Defined once, in `audio/transport.py:84`, as a frozen dataclass of pure arithmetic — so
the entire formula is testable **with no sound card at all**. All three terms are
mandatory:

- **`stream_time`** — frames the device has *consumed*. A GUI timer is self-consistent
  and cannot tell you whether the game feels right.
- **`t0`** — read from the device in `play()` (`transport.py:431`). **`stream.time` is
  not a count of seconds since you opened the stream**: on this machine's PipeWire
  default it reports `1790470436.39`, about fifty-five years. A `t0` of 0 does not
  raise — it returns a position of 1.8 billion seconds, which reads as a bug in the
  caller rather than in the clock. There is a test asserting the origin is not
  zero-based, with a comment saying which half to believe if a future device disagrees.
- **`latency`** — `stream.latency`. **Omitting it biases every note 10–20 ms early,
  systematically**, which inside a ±35 ms Perfect window reads as "the app is broken"
  rather than as an off-by-ten.

### 6.1 The crash that shaped this module

`audio/transport.py`'s module docstring records two core dumps, which were the same mistake
wearing different clothes: **holding a device handle across a blocking call.**

1. Closing a stream while a feeder thread was blocked inside `write()` — a
   use-after-free in C: *"corrupted double-linked list"*, a PulseAudio refcount
   assertion.
2. When the device stalled, `write()` stopped returning, `stop()`'s bounded
   `join(timeout=)` timed out, and the `OutputStream` — the last reference to it — was
   garbage collected *while C was still writing through it*: `malloc(): unaligned
   tcache chunk detected`.

The fix was to stop feeding audio ourselves at all. **PortAudio's callback** calls
*us*, on its own thread, with a buffer to fill. There is no thread of ours inside the
driver, nothing to join, nothing to collect under, and no write that can block.
`stop()` consequently has **no timeout**, because there is no other thread to wait for.

The callback's rules are three, and each exists for a reason:

- **No allocation.** `outdata` is a preallocated view and the copy into it is a memcpy.
- **No blocking.** Nothing there waits on a device — that is the entire point of the
  callback API.
- **No exception may escape.** An escape leaves PortAudio in an undefined state, and
  both crashes above were the driver's memory being freed while it was still using it.
  So the body is wrapped and a failure degrades to silence.

A spent buffer feeds **silence and keeps the stream running**, so the clock never
stops — `is_running` is a flag, not the stream's `active`, or every note after the end
of the song would be judged against a frozen position.

### 6.2 Resolving the clock on the game screen — `ui/game.py:402`

```
if preparing:            return -1.0
if context.is_playing:   return the audio position
if wall_origin is None:  wall_origin = last_audio_position - elapsed * rate
return wall_origin + elapsed * rate
```

Eleven lines, three decisions:

- **`-1.0` means "there is no clock"**, not "at the first note" — and for the real tab
  the first note is 3.16 s in, so the two are visibly different.
- **The branch is on `is_playing`, not on the sign of the position.** A position is
  legitimately negative for the whole count-in *and* for the first `latency` of any
  song, so a sign test handed the song to the wall clock for its first 46 ms **every
  single time**.
- **Mid-song fallback re-anchors** rather than freezing or restarting, so a lost
  device does not make the song jump.

The wall clock exists at all because §1.5's honest reading is *"unless there is no
audio to drive it from"* — a game that will not start without a sound card is worse
than one that starts slightly wrong. It is started as soon as the audio is, so it can
always take over.

### 6.3 Practice tempo — one multiplication, in one place

`bpm` is **absolute**, not a percentage: a rate is song-relative, so one "80%" cannot
mean "slower" across a library, and BPM is the value a transport needs directly. `0`
means "as written" and is stored as `0`, so a re-exported tab is not pinned at an old
value.

The rate is computed in `game.py:463` from the **written** tempo, and then
`retime(chart, rate)` (`chart.py:108`) divides every note time and bar line while
scaling `chart.tempo`. The ordering is load-bearing: ask an *already*-retimed chart
for a half-speed request and it reports 1.0 — three tempo tests did exactly that until
they were run in the right order.

Because the audio is rendered **from the retimed chart**, the music slows too. Until
§29.2 it did not: the tab slowed and the music did not, which is a worse bug than
doing nothing at all.

Hit windows stay in **milliseconds**, so scores are comparable across speeds.

---

## 7. Input: microphone → pitch → judge

```
PortAudio InputStream (44.1 kHz, mono, 512-frame blocks)
  → callback: copy and return                        mic.py:202
  → queue → worker thread
  → PitchDetector.push: roll a 2048-sample window, hop 512
  → pitch.estimate: McLeod NSDF
  → nearest_midi() → Signal(int) → GUI thread        game.py:645
  → position() − input_latency()                     game.py:662
  → GameState.press_pitch(midi, when)
```

### 7.1 The estimator — `audio/pitch.py`

**Normalised autocorrelation (McLeod NSDF)**, about twenty lines of numpy. §24.3
explicitly rejected a six-bandpass filter bank: it would work on frets 0–5 — which is
most of the tab — and fail on the next song tried.

Every constant is measured against this project's own library, not assumed:

| | Value | Why |
|---|---|---|
| `WINDOW` | 2048 | 46 ms. The library's lowest note is 567 samples per period, so this is **3.6 periods**; 4096 would be 7.2 periods for 46 ms more latency the library does not need. |
| `HOP` | 512 | 11.6 ms between estimates |
| `MIN_RMS` | 0.004 | Judged on RMS, not on clarity — **a room's hum has excellent periodicity and no pitch worth reporting.** |
| `MIN_CLARITY` | 0.55 | The number `AGENTS.md` names as unverified against a real guitar. |
| `OCTAVE_CLARITY` | 0.85 | A peak ≥ 0.85 × best is an octave candidate; take the **smallest lag** among them. |
| `MIN_HZ` / `MAX_HZ` | 60 / 1400 | 24th-fret high E is 1319 Hz. |

**Octave errors are the failure that matters** (`pitch.py:31-42`), because harmonics at
2f and 3f match a *different chart note* — the player is marked wrong for playing the
right one. Parabolic interpolation on the NSDF peak is not decoration: at 82 Hz a single
sample is about 3 cents, audible as flatness and enough to push a note outside a
quarter-tone. (`pitch.py:180` says 4.4, which is arithmetically wrong and is listed
in §12.)

`clarity` **rejects; it does not weight** — a note is either offered to the judge or it
is not. And `nearest_midi()` deliberately has **no tolerance parameter**, though the
first version did: a note is at most half a semitone from *some* equal-tempered pitch,
so a 0.5-semitone tolerance can never reject anything. A knob that looks like it is
guarding something and guards nothing is the same disease as a setting nothing reads.

### 7.2 The input latency correction

An estimate describes the **middle** of its 2048-sample window, so it is ~23 ms behind
the moment it was emitted, before PortAudio's own buffering. Uncorrected that is **a
third of the Perfect window**, walked systematically toward MISS. `Settings.input_latency_ms`
is the manual trim on top, and it is the first thing that setting has ever been read
for.

### 7.3 Judged on pitch, not string

§24.2's reasoning, measured on the real library: of 24 distinct pitches, **13 are
reachable on more than one string**, and MIDI 49 is on three. A detected fundamental
does not identify a lane, so the judge keeps a **pitch-keyed index beside the
lane-keyed one** — and critically, **both share `by_note`**, so a note cannot be judged
twice whichever way it is hit.

The subtlety that makes the whole thing work is about held notes. A held note is
detected on *every* analysis window: at a 512-sample hop, a 400 ms note is heard about
**35 times**. So the two cases diverge deliberately:

- Pitch **is** used by the chart but nothing is resolvable → **`None`, silently**.
  "Already judged, or outside the window" is the same note still sounding.
- Pitch is **not** in the chart at all → **`STRAY`, counted.** This is genuinely the
  player playing something else, so it counts.

`Judgement.lane` is the **chart note's** lane, not the string the detected pitch could
have come from — because the playfield draws what the song asked for, and a hit drawn
on the wrong string would be a lie about the tab.

### 7.4 Hit windows — `session/judge.py`

`PERFECT ±35 ms`, `GOOD ±80 ms`, `MISS` past 140 ms.

The search runs over the **MISS** window, not the GOOD window. A press 100 ms off is
within the 140 ms a note lives for, so it resolves that note as a MISS rather than
falling through to become a stray *and* leaving the note to expire separately — which
would count one mistimed hit as a miss **plus** a stray press.

**Strays are counted and never penalised.** Faking through a solo is practice, not
cheating.

A mistimed press **also** produces a MISS: the 80–140 ms band resolves the note it
lands on, and `judge.py:235` increments the counter. What `GameState.update(position)`
is the only path to is a note the player **never touched** — it expires past its MISS
window on a frame, so a song that ends mid-run reports its misses honestly instead of
waiting for a keypress that will never come.

### 7.5 Three accuracy figures, deliberately separate

- `accuracy` = hits / **notes resolved so far** — the live HUD readout. Hits over the
  whole chart would report a near-zero number three seconds in, for a player doing
  perfectly well.
- `song_accuracy` = hits / **whole chart** — the results screen. The fraction of the tab
  actually played.
- `Result.accuracy` is a *third* computation, deliberately not shared: sharing would
  mean importing a live readout into a finished one, and the next reader would have no
  way to tell which of the two they were looking at.

**There are no points.** No 10,000 maximum, no streak multiplier, no invented weight
per verdict. A score needs a formula to tune and a set of edge cases to test, and the
numbers a player actually acts on are the tally and the percentage. So "best" means
best accuracy, which is the only thing worth comparing between attempts — and it is
recorded **per song slug**, not per track, for consistency with the other per-song data.

---

## 8. The UI layer

### 8.1 Shell — `ui/shell.py`

`QMainWindow` + `QStackedWidget`, with three deliberate choices:

- **Screens are registered as factories, not instances.** A built `Game` opens an audio
  device; building everything at startup would open one just to show the main menu.
  Each screen is built on first show and then **cached** — which is why `Game.showEvent`
  must explicitly reload on re-entry, or the player would resume a finished song.
- **The shell owns the `AppContext`**, which is what makes the context outlive every
  screen. §1.7's "screens never own game objects" applied one level up: a screen may
  borrow the library, but popping it must not close one.
- **`context` is a constructor argument**, not `self.shell.context`, so a screen can be
  built against a context with no window behind it. That is what lets screen tests run
  without a `MainWindow`.

`go_back()` resolves two-tier: LIFO history first, then a static fallback table.
Escape quits at a root screen.

### 8.2 Theme — `ui/theme.py`

Three rules, each with a reason:

- **The stylesheet owns typography.** `app.py` deliberately does not call `setFont` —
  two owners for font size means changing one and wondering why nothing happened.
- **Colours are defined once**, in `COLORS`; everything else interpolates.
- **Every length is a 1080p design unit**, multiplied through `theme.px()`.

```
scale = clamp(screen_height / 1080, 1.0, 1.5)
```

**Height, not width and not area** — a 3440×1440 ultrawide is tall enough to read from a
desk, and at scale 1.0 it rendered a 520px column marooned in the middle of it. The
**lower** clamp is the important one: scaling down would trade the layout bugs for fresh
ones. The upper clamp exists because past ~1.5× a menu stops reading as a menu and
starts reading as a web page, and a 4K panel is viewed from further away than a laptop.

`px(n)` is `max(1, round(n * scale))` — **never below 1**, because a zero-height slider
groove is an artefact, not a very thin groove. `radius(n)` scales as **`sqrt(f)`**,
damped, because 6px → 8px at 1.33 is a noticeably rounder button and past ~12px it reads
as a pill. Boxes scale linearly; corners do not.

The scale is a **module global** set once in `build_application`, because the
alternative is `content_column` and `constrained_button` growing a `scale` argument that
every screen has to remember to pass. `conftest.py` resets it and re-applies the
stylesheet after every test.

`LANE_COLORS` is a blue→red hue ramp, **deliberately not monotonic in brightness** —
two lanes of similar luminance are exactly the pair a player confuses.

### 8.3 The game screen's HUD — `ui/game.py:126`

The score row is **children of the screen floated by `resizeEvent`**, not a layout. A
rhythm game that shrinks its highway to make room for a score is worse than one with no
score. Every label is parented to `self`, because a parentless `QLabel` is a *top-level
window* and `show()`ing one opens a second window floating over the game.

### 8.4 The full game lifecycle — `ui/game.py:194`

```
_load_request()
  ├─ context.chart_for()      → None? empty state; the timer never starts
  ├─ rate_for(request.bpm)    → the rate, from the WRITTEN tempo
  ├─ retime(chart, rate)      → the chart retimed, tempo scaled
  ├─ GameState(chart); _view.set_chart(chart)
  ├─ _start_microphone()      → a device failure is recorded, the run continues
  ├─ _render_audio()          → ChartRenderer.start(); _preparing = True
  │                            "About 7s. Rendering the tab into sound."
  └─ _timer.start()           → 16 ms frames begin; position() = -1.0

_on_audio_ready(result)       [worker thread → queued signal → GUI thread]
  ├─ guard: _chart is None
  ├─ guard: not isVisible() → stop_playback, return
  ├─ add_count_in(samples, level=click_volume)   ← the one moment they are separable
  ├─ context.start_playback(mixed, song_start=count_in_seconds(),
  │                        volume=master_volume, device=audio_device,
  │                        offset=the player's alignment)
  ├─ _preparing = False; _audio_live = True
  └─ _clock.start()           ← started even though the audio clock is what gets read
```

Two guards close a real race: leaving during a six-second render used to let it finish
and then start the song **in the menu**, because nothing called `ChartRenderer.cancel()`.
`hideEvent` now cancels, and the `isVisible()` check closes the remaining window for a
render that landed in the moment between the two.

**The clock does not start while a render is in flight.** Starting it immediately and
handing over when the render landed would make the song jump *forwards* by however long
the render took — seven seconds for a five-minute tab — and forward is the direction that
makes a rhythm game feel broken rather than merely wrong.

The per-frame tick does audio-transition detection **first**, then reads the position,
expires missed notes, pushes the position into the widget, refreshes the tally, checks
whether every note is judged, updates the banner, and publishes a result if the song is
over and the sound has stopped.

### 8.5 Threading contract — `ui/library_loader.py` and `ui/render_task.py`

Two independent workers mirror the same **three-part contract**:

1. **The worker never touches a widget.** It parses or renders and emits; the screen
   decides what goes on screen. A `QRunnable` has no business knowing about a
   `QListWidget`.
2. **A reference to the task is kept.** A `QRunnable` with no Python reference can be
   collected mid-`run`, and the failure is a silent hang in a thread pool rather than an
   exception. `_settle` drops it, because `autoDelete` destroys the underlying object
   once `run` returns.
3. **Cancellation is cooperative**, and **exactly one** of
   `completed`/`cancelled`/`failed` always arrives — so a screen waiting on "scan
   finished" is never left waiting forever.

Qt's automatic queued connections marshal signals onto the GUI thread, which is how
`on_pitch` running on the microphone's worker becomes a GUI-thread slot via one `emit`
and gets off the thread immediately.

`start()` on both returns `False` rather than queueing a second run: two renders of the
same chart would race to deliver two buffers, and the loser's would start playing at the
wrong moment.

Cancellation is **honestly coarser** for rendering. A scan can stop between files, but
rendering is one long C call, so `should_stop` is polled **between onsets** and raises
`RenderCancelled` rather than returning a truncated song. Seven seconds of CPU is spent;
none of it is delivered.

### 8.6 Results

Shown when **the sound stops**, not when the last note is judged. Hotel California's last
note is at 380.5 s and its rendered buffer runs to 392.5 s — leaving on the judgement cuts
off twelve seconds of decaying tail, and the player hears a song stop mid-phrase because
a counter reached zero. On the wall-clock path there is no tail, so "every note is judged"
*is* the end.

Publishing is checked **unconditionally** every frame. The first draft checked only on
the audio-stopped transition, which meant a wall-clock run **never published at all** —
there is no "ended" transition when the audio never started. A test caught it.

`last_result` lives on `AppContext` because `navigate()` takes no payload and the shell
caches screens: a results screen built on the second visit must find the *second* song's
result, and a constructor argument would have found the first. Hence an explicit empty
state rather than zeroes.

---

## 9. Settings

Pure: no Qt, no sound device, no audio input. Three rules, each earned:

1. **A missing file is normal**, not an error. First run writes nothing.
2. **A corrupt file falls back to defaults** rather than crashing.
3. **Out-of-range values are clamped and unknown keys are ignored** — a file written by
   a newer build must not break an older one.

Writes are **atomic** (temp file + rename), so an interrupted save cannot leave a
truncated file that rule 2 then has to rescue.

**`SETTINGS_VERSION = 5`**, with a real migration chain: v2 dropped a stale
`collapse_chords`; v3 dropped `camera_device` and mapped `"camera"` to the microphone;
v4 dropped `input_mode` entirely; v5 added `song_best_accuracy`.

Two mechanisms that are easy to get wrong:

- Migrations run **before** the per-field loop, because the loop is what would otherwise
  resurrect the old default — a v1 file has `collapse_chords` *present with `True` in
  it*, and dropping the key is the entire point of that migration.
- The version is **rewritten upward on load**, as `max(stored, SETTINGS_VERSION)`.
  Without that, a v1 file says 1 forever and every migration ever added re-runs on every
  single launch.

`_parse_number` is deliberately **strict** where `_clamp_float` substitutes a default:
per-song offsets need the difference, because `0.0` means "aligned" and quietly rewriting
garbage to `0.0` would claim an alignment the user never set.

`start_playback(volume=, device=)` are **required with no default** — the same §21.2
prescription as `LibraryLoader.start`. `device` existed, defaulted to `None`, and was
omitted at every call site, so the default silently won and `Settings.audio_device` did
nothing while looking exactly like a setting that worked.

Per-song state — `song_offsets_ms`, `song_best_accuracy`, `song_bpm` — is **all keyed
by slug**. A per-track key would be more precise; consistency with the existing per-song
data won.

---

## 10. Testing strategy

**All 967 tests in the suite run in roughly 50 seconds with no audio device, no display
and no network** (983 collected in total; the 16 in `tests/test_docs.py` are excluded
from that number, because the count is self-referential).

The techniques that make it possible:

| Technique | What it buys |
|---|---|
| `ui/widgets/tabview.py` is pure render → rasterises to `QImage` | ~20 tests read pixels back with no device and no event loop. |
| `Position` is pure arithmetic | The clock formula is tested with no sound card. |
| `AppContext` is Qt-free (subprocess-pinned) | Constructed in a test with a hand-built `Library`; never touches the filesystem. |
| `QApplication` is a process-wide singleton with a fixed platform | Platform tests run in **subprocesses**, never sequentially. |
| `model/repeats.py` has no runtime guitarpro import, only a `Protocol` | Unit-tested with hand-built headers and no `.gp5` at all. |
| `tests/test_docs.py` checks the documents against the repository | Counts, `§N` references resolving, file paths that exist, superseded claims staying superseded. |

That last row deserves expanding, because it is unusual. `test_docs.py` exists because
§27 fixed a false claim in `requirements.txt` and wrote down that it had fixed *all*
such claims — and `README.md` was still asserting that `setup.sh` was the only
supported install path, two
paragraphs away, because the test looked at one file. So the tests assert the things that
rot on their own: test counts, section references, file paths, and superseded claims
(a claim can be wrong *for a while* and still read as current, which is the dangerous
kind). `planned` rows are deliberately **not** checked — a `live` row must be true today;
a `planned` row is a statement of intent.

And §35, the most recent work entry, added a **static** check that no button in
`guitaroids/ui/` is created without its `clicked` being connected in the same file. The
first version was behavioural — walk every screen, click every button — and it reported
three false positives, all of which were correct behaviour (two buttons correctly
disabled, one that opens a file dialog a test cannot click through). A rule that needs an
exception list is a rule that gets edited to pass, so the check reads the AST instead:
no exceptions, no false positives, and it needs no window.

The song library can be checked without launching the GUI:

```
.venv/bin/python scripts/import_songs.py --json   # exits 1 if anything is unplayable
.venv/bin/python scripts/screenshot_ui.py --all   # PNGs in /tmp/opencode/ui
```

Screenshots pin scale 1.0 and a 960×640 frame so they stay comparable run to run.

---

## 11. Six traps this project has already paid for

Each of these cost real time, and each is now enforced by a test.

**A layout that does not fit does not clip — it compresses.** Children get squeezed
below their minimum height and end up drawn on top of each other. This shipped a
preferences screen with three overlapping combo boxes (§19.2), and later a song-select
detail card whose six fact rows were drawn on top of each other. Tall forms go in a
`QScrollArea`, and `test_ui_shell.py` fails if any screen squeezes **any widget a layout
owns** — the narrow version only checked group boxes, which is why the card got through.
Related: `QScrollArea.setWidget()` takes ownership, so return the *container*, never the
scroll area's widget, or it is deleted out from under the layout.

**A vertical layout with no stretch item shares surplus height *equally*.** Both
`QLabel` and `QGroupBox` can grow, so on a tall window every one gets the same slice of
the extra and the page opens holes between its own paragraphs. This shows up on big
screens and not small ones.

**A word-wrapped `QLabel`'s minimum is smaller than its text needs.** A layout that
economises hands out `minimumSizeHint` and the last line is silently not drawn — Import
GP shipped a sentence ending mid-thought. `ScreenBase.showEvent` pins every wrapped
label's `minimumHeight` to its `sizeHint`, before `super().showEvent` so it is in place
for the layout pass the event triggers. Do not remove it, and do not add a wrapped label
without a test that its height covers its text.

**`AppContext` must stay free of Qt.** It is pinned by a subprocess test, and it is the
reason the background library loader is owned by a screen rather than by the context.

**The app opens full screen, and the UI scales with the screen.** Every length must go
through `theme.px()` — in the QSS, in `content_column`, in `constrained_button`, and in
raw `setContentsMargins`/`setSpacing` calls. A hardcoded pixel will not move and will
look wrong next to everything that does. The window's *minimum* size has to scale too,
or the enlarged UI is starved of room.

**A setting that nothing reads looks exactly like a setting that works.** "Collapse
chords" was the canonical case (§21.2) and the "Song select" button on the results
screen the most recent (§35). Four times now, the defect has been at a **seam** that no
test covered, while every test covering the field or the object passed. The prescription
is fixed: **test the path the player takes, not the object you built**, and give any
argument a call site could forget a **required, no-default** parameter.

---

## 12. Known staleness in the docs

Found while writing this file, and not yet fixed. Listed because §25.4's argument is
that an absence has to be tested against the artifact — these are absences of a
different kind: **claims that are still there and are no longer true.**

| Where | Says | Actually |
|---|---|---|
| `DECISIONS.md:191` | the pitch-keyed judge is `planned`; *"`press_pitch` is nowhere in the tree"* | It exists at `judge.py:239` and is wired at `game.py:663`. Marked `planned`, so `test_docs.py` does not check it. |
| `DECISIONS.md:207` | "Keys **1–6** for lanes 0–5, hard-coded" is **`live`** | The keyboard is gone (§32). `ui/game.py` has no `keyPressEvent` override and no key handling. This row is marked `live`, which means the doc test *should* have caught it — but that test only checks a `live` row **cites** a real section, not that its claim is still true. |
| `ui/game.py:1-28` | "The clock here is a **wall clock** (`QElapsedTimer`)… Key mapping is `1`-`6`" | The audio device's clock is primary (`game.py:402`); the keyboard is gone. There is also a dangling `#: Digit keys 1-6` comment at `game.py:55` with no constant under it, and `game.py:650` refers to a `:meth:`_press`` that does not exist. |
| `model/chart.py:443` | `collapse` "Defaults to `True`" | The signature says `False` (§21.3). |
| `audio/pitch.py:52` | `WINDOW` is "1.9 periods… 140 ms miss window" | The module docstring's corrected figures: **3.6 periods**, 280 ms. The constant's own docstring predates the correction. |
| `audio/render.py:188` | the long 0-vs-1-indexed preset narrative | `program = FALLBACK_PROGRAM` is **hardcoded** — `chart.channel.instrument` is never read by the render path, so `preset_for()` is exercised only by tests. |

That last row is the only one with teeth in the code. The "one programme sharp" trap the
docstring warns about is currently defended against — but only against a constant, so
the defence is untested on the path that matters.

---

## 13. The open blocker

**Everything is built except one thing that has to be played.** The clock is the audio
device's, a real tab plays through a real sound card, the practice tempo slows the
music, and the input half is wired end to end: `audio/pitch.py` finds a note,
`audio/mic.py` opens a microphone, and the judge matches a detected pitch.

What does not exist is a **verified** note detector. §29.3 checks the estimator against
this project's own synthesis, which is cleaner than a real guitar through a laptop
microphone — no fret buzz, no room, no sympathetic resonance. The next step is playing
an actual guitar and seeing what `MIN_CLARITY` and the analysis window need.

Two honest consequences of that gap:

- **The microphone is the only input** (§32). The keyboard fallback is gone, so a
  machine with no working microphone cannot play the game at all. That is stated rather
  than papered over, but it is a real regression in demoability.
- **The app hears itself.** Headphones are not a recommendation, they are a
  requirement, and that is why the warning is a visible line in Preferences rather than
  a tooltip.
