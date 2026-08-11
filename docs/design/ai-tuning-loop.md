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
- **Interaction is correct-by-example (approach B), only.** The user hand-fixes a spot in edit mode; a **"Fix the rest of the song for this"** action generalises that correction across the whole song. No complaint text box, chips, or per-selection prompts in v1 — those (approaches A/C from the interface critique) can be layered on later if a correction is ever awkward to express as a single edit.
- **Edit provenance is two-layer with strict precedence.** Every change is tagged `user` or `system` (a "fix the rest" pass). **User edits are inviolable; system edits are replaceable.** A system pass never overwrites a user edit; a later system pass supersedes the earlier one. A "fix the rest" pass is previewed as a delta and committed only on the user's OK.

Provisional (revisit during build):
- **Edit handling on re-gen:** user ops replay by anchor + lock; the system layer is regenerated, not replayed (see block 6).

## Current state and the gap

What already exists and is reusable:
- Every stage's quality knobs are **already constructor-parameters** (`ADTOFTranscriber(thresholds=…)`, the ~15 `CheukExpander(…)` args, etc.) — but `factory.build_pipeline` constructs each stage with **hardcoded defaults** and `CLIConfig` only carries `use_drumsep`/`debug_dir`/`verbose`. Nothing threads per-stage params through, and nothing persists them per project.
- The project store (`store.py`) persists `notation` + the drum-stem/drumless WAVs per `video_id`. It does **not** persist the DrumSep sub-stems, the ADTOF hit list, or the beat grid — so any re-run recomputes the whole pipeline (this is why re-transcribing the 7-song library took ~30 min for what was an expander-only change).
- Manual edit affordances exist in `frontend/src/edit.ts` (add / delete / reclassify / nudge), but only the **final** notation is saved — there is no operation log.
- The eval harness (`backend/scripts/eval/_harness.py`) already does bipartite hit-matching + P/R/F1. The **scoring function for the param search is essentially this harness** pointed at the user's edits instead of a tab.
- The expander already emits per-hit diagnostics (`CheukExpander.last_hihat_debug`, dumped under `--debug-dir`) — the seed of the diagnostics summary.

## Architecture overview

```
        ┌─────────────────────────── score view (frontend, edit mode) ────────────────┐
        │  hand-fix one spot (user edit) ──▶ "Fix the rest of the song for this"        │
        └───────────────┬──────────────────────────────────────────────────────────────┘
                        │ {correction (edit ops), project_id}
                        ▼
   ┌────────────────────────────────────────────────────────────────────────┐
   │ "Fix the rest" pass                                                      │
   │  1. diagnose(project, correction-scope) ──────▶ structured diagnostics   │
   │  2. pick knobs: which-stage heuristic (P3) / local LLM (P4)              │
   │        ─────────────▶ {candidate knobs, directions, search bounds}       │
   │  3. param search over those knobs, each candidate:                       │
   │        re-run only affected stages (cached upstream) ─▶ notation         │
   │        score vs the user's correction (reproduce it + generalise)        │
   │        ─────────────▶ best params + system-layer delta                   │
   └───────────────────────────────┬──────────────────────────────────────────┘
                                    │ preview delta (system colour)
                                    ▼
              user OKs ▶ commit as a superseding system layer + new version
                         (user edits stay locked on top; see block 6)
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

## Building block 4 — interaction: correct-by-example ("fix the rest of the song")

The only v1 interaction (approach B from the interface critique; A/C deferred):

1. In **edit mode**, the user hand-corrects one spot with the existing `edit.ts`
   affordances (e.g. reclassify bars 89–100 hi-hats closed→open). That's a
   **user edit** (block 6 provenance).
2. A launcher in the bottom-left slot (replacing the `#poo-doodle` while in edit
   mode) surfaces **"Fix the rest of the song for this"**, scoped to the
   correction just made — its instrument lane and the kind of change.
3. That runs the edit-scored search (block 5) with the user's edit(s) as the
   ground-truth objective, finds the params/labels that reproduce the correction,
   and applies the same fix to the rest of the song as **system edits**.
4. The result is shown as a **preview delta** (block 6 → "Seeing the delta")
   before commit; the user OKs or discards.

There is no free-text complaint box, chips, or per-selection prompt in v1 — the
correction itself is the input, so there is nothing to type or bind. The
correction still **scopes** the diagnostics (block 3) and the search objective
(block 5): "generalise *this* hi-hat open/closed change", not a whole-song reword.
(If corrections ever arise that are awkward to express as a single edit — "it's
dropping the ride on every off-beat" — the chip-based chat from the interface
critique can be added as approach A/C.)

## Building block 5 — suggestion = local LLM + edit-scored param search

Two-stage, exactly as decided:

**(a) LLM narrows the space.** The local model (below) receives `{the user's correction (edit ops), scoped diagnostics, param catalog}` and returns a **structured** proposal:
```json
{ "knobs": ["expander.hihat_unimodal_open_threshold", "expander.hihat_decay_max"],
  "directions": {"...": "increase"}, "bounds": {"...": [0.3, 0.7]},
  "rationale": "user reclassified these hi-hats closed→open in a fast groove; …" }
```
Output is constrained to a JSON schema (grammar-constrained decoding) so parsing is reliable. The LLM does **not** pick final values — it picks *which* knobs and roughly which direction, keeping the search low-dimensional. (Phase 3 ships this stage as a which-stage heuristic; Phase 4 swaps in the LLM.)

**(b) Automated search verifies + optimises.** Over just those knobs, run a small search (coordinate descent / a coarse grid, then local refine; ≤ ~20–40 candidates). Each candidate:
1. sets params, re-runs only the affected stages (cheap, cached upstream),
2. scores the resulting notation against the **objective** — agreement with the
   user's correction, treated as labels and matched with the eval harness's
   bipartite matcher: (i) it must **reproduce** the corrected spot, and (ii) a
   coverage term rewards applying the same change to *similar* passages elsewhere
   (that's the "fix the rest" generalisation). The corrected spot is always
   present as ground truth, so there is no cold-start.
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

### Edit provenance & precedence (user vs system)

Every op carries an `origin`:
- `user` — a manual edit. **Inviolable**, highest precedence, accumulates and persists.
- `system` — applied by a "fix the rest of the song" pass. **Replaceable**, tagged with the `pass_id` that produced it.

Effective notation is layered **base generation → system layer → user layer** (user
applied last, so it always wins). Directly from the locked requirements:
- **A system pass never writes to a position a user edit owns.** User-owned anchors
  are locked and excluded before the pass applies. (They're also the ground truth
  the pass is reproducing, so it should agree there regardless.)
- **A later system pass supersedes the earlier one.** Each "fix the rest" pass
  recomputes the *whole* system layer from the current base + user edits and
  replaces the previous system layer wholesale (keyed by `pass_id`) — system edits
  are never cumulative-immutable, the latest pass wins.
- On **re-generation** (param change / pipeline re-run): user ops replay + lock (as
  above); the system layer is **regenerated** by re-running the pass against the new
  base, not replayed — consistent with "system overwritten by subsequent system".

### Seeing the delta

A "fix the rest" pass is a reviewable **delta** before commit and stays visually
distinct after:
- Preview highlights every note the pass adds/removes/reclassifies in a **system**
  colour, distinct from the **user-edit** colour and the untouched base — so at a
  glance it's obvious what the machine changed vs what you changed.
- The change log groups by origin: user edits listed individually (permanent); each
  system pass as one collapsible group (its whole delta), replaceable/removable as a
  unit. Undoing a system pass reverts only its layer, never a user edit.
- Accept/undo a pass as a unit (per-region accept is a later nicety, not v1).

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
- Put it behind an `LLMSuggester` interface (`propose(correction, diagnostics, catalog) -> Proposal`) with an Ollama implementation as default, so a hosted model can be swapped in for evaluation without touching the loop.

**Re-validate the specific model at build time** — the local-model landscape moves fast; pick by the selection criteria above against whatever is current, benchmarking on a handful of real correction→knob cases from `scripts/eval/`.

Tradeoffs: first pull is a few GB; Metal inference is slower than a hosted frontier model but fine for a single suggestion call per complaint. The compute-heavy part (the param search) uses **no** LLM — it's cached re-runs + scoring.

## UX flow

1. In edit mode, the user hand-fixes one spot (e.g. bars 89–100 hi-hats → open). The bottom-left slot (where the poo-doodle sits) shows **"Fix the rest of the song for this."**
2. Clicking it runs the edit-scored search (block 5) with that correction as ground truth, then shows a **preview delta** — every note it would change, in the *system* colour, distinct from *user* edits and untouched notes.
3. `[Apply]` commits the pass as a **system layer** (replaceable) / `[Discard]` drops it. User edits are never touched.
4. Iterate: fix another spot → "fix the rest" again. The new pass **supersedes** the previous system layer; user edits persist and stay locked.
5. Each committed state is a version (block 7); the change log lists user edits and system passes separately.

## Data-model changes (project record)

Add to each project JSON: `params` (block 1); an `edits` op log (block 6) where each
op carries `origin: "user" | "system"` (system ops also carry the `pass_id` that
produced them); and `versions` (block 7). Stage-cache artifacts live under
`~/.cache/sheetydrums/projects/<video_id>/stages/`. Effective notation is derived by
layering base → system (latest pass) → user.

## Phasing

- **Phase 0** — `PipelineParams` threading + stage caching + re-run DAG. *(Independently valuable; also the prerequisite for everything else. Makes all re-runs cheap.)*
- **Phase 1** — diagnostics summary + a manual params panel (tweak a knob, re-run, before/after diff). Validates caching + re-run + diff UX with no AI.
- **Phase 2** — operations log with **user/system provenance** + the base→system→user layering/precedence + the delta preview (block 6). The foundation "fix the rest" writes into.
- **Phase 3** — **"Fix the rest of the song for this"** (approach B end-to-end): a hand correction drives the edit-scored **search** (block 5), applied as a superseding system layer. Still no LLM — the correction + a which-stage heuristic pick the knobs.
- **Phase 4** — the local **LLM** replaces the heuristic: from the correction + scoped diagnostics it narrows the search's starting knobs/directions.
- **Phase 5** — versioning UI (diff/revert).

## Evidence: the global vs per-song boundary (measured 2026-08)

While diagnosing an open-hi-hat miss (Lazy Eye, bars 89–100 clearly open but
labelled closed) we measured how far a *global* rule can go before the tuning
loop is needed. Method: the open/closed decision is a pure function of the
per-hit decay ratios, so we scored candidate rules offline against the ratio
dumps + ground truth.

Finding: the old "bimodal only if clearly-tight ≥15% AND clearly-loose ≥15%"
gate missed real open sections that are a small minority. Replacing it with a
**threshold-straddle** test (split iff the two ratio clusters land on opposite
sides of the open threshold) — now shipped in `CheukExpander` — captures the
open hits **when they genuinely ring longer than the closed ones**:

| song | open F1 before → after | note |
|---|---|---|
| Disco Inferno | 0% → **53.7%** (P 61% / R 48%) | offbeat + bridge opens; aggregate hi-hat F1 also up |
| Lazy Eye (bars 89–100) | missed → **caught** | clean, clearly-ringing open outro |
| Back in Black | all-open → all-open | both clusters above threshold → no split, no regression |
| **Boogie Oogie Oogie** | 0% → 13% (P 33%, 18 TP / **36 FP**) | **mild regression** — see below |

The **limit** is Boogie: its real chorus opens are fast 16th "barks" choked as
quickly as the closed hats, so open and closed decay-ratios overlap. The high
cluster there is ~⅓ real opens, ~⅔ loud closed accents — by ratio alone
indistinguishable. So the split surfaces a few real opens at the cost of more
false ones (net slightly negative on Boogie). No ratio-only global rule can
separate "real open" from "loud closed" in that regime.

**This is exactly the global/per-song boundary:** the straddle rule is the safe
*global* win (ringing opens); the remaining case — choked opens indistinguishable
by decay-ratio — is what the **per-song loop** resolves (you select the bars and
say "these are open", and the edit-scored search takes the split for *this* song
even though a global default wouldn't), and what the **v2 spectral/trained
classifier** resolves globally. Concretely: for Boogie the loop would score the
split against your edits, see it disagrees, and leave it closed; for Lazy Eye it
would confirm the split.

## Risks / open questions

- **Search objective before edits exist** — early on there are no edits to score against; rely on the complaint-derived objective (block 5b) and revisit its formulation.
- **Anchor drift** — if a param change shifts bar count/positions a lot, edit anchors may not map; conflict-surfacing must be clear, and anchoring may need to be time-based rather than bar-index-based.
- **Local-model reliability** — mitigated by schema-constrained output + the search backstop; still validate on real cases.
- **Autonomy / edit-handling defaults** — provisional (suggest-approve; replay+flag); confirm during Phase 3/4.
- **Cache size** — persisting 6 sub-stem WAVs per project adds up; consider lossy/compressed storage or eviction.
