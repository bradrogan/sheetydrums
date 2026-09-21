# Phase 3 Implementation Plan — "Fix the rest of the song for this"

**Status:** planned. **Owner decisions D1–D5 below are _proposed_ — confirm/adjust before 3a starts.**
**Source of truth:** [`ai-tuning-loop.md`](ai-tuning-loop.md) — Goal/user story, Architecture overview, block 5 (suggestion = proposer + edit-scored search), block 6 (provenance/precedence), "Phasing" (this is **Phase 3**).
**Prerequisites (shipped):**
- **Phase 0** — `PipelineParams` threading + stage cache + re-run DAG (`params.py`, `cache.py`; `build_pipeline(config, params, cache_dir=…)`). A late-stage param tweak re-runs in seconds (verified via `/retune`).
- **Phase 1** — diagnostics summary (`diagnostics.py`) + manual params panel (`/retune` → preview → accept/discard) + delta view.
- **Phase 2** — verified selections (user layer), base→system→user `compose` + `origin_map`, `user`/`system` provenance, delta preview, versioning + revert. The `system_layer` container (`schema/system_layer.schema.json`, `validate.validate_system_layer`, `compose` + `_apply_system_op`, `DELETE /system`) is built and enforced — **but nothing produces a system layer yet.** Phase 3 is what fills it.

## Where this sits

Phase 2 built the container and the precedence rules; Phase 3 is the payoff: a **verified selection drives an edit-scored parameter search** that finds the knobs which reproduce it, applies the same fix to the rest of the song as a **superseding system layer**, and shows it as a reviewable delta. Still **no LLM** — a which-stage *heuristic* picks the knobs (block 5a); Phase 4 swaps the LLM in behind the same interface. Because upstream stage outputs are cached, each candidate evaluation is a ~seconds cached re-run, which is what makes the search interactive.

## Open decisions for owner (proposed — confirm before 3a)

**D1 — Objective scores against ALL verified selections, not just the triggering one.** *(Recommended.)* Each verified selection is a closed-world label set. The search maximises aggregate reproduce-F1 over the **union** of the project's verified selections. Rationale: this is the real "tuning improves with more verifications" signal, avoids a param set that fixes the clicked region but wrecks another verified one, and there is no cold-start (the labels always exist). The "fix the rest" button just names *which* selection prompted the pass; the objective is global over the user layer.
> Alternative: score only the triggering selection (simpler, but a fix can silently regress other verified regions and it doesn't compound across corrections).

**D2 — A pass adds a system-layer *delta*; it does NOT change the base or `project["params"]`.** *(Recommended.)* The search finds winning params, re-runs the pipeline with them, then expresses the result as **system ops** (the diff vs the current base, restricted to the targeted lanes — D4) tagged `origin='system'`, stored as the `system_layer` with a `pass_id`. Base + `project["params"]` stay put. Rationale: keeps the base stable, makes the pass reviewable/removable/supersedable as a unit (block 6), and keeps user edits locked. The winning params ride along on the pass record for provenance/audit only. A full manual re-tune (Phase 1 `/retune`→accept) remains the separate path that *does* rewrite the base.
> Alternative: accept the tuned run as a new base (like manual retune-accept). Rejected: that's the base path, not a replaceable overlay, and it would fight the "system regenerated, not replayed" rule.

**D3 — Objective = reproduce-F1 (primary), least-collateral-change (tie-break).** *(Recommended.)* Primary term: bipartite-matched F1 of each candidate against the verified selections' frozen notes, per the lane's schema-instrument set, at a sixteenth tolerance (reuse `_harness`). Tie-break: prefer the candidate that changes the **fewest notes outside** the verified regions vs the current base (parsimony / least surprise), so a params set that reproduces the labels *and* disturbs the rest least wins. A *positive* "similar passages" coverage reward needs ground truth we don't have outside the selections — so real coverage comes from D1 (more selections), and parsimony guards the unlabelled remainder.
> Alternative: an explicit similar-passage coverage term (deferred — needs a passage-similarity metric and has no labels to validate against).

**D4 — A pass's system ops are limited to the lanes the search targeted.** *(Recommended.)* The knob-map (below) maps proposed knobs → affected lanes (e.g. `expander.hihat_*` → the `hihat` lane; `expander.tom_*` → tom lanes; `transcription.thresholds[snare]` → `snare`). The base-vs-candidate diff emits ops only for those lanes, so a "fix the hats" pass never rewrites kicks just because ADTOF jittered elsewhere. Bounds the delta and keeps the pass legible.

**D5 — Search budget.** *(Recommended defaults.)* Match threshold `F1 ≥ 0.90` = converged; round cap `3` (locked with owner 2026-09-20); ≤ `~24` candidates per round (coordinate descent over ≤3 knobs, coarse grid then one local refine); early-stop a round that yields no score gain. All tunable constants in one module.

## Architecture (Phase 3 slice of block 5)

```
verified selection(s) ─┐
                       ▼
 POST /projects/{id}/fix-the-rest {selection_id}     (job; streams progress)
   loop ≤ D5.round_cap:
     (a) heuristic proposer: {targeted lanes, residual mismatch} → {knobs, directions, bounds}
     (b) search over those knobs — each candidate:
           build_pipeline(params, cache_dir) → candidate base notation   (cached upstream → seconds)
           score = reproduce_F1(candidate, verified_selections)  [tie-break: collateral]   (§3a)
         keep best; if best ≥ match_threshold → converged, stop
         else feed residual mismatch back to (a) for a different knob set
   winner params → candidate notation
   system ops = diff(base, candidate, targeted_lanes, exclude=verified_regions)   (§3c)
   preview = compose(base, {pass_id, ops}, selections) → {effective, origin_map, region conflicts}
     ▼ (job result: preview delta, nothing persisted)
 POST /projects/{id}/system {pass_id, ops, params}   ← Accept: persist + version snapshot
 DELETE /projects/{id}/system                         ← Discard/undo (exists)
```

## Building blocks

### §3a — the scorer (`search/score.py`, pure, no models)

`reproduce_score(candidate: Notation, selections: list[Selection]) -> ScoreResult`

- For each verified selection (lane L, bars [bs,be], frozen `notes`): take the candidate's notes in lane L across [bs,be], and the frozen notes, and bipartite-match **per schema instrument in L's instrument set** at a sixteenth tolerance. Lane→instruments: `snare→{snare}`, `hihat→{hihat_closed,hihat_open}` (chick is its own lane), `tom_*` each its own instrument, cymbals `ride`/`crash`, `kick→{kick}`. This is what makes a closed→open reclassify score as a miss until the params fix it.
- Reuse `scripts/eval/_harness.py` primitives — promote `bipartite_match` + `stats` into `sheetydrums.matching` (importable from the package, not just the eval scripts) and have the eval scripts import from there (no behaviour change, one matcher).
- Aggregate TP/FP/FN across all selections → `overall_f1`, plus `per_selection` and `per_instrument` breakdowns and a **`residual`** list (the still-wrong labels: which (selection, bar, instrument, position) are FP/FN) — the residual is what drives the capped loop's re-proposal (block 5c).
- `ScoreResult.collateral`: count of notes changed **outside** verified regions vs the current base, restricted to targeted lanes — the D3 tie-break.
- **Tests:** pure fixtures, no pipeline. A candidate that exactly matches the selection scores F1=1.0; a closed-vs-open mismatch scores <1; adding an unrelated note outside the region raises `collateral` but not F1; multi-selection aggregation sums correctly.

### §3b — heuristic proposer + capped loop (`search/propose.py`, `search/loop.py`)

- **Knob-map** (static table, the Phase-3 stand-in for the LLM; same output shape so Phase 4 drops in): keyed by the lane + the *kind* of residual mismatch, returns `{knobs: [...], directions: {...}, bounds: {...}}`. Grounded in `params.py`:
  - `hihat` open/closed wrong → `expander.hihat_unimodal_open_threshold`, `expander.hihat_decay_max_seconds`, `expander.hihat_clearly_loose`/`clearly_tight`, `hihat_bimodal_min_fraction`.
  - `tom_*` pitch wrong → `expander.tom_centroid_band_hz`, `tom_uniform_spread_hz`, `tom_min_cluster_gap_hz`.
  - `ride`/`crash` swapped → `expander.cymbal_window_seconds`.
  - missing/extra hits (FN/FP of an onset the class *can* fire) → `transcription.thresholds[<class>]` (direction: FN→lower, FP→higher).
  - coarse snapping → `quantize.subdivisions_per_whole`.
  - (separation knobs deliberately excluded from the default map — expensive, rarely the fix; reachable only if later rounds exhaust the cheap knobs.)
- **Capped auto-loop** (block 5c, owner-locked 2026-09-20): run (a)+(b); if best `F1 ≥ match_threshold` stop; else re-invoke the proposer with the **residual** + knobs-already-tried so it picks a *different* axis; stop at round cap (3), convergence, or a no-gain round. Return best params **across all rounds** + the residual it couldn't fix.
- **Search (b):** coordinate descent over the proposed knobs — coarse grid per knob within `bounds`, then one local refine around the best; ≤ D5 candidates. Each candidate = `build_pipeline(config, PipelineParams.from_dict(cand), cache_dir=store.stages_dir(id)).transcribe(audio)` → `serialize_to_schema` → score. Cached upstream stages make this ~seconds.
- **Tests:** proposer returns the hihat knobs for a hihat residual, tom knobs for a tom residual, and a *different* set on the second round given a residual + tried-set; loop stops at cap and returns the best partial. Search uses a **fake pipeline** (a function mapping params→notation) so the loop/scoring are tested with zero model installs — mirrors `test_pipeline.py`'s DI fakes.

### §3c — system-layer delta + persistence (`layering.py`, `server.py`)

- `notation_to_system_ops(base, candidate, lanes, verified_regions) -> list[Op]` — diff base vs candidate within `lanes`, emitting `add`/`delete`/`reclassify`/`move` ops (`origin='system'`), skipping any op whose (bar, lane) is inside a verified region (compose drops those anyway — skipping here keeps the stored delta honest and the preview clean). Reuse `anchor.find_note`/`parse_position`/tolerance for the position match; a same-position instrument change → `reclassify`, a same-instrument position change within a small window → `move`, else `add`/`delete`.
- Endpoints:
  - `POST /projects/{id}/fix-the-rest {selection_id}` → starts a **job** (reuse the `/retune` job + SSE + `JobState` machinery), streams round/score progress, terminal result = a **preview** `{pass_id, ops, params, effective, origin_map, region_conflicts}` (nothing persisted). `region_conflicts` from `anchor.region_status` so drift under a verified region is surfaced at preview time (matches the delta-view design).
  - `POST /projects/{id}/system {pass_id, ops, params}` → **Accept**: `validate_system_layer`, write `system_layer` wholesale (supersedes any prior pass), snapshot a version (`store.append_version`), return the composed project. Reuse `_save_selections_or_422`-style compose-before-write guarding.
  - `DELETE /projects/{id}/system` → already exists (Discard/undo the pass).
- **Tests:** diff round-trips (base + emitted ops compose back to the candidate within targeted lanes); an op landing in a verified region is dropped; accept persists + versions + supersedes a prior pass; the whole `/fix-the-rest`→`/system` path with a fake pipeline.

### §3d — frontend launcher + preview (`edit.ts`, `main.ts`, `render.ts`, `api.ts`)

- **Launcher:** "Fix the rest of the song for this" in the edit hub (design: the bottom-left slot where `#poo-doodle` sits), enabled when ≥1 verified selection exists, labelled with the triggering selection (lane + bar range).
- **Job + preview:** call `POST /fix-the-rest`, stream progress into the same status UI as the tuning pane; on result, render the preview through the **existing delta view** — system-coloured (`#d9660f`, already reserved in `render.ts`'s origin path) adds/reclassifies, ghosted removals, verified sections still overlaid in the user colour, drifted regions flagged. Reuse the tuning-pane **Accept/Discard** review UX (Q7) verbatim — Accept → `POST /system`, Discard → drop (or `DELETE /system` if it was somehow persisted).
- No new rendering primitives — the origin colouring, delta highlight, and review pane all exist from Phase 2 + Q7.

## Phasing

- **3a** — `sheetydrums.matching` (promoted harness matcher) + `search/score.py` + tests. *Pure, no models — the load-bearing core.*
- **3b** — `search/propose.py` (knob-map) + `search/loop.py` (search + capped auto-loop) + fake-pipeline tests.
- **3c** — `notation_to_system_ops` + `POST /fix-the-rest` job + `POST /system` accept + tests.
- **3d** — frontend launcher + preview wiring (reuses delta view + Q7 review pane).
- Then **Phase 4** (local LLM proposer behind the block-5a interface) and **Phase 5** (versioning diff UI; revert already shipped).

## Risks / notes

- **Base-vs-candidate diff fidelity.** ADTOF/Beat-This are deterministic given cached inputs + params, so a candidate re-run with the *current* params reproduces the current base exactly → an all-default candidate yields an empty delta (sanity check). Non-determinism here would smear the delta; assert it in 3c tests.
- **Tolerance choice** couples the scorer and the diff — one shared constant (sixteenth tolerance) in `sheetydrums.matching`.
- **A pass that can't reach the labels** returns its best partial and says so; the verified selection is the user layer and wins at compose regardless (block 5 "when even the capped loop can't match it"). Tuning only *propagates* the fix — never a precondition for it.
- **Cost guard:** the round cap + candidate cap + early-stop bound the search; log what was tried and the final residual so a plateau is visible, not silent.
