# Phase 3 Implementation Plan — "Fix the rest of the song for this"

**Status:** planned; **owner decisions D1–D5 locked 2026-09-21** (D3's tie-break revised from "least notes changed" to "least parameter movement" per owner — a repeated mistake *should* fix everywhere). Ready to build 3a.
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

**D2 — A pass adds a system-layer *delta*; it does NOT change the base or `project["params"]`.** *(Confirmed 2026-09-21.)* The search finds winning params, re-runs the pipeline with them, then expresses the result as **system ops** (the diff vs the current base, restricted to the targeted lanes — D4) tagged `origin='system'`, stored as the `system_layer` with a `pass_id`. Base + `project["params"]` stay put. Rationale: keeps the base stable, makes the pass reviewable/removable/supersedable as a unit (block 6), and keeps user edits locked. The winning params ride along on the pass record for provenance/audit + to regenerate the delta on a later base change. A full manual re-tune (Phase 1 `/retune`→accept) remains the separate path that *does* rewrite the base.

How a pass differs from a manual base re-tune — both re-run the pipeline, but:

| | manual base re-tune (`/retune`→accept) | "fix the rest" system pass |
|---|---|---|
| params chosen by | you, hand-dialing | the search, scored vs your verified labels |
| result lands as | a new **base** (replaces the generation) | a **system overlay** on the unchanged base |
| precedence | *is* the base; selections sit on top | **below** selections; dropped inside verified regions |
| your verified regions | reconciled/re-anchored onto the new base | untouched by construction |
| undo | revert to a version snapshot | remove the pass as a unit (base + edits intact) |
| scope | whole song | targeted lanes only (D4) |

> Alternative: accept the tuned run as a new base. Rejected: that's the base path, not a replaceable overlay, and it fights the "system regenerated, not replayed" rule.

**D3 — Objective = reproduce-F1 (primary), minimal-parameter-perturbation (tie-break). Propagation is inherent in a global param fix.** *(Revised 2026-09-21 per owner — supersedes the earlier "least-collateral-change" tie-break, which wrongly penalised the desired sweeping fix.)*
- **The fix is a parameter change, and params apply globally.** When the search finds a knob value that reproduces your one verified bar, that same value applies song-wide — so the correction propagates to every similarly-behaving passage *for free*. **You verify one representative bar and say "fix this everywhere"; you never verify the mistake per-bar.** This is the core "fix the rest" behaviour and it falls straight out of tuning-by-param (vs editing note-by-note).
- **Primary term:** bipartite-matched F1 of each candidate against the verified selections' frozen notes, per the lane's schema-instrument set, at a sixteenth tolerance (reuse the matcher). Aggregated over **all** verified selections (D1).
- **Tie-break:** among param sets with equal reproduce-F1, prefer the **smallest parameter movement from the current values** (gentlest knob nudge that achieves the match). This lets a fix propagate wherever it naturally applies while rejecting an extreme param set that reproduces your one bar by coincidence and mangles the rest. It does **not** penalise many notes changing — a repeated mistake *should* change many notes.
- **When one bar under-constrains the fix** (a genuinely mixed section — e.g. some passages open, some closed), verify a second representative bar. That's another label (D1) and further pins the search; the loop then finds the params that satisfy *both*. Additional verifications are only needed when the first doesn't pin it — never to hand-propagate.
- **"Notes changed outside the verified regions"** is kept as a **reported preview stat** (so you can eyeball over-reach in the delta), **not** a scoring term.
> Alternative: a positive similar-passage coverage reward — deferred (needs a passage-similarity metric and has no labels to validate against; D1 + global-param propagation cover the intent for v1).

**D4 — A pass's system ops are limited to the lanes the search targeted.** *(Recommended.)* The knob-map (below) maps proposed knobs → affected lanes (e.g. `expander.hihat_*` → the `hihat` lane; `expander.tom_*` → tom lanes; `transcription.thresholds[snare]` → `snare`). The base-vs-candidate diff emits ops only for those lanes, so a "fix the hats" pass never rewrites kicks just because ADTOF jittered elsewhere. Bounds the delta and keeps the pass legible.

**D5 — Search budget.** *(Recommended defaults.)* Match threshold `F1 ≥ 0.90` = converged; round cap `3` (locked with owner 2026-09-20); ≤ `~24` candidates per round (coordinate descent over ≤3 knobs, coarse grid). A no-gain round does **not** stop the loop — see the Capped auto-loop note (§3b) for why. All tunable constants in one module.

## Architecture (Phase 3 slice of block 5)

```
verified selection(s) ─┐
                       ▼
 POST /projects/{id}/fix-the-rest {selection_id}     (job; streams progress)
   loop ≤ D5.round_cap:
     (a) heuristic proposer: {user edit ops, still-unsatisfied per residual} → {knobs}
     (b) search over those knobs — each candidate:
           build_pipeline(params, cache_dir) → candidate base notation   (cached upstream → seconds)
           score = reproduce_F1(candidate, verified_selections)  [tie-break: min param movement]   (§3a)
         keep best; if best ≥ match_threshold → converged, stop
         else re-propose (residual marks which ops remain) for a different knob set
   winner params → candidate notation
   system ops = diff(base, candidate, targeted_lanes, exclude=verified_regions)   (§3c)
   preview = compose(base, {pass_id, ops}, selections) → {effective, origin_map, region conflicts}
     ▼ (job result: preview delta, nothing persisted)
 POST /projects/{id}/system {pass_id, ops, params}   ← Accept: persist (supersedes prior pass)
 DELETE /projects/{id}/system                         ← Discard/undo (exists)
```

## Building blocks

### §3a — the scorer (`search/score.py`, pure, no models)

`reproduce_score(candidate: Notation, selections: list[Selection]) -> ScoreResult`

- For each verified selection (lane L, bars [bs,be], frozen `notes`): take the candidate's notes in lane L across [bs,be], and the frozen notes, and bipartite-match **per schema instrument in L's instrument set** at a sixteenth tolerance. Lane→instruments: `snare→{snare}`, `hihat→{hihat_closed,hihat_open}` (chick is its own lane), `tom_*` each its own instrument, cymbals `ride`/`crash`, `kick→{kick}`. This is what makes a closed→open reclassify score as a miss until the params fix it.
- Reuse `scripts/eval/_harness.py` primitives — promote `bipartite_match` + `stats` into `sheetydrums.matching` (importable from the package, not just the eval scripts) and have the eval scripts import from there (no behaviour change, one matcher).
- Aggregate TP/FP/FN across all selections → `overall_f1`, plus `per_selection` and `per_instrument` breakdowns and a **`residual`** list (the still-wrong labels: which (selection, bar, instrument, position) are FP/FN) — the residual is what drives the capped loop's re-proposal (block 5c).
- **Ranking:** candidates are ranked by `overall_f1` first, then by the **tie-break = smallest parameter movement from the current values** (the search's job, §3b — the scorer just supplies `overall_f1`). Per D3, this is *not* "fewest notes changed."
- `ScoreResult.collateral`: count of notes changed **outside** verified regions vs the current base, restricted to targeted lanes — a **reported preview stat only** (surfaced in the delta so over-reach is visible), never a scoring term.
- **Tests:** pure fixtures, no pipeline. A candidate that exactly matches the selection scores F1=1.0; a closed-vs-open mismatch scores <1; a candidate that reproduces the label *and* changes many other bars in the targeted lane still scores F1=1.0 (a sweeping fix is not penalised); `collateral` is reported but doesn't change the F1 ranking; multi-selection aggregation sums correctly.

### §3b — heuristic proposer + capped loop (`search/propose.py`, `search/loop.py`)

- **Ops-driven knob-map** (static table, the Phase-3 stand-in for the LLM; same output shape so Phase 4 drops in). Per ai-tuning-loop.md block 5a, the proposer reads **the user's edit ops** — their explicit intent — not a re-inference from the reproduce-residual. The op *type* selects the knobs; the residual is only the **satisfaction oracle** (an op is proposed while the candidate still hasn't reproduced it). This dissolves the cross-lane gap that residual-pairing had: a `reclassify` carries `from→to` in one op even across lanes, so a tom_high↔tom_mid or crash↔hihat confusion needs no fragile pairing. Grounded in `params.py`:
  - `reclassify` **within an ADTOF family** (hihat open↔closed, tom pitch, ride↔crash) → that family's expander split knobs (`expander.hihat_*`; `expander.tom_uniform_spread_hz`/`tom_min_cluster_gap_hz`; cymbal window deferred).
  - `reclassify` **across families** (e.g. crash↔hihat) → an ADTOF class confusion → nudge both classes' `transcription.thresholds[...]` (best available; ADTOF has no knob to move an onset between classes — often a best-partial).
  - `add` / `delete` → `transcription.thresholds[<class>]` for that instrument's ADTOF class.
  - `move` → a timing/grid problem (`quantize.subdivisions_per_whole`) — deferred, not yet mapped.
  - (separation knobs deliberately excluded — expensive, rarely the fix.)
  - Groups are returned reclassify-first (most specific intent), then detection; a same-family reclassify has **no** threshold fallback (a threshold can't fix a label) — if its split knobs are exhausted the proposer is honestly out of ideas.
- **Capped auto-loop** (block 5c, owner-locked 2026-09-20): run (a)+(b); if best `F1 ≥ match_threshold` stop; else re-invoke the proposer with the **residual** + knobs-already-tried so it picks a *different* axis; stop at round cap (3), convergence, or when the proposer is **out of ideas**. Return best params **across all rounds** + the residual it couldn't fix. *(Refinement, post-review: a no-gain round does NOT stop the loop — block 5c's "early stop on no gain" would strand a fixable low-priority group behind a higher-priority one the knobs can't reach. The round cap + out-of-ideas bound the cost instead; `run` is memoised per invocation so the repeated seed/default params re-run at most once.)*
- **Search (b):** coordinate descent over the proposed knobs — coarse grid per knob within `bounds`, then one local refine around the best; ≤ D5 candidates. Each candidate = `build_pipeline(config, PipelineParams.from_dict(cand), cache_dir=store.stages_dir(id)).transcribe(audio)` → `serialize_to_schema` → score. Cached upstream stages make this ~seconds. **Rank candidates by `(overall_f1` desc, parameter-distance-from-current asc`)`** (D3): among equal-F1 candidates the gentlest knob move wins, so a fix propagates globally without over-reaching. Parameter distance is a normalised sum over the touched knobs (each `(value − current) / range`).
- **Tests:** proposer returns the hihat knobs for a hihat residual, tom knobs for a tom residual, and a *different* set on the second round given a residual + tried-set; loop stops at cap and returns the best partial. Search uses a **fake pipeline** (a function mapping params→notation) so the loop/scoring are tested with zero model installs — mirrors `test_pipeline.py`'s DI fakes.

### §3c — system-layer delta + persistence (`layering.py`, `server.py`)

- `notation_to_system_ops(base, candidate, lanes, verified_regions) -> list[Op]` — diff base vs candidate within `lanes`, emitting `add`/`delete`/`reclassify`/`move` ops (`origin='system'`), skipping any op whose (bar, lane) is inside a verified region (compose drops those anyway — skipping here keeps the stored delta honest and the preview clean). Reuse `anchor.find_note`/`parse_position`/tolerance for the position match; a same-position instrument change → `reclassify`, a same-instrument position change within a small window → `move`, else `add`/`delete`.
- Endpoints:
  - `POST /projects/{id}/fix-the-rest {selection_id}` → starts a **job** (reuse the `/retune` job + SSE + `JobState` machinery), streams round/score progress, terminal result = a **preview** `{pass_id, ops, params, effective, origin_map, region_conflicts}` (nothing persisted). `region_conflicts` from `anchor.region_status` so drift under a verified region is surfaced at preview time (matches the delta-view design).
  - `POST /projects/{id}/system {pass_id, ops, params}` → **Accept**: `validate_system_layer`, compose-before-write, write `system_layer` wholesale (supersedes any prior pass), return the composed effective + origin map. **No version snapshot** (decided in build): a pass leaves the base + user layer untouched (D2), versions track *base* generations, and `DELETE /system` is the pass's own undo — a later pass superseding an earlier one is intentional and non-recoverable (block 6). The winning `params` are stored **on the pass** (`system_layer.params`, a new optional schema field) for provenance / future delta regeneration, *not* in `project["params"]`.
  - `DELETE /projects/{id}/system` → already exists (Discard/undo the pass).
- **Tests:** diff round-trips (base + emitted ops compose back to the candidate within targeted lanes); an op landing in a verified region is dropped; accept persists + versions + supersedes a prior pass; the whole `/fix-the-rest`→`/system` path with a fake pipeline.

### §3d — frontend launcher + preview (`fixrest.ts`, `main.ts`, `edit.ts`, `api.ts`) — **shipped**

- **Launcher:** "✨ Fix the rest…" button in the edit hub (next to Tune; edit-mode-only via CSS). Clicking with no verified sections shows a hint to verify first; with a system pass already applied it shows Re-run / Remove.
- **Job + preview:** `api.startFixTheRest` → `streamFixTheRest` streams progress into the `#fixrest-panel`; the terminal `FixPreview` (composed `effective` + `pass_id` + `ops` + `params` + `score_f1` + `targeted_lanes`) is previewed by feeding `effective` to **`EditHandle.preview`** (the same edit-layer path the re-tune preview uses — verified sections stay overlaid, changed bars highlight, drawn in place). A small review panel shows +add/−remove counts, the targeted lanes, and the F1 fit, with **Apply** (`POST /system`) / **Discard** (`clearPreview`). Remove uses `DELETE /system`.
- **Deferred:** per-notehead **system-colour** on the preview (the backend returns `origin_map`, but the renderer colours by user-selection regions only today). The delta reads as changed-bar highlighting for now; precise per-note colouring lands with the **stacked before/after delta view** (v2-backlog) that reworks this surface anyway.

## Phasing

- **3a** — `sheetydrums.matching` (promoted harness matcher) + `search/score.py` + tests. *Pure, no models — the load-bearing core.*
- **3b** — `search/propose.py` (knob-map) + `search/loop.py` (search + capped auto-loop) + fake-pipeline tests.
- **3c** — `notation_to_system_ops` + `POST /fix-the-rest` job + `POST /system` accept + tests.
- **3d** — frontend launcher + preview wiring (reuses the edit-layer preview + Q7 review pane). **Shipped** — completes the Phase-3 (heuristic, no-LLM) end-to-end loop.
- Then **Phase 4** (local LLM proposer behind the block-5a interface) and **Phase 5** (versioning diff UI; revert already shipped).

## Risks / notes

- **Base-vs-candidate diff fidelity.** ADTOF/Beat-This are deterministic given cached inputs + params, so a candidate re-run with the *current* params reproduces the current base exactly → an all-default candidate yields an empty delta (sanity check). Non-determinism here would smear the delta; assert it in 3c tests.
- **Tolerance choice** couples the scorer and the diff — one shared constant (sixteenth tolerance) in `sheetydrums.matching`.
- **A pass that can't reach the labels** returns its best partial and says so; the verified selection is the user layer and wins at compose regardless (block 5 "when even the capped loop can't match it"). Tuning only *propagates* the fix — never a precondition for it.
- **Cost guard:** the round cap + candidate cap + out-of-ideas + per-invocation `run` memoisation bound the search; log what was tried and the final residual so a plateau is visible, not silent.
