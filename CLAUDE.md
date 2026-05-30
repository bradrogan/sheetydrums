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

`backend/` is a `uv` project (Python 3.12, `uv_build` backend). Architecture is dependency-injected: Protocol-based stage interfaces, concrete implementations selected by the factory based on CLI flags.

```
backend/src/sheetydrums/
├── __init__.py        re-exports `main`
├── cli.py             typer CLI; serializes TranscriptionResult to schema dict
├── config.py          CLIConfig (use_larsnet, debug_dir, verbose)
├── interfaces.py      Protocols (MixSeparator, DrumTranscriber, BeatTracker,
│                      DrumSubStemSeparator, ClassExpander, Quantizer) +
│                      data types (DrumHit, BeatGrid, Note, Bar, etc.)
├── pipeline.py        Pipeline class — orchestrates injected stages
├── factory.py         build_pipeline(config) — single point selecting impls
├── audio.py           AudioBuffer + (stub) load_audio
├── debug.py           DebugSink — per-stage artifact dumps when --debug-dir set
├── stages/            One module per stage. Each has a `Stub*` class today;
│   ├── separation.py    real impl (Demucs/ADTOF/...) lands when its task ticket
│   ├── transcription.py is worked, replacing the stub at the factory layer.
│   ├── substem.py
│   ├── expansion.py     StubSubStemExpander (only invoked when substem branch is on)
│   ├── beats.py
│   └── quantize.py      StubQuantizer + the v1 vocabulary-collapse map
└── validate.py        jsonschema validation + cross-field sustain_until check

backend/tests/
└── test_pipeline.py   DI pattern tests — pure fakes, no model installs
```

CLI:
- `sheetydrums INPUT.mp3 -o OUT.json` — full pipeline (7-class output)
- `--no-larsnet` — skip the sub-stem branch (5-class collapsed output)
- `--debug-dir DIR` — dump each stage's intermediate to `DIR/NN-stage.{json,txt}`
- `--quiet` / `-q` — suppress per-stage stat lines

Run with `cd backend && uv run sheetydrums --help`; test with `cd backend && uv run pytest`.

**Vocabulary collapse rule** (in `stages/quantize.py`'s `_INSTRUMENT_MAP`): coarse labels from the 5-class transcriber that don't make it through expansion collapse to schema-valid defaults at the emit boundary: `hihat → hihat_closed`, `cymbal → ride`, `tom → tom_mid`. This makes the output always schema-valid, with `--no-larsnet` simply producing a less-refined (but valid) transcription.

**Stage swap pattern**: when a real model wrapper lands (e.g. `DemucsSeparator` replacing `StubDemucsSeparator`), the change is local to its stage module and `factory.py`. Protocols ensure the rest of the pipeline doesn't notice. Tests stay green because they inject fakes, not real models. The validator reads `schema/events.schema.json` via a relative path from the source tree — this works in editable dev install but will need `importlib.resources` if we ever ship a wheel.

## Status

Walking skeleton works end to end with real Demucs. Pipeline currently:
- **Demucs htdemucs_ft** — REAL (task #4 done). MPS-accelerated on Apple Silicon. First run downloads ~320 MB of weights to `~/.cache/torch/hub/`; subsequent runs are ~9 s for a 4-second clip.
- ADTOF, LarsNet, sub-stem expander, Beat This!, quantizer — still stubs producing deterministic output.

Next stage to swap is ADTOF (task #7) — its drum-stem input is now real.
