# Soundfont attribution

`assets/soundfont.sf3` is **not committed** — `scripts/fetch_soundfont.sh` downloads
it into `assets/` at setup time, and `.gitignore` excludes it.

## What gets fetched

| | |
|---|---|
| File | `FluidR3Mono_GM.sf3` (from `fluidr3mono-gm-soundfont` 2.315-7) |
| Author | Frank Wen (FluidR3), packaged by the FluidR3 / MuseScore community |
| Licence | **MIT** |
| Size | ~23 MB (mono) |

Chosen over the full stereo `FluidR3_GM.sf2` (~114 MB) because the mono build is
permissively licensed either way and mono is ample for a backing track.

### Why not the system soundfont

Most Linux desktops ship `TimGM6mb.sf2`, which is **GPL-2**. Vendoring that into
this repository would impose GPL-2 on the project, so we do not. FluidR3 is MIT,
which is why it is fetched instead.

### Where it came from

```
http://deb.debian.org/debian/pool/main/f/fluidr3mono-gm-soundfont/fluidr3mono-gm-soundfont_2.315-7_all.deb
```

Upstream: <https://github.com/FluidR3/FluidR3>

## Note on the `.sf3` extension

The packaged file is SF3 (a RIFF-wrapped SF2), not a plain `.sf2`. `tinysoundfont`
handles `sf2`/`sf3`/`sfo`, so this is fine — the extension just has to be preserved,
which `fetch_soundfont.sh` does.

## Overriding

Point the app at a different soundfont without touching the repo:

```
export GUITAROIDS_SOUNDFONT=/path/to/your.sf2
```

Or just drop a file at `assets/soundfont.sf2` or `assets/soundfont.sf3`. Discovery
order is `assets/` → `$GUITAROIDS_SOUNDFONT` → system paths → numpy synth fallback
(see `guitaroids/audio/soundfont.py`).

## If none is present

`scripts/fetch_soundfont.sh` failing is **not** fatal. The app falls back to the
numpy **pluck** synth in `guitaroids/audio/render.py` (`backend="pluck"`), which
needs nothing at all beyond numpy. It is additive synthesis with a plucked envelope,
not Karplus-Strong: KS is a per-sample recurrence, so rendering a five-minute chart
with it in Python takes minutes, and a fallback slower than the thing it stands in
for is not a fallback.
