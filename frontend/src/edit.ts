// Manual editing of the transcription. In edit mode, clicking the score selects
// an existing note (reclassify / nudge / delete) or adds a new one at the
// clicked pitch + snapped 16th position. Edits mutate the in-memory notation and
// re-render just the affected bar; Save persists via PUT /projects/{id}.
import {
  drawBar,
  parsePosition,
  formatPosition,
  SCHEMA_CLASSES,
  BAR_SVG_WIDTH,
  BAR_SVG_HEIGHT,
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

  sync.onEditClick = (barView, x, y) => {
    const bar = notation.bars.find((b) => b.index === barView.index);
    if (!bar) return;

    const instrument = nearestInstrument(model, y);
    const sixteenth = resolveSixteenth(barView, x, maxSixteenth);
    const position = formatPosition(sixteenth);

    let note = bar.notes.find(
      (n) => Math.round(parsePosition(n.position) * SUBDIV) === sixteenth && n.instrument === instrument,
    );
    if (!note) {
      note = { instrument, position, duration: '1/8' };
      bar.notes.push(note);
      drawBar(barView, bar, true);
      markDirty();
    }
    openPopover({ note, bar, barView, maxSixteenth, markDirty, screen: toScreen(barView, x, y) });
  };
}

// === Geometry helpers ===

function nearestInstrument(model: RenderModel, y: number): SchemaDrumClass {
  let best: SchemaDrumClass = 'snare';
  let bestDist = Infinity;
  for (const cls of SCHEMA_CLASSES) {
    const d = Math.abs(model.instrumentYs[cls] - y);
    if (d < bestDist) {
      bestDist = d;
      best = cls;
    }
  }
  return best;
}

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

function toScreen(barView: BarView, x: number, y: number): { x: number; y: number } {
  // x, y are viewBox (800×140) units; map back through the SVG's rendered scale.
  const rect = barView.svgHost.getBoundingClientRect();
  return {
    x: rect.left + (x / BAR_SVG_WIDTH) * rect.width,
    y: rect.top + (y / BAR_SVG_HEIGHT) * rect.height,
  };
}

// === Popover ===

let popoverEl: HTMLElement | null = null;
let popoverOutsideHandler: ((e: MouseEvent) => void) | null = null;

function closePopover(): void {
  if (popoverOutsideHandler) {
    document.removeEventListener('mousedown', popoverOutsideHandler, true);
    popoverOutsideHandler = null;
  }
  popoverEl?.remove();
  popoverEl = null;
}

interface PopoverArgs {
  note: NoteObj;
  bar: BarObj;
  barView: BarView;
  maxSixteenth: number;
  markDirty: () => void;
  screen: { x: number; y: number };
}

function openPopover(args: PopoverArgs): void {
  closePopover();
  const { note, bar, barView, maxSixteenth, markDirty, screen } = args;

  const el = document.createElement('div');
  el.className = 'edit-popover';
  el.style.left = `${screen.x}px`;
  el.style.top = `${screen.y + 12}px`;

  // Instrument select (reclassify).
  const select = document.createElement('select');
  for (const cls of SCHEMA_CLASSES) {
    const opt = document.createElement('option');
    opt.value = cls;
    opt.textContent = cls;
    if (cls === note.instrument) opt.selected = true;
    select.appendChild(opt);
  }
  select.onchange = () => {
    note.instrument = select.value as SchemaDrumClass;
    drawBar(barView, bar, true);
    markDirty();
  };
  el.appendChild(select);

  // Nudge ◄ ► by one sixteenth.
  const nudge = (delta: number): void => {
    const cur = Math.round(parsePosition(note.position) * SUBDIV);
    const next = Math.max(0, Math.min(maxSixteenth - 1, cur + delta));
    if (next === cur) return;
    note.position = formatPosition(next);
    drawBar(barView, bar, true);
    markDirty();
  };
  const row = document.createElement('div');
  row.className = 'edit-popover-row';
  row.appendChild(button('◄', 'Nudge earlier', () => nudge(-1)));
  row.appendChild(button('►', 'Nudge later', () => nudge(1)));
  const del = button('Delete', 'Delete note', () => {
    const i = bar.notes.indexOf(note);
    if (i >= 0) bar.notes.splice(i, 1);
    drawBar(barView, bar, true);
    markDirty();
    closePopover();
  });
  del.classList.add('danger');
  row.appendChild(del);
  el.appendChild(row);

  document.body.appendChild(el);
  popoverEl = el;

  // Close when clicking outside (capture so it fires before score handlers).
  popoverOutsideHandler = (e: MouseEvent) => {
    if (el.contains(e.target as Node)) return;
    closePopover();
  };
  // Defer so the click that opened the popover doesn't immediately close it.
  setTimeout(() => {
    if (popoverOutsideHandler) document.addEventListener('mousedown', popoverOutsideHandler, true);
  }, 0);
}

function button(label: string, title: string, onClick: () => void): HTMLButtonElement {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = label;
  b.title = title;
  b.onclick = onClick;
  return b;
}
