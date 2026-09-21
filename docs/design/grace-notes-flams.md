# Design: Flam & ghost-note notation support

**Status:** **shipped** (notation-support only; 2026-09-20). Approved by owner 2026-09-19; ornament set revised per owner review 2026-09-20 (the second ornament is a **ghost note**, not an unslashed grace). Landed: schema (`grace` + `ghost`) + the two `anchor.py` attribute-enumerating call sites, VexFlow render, and the beat-editor flam/ghost toggles. **Detection remains out of scope** — deferred to v2 (see [`../v2-backlog.md`](../v2-backlog.md) → "Flam / grace-note detection").

## Goal

Make **flams** and **ghost notes** on snare + toms **representable, renderable, and manually editable** — so a drummer can notate them, have them draw correctly, and have them persist as a user edit. Nothing here *detects* these; the pipeline never emits one. They arrive only via manual editing (and, once Phase 2 ships, inside a verified selection).

- A **flam** = one hit ornamented by a soft grace note struck just before it (same drum), notated as a small slashed note tucked in front of the main notehead. It is **one hit** with flam notation — not an extra note in the sequence, and it consumes no metric time.
- A **ghost note** = a soft/muted hit, notated by wrapping the hit's own notehead in parentheses. No extra note; it restyles the existing hit.

Both apply to **snare and toms only** — the drums they're played on.

## Scope

**In:**
- **Schema:** an optional `grace` object (the flam) and an optional `ghost` boolean on a Note.
- **Render:** draw the flam via VexFlow's grace-note support (kept inside the bar even on the downbeat), and the ghost via parenthesized noteheads.
- **Manual edit:** per-stroke flam + ghost toggles in the beat editor, on snare/tom strokes only.

**Out:**
- Automatic detection from audio (v2 — needs sub-16th onset timing + per-hit velocity, alongside dynamics).
- Drags, ruffs, and multi-grace ornaments beyond a single grace note (revisit only if asked).
- Grace notes as standalone events not attached to a primary stroke.
- Unslashed grace notes as a distinct ornament (the second toggle is the ghost note instead).

## Schema change (the load-bearing part)

The events schema (`schema/events.schema.json`) has no grace-note concept today. This is a change to the **contract between both halves**, so it lands in one commit touching schema + producer expectations + consumer.

Approach — add an optional `grace` object to a Note:

```jsonc
{
  "instrument": "snare",
  "position": "1/4",
  "duration": "1/8",
  "grace": {                    // optional; absent = ordinary note
    "instrument": "snare",      // grace stroke instrument (usually same as primary)
    "slashed": true             // flam grace notes render with the conventional slash;
                                //  false for an unslashed grace note
  }
}
```

Notes on the shape:
- The grace note is **attached to** its primary note (not a separate array element), which matches how a flam is one musical gesture and keeps quantization/anchoring on the primary note's exact `position`. Phase 2's exact-position anchoring (see [`phase2-plan.md`](phase2-plan.md) §1.4) matches on `(position, instrument)`, so it locates grace-bearing notes unchanged, and `compose` deep-copies frozen notes so a `grace` survives composition untouched. **Two Phase 2 functions enumerate note attributes explicitly and will silently ignore `grace` until it is added to them** (see **Sequencing** step 1 below) — the same treatment `tuplet` and `sustain_until` already get.
- No independent timing for the grace: a flam's micro-offset is a *rendering* convention, not quantized data. This keeps the schema honest (we don't have sub-slot timing in v1) and avoids implying a precision the pipeline can't produce.
- `grace.instrument` allows a cross-voice grace later, but defaults to the primary's instrument.

`grace.instrument` is constrained by `$ref`ing the existing instrument enum in the schema itself — **not** by a hand-written check in `validate.py`, which is reserved for constraints JSON Schema cannot express (`_check_sustain_until`, the selection invariants in §1.5 of the Phase 2 plan).

## Render

**Flam.** VexFlow supports grace notes (`GraceNote` + `GraceNoteGroup`, with `slash` for the flam slash). `render.ts`'s `attachGrace` adds a `GraceNoteGroup` to a column's `StaveNote` when a hit carries `grace`; the grace takes its instrument's staff line + notehead (usually the same as the primary). Because a grace note draws to the *left* of its principal, a flam on the first (leftmost) note of a bar would spill past the left barline into the previous adjacent bar (bars render as separate touching SVGs). `insetLeadingFlam` nudges that leading note right just enough that the grace clears the bar's `contentX0` — applied in all three format paths (grid, readable-tiled, readable-tuplet) after positioning. So a downbeat flam stays "one hit with flam notation" inside its own bar.

**Ghost.** `attachGhost` wraps the ghost hit's notehead in parentheses via VexFlow's `Parenthesis` modifier (LEFT + RIGHT), added at the hit's *key index* within the (possibly chorded) `StaveNote` — so a ghost snare sharing a beat with a normal hi-hat parenthesizes only the snare.

Playhead/geometry capture (`BarView.notes`) is keyed on the primary note's `position` for both, so sync/highlight logic is unaffected.

## Manual editing

In the beat editor (`edit.ts` column/beat popover), a present **snare or tom** stroke gains two independent ornament toggles: **flam** (a slashed grace note) and **ghost** (a parenthesized soft hit). `flam` sets/clears the note's `grace`; `ghost` sets/clears `ghost: true`. Both are scoped to snare + toms — kick/hi-hat/cymbals get no ornament affordance.

Each ornament lives on the note itself — it is **not** a new note in the sequence and consumes no metric time — so there is no new op kind. It rides on the frozen selection notes captured at Verify (`toInput` freezes live notation). Because an ornament toggle records no op, `SelectionController` carries a `touched` flag so an ornament-only draft still counts as edited and can be Verified. This matches the backend `_restore_attributes`/`region_fingerprint` treatment (step 1). Pre-Phase-2 it flows through the existing edit→save path.

## Sequencing

Independent of Phase 2 and mergeable on its own track:

1. **Schema + the two attribute-enumerating call sites** — add optional `grace` (object) and
   `ghost` (boolean) to the Note `$def`, `$ref`ing the shared instrument enum for
   `grace.instrument`, and regenerate the frontend types (`json-schema-to-typescript` build
   step). Then update the two places in `anchor.py` that list note attributes by hand, or a
   user's flam/ghost will be lost/ignored with no error:
   - **`region_fingerprint`** hashes `[bar, position, instrument, duration, sustain_until,
     tuplet]`. `grace` **and** `ghost` must join that list. This is not hypothetical: the
     "Manual editing" section above notes that pre-Phase-2 an ornament toggle "flows through the
     existing edit→save path", and that path writes `project["notation"]` — the **base** layer.
     So a flam/ghost added to or removed from the base underneath a verified region would not
     register as drift, and `region_status` would keep reporting `ok`.
   - **`_restore_attributes`** re-applies only `duration` and `sustain_until` from the matching
     frozen note onto a replayed note, so a replayed `reclassify` on a flammed/ghosted note
     would drop the user's flam/ghost. It now carries `grace` and `ghost` too.

   No behaviour change until something emits/edits `grace`/`ghost`.
2. **Render** — draw `grace` via VexFlow grace notes (kept inside the bar by `insetLeadingFlam`)
   and `ghost` via parenthesized noteheads.
3. **Editor affordance** — per-stroke flam + ghost toggles on snare/tom strokes.

If Phase 2's beat-editor work (2d) is in flight, step 3 should reuse the 2d seam rather than duplicate popover wiring.

## Non-goals restated

This feature does **not** make the transcriber produce flams. A song with flams will still transcribe them as single hits until v2 detection lands; this feature only lets a human notate the flam afterward.
