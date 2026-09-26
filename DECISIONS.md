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
| One malformed file must not break the library — a status, never an exception | live | §6.6 |

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
| Count-in configurable, default 1 bar | planned | §3.4 |
| Per-song audio offset via a manual slider | planned | §3.4 |

Omitting `- stream.latency` biases every note 10–20ms early, systematically, which
inside a ±35ms window reads as "the app is broken" rather than as an off-by-10ms.

## Tabs & chart

| Decision | Status | Ref |
|---|---|---|
| `lane = string number − 1` over 6 lanes — no mapping table | live | §1.6 |
| `seconds = tick / 960 × (60 / tempo)` | live | §1.6 |
| `Beat.start` is an **absolute** tick, so note times are recomputed against a running offset when repeats unroll | live | §6.5 |
| Tempo changes are rejected outright rather than silently misplayed | live | §6.5 |
| Missing audio is **metronome-only**, a supported state rather than an error | live | §3.4 |
| Track is chosen by the user; the **General MIDI program** (24–31 guitar, 32–39 bass) classifies and filters the list | live | §7.3, §8 |
| Chords collapsed to **one note per onset**, default rule "highest pitch", with a per-song toggle for full chords | live | §7.4, §8 |
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
| Fretting hand x-position → lane, EMA-smoothed with hysteresis | planned | §1.4 |
| Strumming hand's downward wrist velocity fires all active lanes | planned | §1.4 |
| Input-latency calibration lands **before** hand tracking, not after | planned | §4.1 |
| Camera feed mirroring direction (affects handedness) | planned | §1.6 |

**Camera latency is expected to dominate all audio-side error.** The chain is
`camera buffer (~30–100ms) → capture thread → inference → lane decision → judge`,
and the figure scales with camera hardware rather than with our code. That is why
calibration is its own milestone rather than a setting buried in the end
(§4.1).
