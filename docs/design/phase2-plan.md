# Phase 2 Implementation Plan — Verified Selections + Provenance + Delta Preview

**Status:** in build (owner decisions Q1–Q5 locked 2026-09-19). **2a** (schemas + store persistence) and **2b** (layering engine + anchor/replay) have shipped to `main`; **2c** is next.
**Source of truth:** [`ai-tuning-loop.md`](ai-tuning-loop.md) — Phasing, "Edit provenance & precedence", block 6 (operations log), block 7 (versioning), "Data-model changes".
**Prerequisites (shipped):** Phase 0 (`PipelineParams` threading, stage cache, re-run DAG), Phase 1 (diagnostics summary + manual params panel + before/after diff).

## Where this sits

Phases 0/1 made re-runs cheap and gave a human a manual "tweak a knob → re-run → before/after diff" loop with no AI. Phase 2 builds **the data foundation and provenance UI that a future "fix the rest of the song" pass writes into** — the verified-selection primitive, the `user`/`system` provenance model with strict precedence, the base→system→user layering engine, and the reviewable delta preview. Phase 2 ships **no AI and produces no system pass**; it builds and exercises the container the system layer will later live in.

## Owner decisions (Q1–Q5), locked

1. **Q1 — Composition is backend-side.** A pure `compose(base, system, selections)` produces the **effective** notation. `GET /projects/{id}` returns *effective* (not raw base); `diagnose` runs on *effective* — so diagnostics reflect the user's corrections instead of silently ignoring them. Raw base stays available under a distinct key for the retune-accept path.
2. **Q2 — Single-note edits stay first-class; verify locks at lane × bar.** You can correct one note; when you mark it verified, the smallest lockable region is that note's **lane × bar**. The UI *shows the region being verified* (highlights the whole bar's lane) so the closed-world claim is visible, and nudges you to extend to the range you actually checked. Single-bar selections are a valid but weak signal for Phase 3's search.
3. **Q3 — Bar-relative anchoring on exact rational position + tolerance** (not rounded sixteenths). Grid-agnostic, so it survives future triplet/tuplet detection. Time-based anchoring stays deferred; all anchor logic is isolated in `anchor.py` so a later swap is one file.
4. **Q4 — Retune default = replay-with-conflict-review**, with all three modes (`replay-all`, `replay-with-conflict-review`, `take-fresh-clean`) offered per run. **Conflicts are auditionable** — each conflict in the review panel drives the existing drums-only playback + loop-region so the user confirms by ear before keep-reposition / discard.
5. **Q5 — Write immutable version snapshots now** (write-only; no reader/UI until Phase 3/5). It's the audit trail Phase 3's param search scores against; skipping it means starting Phase 3 blind.
6. **Q6 — One unified edit-session Save/Discard.** ~~Edit mode is a session; a single top-level Save persists everything, Discard reverts all.~~ **SUPERSEDED by Q7** — bundling the deliberate, low-risk *verify* with the exploratory, high-risk *re-tune* under one gate is the flaw: a Discard to undo a bad tune would also throw away good verifies, and a Save would commit a possibly-worse tune alongside them.
7. **Q7 — Split by kind** (decided 2026-09-20, supersedes Q6). Verify and re-tune are different *kinds* of change and get different controls:
   - **Verify stays immediate** (POST on verify; Remove undoes). It's a deliberate, note-by-note, low-risk correction — persisted the moment you make it, never at risk from a tuning experiment.
   - **Re-tune keeps its own Accept/Discard *in the tuning pane*** — that's its natural home (Accept/Discard only ever apply to a re-tune), so there's no reason to hoist it to a global control. The workflow in the pane must be clear and obvious: Re-run → preview (knobs lock) → Accept or Discard.
   - **Safety net = versioning + revert** (this is what actually answers "a re-tune made it worse than it started", which no Save/Discard shape fixes on its own): 2g writes an immutable base snapshot on every base change (Q5), and a **"revert base to a previous generation / the original"** guarantees a re-tune is never a one-way door. **Priority** — today accepting a re-tune overwrites the old base with no way back.
   - Re-tune **preview renders through the edit layer** (working effective with verifies overlaid + the delta), not a detached `renderScore`, so verified sections don't look "overwritten" during preview.

---

## ~~Unified edit session (Q6)~~ — SUPERSEDED by Q7 (split by kind)

> Kept for history. Q7 replaces this: verify stays immediate (deliberate, safe);
> re-tune keeps its own Accept/Discard in the tuning pane; a bad re-tune is made
> recoverable by versioning + revert rather than by a global Discard. The
> preview-through-the-edit-layer requirement below still holds (it's why verified
> sections don't look overwritten during a re-tune preview).

**Why.** 2c/2d persist each action immediately (Verify → `POST /selections`, Remove → `DELETE`, retune accept → `PUT` base) and the tuning preview renders straight to the score, bypassing the edit layer. That split surfaced real bugs: a re-tune preview could be stranded un-discardably, its highlight leaked into view mode, and verified sections looked "overwritten" during preview. The user wants one obvious Save/Discard governing everything in edit mode.

**Model.**
- **Enter edit** snapshots `{base, selections, effective}`. All subsequent edits mutate in-memory working copies; **no server calls**.
  - Verify a draft → append to the working selection list (marks the session dirty).
  - Remove a selection → drop it from the working list + revert its region to base.
  - Re-tune → the preview folds into the session as a new working **base**; the working effective is recomposed client-side (`applySelections(base, selections)` — the user-layer step of `compose`, ported to TS; Phase 2 has no system layer).
- **Save** (top-level, in the edit hub) persists the whole session atomically: one call sending the working `base` (unchanged unless re-tuned) + the full working selection list. Backend replaces both (extend `PUT /projects/{id}` to accept a `selections` list, or a dedicated save endpoint) and clears the system pass. Clears dirty.
- **Discard** (top-level) reverts the working copies to the entry snapshot, in place (no reload) — since nothing was persisted, the server state is already correct. Clears dirty.
- Leaving edit mode (Done / Back) with a dirty session routes through Save/Discard/Cancel.

**Consequences / what changes from 2c/2d.**
- `POST`/`DELETE /selections` are no longer called per action by the editor; the session Save replaces the whole user layer at once. (The endpoints can stay for other callers / tests.)
- The retune-accept `PUT` merges into the session Save; `reconcile_selections` (§2f) still runs on that Save to re-anchor selections against the re-tuned base and surface conflicts.
- The tuning preview must render **through the edit layer** (working effective, with bands + delta coloring), not a detached `renderScore`, which is also what the delta-preview note (block 6 in `ai-tuning-loop.md`) requires — so this and the per-note delta view are the same integration.

**Interim.** Until this lands, the editor keeps immediate-persist; the tuning preview locks its knobs while pending so it can't be stranded (a stopgap, not the fix).

---

## 0. Scope guardrails

**Phase 2 ships:**

- The **verified-selection** primitive (lane × bar range, both edges adjustable, closed-world materialized notes, mark-verified).
- The **operations log** (`add/delete/reclassify/move`, anchored by exact position) as the change-history/replay representation of user edits.
- The **`user`/`system` provenance** model with strict precedence.
- The **base → system → user layering engine** (`compose`), the verified-region lock, and the **origin-annotated** effective output.
- The **delta-preview** rendering + change-log UI (three-colour scheme, grouped-by-origin log, unit accept/undo).
- **Store + schema plumbing** for all of the above, plus anchor-replay-on-regen with op-level *and* region-level conflict surfacing (auditionable).
- The **immutable version-snapshot write** (block 7) — the write only.
- A **hook** in the beat editor (2d) for a future flam/grace-note affordance (see [`grace-notes-flams.md`](grace-notes-flams.md)) — the hook only; the grace-note feature itself is a separate deliverable.

**Deliberately OUT of Phase 2** (do not build, even partially):

- **No LLM** (Phase 4).
- **No param search / edit-scored optimiser** (Phase 3).
- **No "fix the rest" *execution*.** Phase 2 builds and enforces the container the system layer lives in; **no Phase 2 code produces a system op.** The system layer is validated only against *hand-authored* fixtures.
- **No which-stage heuristic** (Phase 3).
- **No complaint text box / chips / per-selection prompts** (deferred indefinitely).
- **No versioning diff/revert UI** (Phase 5). The immutable snapshot *write* is included (2g); nothing reads it back in a UI.
- **No per-region accept** of a system pass (v1 is unit-only).
- **No flam/grace-note detection or rendering** — that lives in the separate grace-notes feature and v2 detection backlog; Phase 2 only leaves the editing hook.

Scope-creep test: if a task requires *inventing* a system op to exercise it, that task is Phase 3.

---

## 1. Data model

### 1.1 Storage split (where each layer lives)

| Layer | Nature | Storage | Why |
|---|---|---|---|
| **base** | generator output, replaced on re-gen | `project["notation"]` (unchanged) | already the events contract; retune replaces it wholesale |
| **user** | list of verified selections; accumulates; inviolable; individual selections are edge-adjusted / re-verified / undone | **rewritable field `project["selections"]`** | selections *mutate* after creation; append-only can't edit/delete a record without a rewrite, and the design names a field |
| **system** | recomputed & superseded *wholesale* per `pass_id` | field `project["system_layer"]` | "later pass replaces previous wholesale" is a field overwrite |
| **versions** | immutable snapshots, append-only | JSONL `<video_id>.versions.jsonl` | genuinely append-only — the one honest fit for the JSONL helpers |

`delete_project`'s existing `*.jsonl` glob (`store.py`) cleans the version log automatically.

### 1.2 The verified-selection record (user layer)

Selections are created/edited **client-side as drafts**; nothing is persisted until the user marks a selection **verified**. Persisted selections are always `verified: true`.

```jsonc
// element of project["selections"] (a rewritable list-field)
{
  "selection_id": "sel_<uuid>",       // stable id for edit/undo
  "origin": "user",                   // provenance tag (always "user" here)
  "lane": "hihat",                    // the staff lane — its OWN 9-value vocabulary,
                                      //  not the 10 drum classes: `hihat_closed` and
                                      //  `hihat_open` share one `hihat` lane (open/closed
                                      //  is a per-note property, so a verified hi-hat
                                      //  region owns every closed AND open hat in it).
                                      //  `hihat_chick` is its own lane. Mirrors edit.ts's
                                      //  laneOf; `anchor.lane_of` re-implements it.
  "bar_start": 89,                    // inclusive schema bar index
  "bar_end": 100,                     // inclusive; lock granularity is WHOLE BARS (Q2)
  "notes": [                          // FROZEN closed-world ground truth:
    // every corrected note for `lane` across bars 89..100. Each is a
    // RegionNote — a full events-contract Note (position, instrument,
    // duration, sustain_until where applicable) TAGGED WITH ITS BAR, since
    // `position` is bar-relative and the region spans many bars, so a bare
    // Note would be ambiguous. Authoritative at compose time AND the labels
    // Phase 3's scorer reproduces. Frozen at verify time.
    {"bar": 91, "note": {"instrument": "hihat_open", "position": "1/4", "duration": "1/8"}}
  ],
  "ops": [                            // change-history + replay mechanism only:
    // NB: op `instrument`/`from`/`to` ARE the 10-class instrument enum —
    // only `lane` above uses the 9-value lane vocabulary.
    {"kind": "reclassify", "bar": 91, "position": "1/4",  "from": "hihat_closed", "to": "hihat_open"},
    {"kind": "delete",     "bar": 92, "position": "1/2",  "instrument": "hihat_closed"},
    {"kind": "add",        "bar": 93, "position": "0",    "instrument": "hihat_open", "duration": "1/8"},
    {"kind": "move",       "bar": 94, "from_position": "1/8", "to_position": "3/16", "instrument": "hihat_closed"}
  ],
  "verified": true,
  "base_fingerprint": "…",            // hash of the base notes in this lane×bar region at
                                      //  verify time — for region-drift detection on re-gen
  "created_at": "2026-09-19T…"
}
```

- **Materialized `notes` are the source of truth.** The frozen region notes are what `compose` applies and what Phase 3 scores against. A re-generation that changes the base cannot silently move this ground truth. (Honours the design's "store the corrected notes inside it.")
- **`ops` are secondary** — they drive the change-log UI and the replay/conflict story (§1.4); not the sole source of truth. Op kinds mirror `edit.ts` affordances exactly. **Positions are exact rational strings** (`"1/4"`, `"1/12"`), never rounded sixteenth indices (Q3), so a future triplet op is representable.
- **Lock granularity is lane × whole-bar** (Q2). No slot sub-range in the persisted model; `notes` carries the exact positions. Removes the non-4/4 slot-boundary hazard entirely.
- **Single-note / single-bar** selections are allowed (weak signal); the UI nudges toward the verified range.
- **`base_fingerprint`** lets re-gen detect that the base under a verified region changed (§1.4).

### 1.3 The system-layer record (container only; Phase 3 fills it)

```jsonc
// project["system_layer"] — single object, replaced wholesale per pass; null when none
{
  "pass_id": "pass_<uuid>",
  "created_at": "2026-…",
  "ops": [ /* same op shapes; each carries "origin":"system" */ ]
}
```

Precedence enforced at compose time: **a system op is dropped if its `(bar, lane)` falls inside any verified selection's `lane × [bar_start..bar_end]` region.** Exercised in Phase 2 only via a synthetic fixture.

### 1.4 Anchoring & replay-on-regen (Q3 — exact rational position)

All anchor logic lives in **`anchor.py`** so a later swap to time-based anchoring is a one-file change.

- **Position representation:** positions are already exact rationals in the events contract (`"1/4"`, parsed with `Fraction(...)`). The anchor keys on the **exact `Fraction(position)`**, never on `round(position * 16)`. It deliberately does **not** reuse the eval harness's `events_bar_hits` binning: that function collapses instrument classes (`_apply_collapses`) and clamps to 0–15, both wrong for a provenance anchor. (The v1 base is straight-16ths *today*; exact-position matching is a superset that costs nothing now and is triplet/tuplet-ready for v2.)
- **`find_note(notes, position, instrument, tol) -> note | None`** — the `instrument` note in `notes` whose position is nearest `position` within `tol`. Takes **one bar's `notes` list**, so the caller selects the bar; there is no separate anchor-tuple helper (an earlier draft of this plan specified `note_anchor(...)` and a `match_note(base_notation, bar, ...)` taking the whole notation — neither was built, and the anchor tuple stayed implicit). `tol` is a `Fraction` compared strictly (`dist < tol`), defaulting to `DEFAULT_TOL = 1/32` whole-note — exactly half a 16th's spacing, so adjacent 16th slots never cross-match. Grid-agnostic: works for straight 16ths and triplets alike.
- **`lane_of(instrument) -> lane`** — the instrument→lane mapping (`hihat_closed`/`hihat_open` → `hihat`, everything else to itself). Mirrors `edit.ts`'s `laneOf`; every lane comparison in `anchor.py` and `layering.py` goes through it.
- **`canonical_position(position) -> str`** — lowest-terms string form, so anything comparing or hashing positions as text is immune to re-spellings (`"0"` vs `"0/1"`, `"1/4"` vs `"2/8"`).
- **`region_fingerprint(notation, lane, bar_start, bar_end) -> str`** — the hash stored as `base_fingerprint`. Covers `bar`, canonicalized `position`, `instrument`, `duration`, canonicalized `sustain_until`, and `tuplet`; deliberately **excludes** `confidence`, which moves on every re-run without changing the notation the user verified. **Any new Note attribute must be added here** or a change to it will not read as drift.
- **Op-level replay** (re-gen with "keep my edits"): for each user op, re-derive its anchor against the fresh base:
  - `reclassify`/`delete`/`move`: apply if the anchored note still matches; else **op conflict** → surface (auditionable, §2f).
  - `add`: re-add if absent at that position.
- **Region-level drift** (distinct failure mode): before op replay, validate each verified selection's region against the new base:
  - `bar_end` exceeds the new bar count → **region conflict** (`"verified bars 89–100 no longer exist — song is now N bars"`); user keeps (clamp) or discards. Never silently dropped.
  - region survives but `base_fingerprint` mismatches → flag "base changed under your verified edits".
- **`sustain_until` / `duration`:** `compose` applies the **frozen `notes`** (each a `{bar, note}` RegionNote carrying a full Note), so these are preserved verbatim at compose time. On the replay path (ops onto a fresh base) the frozen note's `sustain_until`/`duration` are re-applied. Every composed/replayed result is re-run through `validate()` (cross-field `sustain_until` check is the backstop).
- **User = replay + lock; system = regenerated, not replayed.** On re-gen the system layer is cleared (Phase 3 would re-run its pass).

### 1.5 Effective-notation composition + origin map

```
effective, origin_map = compose(base, system_layer, selections)
```

1. Start from `base`; tag every note `origin: base`.
2. Apply `system_layer.ops` — **skip any op whose `(bar, lane)` lands inside a verified region**; tag survivors `origin: system`.
3. For every verified selection, **overwrite its `lane × [bar_start..bar_end]` region with the selection's frozen `notes`**; tag them `origin: user`. User always wins.
4. `validate()` the composed events notation.
5. Return `(effective, origin_map)` — `origin_map` maps each emitted note to `base | system | user` so the frontend colours the delta without re-running compose.

`compose` is pure, lives in **`layering.py`**, and is the single authority for "final notation."

**Conflict rules.** Steps 2 and 3 each have a case the above glosses over. All three are enforced, because none of them is caught downstream:

- **One owner per `(lane, bar)` — overlap is rejected, not resolved.** Step 3 clears the whole lane across a region before writing its frozen notes, so two verified selections overlapping on one lane would make the output depend on list order (the later one wipes the earlier one's ground truth, and drops it from Phase 3's labels). Rather than pick an order, overlap is refused: `validate._check_no_lane_overlap` runs in `store.save_project` **and** again inside `compose`. Selections on *different* lanes may share bars freely; touching-but-disjoint ranges on one lane are fine. **2d must therefore prevent or merge an overlapping drag** — the invariant cannot be left to the UI happening to behave.
- **A `reclassify` op touches two lanes.** Step 2's "`(bar, lane)`" is singular, but a reclassify carries `from` and `to`, which can be different lanes (`snare` → `tom_mid`). The op is dropped if **either** lane is verified at that bar (`layering._op_lanes`) — the conservative reading, so a system pass can never reach into a user-owned lane from outside it.
- **Duplicate-producing ops are dropped.** A system `add` onto a position its instrument already occupies, or a `reclassify`/`move` landing on one, is skipped rather than applied — matching `anchor._apply_op`'s conflict semantics for the same op vocabulary. This must live in steps 2/3: `events.schema.json` has **no `uniqueItems`**, so step 4's `validate()` would not catch a stacked duplicate, and the renderer would simply draw two identical noteheads.

### 1.6 Back-compat

- `system_layer` and `selections` are **optional/additive** in `project.schema.json`. A legacy project lacking them loads unchanged; `compose(base, None, [])` returns `base` with an all-`base` origin map.
- `save_project` continues to validate `notation` and validates `system_layer`/`selections` **only when present** — adding the wrapper fields must not retroactively reject pre-Phase-2 documents.

---

## 2. Ordered sub-steps

Data/plumbing first, UI last, integration capstone — mirroring how Phases 0/1 shipped.

### 2a — Schemas + store persistence (backend, no behaviour change)

- **New** `schema/selection.schema.json` — the verified-selection record (§1.2); op shapes as `$defs` reused by the system layer; `notes` are `RegionNote`s (`{bar, note}`) whose `note` `$ref`s the existing Note `$def`; `lane` is constrained to the **9-value `Lane` enum** (§1.2 — `hihat` covers closed+open) while op `instrument`/`from`/`to` take the 10-class instrument enum; `verified` is `const: true` (persisted selections are always verified, so the store can't hold one `compose` would skip); positions are pattern-validated rational strings.
- **New** `schema/system_layer.schema.json` (or `$defs` block) — §1.3.
- **Edit** `schema/project.schema.json` — add `params` (already written but undeclared), `selections` (optional array, default `[]`), `system_layer` (optional, nullable). `notation` `$ref` stays pure. Document all three as additive/optional (§1.6).
- **Edit** `store.py`: `save_selections` / `read_selections` (rewrite the field via `save_project`); `save_project` validates `system_layer`/`selections` **only when present**; `append_version(video_id, snapshot)` via the existing `append_event(video_id, "versions", …)`.
- **Tests** (`test_store.py`): selection round-trip; edit-a-selection (edges + `verified`) rewrites the field; delete removes exactly one; malformed `system_layer`/`selections` rejected; **legacy project with neither field still accepted** (back-compat guard); schema-validation unit tests.

### 2b — Layering engine + anchor/replay (backend, pure, no endpoints)

- **New** `anchor.py`: `parse_position`→`Fraction`; `canonical_position`; `lane_of`; `find_note(notes, position, instrument, tol)` (§1.4); `region_fingerprint(notation, lane, bar_start, bar_end)`; `replay_ops(base, ops, frozen_notes, tol) -> (new_notation, op_conflicts)`; `region_status(selection, base) -> "ok"|"drifted"|"missing"`.
- **New** `layering.py`: `compose(base, system_layer, selections) -> (notation, origin_map)`; `region_contains(selection, bar, lane)` (**lane × bar**); `verified_regions(selections)`. `compose` re-checks the §1.5 selection invariants itself (via `validate.check_selection_invariants`) rather than trusting its caller — it is the single authority, and the checks are pure Python.
- **Tests** (`test_layering.py`): identity/legacy `compose(base, None, [])`; user overwrites its region + origin map tags `user`; system op inside a verified region dropped, outside applied + tagged `system` (synthetic fixture); later `pass_id` replaces earlier; `replay_ops` match/miss/add with exact-position tolerance, incl. a **triplet-position** case that must anchor exactly; frozen `sustain_until`/`duration` preserved; `region_status` missing/drifted; composed output always passes `validate()` (incl. a `hihat_open` sustain case).

### 2c — Selection endpoints + layered read (backend HTTP)

- `POST /projects/{id}/selections` — server stamps `selection_id`/`origin`/`verified`/`created_at`/`base_fingerprint`. **Semantic validation on create**: reject `delete`/`move` of a nonexistent note, `add` at an occupied position, `move` into an occupied position, out-of-enum instrument (HTTP 422 + offending op). Frozen `notes` must pass `validate()`.
- `PUT /projects/{id}/selections/{sid}` — edit edges / notes / re-verify (field rewrite).
- `DELETE /projects/{id}/selections/{sid}` → `{deleted: sid}` (undo a user selection).
- `DELETE /projects/{id}/system` → clears `project["system_layer"]` (undo a system pass; never touches a user edit).
- `GET /projects/{id}/layers` → `{base, system_layer, selections, effective, origin_map}`. `origin_map` is keyed by **bar index as a string** on the wire (JSON has no integer keys); `compose` returns it as `dict[int, ...]` in-process.
- **Change** `GET /projects/{id}` to return `effective` as the notation the client renders/plays (Q1); `base` available under a distinct key (`base_notation`, documented in `project.schema.json` beside the other runtime-only response fields).
- **Change** `diagnose` to run on `effective` (Q1).
- **Change** `PUT /projects/{id}` to "base notation only" (the retune-accept path); user edits go through `/selections`. **The endpoint enforces this rather than documenting it**: once a project has any layer, PUT requires `{"layer": "base"}` in the body, else 409. Without that, a client that GETs a project and PUTs its `notation` back silently writes the composed layers into the base — the raw generator output is lost, every `base_fingerprint` goes stale (`region_status` → `drifted` forever), and a system pass baked into the base can no longer be reverted by `DELETE /system`. On the frontend the two paths are separate functions (`api.acceptRetune` sends the flag; `api.saveNotation`, used by the 2c-era score editor, does not and so fails loudly on a layered project until 2d routes those edits through `/selections`).
- **Write paths validate the *composition*, not just the records.** A frozen note can be schema-legal alone and illegal once merged (`sustain_until` at/behind `position` is the concrete case). Create/update compose before saving so the offending request is the one that 422s; `validate._check_selection_fields` holds a frozen note to the same `sustain_until` rule as a generated one; and the read paths turn a composition failure into a 409 naming the endpoint that clears it (never a 500) — because `compose` runs on every read, one bad record would otherwise take out `GET /projects/{id}`, `/layers` and `/diagnose` at once, leaving no reachable recovery. Removal deliberately skips the compose check, so a bad record is always deletable.
- **Change** the transcribe job to seed `system_layer: null`, `selections: []`.
- **Tests** (`test_selections_api.py`, FastAPI `TestClient`): POST→appears in layers, effective + origin_map reflect it, `GET /projects` now returns effective; invalid-op POST→422; DELETE reverts; synthetic on-disk `system_layer` shows in effective unless inside a verified region; `diagnose` reflects a correction; PUT replaces base, leaves selections intact.

### 2d — The selection interaction (frontend: drag a lane × bar range)

- **New** `selection.ts` — `SelectionController` owning draft `{lane, barStart, barEnd, notes, ops, verified}`; drafts stay client-side until verified.
- **Edit** `render.ts`: export `laneAtY(model, y)` (inverse of the lane y-layout); `drawSelectionOverlay(bv, selection)` — absolutely-positioned `%`-sized div in the stable `svgHost`, `pointer-events:none`, re-applied after each `drawBar` (same idiom as the `edit-col-highlight` re-tint).
- **Edit** `sync.ts`: add pointer `mousedown/mousemove/mouseup` alongside the existing `click` binding, gated on `editMode`, reusing px→viewBox conversion; emit `onLaneSelect/extend/commit`; `preventDefault` on `selectstart`/`dragstart` inside the drag.
- **Edit** `edit.ts`: `setupEditing` wires the `SelectionController`; `addNote`/`deleteNote`/`setInstrument` record **both an op (exact position) and the resulting note** into the active draft selection, so verify-time freezes `notes` and submits `ops`.
- **UI:** drag in edit mode paints a lane × bar rectangle; **the rectangle shows the whole bar(s) being locked** (Q2), both edges draggable; "mark verified" freezes `notes` + POSTs; single-bar weak-signal nudge.
- **Overlap is the controller's problem, not the backend's** (§1.5): a drag or edge-adjust that would make a verified region overlap an existing one *on the same lane* must be clamped or merged into the existing selection before submitting. The backend rejects overlap (HTTP 400 from `save_project`), so shipping this without the client-side guard turns an ordinary drag into an error toast. Different lanes sharing bars are fine, as is a range that merely touches another's edge.
- **Flam hook (notation-support deliverable, not built here):** structure the beat editor so a future "flam/grace" affordance drops in per stroke without a rewrite — see [`grace-notes-flams.md`](grace-notes-flams.md). Phase 2 only leaves the seam.
- **Tests:** drive via the `verify`/`run` skill (create → adjust edges → mark verified → observe overlay + POST payload incl. materialized `notes`).

### 2e — Delta-preview rendering + change-log UI (frontend)

- **Edit** `render.ts`: tint notes by the **`origin_map` from `GET /layers`** — three states: `system-changed`, `user-edited`, untouched `base`. No client-side compose.
- **New** `changelog.ts` (or fold into `edit.ts`): panel listing **user edits individually (permanent)** and **each system pass as one collapsible, unit-removable group**. Undo: system pass → `DELETE /system`; user selection → `DELETE /selections/{sid}`.
- **Edit** `style.css`: three origin colour tokens (reuse crayon palette vars).
- **Edit** `api.ts`: add `getLayers`, `saveSelection`, `updateSelection`, `deleteSelection`, `clearSystemPass`; point the renderer/playback data source at `effective`.
- **Reuse:** the tuning preview→Apply/Discard seam supplies the chrome; the delta renders as an in-place origin overlay, not a whole-score swap.
- **Tests:** hand-authored `system_layer` fixture → three colours render from `origin_map`; system group collapsible + unit-undoable; undo leaves user edits intact; empty-system case colours only user edits.

### 2f — Anchor-replay-on-regen + auditionable conflict review (backend + frontend)

- **Edit** `server.py` retune / retune-job: on accept, `base` is replaced by the new generation; **selections are re-locked and their ops replayed** via `anchor.replay_ops`, with **region-drift checked first** via `anchor.region_status`; the **system layer is cleared**. Both op-level and region-level conflicts returned in the preview payload (`{op_conflicts:[…], region_conflicts:[…]}`).
- **New** per-run choice `keep_edits ∈ {replay-all, replay-with-conflict-review, take-fresh-clean}`; default `replay-with-conflict-review` (Q4).
- **Frontend** retune UI: a mode chooser; a **conflict-review panel** listing unmapped ops **and** drifted/missing regions. **Each conflict is auditionable (Q4)** — clicking it seeks/loops the **drums-only stem** at that bar (reusing the shipped drums-only source + click-to-seek + loop-region primitives), so the user confirms by ear before keep-reposition / discard.
- **Tests** (`test_retune_replay.py`): ops replayed onto new base; a param shift that moves a note → op conflict (not silently dropped); a shift that reduces bar count below `bar_end` → region conflict; `take-fresh-clean` drops all user edits; system layer empty after re-gen. Conflict-audition wiring covered in 2g.

### 2g — Version snapshot write + integration capstone

- **Edit** `store.py` — `append_version` written on every commit (selection verified, base accepted, system pass cleared). Snapshot shape: `{params, notation, edits_applied, diagnostics, timestamp, parent_version}`; `params` reuses `PipelineParams.to_dict()`. **No reader in Phase 2.**
- **e2e** extend the `--run-slow` pattern (DI fakes): transcribe → create verified selection (materialized `notes`) → compose effective + origin map → retune → replay-with-conflict + region-drift → accept → assert layers + version log. Frontend e2e via `verify`/`run`: edit → drag selection → mark verified → three-colour delta → undo → audition a conflict.
- **Tests:** the capstone; assert `delete_project` still cleans `.versions.jsonl`.

---

## 3. Reuse ledger

- **JSONL helpers** (`append_event`/`read_events`/`event_log_path`) — reused **only** by the append-only version log. The user layer is a rewritable field, so it does *not* use them.
- **`PipelineParams.to_dict`/`from_dict`** — reused verbatim inside each version snapshot; no param change in Phase 2.
- **`Fraction`-based position parsing** — the anchor parses exact positions (as the harness does) but **anchors on the exact fraction**, not the harness's `*16` bin / collapse / clamp.
- **`edit.ts` affordances** (`addNote`/`deleteNote`/`setInstrument`; lifecycle; `setDirty`/`doSave`) — now record an op (exact position) **and** the resulting note into the active draft selection.
- **`sync.ts` click geometry** (px→viewBox; `editMode` gate; stable `svgHost`) — drag handlers hang off the same seam.
- **`render.ts` overlay idiom** (`instrumentYs`, `gridXs`, absolute-`%`-div; `edit-col-highlight`) — reused for the selection rect and the origin-map delta.
- **Retune preview seam** + **drums-only playback / click-to-seek / loop-region** — reused for the Apply/Discard commit loop and the auditionable conflict panel.
- **`validate()`** — now also gates composed effective notation and replayed output, plus per-layer schema validation.

---

## 4. Risks & open questions

Resolved by Q1–Q5 (baked into 2a–2f): storage of the user layer (field, not JSONL); closed-world materialized notes; lock granularity (lane × bar); anchoring (exact rational position); retune default (replay-with-conflict-review, auditionable); version-snapshot write (now).

Remaining / to watch:

1. **Anchor drift at meter changes.** Exact-position anchoring is stable across classification retunes; conflicts arise mainly when a param changes bar count/meter. Handled by region-drift detection + auditionable conflict review. Time-based anchoring stays the escape hatch (isolated in `anchor.py`).
2. **Tolerance tuning.** `find_note`'s `tol` must stay at or below half the local grid spacing. `DEFAULT_TOL` is `1/32` whole-note — exactly half a 16th's spacing, compared strictly — and nearest-wins disambiguates when two candidates are in range. On a mixed straight+triplet grid the safe tolerance is smaller (`1/12` and `1/16` sit only `1/48` apart); revisit if triplet detection (v2) lands.
3. **Composition-authority shift.** `GET`/`diagnose` now serve effective — the biggest behavioural change. Legacy projects (no selections) get `effective == base`, so their behaviour is unchanged.

---

## 5. Recommended merge order

1. **CSS selection-bug fix** (PR #28) — smallest, independent. *Still open.*
2. ~~**2a** schemas + store persistence~~ — **shipped** (PR #30). Settled data model, zero endpoint/UI change, fully unit-testable; dependency root.
3. ~~**2b** layering + anchor~~ — **shipped** (PR #31), together with a review pass that added the §1.5 conflict rules and the selection invariant checks.
4. **2c** endpoints + layered read — **next**.
5. **2d → 2e → 2f → 2g** frontend + integration.

The **grace-note/flam notation feature** ([`grace-notes-flams.md`](grace-notes-flams.md)) is independent of Phase 2 and can land on its own track; Phase 2 only leaves the 2d editing seam.
