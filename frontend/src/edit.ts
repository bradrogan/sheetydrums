// Manual editing of the transcription. In edit mode, clicking the score snaps to
// the nearest 16th COLUMN (x only — a wide target, no vertical precision) and
// opens a "beat" editor listing every hit at that slot: each is removable, the
// hi-hat carries an open/closed toggle, and any absent instrument can be added.
// Editing by time-column (not by clicked pitch) means simultaneous hits (e.g.
// crash + hi-hat on the same beat) and the open/closed distinction are both
// unambiguous — the old pitch-from-y hit-test couldn't express either. Edits
// mutate the in-memory notation and re-render the affected bar; Save persists
// via PUT /projects/{id}. See docs/design/edit-ux.md.
import {
  drawBar,
  parsePosition,
  formatPosition,
  BAR_SVG_WIDTH,
  type RenderModel,
  type BarView,
  type SchemaDrumClass,
} from './render';
import type { SyncController } from './sync';
import * as api from './api';
import type { Project, Notation } from './api';

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

/** Modal asking what to do with unsaved edits when leaving edit mode. */
function confirmLeave(): Promise<'save' | 'discard' | 'cancel'> {
  return new Promise((resolve) => {
    const overlay = document.createElement('div');
    overlay.className = 'edit-modal-overlay';
    const box = document.createElement('div');
    box.className = 'edit-modal';
    const msg = document.createElement('p');
    msg.textContent = 'You have unsaved changes. Save them before leaving edit mode?';
    box.appendChild(msg);
    const row = document.createElement('div');
    row.className = 'edit-modal-row';
    const mk = (label: string, choice: 'save' | 'discard' | 'cancel', cls: string): void => {
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
    mk('Save', 'save', 'primary');
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
}

export function setupEditing(ctx: EditContext): void {
  const { project, notation, model, sync, editToggle, saveBtn, scoreEl } = ctx;
  const maxSixteenth = Math.round(
    (notation.time_signature.numerator / notation.time_signature.denominator) * SUBDIV,
  );
  let dirty = false;
  // Baseline to revert to on Discard (refreshed on entering edit mode + each save).
  let saved: Notation = structuredClone(notation);

  const rerenderAll = (gridMode: boolean): void => {
    for (const bv of model.bars) {
      const bar = notation.bars.find((b) => b.index === bv.index);
      if (bar) drawBar(bv, bar, gridMode);
    }
  };

  const setDirty = (d: boolean): void => {
    dirty = d;
    editSession.dirty = d;
    saveBtn.disabled = !d;
    saveBtn.textContent = d ? 'Save •' : 'Saved';
  };
  const markDirty = (): void => setDirty(true);

  const enterEditMode = (): void => {
    sync.editMode = true;
    saved = structuredClone(notation);
    editToggle.textContent = 'Done';
    editToggle.classList.add('active');
    scoreEl.classList.add('editing');
    saveBtn.hidden = false;
    setDirty(false);
    // Switch the whole score to the fixed 16th grid so notes stay put.
    rerenderAll(true);
    // Redrawing moved the note x-positions the playhead anchors to — put the
    // marker back where playback last left it (e.g. where the user paused).
    sync.refresh();
  };

  const exitEditMode = (): void => {
    sync.editMode = false;
    editToggle.textContent = 'Edit';
    editToggle.classList.remove('active');
    scoreEl.classList.remove('editing');
    saveBtn.hidden = true;
    editSession.dirty = false;
    closePopover();
    rerenderAll(false); // back to proportional view
    sync.refresh(); // reposition the retained marker for the proportional layout
  };

  const doSave = async (): Promise<boolean> => {
    saveBtn.disabled = true;
    saveBtn.textContent = 'Saving…';
    try {
      await api.saveNotation(project.video_id, notation);
      saved = structuredClone(notation);
      setDirty(false);
      return true;
    } catch (err) {
      setDirty(true);
      alert(`Save failed: ${err instanceof Error ? err.message : String(err)}`);
      return false;
    }
  };

  const leaveEditMode = async (): Promise<boolean> => {
    if (!sync.editMode) return true;
    if (dirty) {
      const choice = await confirmLeave();
      if (choice === 'cancel') return false;
      if (choice === 'save') {
        if (!(await doSave())) return false;
      } else {
        // discard: revert notation to the baseline
        notation.bars = structuredClone(saved).bars;
        setDirty(false);
      }
    }
    exitEditMode();
    return true;
  };

  // Let navigation (Back) route through the same unsaved-changes guard.
  editSession.leave = leaveEditMode;

  // Start clean each time a project opens.
  sync.editMode = false;
  editSession.dirty = false;
  editToggle.textContent = 'Edit';
  editToggle.classList.remove('active');
  saveBtn.hidden = true;
  closePopover();

  editToggle.onclick = () => {
    if (!sync.editMode) enterEditMode();
    else void leaveEditMode();
  };
  saveBtn.onclick = () => void doSave();

  // Click anywhere in a bar → snap to the nearest 16th column and open the beat
  // editor for that slot. The y coordinate is intentionally ignored (no pitch
  // guessing); the column editor lists/edits every instrument at the slot.
  sync.onEditClick = (barView, x, _y) => {
    const bar = notation.bars.find((b) => b.index === barView.index);
    if (!bar) return;
    const sixteenth = resolveSixteenth(barView, x, maxSixteenth);
    openColumnPopover({
      bar,
      barView,
      sixteenth,
      maxSixteenth,
      numerator: notation.time_signature.numerator,
      markDirty,
    });
  };
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

function laneOf(inst: SchemaDrumClass): string {
  return inst === 'hihat_open' || inst === 'hihat_closed' ? 'hihat' : inst;
}

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
  markDirty: () => void;
}

/** Notehead glyph for a lane's toggle cell — X-heads for cymbals/hi-hat (⊗ when
 * the hi-hat is open), filled ● for drums — so the panel reads like a staff. */
function laneGlyph(lane: Lane, note: NoteObj | undefined): string {
  if (lane.hihat) return note && note.instrument === 'hihat_open' ? '⊗' : '✕';
  return lane.add === 'crash' || lane.add === 'ride' || lane.add === 'hihat_chick' ? '✕' : '●';
}

function openColumnPopover(args: ColumnPopoverArgs): void {
  closePopover();
  const { bar, barView, sixteenth, maxSixteenth, numerator, markDirty } = args;

  const el = document.createElement('div');
  el.className = 'edit-popover col-popover';

  const notesHere = (): NoteObj[] =>
    bar.notes.filter((n) => Math.round(parsePosition(n.position) * SUBDIV) === sixteenth);

  // Redraw the bar (grid mode) and re-apply the column tint, which drawBar wipes.
  const redraw = (): void => {
    drawBar(barView, bar, true);
    addColHighlight(barView, sixteenth, maxSixteenth);
  };

  const mutate = (fn: () => void): void => {
    fn();
    redraw();
    markDirty();
    rebuild();
  };
  const addNote = (inst: SchemaDrumClass): void =>
    mutate(() => bar.notes.push({ instrument: inst, position: formatPosition(sixteenth), duration: '1/8' }));
  const deleteNote = (note: NoteObj): void =>
    mutate(() => { const i = bar.notes.indexOf(note); if (i >= 0) bar.notes.splice(i, 1); });
  const setInstrument = (note: NoteObj, inst: SchemaDrumClass): void =>
    mutate(() => { note.instrument = inst; });

  // Toggle a lane on/off at this column (hi-hat turns on as closed by default;
  // the open/closed choice is a separate toggle on the row, below).
  const toggleOnOff = (lane: Lane, note: NoteObj | undefined): void => {
    if (note) deleteNote(note);
    else addNote(lane.add);
  };

  // Rebuild from live bar state so each toggle reflects immediately (and you can
  // stack several hits on one beat) without closing the panel.
  function rebuild(): void {
    el.innerHTML = '';
    const head = document.createElement('div');
    head.className = 'col-popover-head';
    head.textContent = beatLabel(sixteenth, maxSixteenth, numerator);
    el.appendChild(head);

    const here = notesHere();
    const staff = document.createElement('div');
    staff.className = 'col-staff';
    // One row per lane, top-to-bottom in staff order — a vertical mini-staff the
    // user clicks on/off per instrument position.
    for (const lane of LANES) {
      const note = here.find((n) => laneOf(n.instrument) === lane.key);
      const on = note !== undefined;
      const row = document.createElement('div');
      row.className = `col-staff-row${on ? ' on' : ''}`;

      // The on/off toggle (cell glyph + label) is the big click target.
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.className = 'col-toggle';
      toggle.title = `${lane.label}: ${on ? 'on — click to remove' : 'off — click to add'}`;
      const cell = document.createElement('span');
      cell.className = 'col-cell';
      cell.textContent = laneGlyph(lane, note);
      const name = document.createElement('span');
      name.className = 'col-row-label';
      name.textContent = lane.label;
      toggle.append(cell, name);
      toggle.onclick = () => toggleOnOff(lane, note);
      row.appendChild(toggle);

      // Hi-hat: an explicit closed|open toggle, shown only when the hi-hat is on.
      if (lane.hihat && note) {
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

      staff.appendChild(row);
    }
    el.appendChild(staff);
    positionBesideColumn();
  }

  // Place the panel beside the active column (never over it), flipping to the
  // other side and clamping to the viewport so far-left/right columns work.
  function positionBesideColumn(): void {
    const col = columnRect(barView, sixteenth, maxSixteenth);
    const w = el.offsetWidth || 180;
    const h = el.offsetHeight || 260;
    const gap = 14;
    const m = 8;
    let left = col.right + gap; // prefer the right of the column
    if (left + w > window.innerWidth - m) left = col.left - gap - w; // flip left near the right edge
    left = clamp(left, m, window.innerWidth - w - m);
    let top = col.top + (col.bottom - col.top) / 2 - h / 2; // vertically centred on the staff
    top = clamp(top, m, window.innerHeight - h - m);
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
  }

  document.body.appendChild(el);
  popoverEl = el;
  rebuild();
  addColHighlight(barView, sixteenth, maxSixteenth);

  // Close on outside click (capture so it beats the score handler). Deferred so
  // the opening click doesn't immediately close it.
  popoverOutsideHandler = (e: MouseEvent) => {
    if (el.contains(e.target as Node)) return;
    closePopover();
  };
  setTimeout(() => {
    if (popoverOutsideHandler) document.addEventListener('mousedown', popoverOutsideHandler, true);
  }, 0);
}
