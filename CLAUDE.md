# sheetydrums — notes for Claude

Audio-to-drum-sheet-music project. Read `README.md` first for the high-level pipeline and `schema/events.schema.json` for the data contract between the two halves.

## Architecture in one paragraph

A Python backend (`backend/`) ingests a full song mix, separates the drums with Demucs, detects and classifies hits, tracks beats, quantizes, and emits `events.json`. A TypeScript frontend (`frontend/`) loads that JSON and renders an interactive drum score with VexFlow plus synced audio playback. The two halves communicate only through `schema/events.schema.json` — treat that file as load-bearing.

## v1 scope (locked)

- Input: full song mixes only (not pre-isolated stems)
- Output: interactive web view, no PDF/MusicXML
- Drum vocabulary: kick, snare, hihat_closed, hihat_open, ride, crash, tom_high, tom_mid, tom_low
- Out of scope: ghost notes, dynamics, swing detection

## Conventions

- Backend and frontend are independent codebases in one repo. Don't introduce a top-level package manager that couples them.
- The JSON schema is the single source of truth for the event format. If you change one half's understanding of the format, update the schema in the same change.

## Backend layout

`backend/` is a `uv` project (Python 3.12, `uv_build` backend). Layout:

```
backend/
├── pyproject.toml
├── uv.lock
├── src/sheetydrums/
│   ├── __init__.py   re-exports `main` for the `sheetydrums` console script
│   ├── cli.py        typer CLI: sheetydrums INPUT.mp3 -o OUT.json
│   ├── pipeline.py   transcribe() + private _stage functions (currently stubs)
│   └── validate.py   jsonschema validation + cross-field sustain_until check
└── tests/            (not created yet)
```

Run with `cd backend && uv run sheetydrums --help`. Stages in `pipeline.py` are stubs returning hardcoded data; each `_*` function gets replaced one at a time. The validator reads `schema/events.schema.json` via a relative path from the source tree — this works in editable dev install but will need `importlib.resources` if we ever ship a wheel.

## Status

Walking skeleton works end to end on a stub: input audio → schema-valid events.json. Next stage to replace is `_separate` (Demucs). Then onset detection, classification, beat tracking, quantization.
