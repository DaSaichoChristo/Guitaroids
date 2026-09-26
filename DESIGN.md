# DESIGN.md — design & work log

Guitaroids. A Guitar Hero-style app that tracks your hands via webcam and walks you
through Guitar Pro tabs at tempo, counting misses.

**This file is the project's memory.** §1 is the original plan. Everything after that
is dated work entries.

> **Want the decisions, not the history?** Read [`DECISIONS.md`](DECISIONS.md) — a
> one-screen index of every current decision with a status and a link to the
> section here that justifies it. The pointer is the only navigational addition
> this preamble has ever received; no section has been edited.

## The convention (this file's rule about itself)

This log is **append-only**. Each unit of work appends a new numbered, dated section
at the end. Earlier sections are never edited.

When something later turns out to be wrong, do **not** fix it in place. Append a
section that names the section it overturns by number, states what was believed, what
the evidence showed, and what changed. **The higher-numbered section wins.**

Each section ends with an explicit **Not done** list: what was skipped, what was
deferred and why, and what is still unverified. Claims carry their evidence — what
ran, what the numbers were. An assumption that could not be checked is written as an
assumption, in those words.

Sections are addressed as `§N` and `§N.M`. They are stable addresses, so "§4.1
supersedes §2" resolves without ambiguity.

---

## §1 — Plan (2026-09-26)

First pass. No code written. Decisions below were reached by discussion; the
dependency facts in §2 were verified against upstream sources.

### §1.1 Product

Walk a `.gp5` tab at the tab's own tempo. A 6-lane note highway scrolls toward a hit
line. The player selects a lane with their fretting hand and strikes with their
strumming hand. Misses are counted. `.gp5` files carry no audio, so sound is a
metronome built from `song.tempo`, plus an optional backing audio file placed
alongside the tab.

### §1.2 Stack

| Package | Role |
|---|---|
| PySide6 6.11 | Qt **Widgets + QPainter** (not QML — a note highway wants direct 2D control) |
| mediapipe 1.0.1 | Hand landmarks, Tasks API only |
| opencv-contrib-python-headless | Camera capture (forced transitive dep — see §2.2) |
| PyGuitarPro 0.11 | `.gp5` parsing (already in `requirements.txt`) |
| numpy ≤ 2.2.6 | Python 3.10 caps the available numpy |

`mediapipe` pulls `sounddevice~=0.5` transitively, so audio output arrives free.

### §1.3 Layering

Four layers, strictly one-directional. Nothing below imports anything above it.

```
L4  ui/          widgets, QPainter, navigation
L3  session/     GameSession, Judge, scoring
L2  devices/     Transport, HandTracker, camera
L1  model/       Chart, Note, TimeScale   (pure data, zero I/O)
```

Two rules hold it together:

- **L1 is pure.** `charts.py` imports no Qt, no OpenCV, no sounddevice. This is what
  makes the chart parser testable with no camera, no audio device, and no display —
  which is most of the time, on a hackathon.
- **L2 devices never import L3/L4.** `HandTracker` does not know what a lane is. It
  emits normalized hand positions and strum events; `GameSession` assigns meaning.
  Swapping camera input for keyboard becomes a one-file change.

### §1.4 Threading

| Thread | Owns | Rule |
|---|---|---|
| Main (Qt) | all widgets, paint, key events | never calls `cap.read()`, `write()`, or inference |
| Audio | PortAudio stream, click track | **master clock** |
| Capture + tracking | `cv2.VideoCapture`, `HandLandmarker` | emits frames and landmarks |

Cross-thread communication is signals carrying **immutable snapshots**, never shared
mutable state. If tracking stalls, rendering keeps 60fps and holds the last pose.

**`VIDEO` mode, not `LIVE_STREAM`.** `LIVE_STREAM` + `detect_async` silently drops
frames when busy; in a rhythm game, silently dropped input frames are invisible
latency. `VIDEO` + `detect_for_video(image, timestamp_ms)` processes every frame we
hand it, timestamped by us, on our own thread.

### §1.5 The clock

`Stream.write()` returns when the device has **consumed** the samples, not when they
are audible. Two consequences, both from the sounddevice 0.5.6 docs:

- `Stream.time` is a free-running clock — *"Starting and stopping the stream does not
  affect the passage of time as provided here."* It is **not** a song position.
- `Stream.latency` is the device's output latency.

So audible song position is:

```python
song_pos = (stream.time - t0) - stream.latency
```

Both terms are required. Omitting `latency` makes every note read 10–20ms early,
systematically — inside a ±35ms Perfect window that is a persistent bias that reads
as "the app is broken." Also: `blocksize=0`, and surface `stream.cpu_load`,
`status.output_underflow`, and `write()`'s underflow return in a debug HUD, because
audio glitches otherwise look identical to application bugs.

### §1.6 Timing model

```python
seconds = beat.start / 960 * (60 / song.tempo)    # 960 = Duration.quarterTime
```

`beat.start` is an absolute tick. Hit windows: Perfect ±35ms, Good ±80ms, Miss past
140ms. One bar of count-in before t=0.

**Lane = the tab's string number − 1.** Existing `.gp5` fingering lands on the
highway with no mapping table. `fret` is carried for display only; lane is judged.

### §1.7 Screens

`QStackedWidget` over a `Screen` enum. One rule: **screens never own game objects.**
Leaving a screen tears down its widgets; `GameSession` outlives them and is passed
into the replacement. Otherwise audio streams and camera handles leak on every
navigation and the app crashes on the third song.

### §1.8 Milestones

1. Shell + song select — scan `songs/`, parse `.gp5`, list title/artist/tempo.
2. Highway + transport + metronome, **keyboard test mode** (6 keys).
3. Judging, scoring, results screen, miss counter.
4. Hand tracking + calibration + lane mapping, keyboard retained as fallback.
5. Optional backing audio, polish.

Milestone 2 is deliberately playable with no camera. It is the main de-risking move:
the entire game becomes debuggable while tracking is still broken.

### Not done — §1

- **No code written.** Nothing in this section has been executed.
- **No dependency installed.** No claim here is backed by a running import.
- **No `.gp5` file is present in the repo.** Milestone 1 cannot be verified until
  someone sources a tab. Unresolved and blocking.
- **Empty scaffolding already in the repo** — `business/`, `data/`, `presentation/`
  (all empty directories) and a zero-byte `main.py`, all untracked. Their intended
  relationship to the `guitaroids/` package layout in §1.3 is **unconfirmed**. Not
  reconciled; flagging rather than overriding.
- Unanswered from the user: hammer-on/pull-off support (§1.6 judges strums only);
  tempo-change handling; camera mirroring direction; difficulty filtering.
- No tests written. §1.3's testing seams are described, not built.

---

## §2 — Dependency research (2026-09-26)

Verified against upstream sources before any install. Each finding cites what was
checked. This section exists because two of these silently invalidate the plan in §1.

### §2.1 mediapipe 1.0 deleted the legacy hand API

`mp.solutions.hands` no longer exists. Google describes that legacy path as
"completely ended." Only the Tasks API is available:

```python
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

options = vision.HandLandmarkerOptions(
    base_options=python.BaseOptions(model_asset_path="assets/hand_landmarker.task"),
    running_mode=vision.RunningMode.VIDEO,   # not LIVE_STREAM, per §1.4
    num_hands=2,
)
```

Result object, read from `mediapipe/tasks/python/vision/hand_landmarker.py` @ master:

- `hand_landmarks: list[list[NormalizedLandmark]]` — normalized image coords
- `handedness: list[list[Category]]` — `category_name` is `"Left"`/`"Right"`,
  plus `score`, `index`
- `hand_world_landmarks: list[list[Landmark]]` — metric coords

`handedness` and `hand_landmarks` are **index-parallel lists**, not separate
per-handedness fields.

Requires a model file. Verified reachable, HTTP 200, 7,819,105 bytes:

```
https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task
```

Decision: **vendor it into `assets/`** so a fresh clone needs no network.

### §2.2 The OpenCV ↔ PySide6 conflict is real — highest risk in the stack

mediapipe 1.0.1's `requires_dist` includes **`opencv-contrib-python`** — the GUI
build, not headless. That build ships its own Qt plugins under `cv2/qt/plugins`,
which hijack `QT_PLUGIN_PATH` and produce the documented failure:

```
qt.qpa.xcb: could not connect to display
Could not load the Qt platform plugin "xcb" in ".../site-packages/cv2/qt/plugins"
```

This is why projects built on this combination fall back to pygame. Mitigation, in
order:

1. Uninstall `opencv-contrib-python`; install `opencv-contrib-python-headless` at the
   **identical version number.** Mismatched cv2 builds are a separate bug class.
2. Defensively set `QT_PLUGIN_PATH` to PySide6's bundled plugins in the entrypoint,
   before any Qt import.
3. Never call `cv2.imshow` — headless costs nothing here.

**This is the first thing to test, on day one, before writing game code.** If §1.2's
stack cannot open a window, the framework choice in §1.2 is wrong and everything
downstream changes.

### §2.3 Audio device ownership

Qt does **zero** audio playback; `sounddevice` (PortAudio) owns the device
exclusively. PortAudio cannot share a device portably, and we need the audio clock
from §1.5 regardless. This removes a class of conflict rather than managing one.

### §2.4 sounddevice API notes (0.5.6)

- `OutputStream(...).write(data)` blocks until the device consumes the buffer.
  Blocking write mode from a dedicated thread is sufficient; a stream callback is
  not required.
- `write()` returns an `underflowed` bool. Track it.
- The stream callback runs at real-time priority and must not allocate, do file I/O,
  or call PortAudio. Therefore the click track is **pre-rendered into a numpy buffer
  before the stream starts**, and the audio thread only slices it.
- `latency='high'` is the default and is "typically too large for interactive
  applications" per the docs — actively wrong for a rhythm game.

### §2.5 Python version ceiling

- numpy 2.5.3 declares `requires_python >= 3.12`. On this repo's Python 3.10.12, the
  newest numpy with a cp310 manylinux x86_64 wheel is **2.2.6**.
- mediapipe 1.0.1, PySide6 6.11.2, and sounddevice 0.5.6 all ship cp310 wheels.
- opencv-python-headless uses `cp37-abi3` wheels, which install on 3.10.

### Not done — §2

- **Nothing was installed and no import was run.** Every finding above is from
  upstream metadata and documentation, not from this machine. §2.2 in particular is
  read from issue reports and must be reproduced locally before it is trusted.
- The wheel-version compatibility *between* mediapipe 1.0.1 and a specific
  opencv-headless build is unverified. §2.2 requires a matching version number but
  the required number was not determined.
- The 7.8MB model file was confirmed reachable but **not downloaded** and not yet
  committed to the repo.
- mediapipe's `VIDEO`-mode behavior under a slow capture loop is untested; §1.4's
  reasoning is from documentation, not measurement.
- §2.4's `latency` magnitude on the actual demo hardware is unknown, so the size of
  the bias described in §1.5 is estimated from the docs, not measured.

---

## §3 — Backing audio pipeline (2026-09-26)

Decision: backing audio is **in scope for v1**, not optional. §1.1 called the audio
file "optional"; this section makes it a first-class input. It does not contradict
§1.1 — a tab with no audio still plays, in metronome-only mode.

§1.2's audio decision (`sounddevice` owns the device, Qt plays nothing) is
**upheld**, and §2.3's reasoning is confirmed by the Qt API survey in §3.2.

### §3.1 Decoder selection — soundfile

Surveyed every candidate for a self-contained cp310 wheel that decodes mp3:

| Package | Verdict | Evidence |
|---|---|---|
| **soundfile 0.14.0** | **chosen** | Self-contained `manylinux_2_28_x86_64` wheel, `requires_python >=3.10`. MP3 via libsndfile 1.1.0+, which added MPEG decode using libmpg123. Only dep is numpy. |
| `av` 18.1.0 | rejected | `requires_python >=3.11`. Unavailable on this repo's 3.10.12. |
| `pydub` 0.25.1 | rejected | No wheels; requires an `ffmpeg` binary on the host machine. Unacceptable for a demo on an unfamiliar laptop. |
| `miniaudio` 1.71 | held in reserve | `cp310 manylinux_2_24` wheel, one dep (cffi), decodes mp3/flac/vorbis. Note: published as `miniaudio`, **not** `pyminiaudio` — the latter is 404 on PyPI. |
| `soxr` 1.1.0 | fallback only | `cp310` wheel present. Needed only if a device rejects the file's native rate. |

**No resampler in the happy path.** The PortAudio stream is opened *at the audio
file's own sample rate* and the host API's converter handles the difference.
`stream.time` is in seconds regardless of rate, so §1.5's formula is unaffected.

### §3.2 The clock survives; Qt is ruled out with numbers

§2.3 rejected Qt audio on the grounds that PortAudio and Qt fight over the device.
That reason is weaker than the real one, which is that **Qt cannot supply a
device-accurate position at all**:

- `QMediaPlayer.position()` — disqualified. Observed `positionChanged` granularity
  is ~50ms (`1828 1828 1828 1880 1880 1880 1933`), and on macOS it snaps to
  keyframes. Against a ±35ms Perfect window this is not a clock.
- `QAudioSink` — capable but not equivalent. It exposes `elapsedUSecs()` and (new in
  6.11) `processedUSecs()`, but Qt's own source shows `elapsedUSecs()` returns
  `d->elapsedTime.nsecsElapsed()` — a **wall clock**, not a device clock. Qt 6.6+
  docs state the sink's buffer size is "determined by the audio backend", so the
  term §1.5's `- stream.latency` depends on is neither controlled nor readable.
  The default ringbuffer is 250ms, which is an order of magnitude past the Perfect
  window.

PortAudio remains the choice because its docs make the intended use explicit:
`Stream.time` "may be used for synchronizing other events to the audio stream, for
example synchronizing audio to MIDI," and `Stream.latency` is the implementation's
own measured estimate rather than a backend-determined unknown.

### §3.3 One buffer, one stream, one clock

Resolves the "which clock is authoritative" question permanently — the click and the
music are the same samples, so they cannot disagree.

```
buffer = [ count-in bars: click only ][ song: music + additive clicks @ 0.25 gain ]
                                  ↑ song_pos = 0 here
```

Clicks are summed over the music rather than ducking it — cheaper, and good enough
at low gain. Click generator produces samples at the stream's rate, so click
placement is exact by construction.

```python
transport_pos = (stream.time - t0) - stream.latency
song_pos      = transport_pos - count_in_seconds - user_offset
```

This **extends** §1.5 rather than replacing it: same clock, same `- stream.latency`
term, plus two constant offsets.

### §3.4 Conventions and degradation

- **Pairing:** `songs/<slug>.gp5` + `songs/<slug>.{mp3,ogg,wav,flac}`, same basename,
  extension search ordered. Missing audio → **metronome-only mode**, badged in the
  UI. Not an error path.
- **Offset:** manual slider, persisted per song, default 0. Tabs are transcriptions
  and the music rarely starts exactly at tick 0. Auto-detection by onset
  cross-correlation is a stretch goal, explicitly not v1.
- **Count-in:** configurable 0–2 bars, **default 1 bar**. Songs whose audio already
  opens with a lead-in turn it to 0.
- **Duration mismatch:** play to `max(audio_length, last_note_time)`, then fade out.
- **Attribution:** audio files are not committed. `.gitignore` covers them;
  `songs/ATTRIBUTION.md` records provenance. If CC-BY music is used, a credits line
  is a product requirement, not a nicety.
- **Decode** happens on a worker thread with a progress indicator and cancel;
  a 4-minute stereo 48kHz float32 buffer is ~92MB, allocated up front.

### §3.5 The highest-value test in the project

The timing core is verifiable **with no sound card, no camera, and no display**.
Build a mix buffer for a synthetic chart at a known tempo and assert every click
lands on its exact expected sample index:

```python
assert click_indices == [i * samplerate * 60 // bpm for i in range(beats)]
```

That one assertion exercises chart parsing, tempo math, buffer assembly, and the
click generator together. It is scheduled **before** the highway widget, not after,
because it is the only cheap way to catch a tempo or offset bug before a human
notices it as "the game feels wrong."

### Not done — §3

- **No decoder was installed or executed.** §3.1 is wheel-metadata research. Whether
  soundfile 0.14.0 decodes the actual mp3s cleanly on this machine is untested.
- **An mp3 decode edge case is known but unmeasured:** libsndfile's changelog lists a
  fix for "Reading MP3 files without Xing or INFO headers," so variable-bitrate
  files without headers should work — but the specific files have not been tried.
- **ogg from ffmpeg is best-effort.** libsndfile has a documented history of failing
  on some ffmpeg-produced opus streams. Prefer mp3 or wav; treat ogg as untested.
- **The `soxr` fallback path does not exist yet** and the device-rejects-the-rate
  condition has never been observed. It is speculative.
- `stream.latency` in §3.3 is still an estimate, and the 10–20ms bias described in
  §1.5 is still inferred from documentation, not measured on this hardware.
- **No audio files and no `.gp5` files exist in the repo.** The whole pipeline is
  unexercised end to end, and this remains the top blocker.
- Hammer-ons/pull-offs remain unimplemented; §3.3's `song_pos` says nothing about
  whether a note may be satisfied by fretting alone.

---

## §4 — Input latency, and a revised milestone order (2026-09-26)

### §4.1 Input latency now dominates

§1.5 and §3.3 make the **output** side accurate. The **input** side is worse, and
§3 made it matter: a player following real music is far more sensitive to timing
error than one following only a click.

Evidence, from a shipping rhythm-game codebase (Funkin, PR #7790) measuring the
same class of problem for *keyboard* input:

> "there is latency between the exact frame the input was pressed and the frame at
> which the input is actually being processed… Tested it out and the latency seems to
> be framerate dependent, on 60 FPS I was getting around ~15-20ms while on 30 FPS it
> was above 30ms."

That is for keys. Our path is strictly longer:

```
camera buffer (~30-100ms) → capture thread → inference → lane decision → judge
```

A typical webcam buffers more than the entire audio chain above was designed to
correct, and the figure scales with camera hardware rather than with our code. It is
plausibly the largest single error term in the product.

Consequences, both new:

- **A per-user input-latency offset** is required before hand tracking is usable:
  auto-estimated by tapping along to the metronome, plus a manual slider override.
- **Raw input timestamps are logged**, so the real number is measured rather than
  assumed. The estimate above is from someone else's codebase on other hardware.

### §4.2 This supersedes §1.8's milestone order

§1.8's five milestones are preserved in substance. The ordering gains a hard gate
and one new step. Per this file's convention, §1.8 is left standing and superseded
here rather than edited.

- **M0 — unblock (new gate).** Install, resolve §2.2's cv2/Qt plugin conflict,
  **open a window**. Nothing else starts until this passes. If it cannot pass, the
  §1.2 framework choice is wrong and every later section needs revisiting.
- **M1 — pure core, no hardware.** Chart parser, `build_mix_buffer`, and §3.5's
  sample-exact click test.
- **M2 — shell + song select.** Scan `songs/`, parse, pair with audio, offset and
  count-in settings.
- **M3 — highway + transport + results.** Judging, scoring, miss counter. Playable
  on 6 keyboard keys with no camera.
- **M4 — input-latency calibration (new).** Tap-along estimator + manual slider.
  Lands *before* tracking, per §4.1.
- **M5 — hand tracking + hand calibration.** Fret-hand x → lane with EMA and
  hysteresis, strum detection. Keyboard retained as fallback throughout.
- **M6 — polish.** Credits/attribution line, difficulty filter, hammer-ons if in
  scope.

The ordering principle: **prove the timing chain offline (M1), then make the game
playable without a camera (M3), so tracking is never on the critical path.**

### Not done — §4

- **The 30–100ms camera figure is not measured on this hardware** and no camera has
  been tested. It is a documented-typical range, cited as such.
- The Funkin numbers are for a different engine, input device, and machine. They
  establish that the effect is real and framerate-dependent; they do not predict our
  value.
- §4.2 changes ordering only. No milestone in it has started.
- Whether the tap-along estimator can separate *input* latency from the player's own
  reaction bias is unresolved — it likely cannot, which may make it a coarse
  calibration rather than a measurement. Untested.
- The §1.6 open questions (hammer-ons, tempo changes, camera mirroring, difficulty
  filtering) are all still open. None is answered in this section.

---

## §5 — M0 executed: the gate passes (2026-09-26)

M0 is the hard gate from §4.2. **It passed.** This is the first section in the log
backed by a command that actually ran on this machine.

### §5.1 §2.5's Python version analysis was wrong — the venv is 3.14

§2.5 analysed version ceilings against **Python 3.10.12** and concluded numpy was
capped at 2.2.6, and §3.1 rejected `av` 18.1.0 for requiring `>=3.11`. Both rest on
a wrong premise.

The repo's `.venv` is **Python 3.14.6**, not 3.10. The system `python3` is 3.10.12,
which is what misled the earlier check. Worse, the venv had been
`venv --upgrade`d from 3.10 to 3.14, which left two `site-packages` trees:

```
.venv/lib/python3.10/site-packages/   <- guitarpro, PySide6 (orphaned, unusable)
.venv/lib/python3.14/site-packages/   <- pip only; every real dep was missing
```

`pip list` reported a single package: `pip`. Nothing from `requirements.txt` was
actually installed in the interpreter that would run the app. The earlier
`PyGuitarPro` reads in §1 were against the **orphaned 3.10 tree** and happened to be
valid for API shape, but nothing had been installed for the real interpreter.

Corrections, per this file's convention, stated here rather than edited into §2.5:

| §2.5 / §3.1 said | Actually |
|---|---|
| numpy capped at 2.2.6 | **numpy 2.5.3 installed** (3.10 ceiling does not apply) |
| `av` 18.1.0 rejected, needs `>=3.11` | would have been available on 3.14 |
| `opencv-contrib-python-headless` installs on 3.10 via `cp37-abi3` | installed as a native `cp314` wheel |

§3.1's *conclusion* (use soundfile) still stands, but on different grounds: soundfile
has one dependency (numpy) where `av` bundles ffmpeg. The stated rejection reason is
void, not the decision.

Lesson worth carrying: **check the interpreter the venv actually uses, not the
system `python3`.** This nearly produced a requirements file pinned to the wrong
Python.

### §5.2 The §2.2 conflict was real, and is fixed

Reproduced first, then fixed — not assumed.

```
BEFORE:  .venv/.../cv2/qt/plugins/platforms   <- present, the §2.2 hazard
FIX:     pip uninstall -y opencv-contrib-python
         pip install opencv-contrib-python-headless==5.0.0.93
AFTER:   cv2/qt                               <- gone
```

Both OpenCV builds were version 5.0.0.93, so the "identical version" requirement in
§2.2 is satisfied. `import cv2` succeeds (cv2 5.0.0), so the separate
`libGL.so.1` failure mode reported in the mediapipe issue tracker does **not** apply
on this machine.

This is a **trap for `pip install -r requirements.txt`**: pip treats
`opencv-contrib-python` and `opencv-contrib-python-headless` as unrelated
distributions, so it will happily install the GUI build alongside our pin and
silently reintroduce the conflict. `scripts/setup.sh` now encodes the correct order
and runs the M0 gate as its last step.

### §5.3 What M0 actually verified

`tests/test_m0_window.py` — 6 tests, all passing:

| Test | Result |
|---|---|
| PySide6 `Qt/plugins` + `libqxcb.so` resolve | pass |
| `cv2/qt` does not exist (the §2.2 hazard, asserted not assumed) | pass |
| all six dependencies import | pass |
| `QT_PLUGIN_PATH` resolves to PySide6's plugins | pass |
| real `QApplication` + `QPainter` pass, `offscreen` | pass |
| real `QApplication` + `QPainter` pass, `xcb` on `:1` | pass |

The window tests run in **subprocesses**, because `QApplication` is a process-wide
singleton whose platform is fixed at construction. Testing two platforms in one
process silently reuses the first application. This was found the hard way: an
earlier version of the test appeared to pass both because the instance happened to be
garbage-collected between tests, and only surfaced once the render assertion got
stricter. Each test also asserts the `QPainter` pass produced **more than one
colour**, so a blank surface cannot pass as success.

`scripts/m0_visual_proof.py` captured the X root on `:1` (5360x1802) and
**29,591 pixels match `#1b6b3a` exactly** — the stylesheet colour set on the test
widget. The window is genuinely mapped and rasterised on a real display, not merely
constructed.

### §5.4 §2.1's API claims verified

```
HandLandmarker created OK (VIDEO mode, num_hands=2)
detect_for_video OK
fields: ['hand_landmarks', 'hand_world_landmarks', 'handedness']
mp.solutions present: False
```

Every field §2.1 named exists with the documented type, and the legacy API is
confirmed gone. `handedness` returns `[]` on a blank frame, consistent with the
index-parallel structure §2.1 describes.

`assets/hand_landmarker.task` downloaded: **7,819,105 bytes**, matching the
Content-Length verified in §2.1 exactly.

### Not done — §5

- **Still no `.gp5` and no audio file in the repo.** M1 cannot be verified end to end
  until real files are dropped into `songs/`. This is unchanged from §1 and §3 and
  remains the only blocker, and the only one I cannot clear myself.
- **No camera was opened.** `cv2` imports and the class exists; no device was
  enumerated and no frame was read. The §4.1 30–100ms latency figure is still
  unmeasured on this hardware.
- **No audio device was opened.** `sounddevice` imports; PortAudio was never
  initialised, no stream created, and `stream.latency` never read. §1.5's bias
  estimate is still documentation-derived, not measured.
- **M0 proves the stack runs, not that it performs.** Nothing here measures frame
  rate, inference latency, or audio underflow. Those need M3/M5 on real hardware.
- Only the `xcb` backend was exercised. Wayland (`libqwayland.so` is present) and
  the `offscreen` path pass, but Wayland itself was not tested.
- §1.6's open questions remain open: hammer-ons, tempo changes, camera mirroring,
  difficulty filtering.
- Nothing has been committed. The working tree holds the new files uncommitted.

---

## §6 — Song import system (2026-09-26)

M1's code, minus the parts that need real files. Everything below is unit-tested and
passing; nothing has been run against a real tab.

```
93 passed in 1.58s
```

### §6.1 Housekeeping

`main.py`, `business/`, `data/`, `presentation/` removed. All four were empty and
untracked — git does not track empty directories, so this was a no-op for version
control and no data was lost (verified with `find` and `git ls-files` first).

### §6.2 Requirements: curated, not frozen

Three files, deliberately:

| File | Contents |
|---|---|
| `requirements.txt` | 9 direct deps, curated, with the §2.2 opencv warning as a comment |
| `requirements-dev.txt` | pytest only |
| `requirements-lock.txt` | `pip freeze`, 31 packages, header-marked generated |

`pip freeze` was **not** used to replace the curated list. It would have locked in
`matplotlib` plus six packages nothing in this project imports (contourpy, cycler,
fonttools, kiwisolver, pillow, pyparsing), mixed pytest into the runtime set, and —
the real cost — overwritten the one comment that documents the opencv workaround.
The lock file records the same caveats in its header so nobody installs from it by
accident.

Note the freeze confirms `opencv-contrib-python` (GUI) is **absent**, so the §2.2
fix is currently holding in the environment.

### §6.3 The `.gpx` wall

`PyGuitarPro 0.11` recognises exactly ten version strings, all GP3/GP4/GP5
(`guitarpro/io.py:11-24`). Anything else raises
`GPException: unsupported version`. **Guitar Pro 7 and 8 save `.gpx` by default**,
so most tabs downloaded today are unreadable by our parser. `.gpx` is a compressed
XML format; supporting it means a new parser, not a config change.

The user has confirmed their library is `.gp5`, so this is a documented wall rather
than a blocker. It is surfaced as its own `Status.UNSUPPORTED_VERSION` so the reason
is visible per file, and `songs/README.md` tells anyone dropping files in to
convert first.

### §6.4 Repeat unrolling

Chosen over linear playback, and it is the least verifiable code in the repo.

Semantics re-derived from source rather than assumed. `gp5.py:327-328` does
`if header.repeatClose > -1: header.repeatClose -= 1`, so the attribute holds the
file's pass count **minus one**, and `-1` is both the "no close" sentinel and what a
file byte of `0` decodes to:

| file byte | stored | passes |
|---|---|---|
| 0 | -1 | no repeat |
| 1 | 0 | 1 (degenerate) |
| 2 | 1 | 2 (standard double repeat) |
| 3 | 2 | 3 |

`passes = stored + 1`. **This mapping is derived from the reader, not confirmed
against a real tab.** `test_stored_to_pass_mapping` pins it, and
`repeats._repeat_passes` names itself as the line to flip if reality disagrees.

A section is handled at its **opening** measure with the walk looking forward for
the close. The first implementation handled it at the close, which double-emitted
the opening measure — the plain walk had already passed through it on the way. Two
regressions are pinned by name in `tests/test_repeats.py`:

- `test_two_independent_sections` — a backward scan without a floor binds the second
  section to the *first* section's opening measure, swallowing everything between.
- `test_two_endings_last_one_wins` — endings sit after the close barline, so without
  absorbing them into the section every ending plays in sequence.

Note times are recomputed against a running offset, because repeated measures reuse
their written ticks:

```python
seconds = (offset + (beat.start - header.start)) / 960 * (60 / tempo)
offset += header.length        # follows time-signature changes
```

Guards: `MAX_REPEAT_DEPTH` and `MAX_EMITTED_MEASURES = 10_000` raise rather than
loop. An unpaired close plays its measure once — there is nothing to repeat back to,
and inventing a repeat is more surprising than ignoring a stray barline.

### §6.5 Chart model

`guitaroids/model/chart.py`, L1, no Qt and no device I/O. `Chart.from_gp5` converts
every failure into `ChartError`, so one bad file cannot take the app down.

Track selection prefers non-percussion, non-bass, unmuted, visible, 6-string, lowest
number. `clefTranspose == 12` identifies bass, so bass is excluded deliberately
rather than by accident.

A `Beat.start` is an **absolute** tick. A beat landing before its own measure's
start means a malformed file; such beats are skipped with a warning rather than
played at a negative offset.

Tuning to 4/4 at 120bpm, all asserted in `tests/test_chart.py` with a synthetic
in-memory `Song`: a quarter note is 960 ticks = 0.5s, a 4/4 bar is 3840 ticks =
2.0s, a 3/4 bar is 1.5s, and a repeated bar's second pass lands 2.0s after the
first.

Tempo changes are rejected outright, per §1.6. A mix-table change that leaves tempo
alone is explicitly *not* a rejection — that distinction has its own test.

### §6.6 Library scanning

`guitaroids/songlib.py` turns every candidate into a `SongEntry` with a `Status`
rather than raising: `OK`, `NO_AUDIO`, `UNSUPPORTED_VERSION`, `PARSE_ERROR`,
`TEMPO_CHANGE`, `NOT_GUITAR`, `EMPTY`. Only `OK` and `NO_AUDIO` are playable; the
rest are surfaced in a collapsed problems list.

`classify()` matches on `ChartError` message substrings. This is fragile and it is
known to be — PyGuitarPro raises one `GPException` type for every version problem,
and the alternatives (sniffing magic bytes, duplicating its version table) are
worse. `test_classify` pins the current mapping so drift is caught rather than
discovered in the UI.

`scripts/import_songs.py` prints a human report or `--json`, and exits non-zero when
anything is unplayable — in **both** modes, so it works as a CI check on a library.
(The JSON branch originally hardcoded `return 0`; a test caught the inconsistency.)

### §6.7 Structure

```
guitaroids/
  qtenv.py             Qt plugin bootstrap (import before PySide6/cv2)
  songlib.py           library scan, pairing, status
  model/               chart.py  repeats.py                  (L1, pure)
  devices/             empty — Transport, HandTracker        (§4.2 M3)
  session/             empty — GameSession, Judge             (§4.2 M3)
  ui/  ui/widgets/     empty — screens, highway              (§4.2 M2-M3)
  audio/               empty — decode, mix                   (§4.2 M2)
tests/                 93 tests
scripts/               setup.sh  fetch_model.sh  import_songs.py  m0_visual_proof.py
songs/                 README.md  ATTRIBUTION.md
```

### Not done — §6

- **Still no `.gp5` file and no audio in `songs/`.** Every test uses synthetic
  objects. §6.3's format support, §6.4's pass-count mapping, and §6.6's status
  classification are all unverified against real Guitar Pro output. This remains the
  only blocker, and the only one I cannot clear.
- **No audio decode has run.** `soundfile` is installed and imports; no file has been
  decoded, so §3.1's mp3 claims are untested.
- **Difficulty banding is naive** — notes per second, fixed thresholds
  (2.0 / 4.5 / 7.0). It ignores tempo and phrasing, so two songs at the same density
  can feel very different. Replace with something real, or expose the raw number.
- **D.C./D.S. codas and nested repeats are not handled at all.** Those constructs
  live in `LineBreak`/`Marker`, which this importer ignores.
- **The §1.6 open questions are all still open:** hammer-ons/pull-offs, camera
  mirroring, difficulty filtering. (Tempo changes are now *rejected* rather than
  silently misplayed, which is arguably an answer to that one.)
- No UI has been written. The library scanner has no consumer yet; song select does
  not exist.
- Nothing committed.

---

## §7 — Real tab, track choice, and generated audio (2026-09-26)

The first real `.gp5` arrived (Hotel California, 9 tracks, 121 measures), which
finally exercised §3–§6 against reality. It found three things, and the audio
question turned into a dependency investigation with a measured answer.

### §7.1 Python version — settled at 3.12, after a wrong turn

This is the third time the interpreter version has bitten this project (§5.1 was the
first). The reasoning:

`tinysoundfont` — the chosen synth, §7.4 — publishes wheels for **cp310 and cp312
only**. Verified by asking pip to resolve rather than by reading filename tags,
which is what caused the §5.1 error:

| | PySide6 | mediapipe | soundfile | **tinysoundfont** | pyaudio |
|---|---|---|---|---|---|
| 3.10 | wheel | wheel | wheel | **wheel** | source only |
| 3.12 | wheel | wheel | wheel | **wheel** | source only |
| 3.13 | wheel | wheel | wheel | **source** | source only |
| 3.14 | wheel | wheel | wheel | **source** | source only |

`pyaudio` never has a wheel, but that is irrelevant: it is a **lazy import**
(`tinysoundfont/synth.py:371`) used only by `start()` for real-time playback. We
render offline with `generate_simple()`, so it is installed `--no-deps` and never
entered.

Staying on 3.14 was tested, not assumed, and **fails**:

```
pip install tinysoundfont --no-deps
  -> FAILED: CMake Error: Imported target "pybind11::module"
     includes non-existent path
```

Cause: no `Python.h`. `python3.14-dev` and `python3.12-dev` were both **missing**,
so compiling needs `sudo apt install` — a package manager operation, which is the
thing being avoided. `python3.12` was not installed either; it has since been
installed by the user.

Result: **`.venv` rebuilt from scratch on Python 3.12.14**, deliberately *not* an
in-place change. The old venv carried two orphaned site-packages trees
(`python3.10` *and* `python3.14`, per §5.1) and a clean rebuild is the only honest
fix.

```
93 passed in 1.58s          # full suite on 3.12
6 passed in 1.00s           # M0 gate
```

Cost of 3.12: numpy is capped at **2.2.6** (2.5.3 needs `>=3.12`; it was available
on 3.14). Everything else is unchanged.

### §7.2 The §2.2 install-order bug, found the hard way

`setup.sh` had the opencv swap in an order that broke the build, and the M0 test
caught it:

```
$ scripts/setup.sh
  ModuleNotFoundError: No module named 'cv2'
```

Mechanism: the GUI build is uninstalled *after* headless is installed. Both write to
the same `cv2/` directory, so removing the GUI build **deletes the headless files**.
headless's `dist-info` survives, so pip then reports "already satisfied" on the
reinstall and restores nothing. `cv2/` was gone but pip believed otherwise.

Fix: uninstall **both**, then install headless cleanly. This is now documented in
`requirements.txt` and `setup.sh`, and the M0 test is the regression guard.

### §7.3 Track selection was picking the Vocals

`_score_track` from §6.5 chose **"Vocals"** — not a guitar part. Its tiebreak was
"6 strings, then lowest number", but guitarpro gives *every* track 6 strings by
default, so the tiebreak just picked track #1.

`track.channel.instrument` is a General MIDI program number and discriminates
cleanly. Verified on all 9 tracks of the real file:

| # | name | GM | class | notes |
|---|---|---|---|---|
| 1 | Vocals | 87 | other | 420 |
| 3 | 12-stg Guitar (1) | 25 | **guitar** | **4099** |
| 4 | Acoustic Guitar (2) | 24 | guitar | 134 |
| 6 | Solo Guitar 1 | 29 | guitar | 539 |
| 8 | Bass | 35 | bass | 885 |
| 9 | Drums | — | percussion | 1546 |

Decision: the **user chooses the track** in song select. `classify_track()` uses GM
24–31 / 32–39 to filter bass and drums out of the list, and `suggest_track()`
(densest guitar) provides the default. Both plausible tiebreaks independently
select track 3, so the default is not fragile. GM classification is what makes the
list trustworthy, so it stays even though the user picks.

### §7.4 The audio problem: 90% of notes are chords

```
4099 notes over 380.5s, 1108 distinct onsets
simultaneous-note histogram: {1: 406,  2: 12,  4: 2,  5: 467,  6: 221}
```

**688 of 1108 onsets are 5- or 6-note chords.** This invalidates the input model in
§1.4: a fretting hand's x-position selects *one* lane and cannot hold a 6-fret
barre, so the most common event in the song is unplayable as designed.

Decision: **collapse to one note per onset** (default: highest pitch), with a
per-song toggle to keep full chords for keyboard play. Result: 1108 notes instead of
4099, and density drops 10.77 → 2.91 n/s, which also makes the §6.7 difficulty
banding honest — its current "Easy" reading came from the Vocals track.

Deliberate mismatch: the rendered audio keeps full chords while the gameplay chart
is simplified. That is what a beginner chart *is*, but the player will hear chords
they are not being asked to hit.

### §7.5 Synth: tinysoundfont, not fluidsynth

PyGuitarPro **cannot generate audio** — it is a file-format library only. Every
"MIDI" reference in it is `MidiChannel`, a settings container inside the file. The
summary is "Read, write, and manipulate GP3, GP4 and GP5 files."

Generating audio from the parsed tab is nonetheless straightforward: `Chart` has note
times, frets and strings, and `track.strings[n].value` has the tuning, so
pitch = tuning + fret.

`fluidsynth` was chosen first and then **rejected on measurement**. Debian's build
links against **60 shared libraries** — glib, X11, Wayland, PipeWire, PulseAudio,
ALSA, SDL2, FLAC, opus, and systemd's whole tree — because it is built with every
audio driver enabled. Vendoring "just libfluidsynth" is not possible; the only
self-contained route is compiling a minimal build, which is *more* setup than
`apt install`. It is also LGPL-2.1+.

`tinysoundfont` is the opposite: a 187KB wheel whose `.so` links only
`libstdc++`, `libm`, `libgcc_s`, `libpthread`, `libc`. **MIT licensed**, header-only
C++, and its API is exactly the offline-render shape we want — `sfload`,
`program_select`, `noteon`, `noteoff`, `generate_simple`.

Measured on the real setup:

```
sfload(assets/soundfont.sf3) -> 0
rendered 1.0s in 0.0018s (558x realtime)
```

558× realtime means 6:21 of audio renders in ~0.7s. **The render-time risk
previously flagged as "must measure" is eliminated**, which makes a render cache
unnecessary for correctness — still worth having for repeat loads.

#### Soundfont licensing forced the choice

The soundfont installed on a typical Linux desktop is `TimGM6mb.sf2`, which is
**GPL-2** — vendoring it would impose GPL-2 on this project. Chosen instead:
**FluidR3 mono** (Frank Wen, **MIT**), fetched by `scripts/fetch_soundfont.sh`.
The mono build is ~23MB versus ~114MB for the full stereo `FluidR3_GM`, and mono is
ample for a backing track.

One surprise: the packaged file is `FluidR3Mono_GM.sf3`, an **SF3** (RIFF-wrapped
SF2), not a plain `.sf2`. `tinysoundfont` handles `sf2/sf3/sfo`, so this works, but
the fetch script originally globbed only `*.sf2` and silently found nothing.

#### Soundfonts clip, and `sfload(gain=...)` does not help

A 6-note chord renders **hot**:

| gain passed to `sfload` | peak | rms | clipped samples |
|---|---|---|---|
| 1.0 | 1.000 | 0.539 | 166 |
| 0.3 | 1.000 | 0.540 | 206 |
| 0.1 | 1.000 | 0.540 | 176 |

`sfload`'s `gain` argument has **no effect on level** — the numbers are identical
across a 10× range. Gain must therefore be applied to the **rendered buffer**:

```
raw  peak 1.000
x0.25 peak 0.250   clipped 0
tanh(a*0.9) peak 0.716   clipped 0
```

Both linear scaling and soft-clipping verified. This is a real implementation
requirement, now recorded in `AGENTS.md` so it is not rediscovered.

#### Layering

```
audio/soundfont.py            discovery: assets/ -> $GUITAROIDS_SOUNDFONT -> system
audio/synth.py                interface: render(chart) -> np.ndarray
audio/synth_tinysoundfont.py  SF2/SF3 sampled      preferred
audio/synth_numpy.py          Karplus-Strong       always available
audio/midi.py                 chart -> .mid         optional export via mido
```

`mido` drops to **optional**: the numpy synth reads the `Chart` directly, so MIDI
is only needed to export `.mid` files as a practice extra.

### Not done — §7

- **§7.3 and §7.4 are analysed but not implemented.** Track selection still uses the
  broken `_score_track`, and `chart_from_song` still emits all 4099 notes. Both are
  written up here precisely so the next session does not re-derive them.
- **`audio/` is still empty.** None of §7.5 exists as code; it is a design plus the
  measurements that justify it. The synth interface, the Karplus-Strong
  implementation, soundfont discovery and the gain/limiting stage are all unwritten.
- **Karplus-Strong has never been heard.** The fallback synth is untested even
  informally; it has no code at all.
- **No MIDI export.** `mido` is neither installed nor exercised.
- **This tab has zero repeat barlines**, so §6.4's unroller is *still* unverified
  against real Guitar Pro notation. A tab that actually uses `|:` and `:|` is still
  wanted.
- **D.C./D.S. codas remain unhandled** — they live in `LineBreak`/`Marker`, which
  the importer ignores.
- **Rendered audio has never been listened to.** It renders and is numerically
  sane, but nobody has heard whether FluidR3 mono at 558× realtime sounds right for
  a rhythm-game backing track, or whether the post-render gain value is right in
  practice alongside the click track.
- **The 12-string part is stored as 6 strings**, so pitch uses standard tuning and
  the render will not match the real 12-string sound. Cosmetic, but audible.
- `GUITAROIDS_SOUNDFONT` is read by the fetch script but nothing reads it at
  runtime yet, because `audio/soundfont.py` does not exist.
- Nothing committed.

---

## §8 — Track choice and chord collapse implemented (2026-09-26)

§7.3 and §7.4 were analysed but not built. Both are now code, verified against the
real tab. Suite is **127 tests**, up from 93.

### §8.1 Track selection — the Vocals bug is fixed

`_score_track` is gone. Replaced by classification plus a separate default:

```python
classify_track(track) -> TrackKind        # GUITAR | BASS | DRUMS | OTHER
playable_tracks(song) -> list             # GUITAR only
suggest_track(song) -> track              # densest guitar, with notes
```

`classify_track` reads `channel.instrument`: GM 24–31 is guitar, 32–39 is bass.
Two signals override it, because hand-written tabs get the programme number wrong:

- `isPercussionTrack` → `DRUMS`
- `clefTranspose == 12` → `BASS` (independent of the programme)

Density is now only a tiebreak *among tracks already classified as guitar*, so it
can no longer promote a vocal line — which was the §7.3 bug exactly.

Verified on the real file, all 9 tracks:

```
#3 12-stg Guitar (1)     GM 25  guitar   4099 notes  <- default
#4 Acoustic Guitar (2)   GM 24  guitar    134 notes
#6 Solo Guitar 1         GM 29  guitar    539 notes
#7 Solo Guitar 2         GM 29  guitar    347 notes
#1 Vocals                GM 87  other     <- filtered out
#8 Bass                  GM 35  bass     <- filtered out
#9 Drums                 percussion       <- filtered out
```

Five guitar tracks in the tab; **#5 "Electric Guitar mute" has 0 notes and is now
omitted from the list entirely**, because offering a track that cannot be charted is
a dead end in a UI.

`songlib.SongEntry` gained `tracks: tuple[TrackInfo, ...]`, `default_track`, and
`chart_for(track_number)` which builds a chart on demand. The tab's parsed song is
retained on the entry, so switching tracks in song select does not re-parse the
file. `Status.NOT_GUITAR` now fires only when a tab has genuinely no guitar track;
a tab full of guitar tracks that all chart to nothing is `EMPTY` instead.

### §8.2 Chord collapse

`Note` gained `pitch` (`tuning[string] + fret`) and `chord_size`. Three rules:

| Rule | Picks |
|---|---|
| `HIGHEST` (default) | highest pitch — most melodic, and what beginner charts do |
| `LOWEST` | the chord's bass note |
| `COMMON` | the most-used string across the chart, pitch breaking ties |

The property that matters, and the one tested hardest:

```python
assert len(collapsed.notes) == len({n.time for n in full.notes})
```

**Every onset survives.** Collapsing changes only which pitch is asked for, never
the rhythm, so the game still has exactly the same number of things to hit. That is
tested for all three rules against both synthetic fixtures and the real tab.

Measured on Hotel California, track 3:

| | notes | n/s | difficulty | max chord |
|---|---|---|---|---|
| full chords | 4099 | 10.77 | Expert | 6 |
| collapsed (default) | **1108** | **2.91** | **Medium** | 6 |

`chord_size` is reported on the kept note even when collapsed, so the UI can label
a six-note onset and the per-song toggle can render full chords. All six lanes
survive collapsing — a test asserts this, because a rule that sterilised the
highway would be worse than the problem it solved.

Note the difficulty band moved *Medium* on the corrected data. The earlier "Easy"
reading came from the Vocals track, so it was never a real measurement.

### §8.3 CLI

`scripts/import_songs.py` now shows the track list, max chord size, and the
collapse warning, and takes `--full-chords` and `--rule {highest,lowest,common}`.
It uses `argparse` rather than hand-rolled `startswith('--')` parsing.

### Not done — §8

- **The full-chord toggle is exposed but not wired to storage.** `chart_from_song`
  and `load_tab` take `collapse=` and `scan_library` passes it through, so the CLI
  can exercise both paths, but no per-song *setting* persists it. The session
  settings object does not exist yet (§4.2 M2).
- **The song-select UI does not exist**, so `SongEntry.tracks` and
  `chart_for()` have no consumer. The track choice is currently CLI-and-API only.
- **§6.4's repeat unroller is still unexercised by a real tab.** This one is
  linear. The 3 `assumed` rows in `DECISIONS.md` are unchanged by this section.
- **Difficulty banding is still naive** — notes per second with fixed thresholds,
  now on corrected data, but it still ignores tempo and phrasing. Medium for this
  part is not a judgement about the part's difficulty.
- **`pitch` assumes standard tuning** for 12-string parts, since guitarpro stores
  them as 6 strings. Fine for lane play, wrong for MIDI export of a 12-string part.
- No audio has been rendered or heard (§7.5); `audio/` is still empty.
- **Nothing committed.** 13 untracked items.

---

## §9 — MIDI removed; the dependency set is now complete (2026-09-26)

### §9.1 The MIDI question, answered by measurement

`tinysoundfont` does support Standard MIDI natively — `midi.load()` /
`load_memory()` return `Event` objects whose `t` is **already in seconds**, and
`Sequencer.process()` drives a synth with no audio device and no pyaudio. A
hand-built SMF fed to `load_memory()` parsed correctly, with a one-beat note at
120bpm landing at `t=0.5000`.

The round trip would also have been lossless, which is the genuinely elegant part:

```
gp_tick   = seconds x 960  x tempo/60      # guitarpro Duration.quarterTime
midi_tick = seconds x PPQ  x tempo/60
```

At **PPQ = 960** those are the same number at any tempo, so a `.mid` would be a
byte-faithful copy of the tab's own tick grid with no quantization.

**Decision: no MIDI.** `mido` is off the table and `.mid` export is dropped. The
synth reads the `Chart` directly, so there is one code path and one fewer
dependency. For a practice app the `.mid` was arguably the most useful artifact a
tab could produce, so this is recorded as a real cost rather than a free win — but
it was not worth a second library and a serialize/deserialize hop in the audio
path.

Bypassing MIDI does create one gap that MIDI would have papered over: there are no
explicit note-off events, so **we** must decide when notes stop, and `Note` has no
duration. The proposed rule is to hold each onset's notes until the next onset,
capped at 2 beats, which gives every note exactly one on and one off and stops a
repeated pitch overlapping itself. That is a pure function over the `Chart`, so it
is testable without hardware — which matters more now that it is our
responsibility.

### §9.2 `tinysoundfont` must be `--no-deps`, and that is not fixable in a file

The question behind the requirements rewrite: can `tinysoundfont` just be a normal
`requirements.txt` entry? **No.** Its only declared dependency is `pyaudio`, which
has **no Linux wheel in any release** (checked 0.2.12 through 0.2.14) and cannot be
built here either — `libportaudio2` is installed but `portaudio.h` is not:

```
$ pip install tinysoundfont
src/pyaudio/device_api.c:9:10: fatal error: portaudio.h: No such file or directory
ERROR: Failed building installable wheels for pyaudio
```

`pip install --dry-run` **exits 0** on this and reports "Would install PyAudio" —
a false positive, because it only prepares metadata. Worth remembering before
trusting a dry run here.

`pyaudio` is a lazy import inside `tinysoundfont`, used only by `Synth.start()` for
real-time playback. Offline rendering never reaches it, so `--no-deps` is free.

### §9.3 Three requirement files, one supported install path

```
requirements.txt            installable as-is; every package pip can resolve
requirements-optional.txt   must use --no-deps (tinysoundfont), with the why
requirements-dev.txt        pytest, layered on requirements.txt
```

`requirements.txt` is the complete inventory the user asked for, including the
rejected packages and why they lost (`mido`, `pyfluidsynth`, `pyaudio`, `av`,
`pydub`) so the file is a record and not just a list. `tinysoundfont` is named
there but kept in `requirements-optional.txt`, because listing it in the main file
would make `pip install -r requirements.txt` **fail** rather than merely omit it.

`scripts/setup.sh` reads all three and is the only supported path.

### §9.4 The decision is now locked by tests

`tests/test_audio_deps.py` fails if this ever stops being true:

- `pyaudio` is **not installed** (so nobody reintroduced it)
- `tinysoundfont` imports without it
- a fetched soundfont loads and renders something non-silent
- the render is hot enough to require post-gain, documenting §7.5
- `Sequencer` exposes `process` / `midi_load`, i.e. no device needed

### §9.5 Verified from an empty venv

```
$ rm -rf .venv && scripts/setup.sh
Python 3.12.14
    known-good: every dependency including tinysoundfont has a wheel
==> Installing headless OpenCV build (GUI build 5.0.0.93 removed)
==> Installing optional packages that need --no-deps (requirements-optional.txt)
    ok - 1 package(s)
...
6 passed in 1.06s        # M0 gate
127 passed in 3.52s      # full suite
```

`pyaudio` confirmed absent afterwards; `tinysoundfont` still imports and renders
(peak 1.000, rms 0.543).

### Not done — §9

- **`audio/` is still empty.** This section decided the dependency set and
  answered the MIDI question; it built no synthesis code. `schedule.py`,
  `soundfont.py`, `synth.py`, `synth_numpy.py` and `synth_tinysoundfont.py` are all
  still unwritten.
- **The note-duration rule is proposed, not implemented or tested.** It is the
  main new design surface created by dropping MIDI.
- **The FFT pitch test for Karplus-Strong is not written** — that was going to be
  the strongest available check that the fallback synth produces the right note.
- **No audio has been rendered end to end or heard.** We have rendered single
  chords in isolation; nothing has been listened to.
- `.mid` export is gone. If it is ever wanted, it is a self-contained
  `audio/midi.py` plus `mido` in `requirements-optional.txt`, and §9.1's PPQ=960
  argument is the design note to start from.
- `requirements-lock.txt` was generated back when the venv was on Python 3.14 and
  had gone stale; it has been **regenerated** from the Python 3.12 environment and
  now records `tinysoundfont` separately as a `--no-deps` install.
- **Nothing committed.** 13 untracked items.

---

## §10 — PowerShell port, and an intermittent test failure (2026-09-26)

### §10.1 `scripts/setup.ps1`

A Windows equivalent of `setup.sh`, for demoing on another machine. Two
platform-specific problems had to be solved rather than translated.

**Native exit codes.** `$ErrorActionPreference = 'Stop'` does *not* trap a
non-zero exit from a native executable in Windows PowerShell 5.1. Without an
explicit check, every failing `pip` call would pass silently and the script would
report success having installed nothing. All external calls therefore go through an
`Invoke-Native` wrapper that throws on a non-zero `$LASTEXITCODE`. The one
exception is `pip show opencv-contrib-python`, which is a *probe* whose exit code
is the signal being read, and which is guarded by `if ($LASTEXITCODE -eq 0)`.

**Soundfont extraction.** The `.deb` is an `ar` archive containing `data.tar.xz` —
verified: magic bytes `!<arch>`, members `debian-binary / control.tar.gz /
data.tar.xz`, and the inner tar holds `./usr/share/sounds/sf3/FluidR3Mono_GM.sf3`.
`dpkg-deb` is used where present; otherwise the two-stage fallback relies on the
bsdtar that ships with Windows, which reads the `ar` layer directly, so
`tar -xf file.deb` then `tar -xf data.tar.xz` extracts it. If neither works the
step is skipped with an explanation rather than failing setup, because the numpy
synth is the fallback.

**Not verified.** There is no PowerShell on this machine, so the script has never
been executed. It is checked structurally — braces, brackets and parens balance
outside strings and comments — and by a test asserting it routes pip through the
wrapper and uses the `Scripts/` venv layout. That is not the same as having run
it. Treat the Windows path as untested.

### §10.2 The two scripts are kept from drifting by a test

Hand-maintained duplicates are the worst case: nothing fails when one is updated
and the other is not, the install just quietly does the wrong thing on one
platform. `tests/test_setup_scripts.py` pins what must agree — the headless
OpenCV version, the model and soundfont URLs, the `--no-deps` install, the M0
gate, the numpy fallback, and that each file mentions the other.

Writing that test immediately found **five mismatches**, three of them real:

- `setup.ps1` put the OpenCV version in a PowerShell variable, so the two could
  not be compared. Now a literal in both.
- `setup.sh` never said it was the only supported install path; that warning lived
  only in `requirements.txt`. Added to both.
- The `pip install --dry-run` trap was documented in `AGENTS.md` and §9 but not in
  `setup.sh` — the one file a person is actually reading when they might
  substitute a bare `pip install`. Added.

Two more were the test's fault, not the scripts': it assumed the URLs lived in
`setup.sh` (they are in the `fetch_*.sh` helpers, so the test now compares the
`.ps1` against those), and it flagged the `pip show` probe as an unchecked
invocation when reading its exit code is the entire point.

### §10.3 An intermittent abort in the M0 gate, and its likely cause

While re-running `setup.sh` after editing it, the M0 gate aborted once with
SIGABRT (exit 134) and passed on every subsequent run — including 12 consecutive
standalone runs. An intermittent failure of the one gate everything else depends
on is worse than a consistent one, so it was worth chasing rather than re-running
until green.

The cause was most likely the subprocess in `test_m0_window.py`: it creates a
`QApplication`, processes events, prints, and exits, so the interpreter tears a
live `QApplication` down at shutdown. Whether that aborts depends on GC timing,
which is exactly the kind of failure that hides during casual testing. The fix is
`os._exit(0)` after flushing stdout, which skips static destructors entirely and
removes the race rather than retrying around it.

Verified after the fix: 40 consecutive gate runs, 5 consecutive full-suite runs,
4 concurrent gate runs (contending for the same X display), and 3 full
`rm -rf .venv && setup.sh` cycles — all clean, 143 passing each time.

**Stated honestly:** the failing child's output was never captured, so the abort is
*not proven* to have been that destructor. What is proven is that the mechanism
was removed and the flake did not recur. Leaving a `QApplication` to be destroyed
at interpreter exit is a known Qt anti-pattern regardless, so the change is
correct on its own terms.

### Not done — §10

- **`setup.ps1` has never been run.** No PowerShell on this machine. The soundfont
  extraction path in particular is untested, and it is the part most likely to
  differ on Windows.
- The structural check is a brace counter, not a parser. It cannot catch a wrong
  cmdlet name, a bad parameter, or PowerShell 5.1 vs 7 semantic differences.
  `-UseBasicParsing` and the TLS bump are written to work on both, but unverified.
- **`setup.ps1` duplicates the version-check and opencv-swap logic in a third
  place** (with `requirements.txt` and `setup.sh`). The consistency test covers the
  scripts, not the requirements file.
- `fetch_model.sh` / `fetch_soundfont.sh` still have no `.ps1` equivalents;
  `setup.ps1` inlines that logic instead, so a change to the fetch scripts will
  not reach the PowerShell path. The URL test covers that specific drift, nothing
  more.
- The §10.3 flake is fixed but not root-caused with certainty. If it recurs, the
  child's stdout and exit code need capturing rather than guessing again.
- Nothing committed since §8.


## §11 — The menu screens, built (2026-09-26)

Six screens exist; four are real. `GAME` and `RESULTS` are still placeholders, and
that is the whole of what is left before the highway and the judging.

**Tests: 422, all passing. M0 gate: 6/6. Verified on `xcb` with the real library
(`Hotel California`, 1 tab, 4 selectable tracks, default track 3).**

### 11.1 Why `AppContext` exists, and why it is not on the shell

`ScreenBase` gave a screen nothing but the shell, and `ShellBase` deliberately
declares only `navigate`, `go_back` and `current` — the shell's job is navigation.
So there was nowhere for song select to get the library. Widening the shell into a
holder for everything would have worked and would have been the wrong shape, so the
shared state moved to `AppContext`, which the shell owns and hands to each screen.

That is §1.7's "screens never own game objects" applied one level up: a screen may
borrow the library, but navigating away must not lose it. Tested by navigating
through all six screens, calling `unload_all()`, and asserting the play request
survives.

`context` is a **constructor argument** rather than read off `self.shell.context`.
Reaching through the shell would have needed no signature change at all, but then
every screen test has to construct a whole `MainWindow` first. There is a test that
builds `MainMenu` against a context with no window behind it.

`MainWindow` requires a context rather than defaulting to one. Three call sites had
to change, which is the point: `AppContext()` with default fields is cheap and would
have silently produced an empty library at a real call site.

### 11.2 The background scan

A rescan triggered from a button is ~0.2s a tab, so 50 songs is ten seconds of
frozen window. `AppContext.create()` still scans synchronously, before the window
exists, so the first screen drawn already has the library; only *rescans* go through
`LibraryLoader`.

- **Cancellation is checked at file boundaries**, not by abandoning the result.
  Discarding a finished scan still burns ten seconds of CPU after the user navigated
  away, on a machine also trying to run a webcam preview.
- **`start()` clears the cancel flag.** A screen that cancels on hide and rescans on
  show must not come back to a scan that reports cancelled and populates nothing.
- **A second concurrent scan is refused**, not queued. Two scans of one directory
  race to write the library and the loser's result gets shown.
- **Exactly one terminal signal** fires per scan, on every path. A screen waiting on
  "scan finished" and never woken is worse than one woken with bad news.
- **A reference to the `QRunnable` is held.** One with no Python reference can be
  collected mid-`run()`, and the symptom is a pool thread that silently never
  finishes.

`find_tabs()` was lifted out of `scan_library` so both paths share discovery. A
loader that globbed separately would rescan a different set than a restart does, and
only on a library with a nested directory or an oddly cased extension. There is a
test asserting the two agree exactly.

**A `QRunnable` was chosen over a bare `threading.Thread`** because the thread pool
is bounded and the signal delivery is the same either way. This is a preference, not
a finding.

### 11.3 The loader is owned by a screen, not by the context

`AppContext` is pinned free of Qt, `cv2` and `sounddevice` by a subprocess test.
Putting a `QObject` there would have made every context test a Qt test. So the
loader is parented to the screen that needs it, and cancelled in `hideEvent`.

`hideEvent` rather than `closeEvent`: the shell keeps built screens in a stack and
only ever hides them, so a `closeEvent` handler would never run. A scan that outlived
its screen would emit into a destroyed widget.

### 11.4 Song select

Two columns. It fills the page, so it is top-aligned and wider than the menu's
column, reusing `content_column` with a larger `max_width` rather than growing a
second layout convention.

- **Playable and unplayable are separate lists.** A problem row in the main list
  would be selectable and would need a "no" path out of it. The problems list
  appears only when non-empty — the collapsed section §6.6 asks for.
- **The whole `SongEntry` is stashed in `UserRole`**, not the slug. One object, no
  second lookup, and the detail pane cannot disagree with the row. The list is
  rebuilt on every rescan, so a stale entry cannot outlive a scan.
- **The offset writes settings on Play, not on every slider move.** `save()` is
  temp-file-and-fsync, and a drag emits `valueChanged` continuously.

The test that matters most is the one a screenshot cannot make: that Play records a
request the game screen can actually resolve, and that the chosen track is what
comes back out of `context.chart_for()`.

### 11.5 A real bug: preferences wiped the per-song offsets

`song_offsets_ms` belongs to song select. Preferences has no control for it — but
`save()` was writing the draft's *copy* back. Since the draft is built when the
screen is first constructed, opening preferences after tuning an offset in song
select and pressing Save silently discarded the tuning. `save()` now never touches
the field. This is the class of bug a draft introduces, and it is why the test
exists rather than the assertion being obvious.

### 11.6 Layouts compress; they do not clip

Preferences on first build had **three combo boxes 19px apart when each needed 34**,
drawn on top of each other. A `QVBoxLayout` that cannot fit its children does not
clip them — it compresses them below their minimum. Measured, not guessed: the Input
group wanted 198px and was given 125.

Two fixes, and the second matters as much as the first:

- The form **scrolls**. Scrolling degrades honestly; compressing lies about what is
  on screen.
- The Save/Reset/Back row sits **outside** the scroll area. A Save button below the
  fold means you move a slider, go hunting for Save, and cannot tell whether the
  change stuck.

`test_ui_shell.py` now checks every screen for compressed group boxes and for
children wider than the window. The bug was found by looking at a screenshot, so the
check belongs in the shared screen tests where the next screen inherits it.

### 11.7 Import is a copy, and the decisions are pure

`guitaroids/importer.py` decides *whether* to import; the screen only asks the
questions that module reports. The split is because "must this ask before
overwriting?" is the interesting behaviour and a `QMessageBox` cannot be asserted
against. Both dialogs are injectable attributes — `QFileDialog.getOpenFileName` is a
blocking static call that would hang a test outright.

- **Never overwrites without asking.** Tested for both answers.
- **Copies via temp-file-and-rename.** A failure part-way through cannot leave a
  truncated `.gp5` that scans as corrupt and sits in the problems list forever,
  blaming the user for a failure that was ours.
- **`.gpx` is refused at import** with a reason, rather than copied in to fail at
  scan time. The problems list exists for files that were already there; filling it
  with something predictable is clutter.
- **The destination extension is lowercased.** `songlib` matches with
  `suffix.lower()` so it would find `song.GP5` — but a library that accumulates
  mixed-case extensions is a nuisance to look at and to script against.

### 11.8 PySide6 notes worth keeping

- `QFormLayout.itemAt(row, role)` takes an **`ItemRole` enum, not an int**. Passing
  an int is a `TypeError` at runtime, not a parse error.
- A word-wrapped `QLabel` in a tight vertical stack reports a height for the width it
  happens to have, and the text then spills over whatever is below it. Long
  explanations belong in tooltips; §11.6 is what that cost.
- `QScrollArea.viewport()` paints its own background by default and covers the
  themed app background. `setAutoFillBackground(False)`.
- A `QStackedWidget` cannot show a child of a hidden window, so `showEvent`-based
  reloads do not fire in a test unless the window is shown first.

### Not done — §11

- **`GAME` and `RESULTS` are placeholders.** Nothing about note timing, judging, the
  highway or scoring has been built or tested. The clock (§1.5) is still the
  largest untested risk in the project and is untouched by this section.
- **No audio has been played.** `AppContext.create()` and the loader both read
  charts; nothing opens a device.
- **The offset slider has never been validated against a real tab.** Its range
  (±500ms) and step (5ms) are reasoned, not measured. The remembered per-song
  offsets are stored and round-trip, but no human has confirmed a slider position
  that actually sounds right.
- **Song select has been seen with exactly one song.** Layout was checked at 960×640
  and on `xcb`, but a 50-song list, a long title, and a non-ASCII title are untested.
- **Difficulty bands are unvalidated** (§6.7). "Medium" for Hotel California at
  2.91 nps is the formula agreeing with itself, not with a player.
- **The 30s/15s rescan tests use small generated tabs.** They prove the mechanism,
  not the timing at 50 real tabs, which is the number the design actually rests on.
- **Device pickers are disabled placeholders.** Audio and camera enumeration needs
  the `devices/` layer.
- **No test renders a screen to a pixel and checks the pixels**, beyond "the grab is
  not blank". The overlap bug in §11.6 was caught by eye, and an eye is not a
  regression suite — the geometry assertions in `test_ui_shell.py` are the partial
  mitigation, and they only check group boxes and direct children.
- One crash-dump artifact appeared when the M0 gate was chained immediately after
  the full suite. It did not reproduce in four attempts, and the gate passes 6/6
  standalone and in-suite. Noted, not root-caused.

---

## §12 — Full screen, and a self-test that was lying (2026-09-26)

The app now opens full screen. `showFullScreen()`, not `showMaximized()`: this is a
game, and a title bar and a taskbar entry are chrome the player has to click past.
`--windowed` is the escape hatch, for development and for running beside other
windows, and it restores the old 960x640.

### 12.1 The self-test reported a geometry that was simply wrong

Worth recording, because it is the same failure shape as §11.6.

A single `processEvents()` after `showFullScreen()` is **not** enough. Until the
window manager has done its round trip, the window is still sitting at its minimum
size. So the self test printed:

```
self-test ok: ... size=720x480 fullscreen=true ...
```

720x480 is `MainWindow.setMinimumSize(720, 480)`. It was not a real result — the
window was not full screen yet, it had not been mapped. The check still exited 0,
so it passed while reporting something false, which is worse than no self-test,
because it looks like a passing check.

`_settle()` now spins the event loop until `windowHandle().isExposed()`, capped at
one second. Costs **3ms on offscreen, ~60ms on a real display**, which is why it can
live in the entry path rather than only in tests.

Verified on this machine, both modes, both platforms:

| platform | mode | reported |
|---|---|---|
| xcb | full screen | `size=3440x1440 fullscreen=true` |
| xcb | `--windowed` | `size=960x640 fullscreen=false` |
| offscreen | full screen | `size=800x800 fullscreen=true` |

`test_the_app_asks_for_full_screen_not_maximized` asserts on the *source text*
rather than on behaviour, because `showMaximized()` would look perfectly correct in
a screenshot and would still be the wrong choice.

### 12.2 What full screen looks like, honestly

Checked at 3440x1440, 1920x1080 and 1366x768. The menu screens are a width-capped
centred column, so:

- **1920x1080 and below: good.** A centred column with generous whitespace, which is
  what a title screen is supposed to look like.
- **3440x1440 ultrawide: small but not broken.** The column is 520px on a 3440px
  screen and the type is fixed at 14px, so the UI reads as a small strip in the
  middle of a lot of empty space.

The layout was designed against a 960x640 assumption and nothing scales with the
screen. This is cosmetic, and the alternative -- a scale factor applied to the QSS
font sizes, `content_column`'s `max_width` and `constrained_button`'s widths -- is a
theme refactor that touches every screen. Not done on this section's evidence; see
below.

### Not done — §12

- **No UI scaling.** Type and control widths are fixed pixel values, so an ultrawide
  display renders a small UI. Cosmetic, and only visible above ~2560px wide, but
  real. Fixing it means a `build_stylesheet(scale)` in `theme.py` plus scaling
  `content_column` and `constrained_button`, which is a theme refactor rather than a
  two-line change.
- **`--self-test` does not assert anything.** It prints values and exits 0. The
  checks are in `test_ui_shell.py`, which parses that output; a broken `_settle`
  would show up there, not in the self-test itself.
- **Full screen was verified on xcb only.** The macOS and Windows paths are the same
  Qt call, but neither was run.
- **Escape still navigates back rather than leaving full screen.** On the main menu
  it does nothing, so a full-screen app with no title bar has no keyboard route out
  of full screen at all — only the Quit button. A keybinding for that does not exist.
- **The window title is now invisible in normal use.** `navigate()` still sets
  `Guitaroids - <screen>`, which is only visible in `--windowed` mode or in the task
  manager. Harmless, but the title bar is no longer a debugging surface.
- **Screen selection is not handled.** With two monitors the window goes to whichever
  screen it is on, defaulting to the primary. There is no flag to choose a display.

---

## §13 — Two layout bugs that only appear on a tall screen (2026-09-26)

Reported after §12 put the app full screen: the preferences window had "a lot of
space" between the title, the description and the setting boxes, and part of the
Import GP description was not drawn. Two unrelated bugs, and neither is visible at
the 960x640 the layouts were originally built against.

**Tests: 446, all passing. Checked at 960x640, 1920x1080, 1366x768 and 3440x1440.**

### 13.1 Preferences: a column with no stretch item

When the form was made scrollable (§11.6) the action row moved out to the screen's
own layout — and took the column's `addStretch(1)` with it. A `QVBoxLayout` with no
stretch item and surplus height to distribute **shares the surplus equally among
every widget whose vertical size policy allows growth**, and both `QLabel` and
`QGroupBox` do. At 1440px:

| widget | height | needed |
|---|---|---|
| "Preferences" title | 203 | 31 |
| "Changes are saved when you press Save." | 203 | 54 |
| Sound / Input / Devices groups | 203 each | ~120 each |

So the content was correct and the *spacing* was the bug: a 120px hole under the
heading, then 70px before the first group box. Invisible at 960x640, because there
is no surplus to distribute there. One `addStretch(1)` fixes it.

The general rule, now written into `AGENTS.md`: **a vertical layout that fills a
variable-height container needs a stretch item.** A `QScrollArea` with
`setWidgetResizable(True)` makes the content exactly as tall as the viewport, so
this bites on tall screens and not on short ones — the worst kind of bug to
reproduce.

### 13.2 Import GP: a word-wrapped label clipped to its minimum

A word-wrapped `QLabel` reports two different heights:

- `sizeHint` — for the width it will actually have (72px here)
- `minimumSizeHint` — which Qt derives from `heightForWidth` (54px here)

When a layout economises it hands out the **minimum**, and the last line is simply
not drawn. Import GP lost the end of its own sentence: the text stopped at
"…is already in the library you will be asked" and stopped being a complete thought
with nothing on screen to say so.

Not caused by a short window either — it reproduced at 1440px, because the container
had cached its size from before the stylesheet was applied, when the label's size
hint was still the pre-QSS one. The few-pixel shortfall was absorbed by the one
label that could give it up.

The fix is `pin_wrapped_label_heights()` in `ScreenBase.showEvent`: by then the
stylesheet is applied and the size hint is right, so each wrapped label's
`minimumHeight` is pinned to its `sizeHint().height()`. Applied to whole screens
rather than to the two broken call sites, because every word-wrapped label in the
app can make this mistake.

Pinning cannot happen at construction: the stylesheet owns typography, so a label's
size hint is simply wrong until it is applied. `ensurePolished()` does not fix it
either — measured, still 68px against the 72px actually needed.

### 13.3 Both are now tests, and both were checked to fail

- `test_no_screen_clips_its_own_text` — every screen, three sizes. Fails on Import
  GP at all three and preferences at 1440px with the pinning removed.
- `test_page_content_is_not_stretched_to_fill_a_tall_window` — three sizes. Fails at
  1080p and 1440px with the stretch removed, and correctly *passes* at 960x640,
  because that is the size where the bug does not exist.

The stretch test scopes itself to labels whose parent is the page's `column`
container. A label sharing a form row with a taller control is *meant* to be
stretched to match it — "Mode" next to a 34px combo is 29px tall — so an unscoped
version of this test would have failed on correct code.

Both were verified to fail with the fix reverted, rather than being written and
assumed. That check is the point: a regression test that passes on the broken code
is worse than no test, because it is a test that lies.

### Not done — §13

- **The UI is still small at 3440x1440.** §12.2 is unchanged and this section does
  not address it; it only fixed the *spacing*. Nothing scales with the screen.
- **No test renders pixels and reads the text back.** Both bugs were caught by a
  human looking at a screenshot. The geometry assertions here are a partial
  mitigation: they check label heights against size hints, which is the mechanism
  of these particular bugs, not the rendered result.
- **`pin_wrapped_label_heights` runs on every show.** It walks the widget tree and
  invalidates the layout. Negligible for a screen of tens of widgets, but it is not
  free, and it would need hoisting if a future screen held thousands.
- **Untested on the 96dpi scaling case.** A HiDPI display changes every size hint;
  the pinning follows them, but nothing here was run at `QT_SCALE_FACTOR=2`.

---

## §14 — The UI scales with the screen (2026-09-26)

Closes the §12.2 "not done". At 3440x1440 the app rendered a 520px column with
14px type in the middle of the screen: correct, and small.

**Tests: 468, all passing. Verified at 1.0, 1.33 and 1.5; 1080p is byte-identical
to before this change.**

### 14.1 The policy

```
scale = clamp(screen_height / 1080, 1.0, 1.5)
```

- **1080p is 1.0 by definition** — that is the size the whole UI was measured at, and
  a test asserts `build_stylesheet(scale_for_height(1080)) == STYLESHEET`, so the
  common case cannot drift.
- **Never below 1.0.** A short window should get a scrollbar or a compressed
  layout, not smaller type. Scaling *down* would trade the bugs in §11.6 and §13.2
  for fresh ones, and 14px is already small.
- **Never above 1.5.** Past that the column stops reading as a menu and starts
  reading as a web page, and a 4K panel is viewed from further away than a laptop,
  so it needs less enlargement than the arithmetic suggests.
- **Height, not width or area.** This is a full screen app, and what makes the UI
  look small is being measured against the *vertical* extent of the display. A
  3440x1440 ultrawide is tall enough to read comfortably from a desk.

Measured: 768→1.0, 1080→1.0, 1200→1.11, 1440→1.33, 2160→1.5, 3840→1.5.

### 14.2 One source of truth, and a global

`theme.px(n)` is the only way a design-unit length becomes a pixel. Every length in
the QSS, in `content_column`, in `constrained_button`, and in the raw
`setContentsMargins`/`setSpacing`/`setFixedWidth` calls inside the four screens goes
through it.

The scale is a **module global** in `theme`, set once by `build_application` before
any screen is built. The alternative — threading a `scale` argument through
`content_column`, `constrained_button` and all four screens — was rejected as more
chances to forget it than the global is chances to leak it. Both helpers still take
an explicit `scale_factor`, which is what makes them testable without global state.

The global does need a guard, and getting that wrong cost real time: see §14.4.

**Corner radii are damped, by the square root.** A 6px radius at 1.33 becomes 8px,
which is a visibly rounder button, and past about 12px it reads as a pill. Lengths
scale fully; curvature scales as `sqrt(f)` so the proportion of rounding stays put
while the box grows.

### 14.3 The shell's minimum size scales too

`setMinimumSize(720, 480)` is now `px(720), px(480)`. A minimum that did not grow
would starve the enlarged UI of room, and §11.6 records exactly what a layout does
when given less space than it needs: it does not clip, it compresses, and the
widgets overlap.

### 14.4 A global in the tests, and the 70-second bug it caused

The test suite pins the scale with an autouse fixture, because a global that one
test sets leaks into every test after it. It restores **both** halves of the state:
the scale, and `QApplication.styleSheet`, which is also process-wide and outlives
the session-scoped `qapp`.

The first version restored the stylesheet unconditionally. That made the suite take
**over 70 seconds** and look like a hang, because `QApplication.setStyleSheet`
re-polishes every live widget — measured at **~0.7s per call**, paid 100+ times.

The fix is one line of logic and it is the kind of thing worth remembering:

```python
if app.styleSheet() != default:      # only restore what a test actually changed
    app.setStyleSheet(default)
```

One test changes the sheet, so one test pays. Back to 13s. A test-harness
optimisation that also happens to be the difference between a suite that runs and
one that appears to hang.

### 14.5 The screenshot tool had to be pinned

`screenshot_ui.py` renders a fixed 960x640 frame. It inherited the display's scale
and drew a **1.33x UI into a 1080p frame**, silently clipping the Quit button off the
bottom of the main menu — the frames being the only screenshots anyone reviews.

Its parser is now `build_parser()`, so a test asserts `scale == 1.0` and
`size == "960x640"` as behaviour rather than as source text. `--scale 1.5 --size
1440x960` is how the enlarged layout gets looked at.

### 14.6 What the scale does *not* fix

The pages are centred columns that **size to their content**, not to the window. So
at 3440x1440 song select occupies about 770px — scaled correctly and consistently
(769/570 = 1.35 ≈ 1.33), but it does not *grow into* the extra width, because the
convention in `AGENTS.md` is a centred fixed-width column rather than a full-bleed
form.

For a menu that is correct and looks like a menu. For song select it means the song
list is narrow on a very wide display. Widening it is a layout decision about how
much a song list should span, not a scaling bug, so it is written down rather than
changed here.

### Not done — §14

- **The scale is not user-configurable.** No setting, no `--scale` on the app (only
  on the screenshot tool). The heuristic is `height / 1080` and a user on a 32"
  1080p panel who wants it larger has no way to ask.
- **Only the primary screen is measured**, and §12's "screen selection is not
  handled" still stands. On a two-monitor machine like the one this was built on,
  the scale comes from whichever display is primary, not from the one the window is
  on.
- **HiDPI is untested.** A `devicePixelRatio` of 2 makes Qt do its own scaling, and
  this multiplies on top. Nothing was run at `QT_SCALE_FACTOR=2`, and it is possible
  the result is double-scaled.
- **No test renders the scaled UI and checks the pixels.** The tests check the
  numbers — the stylesheet's font sizes, the column cap, the minimum size — and a
  human looked at the renders. Same gap as §11.6 and §13.
- **The music game's highway is not built**, so the note lane widths, fret markers
  and hit flashes have no scale story at all yet. That is the screen that will
  actually need the factor most, and it will arrive with the scale already in place.

---

## §15 — The game screen (2026-09-26)

Milestone 2 of §1.8: highway, transport and metronome, keyboard test mode. The
metronome is the one part deferred, and §15.6 says why that is a scheduling fact
rather than a redesign.

**Tests: 567, all passing. Verified on `xcb` at 1600x900 with the real tab.**

### 15.1 Three layers, and the split is the point

```
ui/widgets/highway.py   pure render: (chart, position, zoom) -> pixels
session/judge.py        pure logic: the windows, and pending-note state
ui/game.py              owns the clock, routes keys, drives the other two
```

The widget owns **no clock**. `set_position(seconds)` in, pixels out. That is what
makes it renderable to a `QImage` with no audio device, no camera and no event
loop — and therefore the first thing in this suite whose *rendered output* can be
asserted on. Everything before this checked sizes and positions, which catches a
layout that overlaps and cannot catch a widget that draws the wrong thing.

### 15.2 The orientation change, and what it costs

The highway is **horizontal**: time runs left to right, lanes are stacked rows, and
a vertical playline sits at the centre. This contradicts two earlier decisions:

| | §1.1/§1.4 said | now |
|---|---|---|
| highway | "scrolls toward a hit line" (vertical) | time → x, vertical playline |
| input | "fretting hand **x**-position → lane" | lane must come from hand **y**-position |

`lane = string - 1` is unaffected — the *data* is identical, only the axis changes.
But the hand-tracking model has to change, because x-position selected a lane only
by virtue of lanes sitting side by side.

**Hand tracking is unbuilt, so this cost nothing but a paragraph.** The same change
made after mediapipe is wired would be a rewrite of the input pipeline. That is the
whole argument for writing §1.4 down when a design is *considered* rather than when
it is *implemented*: a decision nobody recorded is a decision nobody revisits.

Two things the orientation gains:

- **Full chords draw well.** In `collapse_chords=False` mode the real tab has 4099
  notes, and simultaneous notes share an x and stack vertically. On a classic
  highway that is a wide blob; on a timeline it is a clean chord shape.
- **The fret number is legible**, because a note is 32px wide and 100px tall at the
  default zoom rather than a small trapezoid.

And one it does not: with `CollapseRule.HIGHEST`, 68% of the real chart is lane 0,
so the highway reads as one busy row with occasional excursions. That is the
collapse rule, not the orientation, and it is the right default for practising a
melody.

### 15.3 Windowing, and why the paint loop is cheap

`chart.notes` and `chart.bar_lines` are both time-sorted, and the chart caches both
time arrays as tuples so `visible_notes()` is a `bisect` per paint. At 200px/s in a
1200px widget that is ±3 seconds: **10–18 notes** for the real library, against 1108
in the chart. Iterating all of them every frame is the slow path and there is a test
asserting the drawn count equals the window.

`set_position` only stores and calls `update()`. All drawing happens in
`paintEvent`; nothing per-frame happens outside it.

### 15.4 Four bugs that only a render would find

- **The hit zone was painted over the notes.** It hid the one note that matters
  most — the one being played. It is now under them, with the playline on top. The
  first test for this *passed with the bug present*, because it only asserted that
  pixels varied near the centre, which the playline guarantees regardless. Rewritten
  to check the note's own colour survives, and verified to fail with the bug back.
- **The HUD labels were parentless.** A parentless `QLabel` is a *top-level
  window*; `show()` on one opens a second window floating over the game.
  `heading()` and `constrained_button()` now take a `parent`.
- **The highway had no layout and was never resized**, so it sat at its default
  640×480 inside the window. `_place_hud()` runs from `resizeEvent` *and* once at
  the end of `__init__` — a widget that has never been resized has received no
  `resizeEvent`, so the first frame would have been wrong.
- **The HUD needed `background: transparent`**, or the default `QWidget` fill
  painted opaque rectangles over the alternating lane bands.

### 15.5 A game cannot depend on Qt focus

Qt grants focus only to an **active** window. So a digit key can land on the window
background or the Back button instead of the game screen, and the game is silently
unplayable. This surfaced because the offscreen test platform has *no* focused widget
at all.

The screen installs an application event filter while visible, consumes only the six
lane keys, and passes everything else through — Space still activates the button,
Escape still navigates back, and an event aimed at the screen itself is passed
through so `keyPressEvent` handles it exactly once. The filter is removed on hide,
**with a test**, because a filter left installed keeps playing the game from the
menu.

### 15.6 The clock is a wall clock, and what that means

`QElapsedTimer`, not the audio transport. This is a scheduling fact, not a
reprimand:

- It is **self-consistent.** The number drawn on the highway and the number judged
  against come from one source, so a press exactly as a note crosses the playline
  scores PERFECT. That is enough to verify lane/key alignment, note timing, hit
  feedback, and the whole judging path — which is what a *test mode* is for.
- It **cannot say whether the game feels right.** That needs §1.5's audio clock and
  a metronome the player can hear.

§3.5 calls the click-placement test *"the highest-value test in the project"* and
schedules it **before** the highway widget. It did not get there first, and by the
time the highway existed the widget was already pure, so the audio work is now an
additive milestone rather than a rewrite of anything here. That is the argument for
ordering by risk, and this section is the receipt for having got the order wrong.

### 15.7 Judging decisions

- **Strays are counted, never penalised.** The real chart is 68% one lane after
  collapse; counting faking-through-a-solo as a miss would punish the exact
  behaviour a practice tool exists for.
- **The press window is `MISS`, not `GOOD`.** A press 100ms off is within the 140ms
  a note lives for, so it resolves *that note* as a MISS. Searching only ±80ms would
  make it a stray **and** let the note expire separately — reporting one mistimed
  hit as two failures.
- **The 80–140ms band is a MISS, not a straddle.** §1.6 says "miss past 140ms",
  which describes *expiry* and leaves the band between unaddressed. Treating it as a
  wrong hit is the common rhythm-game convention and resolves the note exactly once.
- **`accuracy` is hits over notes judged *so far*.** It was `resolved / note_count`,
  and `resolved` counts misses, so a run where every note was missed reported
  **100%**. `song_accuracy` is the separate whole-chart figure for the results
  screen.
- **Notes are drawn a fixed 0.16s wide**, not from `duration_beats`, which is 0.0 for
  every note in the real tab. 0.16 is deliberately under the smallest gap in that
  tab (197ms, a 16th at 76bpm) so notes never touch at the default zoom.

### Not done — §15

- **No audio at all.** The clock is a wall clock (§15.6). Nothing has ever been
  heard, and §1.5 remains the largest untested risk in the project.
- **No hand tracking**, so the §15.2 input change is theoretical. The keyboard path
  is the only input, and it is the one that works.
- **The results screen is still a placeholder.** Counts are shown in the HUD and go
  nowhere; `song_accuracy` has no consumer.
- **No hit feedback beyond a word.** Judged notes are not marked on the highway;
  `GameState.by_note` and `GameState.verdict_at` exist for it and nothing reads them.
- **Only one key mapping**, hard-coded, with no settings and no alternative layout
  for players who want `A S D F G H`. `collapse_chords=False` is a setting the
  highway honours but nothing in the UI can change mid-game.
- **The zoom is fixed at 200px/s.** Not a setting, and not adjusted for song density
  — a 6nps chart and a 1nps chart get the same pixels per second.
- **No full chords were played.** 4099-note mode is the case the orientation is
  argued to suit, and it was verified on a synthetic chart only.
- **Windowing is a bisect per paint, not cached.** Fine at 18 notes; a much denser
  chart would want the static layer (lane bands, bar lines) in a pixmap, which this
  section did not build.
- **Nothing was verified at HiDPI**, as §14. Same gap, now with a `QPainter` surface
  in it.

---

## §16 — The highway is replaced: three bars of tab (2026-09-26)

The scrolling highway of §15.2 was the wrong idea, not a badly-tuned one. Replaced
by a view that reads like printed tab. **Tests: 572, all passing.**

### 16.1 What was asked for, and what that means

| | §15.2 highway | §16 tab view |
|---|---|---|
| continuity | continuous scroll | **one bar at a time** |
| progression | position held at the centre | a line sweeping **left to right** |
| on screen | 6 rows, one per lane | **3 bars** — previous, current, next |
| a note | a block with a fret number | **a mark on a string line** |
| lane order | lane 0 at the bottom (rows) | **lane 0 the top line** (tab) |

**"Static" does not mean frozen.** Crossing a bar line shifts the page down by one
measure and pulls in a new bottom measure — discrete, not a sweep. The line still
travels left to right *within* the current bar, which is what makes that axis
legible as beats rather than as an arbitrary drift. There are tests for both: the
sweep reaches the right edge immediately before the bar line, and is back at the
left immediately after.

### 16.2 The number problem, and what tab notation actually does

§15.4 drew the fret inside every note. The reaction was that the number system was
hard to make sense of, and it is: a fret number is only meaningful *given a string*,
and drawing both in one glyph asks the player to read a chord and a pitch at once.

**Printed tab has no numbers on the staff at all.** The string line *is* the
information; a number appears only in a chord diagram. So a note here is a mark on a
line, and which line it sits on says which string. `Note.fret` is still carried, for
display if anyone later wants it.

The lane colours stay. Real tab is monochrome, but this is a game and colour-coding
the six strings is what lets a player confirm "that was lane 3" at a glance.

### 16.3 Two bugs, one of them a repeat

- **The measure-span builder was wrong in a way that broke the whole view.** It took
  `max(next_bar_line, last_note_time)` for *every* bar instead of only the last, so
  every bar claimed to be 380 seconds long. Every note in the chart landed at fraction
  0.0 — the left edge — and the beat line never moved. The regression test is named
  for the symptom rather than the cause.
- **The beat line is drawn over the notes**, which is correct: it points at the note
  being played. But it is a failure class already recorded in §15.4, where a hit zone
  painted over the notes hid them. Here the line is a few pixels against a marker of
  about twenty, so a test asserts the note is still visible either side of it.
- `QPen(str, width)` is not a valid overload: a QPen takes a QBrush, a QColor, *or* a
  str, never a str plus a width.

### 16.4 The tolerance is drawn as edges, not a fill

`GOOD_SECONDS` is 0.08 of a 3.16-second bar, and a bar spans the full window width,
so the judgement window is a **5% slice** — 77px on a 1520px measure. As a filled
band that is a green wall with notes hidden inside it, which reads as a bug rather
than as a tolerance. Drawn as two thin lines at the window edges it says exactly the
same thing and leaves the notation readable.

Worth noting what did *not* change: §15.2's argument that the orientation suits full
chords still holds, and is now stronger — a chord in this view is a vertical stack
of marks on adjacent lines, which is what a chord looks like in tab.

### 16.5 Still pure render

`(chart, position)` → pixels, with no clock, so it rasterises to a `QImage` with no
audio device, no camera and no event loop. The test module **replaces** the
highway's rather than supplementing it: keeping tests for a widget that no longer
exists would preserve the shape of an idea that was rejected, which is the opposite
of what §1.4's append-only record is for.

### Not done — §16

- **The previous bar is not annotated with what you did on it.** `GameState.by_note`
  and `verdict_at` exist and nothing reads them, so a missed note in the bar above
  looks identical to one you never saw. This is the most obvious next thing to build
  and it was left out to keep this section to one idea.
- **A bar is very wide.** On a 1600px window a single 4/4 measure spans the full
  width, so Hotel California's 10 notes per bar look sparse. That is what the music
  is, and the beat divisions make it readable, but it is not how a printed tab is
  proportioned and a narrower measure with the rest of the window left empty is a
  legitimate alternative nobody has asked for yet.
- **No measure numbers.** Deliberate, given §16.2, but a small bar count in the
  corner would help orientation and was not added without asking.
- **The first bar of the song has no previous**, so the view starts as two bars and a
  gap. Correct, and untidy.
- **No audio**, so none of this can be judged for feel. §15.6 still stands: the clock
  is a wall clock and the audio transport is the next milestone.
- **Beat divisions are drawn from the time signature, not from the notation.** A
  chart with a written tuplet or an odd meter will show a 4/4 grid over it, because
  `Chart` carries one `time_signature` and no per-measure changes.

---

## §17 — Fret numbers, back inside the mark (2026-09-26)

Supersedes §16.2, which removed them. §16.2's reasoning is kept below because it is
what makes this a *different* thing from what §15.4 did wrong.

**Tests: 581, all passing. Verified on `xcb` at scale 1.0 and 1.33, at 960x640 and
1600x900.**

### 17.1 The hierarchy is the whole point

| | §15.4 highway | §16 tab | §17 |
|---|---|---|---|
| primary read | the number | the string line | **the string line** |
| the number | the only information | absent | **confirmation, inside the mark** |
| where | a block | — | all three bars |

§15.4 drew the fret in a block and that number was the *only* thing you could read,
so it asked the player to decode a chord and a pitch out of one glyph. That is what
made the number system "weird". Here the line still answers first; the number is
there for a player who wants the fret. §16.2 was right that a fret number is only
meaningful *given* a string, and this keeps that: the number never stands alone.

Measured on the real tab: frets 0–5 only, 694 of 1108 open strings, so every number
is a single digit. 24 is the highest fret on most guitars, so the two-digit case is
the one that has to fit, and it is built synthetically because the real library
never produces one.

### 17.2 The trap: two independent scales

The marker is derived from the **widget's height**; the stylesheet's font is derived
from the **UI scale**. Nothing relates them. Sizing the number from the stylesheet
works on a large display and overflows the mark on a small one — at scale 1.33 in a
960x640 window, 19px of font in a 17.4px circle.

So the size comes from `marker_radius * 2 * 0.57`, which holds two digits to about
70% of the diameter at every size measured:

| window | marker ⌀ | font | 2 digits |
|---|---|---|---|
| 960x640 | 17.4px | 10px | 12px |
| 1600x900 | 24.5px | 14px | 17px |
| 1920x1080 | 29.4px | 16px | 19px |
| 3440x1440 | 39.2px | 22px | 26px |

Verified **identical at UI scale 1.0 and 1.33** for a given window size, and there is
a test that fails if it ever starts following the scale again.

### 17.3 `ensurePolished`, which was not obvious

`QWidget.font()` returns the **application default** — 9pt Sans — until the widget has
been polished, not the style-resolved font. So `marker_font()` was handing back a
Sans family, not the QSS's Monospace, and the module docstring's claim that the family
comes from the stylesheet was false.

Painting implies polishing, so a real paint was correct and only a caller asking
*before* the widget was shown got the wrong answer — which is to say the tests found
it and the app may not have. `marker_font()` now calls `self.ensurePolished()`,
which is a no-op once painted and correct before.

### 17.4 The pixel tests, and what the control caught

Detecting a digit by sampling one pixel does not work: whether a glyph's centre lands
on a stroke or in its counter depends on the digit. So the count is **relative to the
marker's own measured tone** rather than to a fixed colour, which is also what makes
it mean the same thing in a dimmed bar as a bright one — the previous and next bars
draw their marker *and* their ink at reduced alpha, so neither colour is present at
face value.

Three contaminants had to be excluded, and the **bare-marker control caught each one
in turn**:

1. The marker's own rim, drawn as a 1px pen in a darkened lane colour. On a small
   marker that rim is a large share of the radius, and a box at 0.7 of the radius
   counted it as a number.
2. The **staff line** through the middle of every marker. The current bar's marker is
   opaque so it is covered, but a dimmed bar's marker is translucent and the line
   shows through — which would report a number on every note and prove nothing.
3. The **beat line** down the middle of the marker in the current bar. Avoided by
   parking the play position *between* notes rather than on one.

The control is a window short enough to suppress the numbers (under 580px tall) in
the same chart at the same position, where the count must be exactly zero. That is
what makes the positive assertion mean something: it cannot be passing on the rim,
the line, or the background.

### 17.5 Legibility floor

Numbers are dropped below a 9px derived size rather than drawn as an unreadable
smudge. A number too small to read is worse than no number, because it looks like a
rendering fault. Same rule as §15.4's highway, for the same reason.

### Not done — §17

- **Hit feedback is still absent**, as agreed for this pass. `GameState.by_note` and
  `verdict_at` are unread, so a missed note in the bar above looks identical to one
  you never saw. This is the most obvious next thing.
- **No setting to turn the numbers off.** Given they were removed once already, a
  preference is a reasonable thing to want and was not asked for.
- **The ink is fixed dark** (`#101216`), which has ~7:1 against every lane colour,
  but it was not measured against the *dimmed* rendering of a lane colour at 150
  alpha. It reads correctly in the render; the number was not computed.
- **Two-digit frets are untested against real notation.** The path exists and is
  tested synthetically, but no tab in the library produces one.
- **Beat divisions are still drawn from the time signature**, not from the notation
  (§16), so a tuplet or an odd meter will show a 4/4 grid over it.

## §18 — The strings get names, and the tempo is yours to choose (2026-09-26)

**Tests: 618, all passing. Verified on `xcb` at 960x640 and full screen; the BPM
control and the cord names were checked in a render, not only in assertions.**

Two unrelated things that both came from the same note: *"this doesn't look like a
tab, and there's no way to play it slower."* They are separate in the code and were
done together because the tempo work needed the render to be right to be checkable.

### 18.1 `E A D G B E` down the left, and the legend goes away

Printed tab names the strings, so the tab view now does. The letters sit in the
left margin that §16 already reserved (56–84px, scaled) and that nothing was
using, so **the layout did not move at all** — which is why this is not a redesign.

The order is the one trap. **Lane 0 is the high E string and is the top line**
(§16.1), so the tuple the widget draws top-to-bottom is `E B G D A E`, while the
notation reads `E A D G B E` from the high string down. Both are asserted in
`tests/test_tabview.py`, because the tuple is what the renderer indexes and the
reversed one is what the notation means; a test that only checked one of them would
pass with a plausible-looking transposition.

**The key legend along the bottom is deleted, and its QSS rule with it.** It used to
be the only place the mapping "key 1 = lane 0" was stated anywhere on screen, and it
stated it in a form that conflicts with the tab: on the highway, lane 0 was at the
bottom. With the names on the cords the question has one answer, printed where a
guitarist looks, instead of two answers in two places. Deleting it is the fix; the
QSS selector is removed so it cannot be resurrected by a stray style.

Each painter method now sets its own font. A single font for the whole paint had
the fret number and the string names quietly trading sizes — the number is sized to
fit *inside a note* and the letters are sized to the *margin*, and those are
different problems. §17.2's trap (marker derived from widget height, stylesheet from
UI scale, the two unrelated) applies to the letters too.

### 18.2 Practice tempo: one number, and it is BPM

The whole feature is a multiplication at the clock. Everything downstream already
works in **chart time**, so notes keep their times, the geometry is untouched, the
visible slice is unchanged, and note *expiry* slows down on its own with no special
case — the judge has no idea the rate changed.

```python
return (self._t0_ms + self._clock.elapsed()) / 1000.0 * self._rate - self._offset
```

**The offset is applied after the rate**, and that ordering is the point: the audio
offset is a property of the *song* (how far ahead of or behind the backing track the
tab sits), not of how fast you are practising it. Multiply the offset by the rate
and every song would re-tune itself the moment you slowed it down — an invisible
bug, with no symptom except that a fixed offset is silently no longer the offset.

### 18.3 Why per-song absolute BPM, and not a percentage

A percentage is a **rate**, and a rate is song-relative. That kills both global
options:

- one global **"80%"** cannot mean "play it slower" across a library, because 80% of
  a 76 BPM tab and of a 50 BPM tab are different tempi;
- one global **absolute BPM** is worse — set 60 and the 50 BPM tab gets *no slowdown
  at all*, silently, and the control appears to do nothing.

Per-song absolute BPM has neither failure, and it is **also the value a future
audio transport needs directly** (§1.5, the next milestone): a transport wants "play
this at 50", not "play this at 0.66 of whatever the tab says". That is what decided
it, not taste.

`Settings.song_bpm` mirrors `song_offsets_ms` exactly: a slug-keyed dict, unparseable
values **dropped rather than invented**, clamped to 20–400, and no migration needed
because `from_dict` only reads keys that are present.

**A rate is never above 1.0.** A tab cannot usefully be practised faster than
written, so `rate_for` returns `min(1.0, bpm / written)` and the spin box's range is
clamped to the tab's own tempo — the control cannot express a value that would break
the music. **Judgement windows stay in milliseconds**, so scores stay comparable
across speeds rather than drifting toward PERFECT the slower you play.

### 18.4 The re-anchor, which was wrong on the second change

Changing the rate of a *running* clock teleports the song. `_reanchor` has to move
the origin, and the first version derived it from the raw elapsed time:

```python
now_ms = elapsed * old_rate        # WRONG after the first change
```

That is only correct while `t0 == 0`. Once an origin has been set, `elapsed` is
measured from the last `clock.restart()` while `_t0_ms` carries everything before it
— so the two cannot be added. Measured: halving the rate of a clock reading 4s put
the position at **6s**. The song jumped forward two seconds on the second tempo
change and on no other.

```python
now_ms = (self._t0_ms + elapsed) * self._rate   # correct
self._t0_ms = int(now_ms / new_rate)
self._clock.restart()
```

The test that justifies it is *changing tempo does not teleport the song*, asserted
across **two consecutive changes** — the version that shipped first passes a
single-change test and fails that one, which is the only reason it was found.

### 18.5 Two bugs the render caught and no assertion did

- **`set_bpm()` moved the rate and the readout but not the spin box.** Loading a
  remembered tempo set the game to 50 BPM while the control still read 76: the
  control was lying about the tempo in use. `set_bpm` now sets the box with signals
  blocked, so there is one path that changes the tempo and it updates all three of
  rate, box, and readout.
- **The spin box was parentless**, which makes it a *top-level window*. It was never
  shown, so it did not appear in the game at all, and nothing raised — a `QSpinBox`
  constructed without a parent is its own window, and the layout that thinks it
  contains it is describing a rectangle in space. This is exactly §15.4's HUD-label
  bug, and the test written for that now covers **every HUD widget including this
  one**, plus a check that each is actually `isVisible()` rather than merely
  parented. Parenting alone was the lesson; visibility is the stronger assertion.

Tempo is written to disk **once on hide**, not on every step: a save is an fsync,
and stepping 76 down to 60 one notch at a time would otherwise be sixteen of them.

### Not done — §18

- **The tempo still makes no sound.** It slows the chart and nothing else, because
  there is no audio. Playing at 50 BPM is silent. This makes §1.5 the blocker it
  already was, not a smaller one.
- **The song's own tempo is not shown as a fraction of anything.** The readout says
  `BPM 50 of 76 · 66%`; the percentage is still there, but as a *display* of an
  absolute choice, not as the stored value.
- **No way to type a tempo outside the spin box's range**, and no double-click to
  reset to written tempo — the obvious affordance, not built.
- **Tempo is not per-track.** The slug is the song, so choosing a different track in
  the same tab reuses the tempo, which is usually right and is not always.
- **The string names are not shown on the axis of the scroll position**, only inside
  the three bars, so a note is identified by the tab above it rather than by the
  letters at the moment it arrives.
- **Nothing was tested with hand tracking running**, so §15.2's correction (lane
  comes from hand *y*, not x) is still unexercised.

## §19 — The practice tempo moves to song select (2026-09-26)

Supersedes §18.2–§18.5, which put the control on the game screen. §18.1 (the string
names) and §18.3's *reasoning* for an absolute BPM both stand.

**Tests: 639, all passing. Verified by render on `xcb` at scale 1.0 and 1.5, at
960x640 and 1440x960 — the layout bug in §19.2 was only ever visible in a picture.**

### 19.1 The control belongs with the other per-attempt choices

The tempo is a choice about the **next attempt**, made in the same place as the
track and the alignment. The offset has always been there (§ song select), and the
tempo is the same kind of thing: something you tune once and then play several
times. Putting it on the game screen made it a thing you reach by *leaving the song
you are trying to learn* — which is a control for next time, wearing the costume of
a control for now.

So the control moved, and everything downstream followed it:

| | §18 (game screen) | §19 (song select) |
|---|---|---|
| chosen | during play | before play |
| stored | `Game._bpm` → settings on hide | settings on Play |
| travels | a widget on the screen | `PlayRequest.bpm` |
| applied | `_reanchor()` on every change | once, at load |
| can change mid-song | yes | **no** |

`PlayRequest` gained `bpm: float = 0.0`, where **0 means "as written"** — the same
shape as the offset's default and the reason the field needs no `None`. It is
validated (negative is a bug) and clamped in `from_settings` (400 is the ceiling),
mirroring `offset_seconds` exactly. `context.request_play` takes `bpm=` and
`with_offset_ms` carries it, which is the copy helper song select uses while
dragging the offset.

**The rate is now read once, at load, and cannot change while the song plays.** That
deletes §18.4's `_reanchor` outright, and with it `_t0_ms` — the clock is a plain
`QElapsedTimer` again. This is the honest consequence of moving the control: a
feature that can be changed mid-song *needs* a re-anchor, and one that cannot does
not. §18.4's bug is not repeated, it is removed along with the code that had it,
and the test that justified it is replaced by one that asserts the rate is *fixed*
for the run.

The stored value is **0 when the control is at the written tempo**, not the written
tempo itself. "No entry" and "as written" mean the same thing, and pinning `76` into
a file would hold a song at 76 after the tab is re-exported at 84. `from_dict`
therefore *drops* a non-positive entry rather than clamping it to `MIN_BPM`: an
absent key already means "as written", so there is nothing to gain by inventing
one, and 20 BPM would be an actively wrong claim.

Both screens save on **Play**, which is one write per attempt — the game screen's
flush-on-hide existed only because the spin box wrote to settings on every step.

### 19.2 The bug the move caused: a card that stopped fitting

Adding a fourth control to a fixed-width 340px card made its content taller than a
640px window, and **a `QVBoxLayout` that does not fit does not clip — it compresses**
(§13). The six fact rows were drawn on top of each other:

```
Tempo  76 BPM
Length 6:20
Notes  1108          <- all six, overlapping
Density 2.91 nps
Difficulty Medium
Audio  none (click only)
```

The existing guard did not catch it: `test_no_screen_compresses_its_content` walks
`QGroupBox` children, and this card is a `QFrame`. **The test was measuring the
wrong widget**, which is worse than having no test.

Fixed in two places, because either alone is half an answer:

- **A `QScrollArea` inside the card**, `setWidgetResizable(True)`, frameless and
  translucent so the card's own background shows through. This is the answer the
  project already uses for tall forms (§13), and it degrades to the old layout
  exactly when there is room.
- **`test_no_layout_child_is_squeezed_below_its_minimum`**, a general version of the
  guard over every widget a layout actually owns, skipping anything inside a scroll
  area — scrolling past content that does not fit is the documented answer, not a
  fault. Verified to *fail* on the pre-fix layout
  (`Song Select squeezes its content: ['column: 592px < 597px', ...]`), which is the
  only evidence that it is a test and not a decoration.

Two smaller things the render caught, both invisible to assertions:

- **Spacing 10 → 8 and margins 16 → 12** on the card. The tempo control was landing
  exactly on the fold, half drawn, which reads as a broken widget rather than as
  something to scroll. It is now fully visible at 960x640 with only the one-line
  explanation below the fold. All of it goes through `px()`, so it tightens with the
  scale like everything else.
- **A word-wrapped label lost its last glyph.** "Tune once per song" rendered as
  "Tune onc": the vertical scrollbar sits over the right edge of the viewport and
  the label was painted flush against it. A `px(6)` right margin on the scroll
  content fixes it. This one is scale-dependent — invisible at 1.5, present at 1.0
  — which is an argument for rendering at 1.0 as well as enlarged.

One Qt trap worth recording, because it produced a wall of
`Internal C++ object already deleted`: **`QScrollArea.setWidget()` takes ownership.**
Returning the scroll area's *widget* from the builder, so the caller could add it to
a layout, reparents it straight back out and the scroll area then deletes it. The
builder returns the **card**; the content widget is never handed out.

### 19.3 The test that moved with the control, and the ones the move made

Moved to `test_song_select.py`: the range clamp to the written tempo, the `MIN_BPM`
floor, the remembered-per-song behaviour, persistence to disk, and disabled-with-
nothing-playable. The spin box's parenting test is now expressed as *walk up to the
card*, because the immediate parent is the scroll area's widget and asserting on that
would pin an implementation detail that §19.2 had just changed.

New, and only possible because the value crosses a screen boundary: **the game
screen cannot change the tempo.** Asserted negatively — no `_bpm`, no `set_bpm`, no
`_t0_ms` — because a control that is *meant* not to exist is easy to re-add by
accident, and a second place to set the tempo is two answers to one question. Same
reasoning as the key legend in §18.1.

### Not done — §19

- **The tempo still makes no sound.** Unchanged and still the blocker: it slows the
  chart and nothing else, because there is no audio. §1.5.
- **The tempo is still per song, not per track.** Choosing a different track reuses
  the tempo, which is usually right.
- **The card scrolls at 960x640.** It has to, and the control is visible, but a
  340px fixed-width column in a 960px window is a lot of unused width. Widening it
  would remove the scrollbar entirely on a normal window; not done because it moves
  every row in the card.
- **No double-click to reset to the written tempo**, and the control is a spin box
  rather than a slider — a slider cannot express "exactly as written" as easily.
- **`test_no_screen_compresses_its_content` was left in place** alongside the new
  general guard. It is now redundant; it was not deleted because it is not wrong,
  only narrow.
- **The offset and the tempo are two controls doing one job** (both are per-attempt
  alignment of the player to the song). They are not merged, and the reason is that
  they answer different questions — one is "when", the other is "how fast".

## §20 — Import GP gets a second button, and a real .gp4 gets through (2026-09-26)

**Tests: 660, all passing.** Two things, and the second one is why the first was
reported as a missing button.

### 20.1 Choosing a file and adding it are two steps

The screen had **one** button: it opened the file dialog and copied the file
immediately on return. So a player who picked a tab had no moment at which they
could see what they had picked, what name it would land as, or whether it was
already in the library — and no way to change their mind after the dialog closed.

Two buttons now, in one row because they are two steps of one action rather than
two actions:

```
[ Add to library ]  [ Choose a tab... ]
```

- **Choose a tab...** arms the screen and shows `Selected: song.gp4` plus a preview
  of what Add will do — `Will be added to the library as song.gp5`, or
  `Will ask before replacing song.gp5 in the library.` Nothing is written.
- **Add to library** does the copy, and is **disabled until something is armed**.
  It is cleared again afterwards, so one press is one import.

Two decisions inside that:

**The replace question is not asked on selection.** It is asked when Add is
pressed. A modal on selection puts a dialog in front of a player who has not
committed to anything yet, and a native one cannot be un-asked. The screen says it
*will* ask instead, which is the information without the interruption.

**The button is labelled "Add to library", not "Save".** The action is a *copy* and
the screen says so at the top; "Save" would imply the file is written back where it
came from, which it never is.

Focus moves with the step (Add once armed), so Enter follows the same path the mouse
does instead of reopening the dialog. That is asserted by recording the `setFocus`
call rather than through `hasFocus()`, because the offscreen platform has no focused
widget at all and such a test would pass for the wrong reason.

### 20.2 The real file exposed a crash, not a missing button

The report was "I can select a file but there's no way to save it", and the file in
question was a real `.gp4` — Sweet Child O' Mine, which the one-button flow *had*
copied in successfully. It then broke the library scan completely:

```
guitaroids/model/chart.py:299: TypeError: int() argument must be ... not 'MixTableItem'
```

`_has_tempo_change` read `int(change.tempo)`. In PyGuitarPro 0.11
`MixTableChange.tempo` is **not a number** — it is a `MixTableItem`, a
value/duration/allTracks triple — and it is `None` when the change only touches
volume. Worse, there are **two shapes**: some effect types wrap a mix table as
`effect.mixTableChange`, and others *are* the mix table with `effect.tempo` on them
directly. Both appear in the two real tabs in the library, one file each.

So there were three distinct crashes — `int()` of a `MixTableItem`, a missing
`mixTableChange` attribute, and a bare number in one shape where the other is an
object — and **an exception here is not one bad tab, it is a dead library scan**,
because this runs while enumerating songs. Every version that assumed a single
shape raised on a real file. `_mix_tempo` now reads both shapes with `getattr` and
returns `None` for anything it cannot interpret.

### 20.3 A tempo-changing track was being offered, and could not be played

With the scan alive, `test_offered_tracks_all_produce_charts` — a test written for
the real library, and the reason it exists — failed properly:

```
guns_n_roses-sweet_child_o_mine track 3 offered but unplayable
```

§1.6 rejects a tab that changes tempo, because the note clock is
`tick/960 × 60/tempo` with a single tempo. That is a deliberate limitation, but
`describe_tracks` was offering track 3 anyway, so song select listed a track the
game would then refuse to chart. **The tab is playable; one of its tracks is not.**
It is now filtered out, on the same reasoning as `.gpx` and as bass/drums: a choice
in a combo box that leads nowhere is a dead end.

A song with **no usable tempo** is *not* treated as a tempo change, because there
is nothing to compare against — it plays at full speed elsewhere (`Game.rate_for`),
and rejecting it here would make it unplayable in a second, unrelated way.

The real tab now charts: 674 notes, 127 BPM, 5:34, tracks #4 and #5 offered, #3
correctly absent.

### 20.4 A disabled primary button was still green

`QPushButton#primary` is an **id** selector and `QPushButton:disabled` is a
pseudo-state, so the id wins and a disabled primary button keeps the full accent
colour. The new Add button therefore looked completely live while being inert — a
live-looking control that does nothing when pressed, which is worse than a
dead-looking one, and the exact thing the button's own test warns about. Fixed with
`QPushButton#primary:disabled`, and pinned by a test that asserts the rule exists.

### Not done — §20

- **No drag and drop.** Dropping a `.gp5` on the window would skip the file dialog
  entirely, and Qt makes that about twenty lines. It is the obvious next affordance
  for this screen.
- **No "import several" and no per-file queue.** One tab at a time, each needing
  two clicks and possibly a modal.
- **The chosen file's full path is not shown**, only its name. The destination
  directory is on screen at the top, so the pair is enough, but a name alone cannot
  distinguish two `song.gp5` files in different folders.
- **The preview does not say how big the file is or how many notes it has**, which
  is what a player importing a tab is often actually checking.
- **Tempo-changing tracks are dropped silently.** The tab is still playable on its
  other tracks, so there is no status to raise — but nothing tells the player that
  track 3 existed, or why it is not in the list.
- **§1.6's constant-tempo limitation is unchanged**, and is now the reason a real
  track is missing. Tempo *maps* would be a much larger change to the note clock
  than the control in §19.

## §21 — Chords are kept, because the setting that removed them did nothing (2026-09-26)

Supersedes §7.4's collapse-by-default. §7.4's reasoning about a *fretting hand's
x-position* is kept below, because it is the reasoning that made collapsing sound
reasonable and it is still the reason the preference exists.

**Tests: 682, all passing. Verified by render on `xcb`: the real tab now draws as
the tab it is.**

### 21.1 The report, and what it turned out to be

*"The play mode doesn't seem to accurately represent the song."*

Everything about the **timing** turned out to be exact, which is worth recording
because it was the first hypothesis and it was wrong: no repeat signs in either
real tab, constant tempo, all 4/4, **zero** onsets off the 16th grid, no notes
dropped, no bar-line drift. `seconds = tick/960 × 60/tempo` is doing its job.

The inaccuracy was the **chord collapse**, and the numbers are stark:

| tab | notes in the file | notes in play mode | on the high E |
|---|---|---|---|
| Hotel California | 4099 | **1108** (27%) | 757 (68%) |
| Sweet Child O' Mine | 2015 | **674** (33%) | 190 |

Three quarters of the music was invisible, and what remained was the *highest*
note of each chord, so the play view was a column of dots on the top string. It
did not look like the tab the player learned the song from, and it did not sound
like it either once there is audio.

### 21.2 The actual bug: a setting that nothing read

The cause was not the default. It was that the default could not be changed.

`PlayRequest.collapse_chords` was **write-only**. It was set in `from_settings`,
carried by `with_offset_ms`, printed by `describe()`, persisted, and asserted by
six tests — and read by **nothing**. The chart is built by the library scan, and
*both* `loader.start()` call sites omitted the argument, so `LibraryLoader.start`'s
own `collapse: bool = True` won every time. Untick "Collapse chords" in
Preferences, press Save, press Play: an identical song.

That is the §20.4 failure again in a new costume: **a live-looking control that
changes nothing.** It had been in the app since §7.4 and had never once been
tested end to end, because every test either set the field and read it back, or
drove the loader directly. Nothing tested the *seam*.

Three fixes, because the bug was in three places:

- **Both `start()` call sites pass the setting**, and `LibraryLoader.start` now takes
  `collapse` as a **required** argument with no default. A required argument is the
  fix that cannot come back: a third call site now has to say which it wants. This
  is the same reasoning as the immutable `QPen(str, width)` and the parented HUD
  labels — make the mistake impossible rather than documented.
- **`AppContext.create` loads its settings *before* scanning.** It had them the
  other way round, so the first library was built with a default the player may not
  have chosen. The same bug one layer down, found by looking for the first one.
- **`AppContext.chart_for` passes `request.collapse_chords`**, and
  `SongEntry.chart_for` grew a `collapse=` override. This is the good part: the
  request is frozen per attempt, so unticking the box changes the **next** song
  without a rescan and without touching a song that is already playing — which is
  what the field's own docstring always claimed.

Every `collapse` default in the project is now `False` — the model, `SongEntry`,
`load_tab`, `scan_library`, `Settings` and the test fixture — because four
defaults that can disagree is the same hazard as one that is wrong.

### 21.3 Full chords, and the migration that made it reach anyone

`collapse_chords` defaults to `False`. An existing settings file has the key
present with the *old* default in it, so flipping the dataclass default alone
would have left every current installation exactly where it was.

`SETTINGS_VERSION` goes to **2**, and a file below it has the `collapse_chords`
key **dropped** on load, so the new default applies. A file that says `false`
already agrees, so nothing is lost by dropping it — the migration can only ever
change behaviour for someone sitting on the old default.

Loading also now **upgrades the version** rather than keeping the file's. That was
not in the plan and the round-trip test found it: without it, a version-1 file
says 1 forever and every migration added later re-runs on every single load. A
file from a *newer* build keeps its own number, so downgrading does not re-run old
migrations over data that is already past them.

**No performance cost.** The whole library scans in 0.70s collapsed and 0.67s
full: the parse dominates and the collapse is free. `GameState.update` is
`bisect` per lane over time-sorted notes, so more notes in the same lanes costs
nothing. Measured, not assumed.

### 21.4 A chord was always playable

§7.4 collapsed chords because "a fretting hand's x-position selects one lane".
That is true of a *hand*, and it was used to justify a chart shape for a keyboard.
`GameState.press` resolves **one lane at a time** and each note is independent, so
a six-note chord was always six simultaneous presses and six PERFECTs — verified,
and now pinned by four tests including the one that matters: a chord you played
completely must not age into a miss.

So the preference was never a capability limit. It stays, because a six-note chord
is genuinely six keys at once and that is hard on a keyboard — but it is now a
*choice* rather than a mutilation, and its tooltip says so in both directions
instead of claiming, as it did, that keeping every note "is what keyboard play
needs".

### 21.5 Difficulty bands on onsets, because a checkbox is not a difficulty

`SongEntry.difficulty` banded on `note_count / duration`. Keeping every note takes
Hotel California from 2.91 nps to 10.77, which is "Expert" — and so is every real
rock tab, which leaves the column saying nothing at all. The songs had not got
harder; a preference had changed.

`Chart.onset_count` (distinct note times, one pass over a time-sorted tuple) and
`onsets_per_second` now exist, and **difficulty bands on those**. A chord is one
rhythmic event however many strings it covers, so this is the measure that does
not move when the setting does. The **displayed** density stays note-based,
because "10.77 nps" is the truth about how much is written down. The two disagree
on purpose and both are labelled for what they are.

`onset_count` compares times with `==`, which is only safe because notes sharing an
onset share a computed float — the assumption `group_by_onset` and
`count_chord_sizes` already make. Consistency with them beats a rounding fudge
that would disagree with the collapse.

### Not done — §21

- **The string names sit 6px from the first note in a bar.** The marker is already
  nudged clear of the staff edge, so they do not overlap, but in a dense bar the
  leftmost chord is drawn hard against the `E A D G B E` column. Tightening it means
  widening the margin or insetting the first note, and both move every bar.
- **Scores are not comparable between the two modes.** An accuracy figure is out of
  1108 notes collapsed and out of 4099 full, so the same performance reads
  differently. There is no leaderboard to be inconsistent with, and the HUD does not
  say which mode produced a number.
- **A chord is still one verdict at a time in the HUD.** Pressing six strings fires
  six judgements and the flash shows the last, so a completed chord reads as one
  hit rather than six. `GameState` has everything needed to aggregate per onset.
- **`--full-chords` on `import_songs.py` is now `--collapse-chords`.** The report
  used to default to collapsed, so it was describing a song nobody would ever see.
- **Hit feedback is still absent**, unchanged from §18.5, and now harder to
  motivate on a chord.
- **The preference is global, not per song**, and there is still no way to see the
  two shapes side by side before committing to one.
