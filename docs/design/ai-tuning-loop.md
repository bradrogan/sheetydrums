# Design: AI-in-the-loop transcription tuning

**Status:** planned — this is the next feature after the current round of bug fixes.
Supersedes the sketch in `docs/v2-backlog.md` → "Adaptive tuning (AI-in-the-loop)".

## Goal / user story

A transcription is rarely perfect. Today the pipeline emits `events.json` and the
user can hand-edit notes. We want a tighter loop:

> The user looks at the generated score, **selects the bars/notes that are wrong**,
> and says what's wrong in plain language ("these hi-hats should be closed", "it's
> missing the ride on the off-beats", "the toms are all being called mid"). The
> system figures out **which pipeline parameters** would address it, **re-runs only
> the affected stages**, and shows a before/after. The user's **manual edits are
> optionally preserved** across the re-run, and also used as ground truth to
> **score** candidate parameter sets so the change provably improves agreement with
> what the user actually wants.

Design decisions locked with the user (2026-08):
- **Parameterize every knob that meaningfully affects transcription quality** (full catalog below).
- **Local, open-source LLM** so the feature is free to run for the user (no per-call cost).
- **Suggestion = LLM + automated parameter search scored against the user's edits** (LLM narrows the space; search guarantees improvement).
- **Input allows bar and note selection** on the score, not just free text.

Provisional (recommended defaults, revisit during build):
- **AI autonomy:** suggest-and-approve — nothing re-runs until the user clicks Apply.
- **Edit handling on re-gen:** replay edits by anchor and **flag conflicts** that no longer map.

## Current state and the gap

What already exists and is reusable:
- Every stage's quality knobs are **already constructor-parameters** (`ADTOFTranscriber(thresholds=…)`, the ~15 `CheukExpander(…)` args, etc.) — but `factory.build_pipeline` constructs each stage with **hardcoded defaults** and `CLIConfig` only carries `use_drumsep`/`debug_dir`/`verbose`. Nothing threads per-stage params through, and nothing persists them per project.
- The project store (`store.py`) persists `notation` + the drum-stem/drumless WAVs per `video_id`. It does **not** persist the DrumSep sub-stems, the ADTOF hit list, or the beat grid — so any re-run recomputes the whole pipeline (this is why re-transcribing the 7-song library took ~30 min for what was an expander-only change).
- Manual edit affordances exist in `frontend/src/edit.ts` (add / delete / reclassify / nudge), but only the **final** notation is saved — there is no operation log.
- The eval harness (`backend/scripts/eval/_harness.py`) already does bipartite hit-matching + P/R/F1. The **scoring function for the param search is essentially this harness** pointed at the user's edits instead of a tab.
- The expander already emits per-hit diagnostics (`CheukExpander.last_hihat_debug`, dumped under `--debug-dir`) — the seed of the diagnostics summary.

## Architecture overview

```
        ┌─────────────────────────── score view (frontend) ───────────────────────────┐
        │  select bars/notes ──▶ complaint text ──▶ "Refine" panel                      │
        └───────────────┬──────────────────────────────────────────────────────────────┘
                        │ {complaint, selection, project_id}
                        ▼
   ┌────────────────────────────────────────────────────────────────────────┐
   │ Suggester service                                                        │
   │  1. diagnose(project, selection) ─────────────▶ structured diagnostics   │
   │  2. local LLM: (complaint + diagnostics + param catalog)                 │
   │        ─────────────▶ {candidate knobs, directions, search bounds}       │
   │  3. param search over those knobs, each candidate:                       │
   │        re-run only affected stages (cached upstream) ─▶ notation         │
   │        score vs user edits (+ complaint objective) on the selection      │
   │        ─────────────▶ best-scoring params + before/after                 │
   └───────────────────────────────┬──────────────────────────────────────────┘
                                    │ proposed params + preview
                                    ▼
                        user Applies ▶ commit new version (params, notation,
                                        edits-applied, diagnostics)
```

Because upstream stage outputs are cached (below), step 3 evaluates each candidate
in ~seconds, which is what makes the search — and the whole loop — interactive.

## Building block 1 — `PipelineParams` (full parameter catalog)

A single typed, nested, JSON-serialisable dataclass; defaults reproduce today's
behaviour. Threaded `build_pipeline(config, params)` → each stage constructor, and
persisted as `project["params"]`.

Catalog of the knobs worth exposing, grouped by stage, with the complaints each
addresses and the re-run cost (which stages must recompute when it changes):

### separation — Demucs (`stages/separation.py`)
| param | effect | addresses | re-run |
|---|---|---|---|
| `model` (`htdemucs_ft` / `htdemucs` / `htdemucs_6s`) | separation quality vs speed | muddy stem, bleed-driven false hits | Demucs → all |
| `shifts` (test-time aug passes) | cleaner stem, slower | bleed ghosts | Demucs → all |
| `overlap` | segment blending quality | artifacts at segment seams | Demucs → all |
| `segment` (seconds) | memory/quality | — | Demucs → all |

*Rarely the right fix (expensive), but sometimes the root cause.*

### transcription — ADTOF (`stages/transcription.py`)
| param | effect | addresses | re-run |
|---|---|---|---|
| `thresholds[kick]` | kick peak-pick sensitivity | missing/extra kicks | ADTOF → expander → quantize |
| `thresholds[snare]` | snare sensitivity | missing/extra snares, ghost notes | ADTOF → … |
| `thresholds[tom]` | tom sensitivity (weakest-recall class) | missing tom fills | ADTOF → … |
| `thresholds[hihat]` | hi-hat sensitivity | dropped off-beat hats | ADTOF → … |
| `thresholds[cymbal]` | ride/crash sensitivity | dropped steady ride | ADTOF → … |
| `min_distance` (if added to the peak-picker) | dedupe double-triggers | one hit rendered as two | ADTOF → … |

*The primary recall/precision knobs — most "missing X" / "extra X" complaints land here.*

### sub-stem — DrumSep (`stages/drumsep.py`)
| param | effect | addresses | re-run |
|---|---|---|---|
| `enabled` (`use_drumsep`) | turns on 7-class expansion | no open/closed or ride/crash split at all | DrumSep + expander → quantize |

### expansion — CheukExpander (`stages/expander.py`)
| param | effect | addresses | re-run |
|---|---|---|---|
| `cymbal_window` | ride-vs-crash energy window | ride/crash swapped | expander → quantize |
| `hihat_attack_window` | attack baseline for the open/closed ratio | over/under-sensitive hat split | expander → quantize |
| `hihat_decay_start` / `hihat_decay_max` / `hihat_next_onset_guard` | the adaptive decay window | fast grooves mis-split | expander → quantize |
| `hihat_clearly_tight` / `hihat_clearly_loose` / `hihat_bimodal_min_fraction` | when to treat a song as a genuine open/closed mix | song wrongly all-open or all-closed | expander → quantize |
| `hihat_unimodal_open_threshold` | whole-song open/closed cutoff | song's overall hat character wrong | expander → quantize |
| `hihat_ratio_clip` | outlier clamp before clustering | a few loud/quiet hats skew the split | expander → quantize |
| `tom_body_window` / `tom_centroid_band` | tom pitch feature | tom pitches mislabelled | expander → quantize |
| `tom_uniform_spread_hz` / `tom_min_cluster_gap_hz` | how readily toms split into hi/mid/low | toms over- or under-split | expander → quantize |

*The knobs we've been living in — cheap to re-run (no re-separation, no re-detection).*

### beats — Beat This! + phase correction (`stages/beats.py`, `phase_correct.py`)
| param | effect | addresses | re-run |
|---|---|---|---|
| `allowed_beats_per_bar` (currently `{2,3,4}`) | meter inference constraint | wrong time signature | beats → quantize |
| `tempo_multiple_guard` (half/double-time) | tempo octave | "it's half/double the real tempo" | beats → quantize |
| phase-correct `min_kicks` / `confidence` | downbeat re-alignment aggressiveness | bar lines / downbeat off-phase | beats → quantize |

### quantize — StubQuantizer (`stages/quantize.py`)
| param | effect | addresses | re-run |
|---|---|---|---|
| `subdivision` (16th → 32nd) | grid resolution | notes snapped too coarse | quantize only |
| `swing` / `triplet_grid` (v2) | non-straight grids | shuffle/triplet feel lost | quantize only |

The catalog is also authored as a machine-readable table (name, group, type,
range, default, one-line effect, complaint tags, downstream-stages) — this same
table is the **param catalog the LLM reads** and the **search space** the optimiser
walks.

## Building block 2 — stage caching + re-run DAG

Persist each stage's output, keyed by a hash of *(the stage's params + the hashes
of its inputs)*:

| artifact | produced by | key inputs |
|---|---|---|
| `drums.wav` (already saved) + `drumless.wav` | Demucs | mix + `separation` params |
| `substems/*.wav` (kick/snare/toms/hh/ride/crash) | DrumSep | drums + `drumsep` params |
| `hits.raw.json` (time, class, confidence) | ADTOF | drums + `transcription` params |
| `hits.expanded.json` | expander | hits.raw + substems + `expander` params |
| `grid.json` (beats, downbeats, tempo, meter) | Beat This! + phase-correct | mix + `beats` params + hits (phase-correct uses kick times) |
| `notation` | quantizer | hits.expanded + grid + `quantize` params |

On re-run: recompute a stage iff its cache key changed (its params changed, or any
upstream artifact's hash changed); otherwise load the cached artifact. Store under
`~/.cache/sheetydrums/projects/<video_id>/stages/`.

Consequences:
- An `expander`-only change re-runs expander + quantize → **~seconds**, reusing cached hits + sub-stems.
- A `quantize`-only change (subdivision) is near-instant.
- Only a `separation` change pays the full Demucs cost.
- Independently valuable: makes *all* re-runs cheap (the library re-run problem).

Note the one cross-branch dependency: `phase_correct` consumes kick times, so the
grid depends on `transcription` params too — encode that edge in the DAG.

## Building block 3 — diagnostics summary

`diagnose(project, selection=None) -> dict` — the structured signal the LLM reads
(never raw audio). Fields:
- per-class hit counts + per-bar rate; confidence distribution per class.
- expander hi-hat decision (reuse `last_hihat_debug`: median ratio, tight/loose fractions, bimodal decision, k-means centers); tom clustering summary.
- tempo, meter, bar count; quantization residuals (how far raw onsets sat from the snapped grid).
- heuristic flags: "kick on ~every 16th → over-detection", "section with 0 hats", "ride present but only on down-beats", "hi-hat all-open / all-closed".
- when `selection` is set, all of the above **restricted to the selected bars/instruments**.

## Building block 4 — complaint input with bar/note selection

Frontend "Refine" panel on the score view:
- The user can marquee/click-select bars and/or individual notes (extends the existing edit-mode selection in `edit.ts`).
- The selection is sent as `{bars: [...], notes: [{bar, sixteenth, instrument}], text: "…"}`.
- Selection scopes **both** the diagnostics (block 3) **and** the search objective (block 5) — e.g. "only score agreement on hi-hats in bars 41–56", which is far more targeted than a whole-song objective.

## Building block 5 — suggestion = local LLM + edit-scored param search

Two-stage, exactly as decided:

**(a) LLM narrows the space.** The local model (below) receives `{complaint, selection-scoped diagnostics, param catalog}` and returns a **structured** proposal:
```json
{ "knobs": ["expander.hihat_unimodal_open_threshold", "expander.hihat_decay_max"],
  "directions": {"...": "increase"}, "bounds": {"...": [0.3, 0.7]},
  "rationale": "complaint says too many open hats in a fast groove; …" }
```
Output is constrained to a JSON schema (grammar-constrained decoding) so parsing is reliable. The LLM does **not** pick final values — it picks *which* knobs and roughly which direction, keeping the search low-dimensional.

**(b) Automated search verifies + optimises.** Over just those knobs, run a small search (coordinate descent / a coarse grid, then local refine; ≤ ~20–40 candidates). Each candidate:
1. sets params, re-runs only the affected stages (cheap, cached upstream),
2. scores the resulting notation against the **objective**:
   - **ground truth = the user's manual edits** (treat each edit as a label), matched with the eval harness's bipartite matcher, restricted to the selection;
   - plus a **complaint-derived term** when there are few/no edits yet (e.g. "reduce open-hat count in the selection" → penalise open-hat hits there), so the loop works before the user has hand-corrected much.
3. keep the best-scoring candidate.

Return the best params + a before/after preview. Because every candidate eval is a
fast cached re-run, a 2–3-knob search finishes in a handful of seconds. The search
is what makes the result *provably* better on the objective rather than merely
plausible — the LLM can be small/imperfect and it still converges.

Reuse: the scoring is the `_harness.py` matcher; "edits as labels" is the same shape
as "tab as labels".

## Building block 6 — edit preservation (operations log)

Replace "store only final notation" with an **operations log** — `project["edits"]`,
an ordered list of anchored ops mirroring the `edit.ts` affordances:
```
add        {bar, sixteenth, instrument, duration}
delete     {bar, sixteenth, instrument}
reclassify {bar, sixteenth, from, to}
move       {bar, from_sixteenth, to_sixteenth, instrument}
```
On re-generation, if "keep my edits" is on, **replay by anchor** (`bar + sixteenth +
instrument`, within a small tolerance) onto the fresh notation:
- `reclassify` / `delete` / `move`: apply if the anchored note still exists; else **conflict** → surface ("your change to bar 12 no longer maps").
- `add`: re-add if absent.

Interaction with the param fix: a re-run is often triggered by the *same* problem the
user hand-fixed, so replay is usually a harmless no-op that agrees with the new
output; where replay **disagrees** with the generated result, that's both a conflict
to surface *and* a signal the param change over/under-shot. The edit log is also the
ground truth block 5 scores against — one artifact, two uses.

"Optionally preserve" = per-re-run choice: replay-all / replay-with-conflict-review /
take-the-fresh-generation-clean.

## Building block 7 — versioning

Each generation is an immutable snapshot: `{params, notation, edits_applied,
diagnostics, timestamp, parent_version}`. Keep a version list per project; user can
diff/revert. Makes experimentation safe (formalises the manual backup-dir we used
during the hi-hat fix) and gives the search an audit trail.

## Local LLM choice

Requirements: runs locally on the user's Mac (Apple Silicon / Metal), free, reliable
**instruction-following + structured/JSON output**, small enough to be responsive.
The task is constrained (map a complaint → which knobs + direction, from a fixed
catalog) and the search does the correctness heavy-lifting, so a small model suffices.

Recommendation:
- **Serving: [Ollama](https://ollama.com)** — one-command local server on macOS with Metal acceleration, OpenAI-compatible `/v1/chat/completions`, easy model pulls, and **structured-output/JSON-schema support** (`format`) for grammar-constrained decoding. Alternatives: `llama.cpp` server, LM Studio.
- **Model: Qwen2.5-Instruct 7B or 14B** (Apache-2.0) — strong tool-use/JSON adherence, runs well quantized (GGUF Q4/Q5) on Apple Silicon. Use **14B** if the machine has ≥ 32 GB unified memory, else **7B**. Fallback: Llama-3.1-8B-Instruct.
- Constrain the response to the proposal JSON schema so parsing never depends on prose.
- Put it behind an `LLMSuggester` interface (`propose(complaint, diagnostics, catalog) -> Proposal`) with an Ollama implementation as default, so a hosted model can be swapped in for evaluation without touching the loop.

**Re-validate the specific model at build time** — the local-model landscape moves fast; pick by the selection criteria above against whatever is current, benchmarking on a handful of real complaint→knob cases from `scripts/eval/`.

Tradeoffs: first pull is a few GB; Metal inference is slower than a hosted frontier model but fine for a single suggestion call per complaint. The compute-heavy part (the param search) uses **no** LLM — it's cached re-runs + scoring.

## UX flow

1. Score view → "Refine" panel. User selects bars/notes, types the complaint.
2. Panel shows: diagnosis (from diagnostics), the proposed change in plain English + a collapsible param diff, and — once the search runs — the before/after score delta on the selection.
3. `[Apply & re-run]` (default: suggest-then-approve). Toggle `[Keep my edits ✓]`.
4. Re-run (fast, cached) → new score with added/removed/changed notes highlighted; conflicts from edit-replay listed.
5. `[Accept]` saves a new version / `[Discard]` reverts / keep chatting to iterate.

## Data-model changes (project record)

Add to each project JSON: `params` (block 1), `edits` (block 6 op log), `versions`
(block 7 list, or a sibling directory). Stage-cache artifacts live under
`~/.cache/sheetydrums/projects/<video_id>/stages/`.

## Phasing

- **Phase 0** — `PipelineParams` threading + stage caching + re-run DAG. *(Independently valuable; also the prerequisite for everything else. Makes all re-runs cheap.)*
- **Phase 1** — diagnostics summary + a manual params panel (tweak a knob, re-run, before/after diff). Validates caching + re-run + diff UX with no AI.
- **Phase 2** — bar/note selection input + the edit-scored param **search** (still no LLM: a "which knob?" dropdown drives the search). Proves the search + scoring.
- **Phase 3** — the local **LLM** front-end that turns a free-text complaint into the search's starting knobs/directions.
- **Phase 4** — operations log + anchor-replay + conflict surfacing + "keep edits" toggle; wire edits in as the search's ground truth.
- **Phase 5** — versioning UI (diff/revert).

## Risks / open questions

- **Search objective before edits exist** — early on there are no edits to score against; rely on the complaint-derived objective (block 5b) and revisit its formulation.
- **Anchor drift** — if a param change shifts bar count/positions a lot, edit anchors may not map; conflict-surfacing must be clear, and anchoring may need to be time-based rather than bar-index-based.
- **Local-model reliability** — mitigated by schema-constrained output + the search backstop; still validate on real cases.
- **Autonomy / edit-handling defaults** — provisional (suggest-approve; replay+flag); confirm during Phase 3/4.
- **Cache size** — persisting 6 sub-stem WAVs per project adds up; consider lossy/compressed storage or eviction.
