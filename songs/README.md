# Songs

Drop tabs here. A tab is a `.gp5` file (Guitar Pro 3/4/5 format).

**`.gpx` files are not supported.** Guitar Pro 7 and 8 save `.gpx` by default, but
PyGuitarPro 0.11 only reads GP3/GP4/GP5 — it raises `unsupported version` on
everything newer. Convert a `.gpx` to `.gp5` in Guitar Pro first. See `DESIGN.md`
§6.3.

## Pairing with audio

A tab and its backing audio share a basename:

```
songs/
  smoke_on_the_water.gp5
  smoke_on_the_water.ogg     <- optional
```

Search order for audio: `.ogg`, `.mp3`, `.wav`, `.flac`, `.m4a`.

Audio is optional. A tab with no audio plays as metronome-only — that is a normal
state, not an error. Audio files are gitignored; record where they came from in
`ATTRIBUTION.md` instead.

## Checking your library

```
.venv/bin/python scripts/import_songs.py            # human-readable report
.venv/bin/python scripts/import_songs.py --json     # machine-readable
```

The report lists what is playable and, crucially, what is not and why — a `.gpx`
file, a bass tab, a tab that changes tempo. It exits non-zero if anything is
unplayable, so it works as a CI check.

## Tabs this build rejects

| Rejection | Why |
|---|---|
| `.gpx` / unknown version | PyGuitarPro 0.11 cannot read it (DESIGN.md §6.3) |
| tempo changes mid-song | only constant-tempo tabs are supported (§1.6) |
| no 6-string guitar track | the highway is 6 lanes mapped from strings 1-6 |
| no playable notes | nothing to play |

Repeat barlines *are* handled — see `guitaroids/model/repeats.py`.
