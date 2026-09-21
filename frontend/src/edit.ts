// Manual editing of the transcription. In edit mode, clicking the score snaps to
// the nearest 16th COLUMN (x only — a wide target, no vertical precision) and
// opens a "beat" editor listing every hit at that slot: each is removable, the
// hi-hat carries an open/closed toggle, and any absent instrument can be added.
// Editing by time-column (not by clicked pitch) means simultaneous hits (e.g.
// crash + hi-hat on the same beat) and the open/closed distinction are both
// unambiguous — the old pitch-from-y hit-test couldn't express either. Edits
// mutate the in-memory notation and re-render the affected bar; Save persists
// via PUT /projects/{id}. See docs/design/edit-ux.md.
//
// The notation handed in is the project's *composed effective* layer, while PUT
// /projects/{id} writes the BASE layer — so Save is only correct while a project
// has no verified selections and no system pass, which is every project until
// the Phase 2d editor lands. On a layered project the backend 409s rather than
// flattening the layers, and the message surfaces through doSave's alert. Phase
// 2d's job is to route these edits to /projects/{id}/selections instead.
import {
  drawBar,
  parsePosition,
  formatPosition,
  laneOf,
  laneAtY,
  laneYOf,
  drawSelectionBand,
  BAR_SVG_WIDTH,
  type RenderModel,
  type BarView,
  type SchemaDrumClass,
  type LaneKey,
} from './render';
import type { SyncController } from './sync';
import { SelectionController } from './selection';
import { diffNotation, type NotationDiff } from './tuning';
import * as api from './api';
import type { Project, Notation, Op } from './api';

/** Handle returned by setupEditing so the tuning panel can drive the re-tune
 * preview *through* the edit layer (verified selections overlaid + delta),
 * rather than a detached renderScore that wipes the edit overlays. */
export interface EditHandle {
  /** Render `previewBase` composed with the current verified selections, in the
   * grid layout with bands + the changed-bars delta; returns the diff summary. */
  preview: (previewBase: Notation) => NotationDiff;
  /** Revert the preview back to the committed effective, in place. */
  clearPreview: () => void;
}

type NoteObj = Notation['bars'][number]['notes'][number];
type BarObj = Notation['bars'][number];

const SUBDIV = 16; // sixteenth-note grid
const SNAP_X_PX = 18; // click within this many px of a column snaps to it

// Shared so navigation (e.g. the Back button) can prompt about unsaved edits
// before leaving. `leave()` resolves true when it's safe to proceed (not
// editing, or the user saved/discarded), false if they cancelled.
export const editSession: { dirty: boolean; leave: () => Promise<boolean> } = {
  dirty: false, // in edit mode with unsaved changes — for synchronous beforeunload checks
  leave: async () => true,
};

/** Modal asking what to do with an unverified selection when leaving edit mode. */
function confirmLeaveSelection(): Promise<'verify' | 'discard' | 'cancel'> {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'edit-modal-overlay';
    const box = document.createElement('div');
    box.className = 'edit-modal';
    const msg = document.createElement('p');
    msg.textContent = 'You have an unverified selection. Verify it before leaving edit mode?';
    box.appendChild(msg);
    const row = document.createElement('div');
    row.className = 'edit-modal-row';
    const mk = (label: string, choice: 'verify' | 'discard' | 'cancel', cls: string): void => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = label;
      if (cls) b.className = cls;
      b.onclick = () => {
        overlay.remove();
        resolve(choice);
      };
      row.appendChild(b);
    };
    mk('Verify', 'verify', 'primary');
    mk('Discard', 'discard', 'danger');
    mk('Cancel', 'cancel', '');
    box.appendChild(row);
    overlay.appendChild(box);
    overlay.addEventListener('mousedown', (e) => {
      if (e.target === overlay) {
        overlay.remove();
        resolve('cancel');
      }
    });
    document.body.appendChild(overlay);
  });
}

export interface EditContext {
  project: Project;
  notation: Notation;
  model: RenderModel;
  sync: SyncController;
  editToggle: HTMLButtonElement;
  saveBtn: HTMLButtonElement;
  scoreEl: HTMLElement;
  /** Persisted verified selections (the user layer), drawn as bands. Mutated in
   * place as selections are committed, so the score never needs a full reload. */
  selections: api.Selection[];
  /** The raw base notation (pre-composition). Removing a selection reverts its
   * lane × bar region to this. */
  base: Notation;
  /** Left-panel container the editing controls (verified-sections list) render
   * into while editing. */
  editPanel: HTMLElement;
}

export function setupEditing(ctx: EditContext): EditHandle {
  const { project, notation, model, sync, editToggle, saveBtn, scoreEl, base, editPanel } = ctx;
  const videoId = project.video_id;
  const numerator = notation.time_signature.numerator;
  const maxSixteenth = Math.round((numerator / notation.time_signature.denominator) * SUBDIV);
  const persisted: api.Selection[] = ctx.selections;
  const sel = new SelectionController();
  // Baseline to revert in-memory edits to when leaving without verifying.
  let saved: Notation = structuredClone(notation);

  // Edits are committed per verified selection (POST /selections), not via a
  // blanket notation PUT, so the old global Save button stays hidden — the
  // selection toolbar's Verify is the commit.
  saveBtn.hidden = true;

  const rerenderAll = (gridMode: boolean): void => {
    for (const bv of model.bars) {
      const bar = notation.bars.find((b) => b.index === bv.index);
      // Grid (edit) mode gets delta coloring from the verified selections;
      // view mode stays uncoloured (provenance is irrelevant during playback).
      if (bar) drawBar(bv, bar, gridMode, gridMode ? persisted : undefined);
    }
    if (gridMode) redrawBands();
  };

  // --- Selection bands: persisted (solid) + the active draft (dashed) ---
  const bandEls: HTMLElement[] = [];
  const redrawBands = (): void => {
    for (const b of bandEls) b.remove();
    bandEls.length = 0;
    const band = (lane: LaneKey, start: number, end: number, cls: string): void => {
      const y = laneYOf(model, lane);
      for (let idx = start; idx <= end; idx++) {
        const bv = model.bars.find((b) => b.index === idx);
        if (bv) bandEls.push(drawSelectionBand(bv, y, cls));
      }
    };
    for (const s of persisted) band(s.lane as LaneKey, s.bar_start, s.bar_end, 'selection-band verified');
    const r = sel.region();
    if (r) band(r.lane, r.barStart, r.barEnd, 'selection-band draft');
  };

  // --- Selection toolbar (Verify / Cancel / Delete) --------------------
  let toolbar: HTMLElement | null = null;
  const hideToolbar = (): void => { toolbar?.remove(); toolbar = null; };
  const showToolbar = (): void => {
    const r = sel.region();
    if (!r) return void hideToolbar();
    if (!toolbar) {
      toolbar = document.createElement('div');
      toolbar.className = 'selection-toolbar';
      document.body.appendChild(toolbar);
    }
    toolbar.innerHTML = '';
    const bars = r.barStart === r.barEnd ? `bar ${r.barStart}` : `bars ${r.barStart}–${r.barEnd}`;
    toolbar.appendChild(el('span', 'selection-toolbar-label', `${laneLabel(r.lane)} · ${bars}`));
    if (r.barStart === r.barEnd) {
      toolbar.appendChild(el('span', 'selection-toolbar-nudge', 'tip: drag across all the bars you checked'));
    }
    toolbar.appendChild(mkBtn('Verify', 'primary', () => void commit()));
    toolbar.appendChild(mkBtn('Cancel', '', () => { sel.clear(); hideToolbar(); redrawBands(); }));
  };

  // --- Left editing panel: verified-sections list + undo -----------------

  // Removing a selection reverts its lane × bar region to the base generation.
  const revertRegionToBase = (lane: LaneKey, start: number, end: number): void => {
    for (const bar of notation.bars) {
      if (bar.index < start || bar.index > end) continue;
      const baseBar = base.bars.find((b) => b.index === bar.index);
      bar.notes = bar.notes.filter((n) => laneOf(n.instrument) !== lane);
      for (const n of baseBar?.notes ?? []) {
        if (laneOf(n.instrument) === lane) bar.notes.push(structuredClone(n));
      }
      const bv = model.bars.find((b) => b.index === bar.index);
      if (bv) drawBar(bv, bar, true, persisted);
    }
  };

  const removeSelection = async (target: api.Selection): Promise<void> => {
    try {
      await api.deleteSelection(videoId, target.selection_id);
    } catch (err) {
      alert(`Couldn't remove selection: ${err instanceof Error ? err.message : String(err)}`);
      return;
    }
    const i = persisted.findIndex((s) => s.selection_id === target.selection_id);
    if (i >= 0) persisted.splice(i, 1);
    revertRegionToBase(target.lane as LaneKey, target.bar_start, target.bar_end);
    saved = structuredClone(notation); // the reverted state is the new baseline
    redrawBands();
    renderEditPanel();
  };

  // The left-panel editing hub content (edit mode only): a short hint + the
  // verified-sections list, each with a Remove (undo) button. Cleared and
  // hidden in view mode so the doodle can show instead.
  const renderEditPanel = (): void => {
    editPanel.innerHTML = '';
    if (!sync.editMode) {
      editPanel.hidden = true;
      return;
    }
    editPanel.hidden = false;
    editPanel.appendChild(el('div', 'edit-panel-head', 'Editing'));
    editPanel.appendChild(el('div', 'edit-panel-hint muted',
      'Drag across a lane × bars (or tap a beat), correct it, then Verify.'));
    editPanel.appendChild(el('div', 'changelog-head', `Verified sections (${persisted.length})`));
    if (persisted.length === 0) {
      editPanel.appendChild(el('div', 'changelog-empty muted', 'None yet.'));
      return;
    }
    const list = el('div', 'changelog-list');
    for (const s of [...persisted].sort((a, b) => a.bar_start - b.bar_start || a.lane.localeCompare(b.lane))) {
      const row = el('div', 'changelog-row');
      const bars = s.bar_start === s.bar_end ? `bar ${s.bar_start}` : `bars ${s.bar_start}–${s.bar_end}`;
      row.appendChild(el('span', 'changelog-label', `${laneLabel(s.lane as LaneKey)} · ${bars}`));
      row.appendChild(mkBtn('Remove', 'danger', () => void removeSelection(s)));
      list.appendChild(row);
    }
    editPanel.appendChild(list);
  };

  const commit = async (): Promise<boolean> => {
    const input = sel.toInput(notation);
    // Editing an already-verified region updates it in place (PUT); a brand-new
    // region creates one (POST). Creating a second selection over the same
    // lane×bar is rejected by the backend's no-overlap invariant — which is what
    // the update path exists to avoid.
    const editingId = sel.editingId;
    let saved_sel: api.Selection;
    try {
      saved_sel = editingId
        ? await api.updateSelection(videoId, editingId, input)
        : await api.createSelection(videoId, input);
    } catch (err) {
      alert(`Couldn't save selection: ${err instanceof Error ? err.message : String(err)}`);
      return false;
    }
    // Update in place — no re-fetch, no re-render, no scroll jump. The score
    // already shows the edited notes (edits mutate `notation`, which the server
    // just froze verbatim), so committing only flips the draft band (dashed) to
    // a verified band (solid) where the user is already looking.
    const existingIdx = editingId
      ? persisted.findIndex((s) => s.selection_id === editingId)
      : -1;
    if (existingIdx >= 0) persisted[existingIdx] = saved_sel;
    else persisted.push(saved_sel);
    sel.clear();
    hideToolbar();
    editSession.dirty = false;
    // Advance the discard baseline: a later Discard reverts only edits made
    // after this commit, not the committed ones (which are already persisted).
    saved = structuredClone(notation);
    redrawBands();
    renderEditPanel();
    return true;
  };

  // Begin a fresh draft. Any uncommitted edits in the current draft were never
  // verified, so revert them to the last-commit/enter baseline before starting a
  // new one — otherwise they linger in the in-memory notation and get baked into
  // the next commit's frozen notes (and into the discard baseline).
  const startDraft = (lane: LaneKey, bar: number): void => {
    if (sel.hasEdits) {
      notation.bars = structuredClone(saved).bars;
      sel.begin(lane, bar);
      rerenderAll(true);
    } else {
      sel.begin(lane, bar);
      redrawBands();
    }
  };

  // The persisted selection that already owns (lane, bar), if any.
  const coveringSelection = (lane: LaneKey, bar: number): api.Selection | undefined =>
    persisted.find((s) => s.lane === lane && s.bar_start <= bar && bar <= s.bar_end);

  // Load an already-verified selection into the draft for further editing, so a
  // commit updates it (PUT) rather than creating an overlapping second selection.
  // Mirrors startDraft's discard-of-uncommitted-edits before switching.
  const editExisting = (existing: api.Selection): void => {
    if (sel.hasEdits) {
      notation.bars = structuredClone(saved).bars;
      sel.edit(existing);
      rerenderAll(true);
    } else {
      sel.edit(existing);
      redrawBands();
    }
  };

  // Enter the right draft for a click/tap at (lane, bar): edit the covering
  // verified selection if there is one, else start a fresh draft.
  const enterDraftAt = (lane: LaneKey, bar: number): void => {
    const existing = coveringSelection(lane, bar);
    if (existing) editExisting(existing);
    else startDraft(lane, bar);
  };

  const enterEditMode = (): void => {
    sync.editMode = true;
    saved = structuredClone(notation);
    editToggle.textContent = 'Done';
    editToggle.classList.add('active');
    scoreEl.classList.add('editing');
    // Gate the Tune panel to edit mode: its preview + changes are only legible
    // in the grid layout, so the toolbar button is shown only while editing (CSS
    // keys off this class).
    document.body.classList.add('editing-mode');
    rerenderAll(true); // fixed 16th grid so notes stay put
    renderEditPanel();
    sync.refresh(); // reposition the retained playhead after the relayout
  };

  const exitEditMode = (): void => {
    sync.editMode = false;
    editToggle.textContent = 'Edit';
    editToggle.classList.remove('active');
    scoreEl.classList.remove('editing');
    // Leaving edit mode hides the Tune button (CSS) — also close the panel if
    // it's open so it doesn't linger with no way to dismiss it.
    document.body.classList.remove('editing-mode');
    document.getElementById('tune-toggle')?.classList.remove('active');
    const tuningPanel = document.getElementById('tuning-panel');
    if (tuningPanel) tuningPanel.hidden = true;
    editSession.dirty = false;
    sel.clear();
    hideToolbar();
    renderEditPanel(); // editMode is now false → removes the panel
    closePopover();
    rerenderAll(false); // back to proportional view
    sync.refresh();
  };

  const leaveEditMode = async (): Promise<boolean> => {
    if (!sync.editMode) return true;
    if (sel.active && sel.hasEdits) {
      const choice = await confirmLeaveSelection();
      if (choice === 'cancel') return false;
      if (choice === 'verify') {
        if (!(await commit())) return false; // commit failed → stay in edit mode
      } else {
        // discard: revert the in-memory edits to the pre-edit baseline
        notation.bars = structuredClone(saved).bars;
      }
    }
    exitEditMode();
    return true;
  };

  // Let navigation (Back) route through the same unverified-edits guard.
  editSession.leave = leaveEditMode;
  editSession.dirty = false;
  const markDirty = (): void => { editSession.dirty = sel.hasEdits; };

  // Start clean each time a project opens.
  sync.editMode = false;
  editToggle.textContent = 'Edit';
  editToggle.classList.remove('active');
  closePopover();

  editToggle.onclick = () => {
    if (!sync.editMode) enterEditMode();
    else void leaveEditMode();
  };

  // Drag on the score → paint a lane × bar selection; the lane comes from the
  // drag's start y, the bars from the span the drag covers.
  sync.onLaneDragStart = (barView, y) => {
    closePopover();
    startDraft(laneAtY(model, y), barView.index);
  };
  sync.onLaneDragTo = (barView) => {
    sel.extendTo(barView.index);
    redrawBands();
  };
  sync.onLaneDragEnd = () => showToolbar();

  // Tap a column → edit that slot. With no draft (or a tap outside the current
  // one), begin a single-bar draft on the tapped lane (Q2 — a lone edit still
  // lives in a lane × bar selection), then open the lane-scoped beat editor.
  sync.onEditClick = (barView, x, y) => {
    const bar = notation.bars.find((b) => b.index === barView.index);
    if (!bar) return;
    const lane = laneAtY(model, y);
    const r = sel.region();
    const inDraft =
      r !== null && r.lane === lane && r.barStart <= barView.index && barView.index <= r.barEnd;
    if (!inDraft) {
      enterDraftAt(lane, barView.index); // edits the covering verified selection if there is one
      showToolbar();
    }
    const active = sel.region();
    if (!active) return;
    const sixteenth = resolveSixteenth(barView, x, maxSixteenth);
    openColumnPopover({
      bar, barView, sixteenth, maxSixteenth, numerator,
      lane: active.lane,
      regions: persisted,
      recordOp: (op) => { sel.recordOp(op); markDirty(); },
      markEdited: () => { sel.markEdited(); markDirty(); },
      afterRedraw: redrawBands,
    });
  };

  // --- Re-tune preview through the edit layer (Q7 #2) --------------------
  const flagChangedBars = (changed: number[]): void => {
    for (const bv of model.bars) bv.row.classList.toggle('bar-changed', changed.includes(bv.index));
  };

  const preview = (previewBase: Notation): NotationDiff => {
    // Compose the re-tuned base with the user's verified selections so their
    // corrections stay overlaid (not "overwritten") in the preview.
    const previewEff = applySelections(previewBase, persisted);
    const diff = diffNotation(notation, previewEff);
    // Reuse the current bars only when the re-tune didn't change the bar layout
    // (the tuning knobs never do). If it did, skip the in-layer render — the diff
    // summary still shows; nothing breaks.
    const sameBars =
      previewEff.bars.length === model.bars.length &&
      previewEff.bars.every((b, i) => b.index === model.bars[i]!.index);
    if (sameBars) {
      for (const bv of model.bars) {
        const bar = previewEff.bars.find((b) => b.index === bv.index);
        if (bar) drawBar(bv, bar, true, persisted);
      }
      redrawBands();
      flagChangedBars(diff.changedBars);
    }
    return diff;
  };

  const clearPreview = (): void => {
    flagChangedBars([]);
    rerenderAll(true); // redraw the committed effective + bands
  };

  return { preview, clearPreview };
}

/** The user-layer step of `compose`, client-side (Phase 2 has no system layer):
 * overwrite each verified selection's lane × bar region in `base` with its
 * frozen notes. Used to keep verifications visible in a re-tune preview. */
function applySelections(base: Notation, selections: api.Selection[]): Notation {
  const result = structuredClone(base);
  for (const sel of selections) {
    if (!sel.verified) continue; // only the verified user layer wins (matches backend compose)
    const lane = sel.lane as LaneKey;
    const byBar = new Map<number, NoteObj[]>();
    for (const rn of sel.notes) {
      const list = byBar.get(rn.bar) ?? [];
      list.push(rn.note);
      byBar.set(rn.bar, list);
    }
    for (let b = sel.bar_start; b <= sel.bar_end; b++) {
      const bar = result.bars.find((x) => x.index === b);
      if (!bar) continue;
      bar.notes = bar.notes.filter((n) => laneOf(n.instrument) !== lane);
      for (const note of byBar.get(b) ?? []) bar.notes.push(structuredClone(note));
    }
  }
  return result;
}

const LANE_LABELS: Record<LaneKey, string> = {
  kick: 'Kick', snare: 'Snare', hihat: 'Hi-hat', hihat_chick: 'Hi-hat (foot)',
  ride: 'Ride', crash: 'Crash', tom_high: 'High tom', tom_mid: 'Mid tom', tom_low: 'Low tom',
};
function laneLabel(lane: LaneKey): string {
  return LANE_LABELS[lane];
}

function el(tag: string, className: string, text = ''): HTMLElement {
  const e = document.createElement(tag);
  e.className = className;
  e.textContent = text;
  return e;
}

function mkBtn(label: string, cls: string, onClick: () => void): HTMLButtonElement {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = label;
  if (cls) b.className = cls;
  b.onclick = onClick;
  return b;
}

// === Geometry helpers ===

/** Snap x to a 16th slot. In grid mode, use the exact rendered slot x's. */
function resolveSixteenth(barView: BarView, x: number, maxSixteenth: number): number {
  if (barView.gridXs && barView.gridXs.length > 0) {
    // A note renders at the LEFT gridline of its cell, so map the click to the
    // cell it falls inside (floor) — nearest-gridline would round a right-half
    // click up to the next column.
    const xs = barView.gridXs;
    let slot = 0;
    for (let i = 0; i < maxSixteenth; i++) {
      if (x >= xs[i]!) slot = i;
      else break;
    }
    return slot;
  }
  // Fallback (proportional view): snap to nearest existing column, else linear.
  let nearest: BarView['notes'][number] | null = null;
  for (const n of barView.notes) {
    if (!nearest || Math.abs(n.xPx - x) < Math.abs(nearest.xPx - x)) nearest = n;
  }
  if (nearest && Math.abs(nearest.xPx - x) <= SNAP_X_PX) {
    return Math.round(nearest.position * SUBDIV);
  }
  const span = barView.contentX1 - barView.contentX0;
  const f = span > 0 ? (x - barView.contentX0) / span : 0;
  return Math.max(0, Math.min(maxSixteenth - 1, Math.round(f * maxSixteenth)));
}

/** Screen-space rect of a 16th column (its x-span across the full staff height),
 * used to place the editor beside the column so it never covers it. */
function columnRect(
  barView: BarView,
  slot: number,
  maxSixteenth: number,
): { left: number; right: number; top: number; bottom: number } {
  const rect = barView.svgHost.getBoundingClientRect();
  const xs = barView.gridXs;
  const vb0 = xs && xs[slot] !== undefined ? xs[slot]! : (slot / maxSixteenth) * BAR_SVG_WIDTH;
  const vb1 = xs && xs[slot + 1] !== undefined ? xs[slot + 1]! : vb0 + BAR_SVG_WIDTH / maxSixteenth;
  const sx = (x: number): number => rect.left + (x / BAR_SVG_WIDTH) * rect.width;
  return { left: sx(vb0), right: sx(vb1), top: rect.top, bottom: rect.bottom };
}

function pct(vbX: number): number {
  return (vbX / BAR_SVG_WIDTH) * 100;
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.max(lo, Math.min(hi, v));
}

// === Column (beat) editor popover =======================================

// Logical lanes shown in the beat editor, top-to-bottom in staff order. The
// hi-hat lane covers both open + closed variants (one row, with an o/x toggle).
interface Lane {
  key: string;
  label: string;
  add: SchemaDrumClass;
  hihat?: boolean;
}
const LANES: Lane[] = [
  { key: 'crash', label: 'Crash', add: 'crash' },
  { key: 'hihat', label: 'Hi-hat', add: 'hihat_closed', hihat: true },
  { key: 'ride', label: 'Ride', add: 'ride' },
  { key: 'tom_high', label: 'High tom', add: 'tom_high' },
  { key: 'tom_mid', label: 'Mid tom', add: 'tom_mid' },
  { key: 'snare', label: 'Snare', add: 'snare' },
  { key: 'tom_low', label: 'Low tom', add: 'tom_low' },
  { key: 'kick', label: 'Kick', add: 'kick' },
  { key: 'hihat_chick', label: 'Hi-hat (foot)', add: 'hihat_chick' },
];

/** Musical label for a 16th slot, e.g. "Beat 2 e". */
function beatLabel(slot: number, maxSixteenth: number, numerator: number): string {
  const spb = Math.max(1, Math.round(maxSixteenth / Math.max(1, numerator)));
  const beat = Math.floor(slot / spb) + 1;
  const sub = slot % spb;
  const names4 = ['', ' e', ' &', ' a'];
  const subLabel = spb === 4 ? (names4[sub] ?? '') : sub === 0 ? '' : ` +${sub}/${spb}`;
  return `Beat ${beat}${subLabel}`;
}

let popoverEl: HTMLElement | null = null;
let popoverOutsideHandler: ((e: MouseEvent) => void) | null = null;
let colHighlightEl: HTMLElement | null = null;

function removeColHighlight(): void {
  colHighlightEl?.remove();
  colHighlightEl = null;
}

/** Tint the active 16th column behind the notes so it's clear which slot is being edited. */
function addColHighlight(barView: BarView, slot: number, maxSixteenth: number): void {
  removeColHighlight();
  const xs = barView.gridXs;
  if (!xs) return;
  const x0 = xs[slot];
  const x1 = xs[slot + 1] ?? (x0 !== undefined ? x0 + (barView.contentX1 - barView.contentX0) / maxSixteenth : undefined);
  if (x0 === undefined || x1 === undefined) return;
  const d = document.createElement('div');
  d.className = 'edit-col-highlight';
  d.style.left = `${pct(x0)}%`;
  d.style.width = `${pct(x1 - x0)}%`;
  barView.svgHost.appendChild(d);
  colHighlightEl = d;
}

function closePopover(): void {
  if (popoverOutsideHandler) {
    document.removeEventListener('mousedown', popoverOutsideHandler, true);
    popoverOutsideHandler = null;
  }
  popoverEl?.remove();
  popoverEl = null;
  removeColHighlight();
}

interface ColumnPopoverArgs {
  bar: BarObj;
  barView: BarView;
  sixteenth: number;
  maxSixteenth: number;
  numerator: number;
  /** The lane the active selection owns — the beat editor is scoped to it. */
  lane: LaneKey;
  /** Verified selections, so the single-bar redraw keeps its delta coloring. */
  regions: api.Selection[];
  /** Record an edit op into the active draft selection. */
  recordOp: (op: Op) => void;
  /** Flag a non-op edit (flam toggle) so the draft counts as edited. */
  markEdited: () => void;
  /** Re-apply selection bands after the bar is redrawn (drawBar wipes them). */
  afterRedraw: () => void;
}

/** Notehead glyph for a lane's toggle cell — X-heads for cymbals/hi-hat (⊗ when
 * the hi-hat is open), filled ● for drums — so the panel reads like a staff. */
function laneGlyph(lane: Lane, note: NoteObj | undefined): string {
  if (lane.hihat) return note && note.instrument === 'hihat_open' ? '⊗' : '✕';
  return lane.add === 'crash' || lane.add === 'ride' || lane.add === 'hihat_chick' ? '✕' : '●';
}

function openColumnPopover(args: ColumnPopoverArgs): void {
  closePopover();
  const { bar, barView, sixteenth, maxSixteenth, numerator, lane, regions, recordOp, markEdited, afterRedraw } = args;
  const position = formatPosition(sixteenth);

  const box = document.createElement('div');
  box.className = 'edit-popover col-popover';

  const notesHere = (): NoteObj[] =>
    bar.notes.filter((n) => Math.round(parsePosition(n.position) * SUBDIV) === sixteenth);

  // Redraw the bar (grid mode), re-apply the column tint (drawBar wipes it), then
  // re-apply the selection bands (also wiped).
  const redraw = (): void => {
    drawBar(barView, bar, true, regions);
    addColHighlight(barView, sixteenth, maxSixteenth);
    afterRedraw();
  };

  const mutate = (fn: () => void): void => {
    fn();
    redraw();
    rebuild();
  };
  const addNote = (inst: SchemaDrumClass): void =>
    mutate(() => {
      bar.notes.push({ instrument: inst, position, duration: '1/8' });
      recordOp({ kind: 'add', bar: bar.index, position, instrument: inst, duration: '1/8' });
    });
  const deleteNote = (note: NoteObj): void =>
    mutate(() => {
      const i = bar.notes.indexOf(note);
      if (i >= 0) bar.notes.splice(i, 1);
      recordOp({ kind: 'delete', bar: bar.index, position: note.position, instrument: note.instrument });
    });
  const setInstrument = (note: NoteObj, inst: SchemaDrumClass): void =>
    mutate(() => {
      const from = note.instrument;
      note.instrument = inst;
      recordOp({ kind: 'reclassify', bar: bar.index, position: note.position, from, to: inst });
    });
  // Two independent notation ornaments on a present stroke, snare + toms only
  // (gated at the call site). Both live on the note itself — no op, no new note
  // in the sequence — so they're frozen into the selection's notes on Verify
  // (see SelectionController.markEdited) and drawn by render's attach helpers.
  //   flam  → a same-instrument slashed grace note tucked before the hit
  //   ghost → the hit's notehead wrapped in parentheses (a soft/muted stroke)
  const toggleFlam = (note: NoteObj): void =>
    mutate(() => {
      if (note.grace) delete note.grace;
      else note.grace = { instrument: note.instrument, slashed: true };
      markEdited();
    });
  const toggleGhost = (note: NoteObj): void =>
    mutate(() => {
      if (note.ghost) delete note.ghost;
      else note.ghost = true;
      markEdited();
    });

  // Toggle a lane on/off at this column (hi-hat turns on as closed by default;
  // the open/closed choice is a separate toggle on the row, below).
  const toggleOnOff = (lane: Lane, note: NoteObj | undefined): void => {
    if (note) deleteNote(note);
    else addNote(lane.add);
  };

  // Rebuild from live bar state so each toggle reflects immediately (and you can
  // stack several hits on one beat) without closing the panel.
  function rebuild(): void {
    box.innerHTML = '';
    const head = document.createElement('div');
    head.className = 'col-popover-head';
    head.textContent = beatLabel(sixteenth, maxSixteenth, numerator);
    box.appendChild(head);

    const here = notesHere();
    const staff = document.createElement('div');
    staff.className = 'col-staff';
    // Scoped to the active selection's lane (one row) — a selection owns one lane,
    // so the beat editor only offers that lane's on/off (+ hi-hat open/closed).
    for (const laneRow of LANES.filter((l) => l.key === lane)) {
      const note = here.find((n) => laneOf(n.instrument) === laneRow.key);
      const on = note !== undefined;
      const row = document.createElement('div');
      row.className = `col-staff-row${on ? ' on' : ''}`;

      // The on/off toggle (cell glyph + label) is the big click target.
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'col-toggle';
      toggle.title = `${laneRow.label}: ${on ? 'on — click to remove' : 'off — click to add'}`;
      const cell = document.createElement('span');
      cell.className = 'col-cell';
      cell.textContent = laneGlyph(laneRow, note);
      const name = document.createElement('span');
      name.className = 'col-row-label';
      name.textContent = laneRow.label;
      toggle.append(cell, name);
      toggle.onclick = () => toggleOnOff(laneRow, note);
      row.appendChild(toggle);

      // Hi-hat: an explicit closed|open toggle, shown only when the hi-hat is on.
      if (laneRow.hihat && note) {
        const seg = document.createElement('div');
        seg.className = 'col-variant';
        const mk = (label: string, inst: SchemaDrumClass, title: string): void => {
          const b = document.createElement('button');
          b.type = 'button';
          b.textContent = label;
          b.title = title;
          if (note.instrument === inst) b.className = 'active';
          b.onclick = () => setInstrument(note, inst);
          seg.appendChild(b);
        };
        mk('x', 'hihat_closed', 'Closed hi-hat');
        mk('o', 'hihat_open', 'Open hi-hat');
        row.appendChild(seg);
      }

      // Flam / ghost ornaments — snare + toms only (these are played on those
      // drums; scope the affordance to them). Two independent toggles: flam (a
      // slashed grace note) and ghost (parenthesized soft hit). Shown only for a
      // present stroke, since both notate an existing hit.
      const ornamentable = laneRow.key === 'snare' || laneRow.key.startsWith('tom_');
      if (note && ornamentable) {
        const seg = document.createElement('div');
        seg.className = 'col-variant col-ornament';
        const mkg = (label: string, active: boolean, onClick: () => void, title: string): void => {
          const b = document.createElement('button');
          b.type = 'button';
          b.textContent = label;
          b.title = title;
          if (active) b.className = 'active';
          b.onclick = onClick;
          seg.appendChild(b);
        };
        mkg('flam', !!note.grace, () => toggleFlam(note), 'Flam — slashed grace note (click to toggle)');
        mkg('ghost', !!note.ghost, () => toggleGhost(note), 'Ghost note — soft hit in parentheses (click to toggle)');
        row.appendChild(seg);
      }

      staff.appendChild(row);
    }
    box.appendChild(staff);
    positionBesideColumn();
  }

  // Place the panel beside the active column (never over it), flipping to the
  // other side and clamping to the viewport so far-left/right columns work.
  function positionBesideColumn(): void {
    const col = columnRect(barView, sixteenth, maxSixteenth);
    const w = box.offsetWidth || 180;
    const h = box.offsetHeight || 260;
    const gap = 14;
    const m = 8;
    let left = col.right + gap; // prefer the right of the column
    if (left + w > window.innerWidth - m) left = col.left - gap - w; // flip left near the right edge
    left = clamp(left, m, window.innerWidth - w - m);
    let top = col.top + (col.bottom - col.top) / 2 - h / 2; // vertically centred on the staff
    top = clamp(top, m, window.innerHeight - h - m);
    box.style.left = `${left}px`;
    box.style.top = `${top}px`;
  }

  document.body.appendChild(box);
  popoverEl = box;
  rebuild();
  addColHighlight(barView, sixteenth, maxSixteenth);

  // Close on outside click (capture so it beats the score handler). Deferred so
  // the opening click doesn't immediately close it.
  popoverOutsideHandler = (e: MouseEvent) => {
    if (box.contains(e.target as Node)) return;
    closePopover();
  };
  setTimeout(() => {
    if (popoverOutsideHandler) document.addEventListener('mousedown', popoverOutsideHandler, true);
  }, 0);
}
