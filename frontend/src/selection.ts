// The draft verified-selection a user builds in edit mode: one instrument LANE
// across a range of whole bars (Q2 — the smallest lockable unit is lane × bar).
// Corrections made while a draft is active are captured as ops; on "Verify" the
// current effective notes for that lane in the region are frozen as the
// closed-world ground truth and POSTed to /selections. Pure state + geometry —
// no DOM, no fetch (edit.ts drives those). See docs/design/phase2-plan.md §2d.
import { laneOf, type LaneKey, type SchemaDrumClass } from './render';
import type { Notation, Op, RegionNote, Selection, SelectionInput } from './api';

export interface DraftRegion {
  lane: LaneKey;
  barStart: number;
  barEnd: number;
}

export class SelectionController {
  private lane: LaneKey | null = null;
  private anchorBar = 0;
  private curBar = 0;
  private ops: Op[] = [];
  /** Set when the draft is editing an already-persisted selection (update vs create). */
  editingId: string | null = null;

  get active(): boolean {
    return this.lane !== null;
  }

  /** Whether any edit ops have been captured into the current draft. */
  get hasEdits(): boolean {
    return this.ops.length > 0;
  }

  region(): DraftRegion | null {
    if (this.lane === null) return null;
    return {
      lane: this.lane,
      barStart: Math.min(this.anchorBar, this.curBar),
      barEnd: Math.max(this.anchorBar, this.curBar),
    };
  }

  /** Start a fresh draft anchored at `bar` on `lane`. */
  begin(lane: LaneKey, bar: number): void {
    this.lane = lane;
    this.anchorBar = bar;
    this.curBar = bar;
    this.ops = [];
    this.editingId = null;
  }

  /** Extend the draft's far edge to `bar` (drag). No-op when idle. */
  extendTo(bar: number): void {
    if (this.lane !== null) this.curBar = bar;
  }

  /** Load a persisted selection into the draft for further editing. */
  edit(sel: Selection): void {
    this.lane = sel.lane as LaneKey;
    this.anchorBar = sel.bar_start;
    this.curBar = sel.bar_end;
    this.ops = [...sel.ops];
    this.editingId = sel.selection_id;
  }

  clear(): void {
    this.lane = null;
    this.ops = [];
    this.editingId = null;
  }

  /** True if (bar, instrument) falls inside the draft's lane × bar region. */
  contains(bar: number, instrument: SchemaDrumClass): boolean {
    const r = this.region();
    return r !== null && r.lane === laneOf(instrument) && r.barStart <= bar && bar <= r.barEnd;
  }

  /** Record an edit op, but only if it lands inside the draft region + lane. */
  recordOp(op: Op): void {
    const r = this.region();
    if (r === null || op.bar < r.barStart || op.bar > r.barEnd) return;
    const lanes = op.kind === 'reclassify' ? [laneOf(op.from), laneOf(op.to)] : [laneOf(op.instrument)];
    if (!lanes.includes(r.lane)) return;
    this.ops.push(op);
  }

  /** Freeze the current effective notes for the region's lane into a POST body:
   * every note in the lane across the region (closed world) plus the captured ops. */
  toInput(notation: Notation): SelectionInput {
    const r = this.region();
    if (r === null) throw new Error('No active selection.');
    const notes: RegionNote[] = [];
    for (const bar of notation.bars) {
      if (bar.index < r.barStart || bar.index > r.barEnd) continue;
      for (const note of bar.notes) {
        if (laneOf(note.instrument) === r.lane) notes.push({ bar: bar.index, note });
      }
    }
    return { lane: r.lane, bar_start: r.barStart, bar_end: r.barEnd, notes, ops: this.ops };
  }
}
