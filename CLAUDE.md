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
mix → Demucs v4 (drums stem) ──┬─► ADTOF-pytorch     → 5-class onsets ─┐
                               └─► DrumSep MDX23C     → 6 sub-stems ────┴─► CheukExpander → 7-class onsets
mix → Beat This! ────────────────► beats + downbeats → derive tempo + time sig
        all the above ─────────► quantize against beat grid → events.json
```

Key library choices:
- **Demucs v4 (htdemucs_ft)** — uncontested SOTA for stem separation
- **ADTOF-pytorch** (CC-BY-NC-SA weights, non-commercial OK for v1) — 5-class drum transcription
- **MDX23C DrumSep by aufr33/jarredou** (CC-BY-NC-SA, via `audio-separator`) — 6 drum sub-stems
  (kick/snare/toms/hihat/ride/crash). Replaces LarsNet (dead repo, broken weights link as of June 2026).
- **CheukExpander** — pure-Python heuristic from arXiv:2509.24853. Splits hihat
  open/closed via decay ratio; ride/crash is now native to the 6-stem separator.
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
├── config.py          CLIConfig (use_drumsep, debug_dir, verbose)
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
│   ├── drumsep.py       DrumSepSeparator (MDX23C 6-stem) — REAL
│   ├── expander.py      CheukExpander (5→7 via sub-stem energy) — REAL
│   ├── beats.py         BeatThisTracker — REAL
│   └── quantize.py      StubQuantizer + the vocabulary-collapse fallback map
└── validate.py        jsonschema validation + cross-field sustain_until check

backend/tests/
└── test_pipeline.py   DI pattern tests — pure fakes, no model installs
```

CLI:
- `sheetydrums INPUT -o OUT.json` — full pipeline. `INPUT` can be a local file path (mp3/wav/flac/ogg) **or a public URL** (YouTube etc., handled by yt-dlp + ffmpeg, cached at `~/.cache/sheetydrums/youtube/<video_id>.wav`)
- `--debug-dir DIR` — dump each stage's intermediate to `DIR/NN-stage.{json,txt}`
- `--quiet` / `-q` — suppress per-stage stat lines
- `--no-drumsep` — skip the 6-stem sub-stem branch (output collapses to 5 classes)

URL handling lives in `src/sheetydrums/fetch.py` — detect URL → download via yt-dlp → return cached WAV path. Idempotent: re-running the same URL hits the cache. `ffmpeg` must be on PATH (`brew install ffmpeg`) for the audio extraction step.

Run with `cd backend && uv run sheetydrums --help`. Test:

- `uv run pytest` — fast unit tests (DI orchestration with fakes; ~0.2s)
- `uv run pytest --run-slow` — adds the end-to-end smoke test that runs the
  real pipeline against `backend/tests/fixtures/eric-keyes-lost.ogg`. ~40s once
  model weights are cached.

**Vocabulary collapse rule** (in `stages/quantize.py`'s `_to_schema_class`): coarse labels from the 5-class transcriber that survive past the expander (only `tom` in the default `--drumsep` mode) collapse to schema-valid defaults at the emit boundary: `hihat → hihat_closed`, `cymbal → ride`, `tom → tom_mid`. With `--no-drumsep`, the expander is skipped and every coarse label hits this map.

The validator reads `schema/events.schema.json` via a relative path from the source tree — works in editable dev install but will need `importlib.resources` if we ever ship a wheel.

## Status

Walking skeleton works end to end with real Demucs + real ADTOF + real Beat This!. Pipeline currently:
- **Demucs htdemucs_ft** — REAL (task #4). MPS on Apple Silicon. First run downloads ~320 MB of weights to `~/.cache/torch/hub/`.
- **ADTOF Frame-RNN (pytorch port)** — REAL (task #7). Weights ship inside the `adtof-pytorch` package (~5 MB), no network on first run. 5-class output: kick / snare / tom / hihat / cymbal. CC-BY-NC-SA weights.
- **Beat This! (final0)** — REAL. First run downloads ~77 MB. Outputs beats + downbeats; the wrapper derives tempo (median 60/IBI) and time signature (most-common beats-per-bar from downbeat positions, restricted to {2,3,4} else falls back to 4/4). MIT license.
- **DrumSep MDX23C (aufr33/jarredou)** — REAL. 6-stem drum sub-stem separator
  (kick / snare / toms / hh / ride / crash), loaded via `audio-separator`. First
  run downloads ~700 MB to `~/.cache/sheetydrums/drumsep/`. CC-BY-NC-SA weights.
- **CheukExpander** — REAL. Pure-Python 5→10 class expander. Cymbal hits choose
  ride vs crash by comparing sub-stem energy at the hit time. Hihat hits get a
  **per-song** classification: collect the late/attack RMS ratio on the hihat
  sub-stem for every hihat hit, check shape: if BOTH a clearly-tight (ratio <
  0.15) and clearly-loose (ratio > 0.70) population have ≥15% of the hits, the
  song genuinely mixes closed and open hihat — cluster via 1D k-means k=2 on
  outlier-clipped ratios and label by rank. Otherwise the song uses a single
  hihat character (always-loose like Back in Black, or always-tight) and every
  hit gets a uniform label decided by whether the song's median ratio exceeds
  0.40. The shape-based test avoids the "k-means finds two clusters in
  unimodal data" trap that outlier-laden ratios produce. Tom hits get a per-song
  classification: collect the band-limited (50–500 Hz) spectral centroid for
  every tom hit, cluster the song's tom population with 1D k-means (k=3 with
  a merge step for clusters whose centers come out within 25 Hz of each other),
  then label by cluster rank — lowest pitch → tom_low, highest → tom_high,
  middle (if a 3-cluster survives the merge) → tom_mid. Per-song clustering
  avoids the kit-tuning problem (a metal kit's floor tom can share centroid
  with a jazz kit's mid tom). Single-pitch songs land on tom_mid uniformly.
- **Validation on Treble Charger — Brand New Low** (see
  `backend/scripts/eval/`): v2-collapsed scoring is **F1 = 91.2%** (matches v1
  within noise, no regression); full 10-class scoring is **F1 = 78.5%**.
  Architectural wins vs v1: crash 0% → 67%, tom_high 0% → 50%, tom_low 0% →
  39%. Hihat open/closed and tom recall are the remaining weak spots —
  hihat split is sensitive to "loose hihat" passages that sit between open
  and closed, and tom recall is limited by ADTOF (the few toms it does fire
  on are classified correctly — `tom_low` precision = 100%).

**v1 backend build complete.** Pipeline produces real Demucs separation, real ADTOF transcription, real Beat This! beat grid, and clean 16th-note quantized positions.

**v1 frontend scaffolded** (Vite + TypeScript + VexFlow). Loads `events.json` via fetch, generates types from `schema/events.schema.json` at build time (json-schema-to-typescript), renders metadata + a placeholder VexFlow stave. Real percussion-staff rendering is the next frontend task. Setup: `cd frontend && pnpm install && pnpm run dev`.

v2 work tracked in `docs/v2-backlog.md`.
