# Design / TODO: score editing UX

**Status:** planned. Captures known problems with manual note editing + the fix
direction. Related: `docs/design/ai-tuning-loop.md` (the bar/note *selection*
model is shared with the correct-by-example "fix the rest" flow, Phase 2).

## Problem (reported 2026-08)

Editing the score is frustrating:
- You can't click a hi-hat note to remove it.
- Changing a hi-hat to open is awkward — you must click in exactly the right spot.
- Selecting bars and expanding/shrinking a selection is difficult.

## Root cause (in `frontend/src/edit.ts`)

`sync.onEditClick` converts a click to **`(instrument, sixteenth)`**, where
`instrument = nearestInstrument(y)` (closest staff line) and `sixteenth` is the
snapped slot. It only *selects* an existing note when **both** match exactly
(`bar.notes.find(...)`); otherwise it **adds** a new note.

- `hihat_open` and `hihat_closed` share one staff line (`INSTRUMENT_LINE` = −0.5
  for both), so `nearestInstrument` can't distinguish them and returns
  `hihat_open` (first in `SCHEMA_CLASSES`). Clicking an existing `hihat_closed`
  therefore fails to match → **adds a stray open hi-hat** instead of selecting
  the note you clicked.
- Requiring an exact lane + slot makes selection pixel-precise; a few px off
  creates a new note on a neighbouring lane.
- There is no bar/span selection model yet.

## Fix direction

1. **Select-by-proximity (core fix).** On click, find the nearest *rendered
   notehead* in 2D (slot→x via `barView.gridXs`, instrument→y via
   `model.instrumentYs`) within a hit radius (~half a cell). If one is near →
   open the editor on *that* note regardless of exact lane. Only add a new note
   when the click is in empty space. This fixes remove + reclassify directly and
   removes the pixel-hunting.
2. **Hover affordance + bigger targets.** Highlight the notehead under the cursor
   before click; pointer cursor over notes vs crosshair over empty; padded hit area.
3. **Fast actions on a selected note.** `Delete`/right-click to remove; `o`/`x`
   to toggle open/closed hi-hat (sidesteps the shared-line ambiguity); keep the
   popover reclassify as the discoverable path.
4. **Bar/span selection model (Phase 2, shared with the tuning loop).**
   Lane-aware drag with draggable edge handles; click a bar number to select the
   whole bar; shift-click to extend. This is the "verified selection" input the
   correct-by-example flow consumes — build it there.
5. *(Optional)* **Palette mode** — pick the active instrument from a palette
   rather than inferring it from click-y; clicks add/remove that instrument.
   Sidesteps y-ambiguity but is modal.

## LLM-editing relationship

Correct-by-example reduces *how many* manual edits are needed (fix a few →
generalise), but does **not** remove the need to make individual corrections
reliably — so the hit-testing fix (1–3) is still required. Item 4 (selection) is
the same surface the LLM flow needs, so it belongs to Phase 2.

## Recommendation

Do **1 + 2 + 3** as a near-term editing fix (small, high-impact). Build **4** as
part of Phase 2's verified-selection work. **5** only if 1–3 prove insufficient.
