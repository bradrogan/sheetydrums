# Design: Grace-note & flam notation support

**Status:** **shipped** (notation-support only; 2026-09-20). Approved by owner 2026-09-19. All three sequencing steps landed: schema + the two `anchor.py` attribute-enumerating call sites, VexFlow render, and the beat-editor flam toggle. **Detection remains out of scope** — deferred to v2 (see [`../v2-backlog.md`](../v2-backlog.md) → "Flam / grace-note detection").

## Goal

Make flams (and grace notes generally) **representable, renderable, and manually editable** — so a drummer can notate a flam they hear, have it draw correctly, and have it persist as a user edit. Nothing in this feature *detects* flams automatically; the pipeline never emits one. They arrive only via manual editing (and, once Phase 2 ships, inside a verified selection).

A flam = a soft **grace note** struck just before a primary stroke (typically on the same drum), notated as a small note tucked in front of the main notehead.

## Scope

**In:**
- **Schema:** a way to attach a grace note to a primary note.
- **Render:** draw the grace note via VexFlow's native grace-note support.
- **Manual edit:** a per-stroke affordance in the beat editor to add/remove a flam (and, generally, a grace note) on a hit.

**Out:**
- Automatic detection from audio (v2 — needs sub-16th onset timing + per-hit velocity, alongside ghost notes/dynamics).
- Drags, ruffs, and multi-grace ornaments beyond a single grace note (revisit only if asked).
- Grace notes as standalone events not attached to a primary stroke.

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

VexFlow supports grace notes (`GraceNote` + `GraceNoteGroup`, with `slash` for the flam slash). The renderer (`render.ts`, `drawBar`) attaches a `GraceNoteGroup` to the `StaveNote` when a note carries `grace`. Percussion-staff considerations: the grace note takes the primary's staff line (or its own if `grace.instrument` differs). Playhead/geometry capture (`BarView.notes`) is keyed on the primary note's `position`, so the sync/highlight logic is unaffected.

## Manual editing

In the beat editor (`edit.ts` column/beat popover), a present **snare or tom** stroke gains a two-button ornament segment: **flam** (slashed grace) and **grace** (unslashed). Clicking the active one removes it. These are mutually exclusive states of the note's `grace` (`slashed: true` vs `false`, or absent). The ornament is scoped to snare + toms — a grace ornaments a primary stroke and is only played on those drums here; kick/hi-hat/cymbals get no ornament affordance.

The grace lives on the note itself — it is **not** a new note in the sequence and consumes no metric time — so there is no new op kind. It rides on the frozen selection notes captured at Verify (`toInput` freezes live notation). Because a grace toggle records no op, `SelectionController` carries a `touched` flag so a grace-only draft still counts as edited and can be Verified. This matches the backend `_restore_attributes`/`region_fingerprint` treatment (step 1). Pre-Phase-2 it flows through the existing edit→save path.

## Sequencing

Independent of Phase 2 and mergeable on its own track:

1. **Schema + the two attribute-enumerating call sites** — add optional `grace` to the Note
   `$def`, `$ref`ing the instrument enum for `grace.instrument`, and regenerate the frontend
   types (`json-schema-to-typescript` build step). Then update the two places in `anchor.py`
   that list note attributes by hand, or a user's flam will be lost/ignored with no error:
   - **`region_fingerprint`** hashes `[bar, position, instrument, duration, sustain_until,
     tuplet]`. `grace` must join that list. This is not hypothetical: the "Manual editing"
     section above notes that pre-Phase-2 a flam toggle "flows through the existing edit→save
     path", and that path writes `project["notation"]` — the **base** layer. So a flam added to
     or removed from the base underneath a verified region would not register as drift, and
     `region_status` would keep reporting `ok`.
   - **`_restore_attributes`** re-applies only `duration` and `sustain_until` from the matching
     frozen note onto a replayed note, so a replayed `reclassify` on a grace-bearing note would
     drop the user's flam.

   No behaviour change until something emits/edits `grace`.
2. **Render** — draw `grace` via VexFlow grace notes.
3. **Editor affordance** — the per-stroke flam toggle.

If Phase 2's beat-editor work (2d) is in flight, step 3 should reuse the 2d seam rather than duplicate popover wiring.

## Non-goals restated

This feature does **not** make the transcriber produce flams. A song with flams will still transcribe them as single hits until v2 detection lands; this feature only lets a human notate the flam afterward.
