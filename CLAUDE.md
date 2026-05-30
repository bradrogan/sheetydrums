# sheetydrums — notes for Claude

Audio-to-drum-sheet-music project. Read `README.md` first for the high-level pipeline and `schema/events.schema.json` for the data contract between the two halves.

## Architecture in one paragraph

A Python backend (`backend/`) ingests a full song mix, separates the drums with Demucs, detects and classifies hits, tracks beats, quantizes, and emits `events.json`. A TypeScript frontend (`frontend/`) loads that JSON and renders an interactive drum score with VexFlow plus synced audio playback. The two halves communicate only through `schema/events.schema.json` — treat that file as load-bearing.

## v1 scope (locked)

- Input: full song mixes only (not pre-isolated stems)
- Output: interactive web view, no PDF/MusicXML
- Schema vocab: 10 classes (kick, snare, hihat_{closed,open,chick}, ride, crash, tom_{high,mid,low})
- **v1 transcriber vocab: 7 classes** — kick, snare, hihat_closed, hihat_open, ride, crash, tom. No `hihat_chick`, no tom splits. All tom hits emit as `tom_mid` until a future fine-tune adds pitch distinction.
- Out of scope for v1: ghost notes, dynamics, swing detection, triplet detection (defer to v2)

## Pipeline (revised per May 2026 SOTA survey)

```
mix → Demucs v4 (drums stem) ──┬─► ADTOF-pytorch  → 5-class onsets
                               └─► LarsNet         → 5 drum sub-stems → 5→7 class expansion
mix → Beat This! ───────────────► beats + downbeats → derive tempo + time sig
        all the above ───────────► quantize against beat grid → events.json
```

Key library choices:
- **Demucs v4 (htdemucs)** — uncontested SOTA for stem separation
- **ADTOF-pytorch** (CC-BY-NC-SA weights, non-commercial OK for v1) — 5-class drum transcription
- **LarsNet** — drum sub-stem separator, used to expand ADTOF's 5 classes to 7 (open vs. closed hi-hat; ride vs. crash)
- **Beat This!** (ISMIR 2024, MIT) — beats + downbeats. Replaces madmom (dead on Python ≥3.10)

The old "onset detection + separate classifier" stages are gone — ADTOF does both jointly. Source separation moves from "optional" to **mandatory upstream**.

Reference docs:
- `docs/research/2026-05-pipeline-survey.md` — full SOTA survey with paper citations, F-scores, license tags, and a glossary of MIR terms used.
- `docs/mir-primer.md` — conceptual primer on Music Information Retrieval for engineers without an audio-ML background. Read this if any of the survey terms feel opaque.
- `docs/v2-backlog.md` — deferred features. Check here before scoping anything not already in v1.

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

Walking skeleton works end to end on a stub: input audio → schema-valid events.json. Next implementations in dependency order: Demucs → ADTOF + LarsNet → 5→7 expansion → Beat This! → beat-grid quantization. The current `pipeline.py` stub names (`_detect_onsets`, `_classify`) will be replaced as the pipeline reshapes — ADTOF subsumes both.
