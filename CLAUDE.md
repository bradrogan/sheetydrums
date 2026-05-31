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
├── stages/            One module per stage.
│   ├── separation.py    DemucsSeparator (htdemucs_ft) — REAL
│   ├── transcription.py ADTOFTranscriber (pytorch port) — REAL
│   ├── beats.py         BeatThisTracker — REAL
│   └── quantize.py      StubQuantizer + the v1 vocabulary-collapse map
└── validate.py        jsonschema validation + cross-field sustain_until check

backend/tests/
└── test_pipeline.py   DI pattern tests — pure fakes, no model installs
```

CLI:
- `sheetydrums INPUT.mp3 -o OUT.json` — full pipeline
- `--debug-dir DIR` — dump each stage's intermediate to `DIR/NN-stage.{json,txt}`
- `--quiet` / `-q` — suppress per-stage stat lines

Run with `cd backend && uv run sheetydrums --help`. Test:

- `uv run pytest` — fast unit tests (DI orchestration with fakes; ~0.2s)
- `uv run pytest --run-slow` — adds the end-to-end smoke test that runs the
  real pipeline against `backend/tests/fixtures/eric-keyes-lost.ogg`. ~40s once
  model weights are cached.

**Vocabulary collapse rule** (in `stages/quantize.py`'s `_INSTRUMENT_MAP`): coarse labels from the 5-class transcriber that don't make it through expansion collapse to schema-valid defaults at the emit boundary: `hihat → hihat_closed`, `cymbal → ride`, `tom → tom_mid`. This makes the output always schema-valid, with `--no-larsnet` simply producing a less-refined (but valid) transcription.

**Stage swap pattern**: when v2 adds a real sub-stem class expander, the change is a new stage file + an import-and-instantiate in `factory.py`. Protocols (`DrumSubStemSeparator`, `ClassExpander`, plus the `SubStemBranch` dataclass) are already defined in `interfaces.py` waiting to be implemented. Tests stay green because they inject fakes against the same Protocols.

The validator reads `schema/events.schema.json` via a relative path from the source tree — works in editable dev install but will need `importlib.resources` if we ever ship a wheel.

## Status

Walking skeleton works end to end with real Demucs + real ADTOF + real Beat This!. Pipeline currently:
- **Demucs htdemucs_ft** — REAL (task #4). MPS on Apple Silicon. First run downloads ~320 MB of weights to `~/.cache/torch/hub/`.
- **ADTOF Frame-RNN (pytorch port)** — REAL (task #7). Weights ship inside the `adtof-pytorch` package (~5 MB), no network on first run. 5-class output: kick / snare / tom / hihat / cymbal. CC-BY-NC-SA weights.
- **Beat This! (final0)** — REAL. First run downloads ~77 MB. Outputs beats + downbeats; the wrapper derives tempo (median 60/IBI) and time signature (most-common beats-per-bar from downbeat positions, restricted to {2,3,4} else falls back to 4/4). MIT license.
- **5→7 class expansion (open/closed hi-hat, ride/crash) is deferred to v2** — see `docs/v2-backlog.md`. The drum sub-stem separator space (LarsNet, jarredou's DrumSep) has packaging + license blockers today. The quantizer's collapse map (`hihat → hihat_closed`, `cymbal → ride`, `tom → tom_mid`) keeps output schema-valid at 5 effective classes.

**All v1 build tasks complete.** Pipeline produces real Demucs separation, real ADTOF transcription, real Beat This! beat grid, and clean 16th-note quantized positions. v2 work tracked in `docs/v2-backlog.md`.
