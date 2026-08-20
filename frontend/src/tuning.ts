// Phase 1 manual tuning panel: show notation diagnostics, tweak a few pipeline
// params, re-run (cache-accelerated preview), see a diff summary, accept or
// discard. No AI yet — this exercises the diagnose → retune → accept loop and is
// the surface the "fix the rest" search will later drive.
import * as api from './api';
import type { Diagnostics, Notation, PipelineParams, Project, RetunePreview } from './api';

type NoteKey = string; // `${instrument}@${position}`

export interface NotationDiff {
  added: number;
  removed: number;
  perClass: Record<string, { added: number; removed: number }>;
  changedBars: number[];
}

/** Data-level diff of two notations, matched by bar index then (instrument,
 * position). Independent of the DOM so it's testable and stable across redraws. */
export function diffNotation(before: Notation, after: Notation): NotationDiff {
  const beforeByBar = new Map<number, Set<NoteKey>>();
  for (const bar of before.bars) beforeByBar.set(bar.index, keysOf(bar));
  const afterByBar = new Map<number, Set<NoteKey>>();
  for (const bar of after.bars) afterByBar.set(bar.index, keysOf(bar));

  const diff: NotationDiff = { added: 0, removed: 0, perClass: {}, changedBars: [] };
  const bump = (cls: string, field: 'added' | 'removed'): void => {
    (diff.perClass[cls] ??= { added: 0, removed: 0 })[field]++;
    diff[field]++;
  };

  const allBars = new Set<number>([...beforeByBar.keys(), ...afterByBar.keys()]);
  for (const idx of [...allBars].sort((a, b) => a - b)) {
    const b = beforeByBar.get(idx) ?? new Set<NoteKey>();
    const a = afterByBar.get(idx) ?? new Set<NoteKey>();
    let touched = false;
    for (const k of a) if (!b.has(k)) { bump(instrumentOf(k), 'added'); touched = true; }
    for (const k of b) if (!a.has(k)) { bump(instrumentOf(k), 'removed'); touched = true; }
    if (touched) diff.changedBars.push(idx);
  }
  return diff;
}

function keysOf(bar: Notation['bars'][number]): Set<NoteKey> {
  return new Set((bar.notes ?? []).map((n) => `${n.instrument}@${n.position}`));
}
function instrumentOf(key: NoteKey): string {
  return key.slice(0, key.indexOf('@'));
}

// The knobs surfaced in the manual panel. Kept small + representative — the full
// catalog lives in the backend PipelineParams. Each maps to a params group/field.
interface KnobDef {
  group: string;
  field: string;
  label: string;
  kind: 'number' | 'select';
  min?: number;
  max?: number;
  step?: number;
  options?: number[];
  hint: string;
}

const KNOBS: KnobDef[] = [
  { group: 'expander', field: 'hihat_unimodal_open_threshold', label: 'Hi-hat open threshold',
    kind: 'number', min: 0, max: 1, step: 0.05, hint: 'Higher = more hats read as closed' },
  { group: 'quantize', field: 'subdivisions_per_whole', label: 'Grid subdivision',
    kind: 'select', options: [16, 32], hint: '16 = sixteenths, 32 = thirty-seconds' },
];
// ADTOF per-class thresholds, in the model's class order.
const ADTOF_CLASSES = ['kick', 'snare', 'tom', 'hihat', 'cymbal'] as const;

export interface TuningContext {
  project: Project;
  notation: Notation;
  panel: HTMLElement;
  /** Re-render the score DOM to show `n` (preview or original). Visual only. */
  renderPreview: (n: Notation) => void;
  /** Reload the project fresh (re-route) so sync/edit rebind after accept/discard. */
  reload: () => void;
}

export function setupTuning(ctx: TuningContext): void {
  const { project, panel } = ctx;
  const videoId = project.video_id;
  // Working copy of params the user is editing (starts from the project's saved
  // params, if any).
  const params: PipelineParams = structuredClone((project as { params?: PipelineParams }).params ?? {});
  let activeSource: EventSource | null = null;
  let preview: RetunePreview | null = null;

  panel.innerHTML = '';
  panel.classList.add('tuning-panel');

  const diagBox = el('div', 'tuning-diag muted');
  panel.appendChild(diagBox);
  void refreshDiagnostics();

  const knobsBox = el('div', 'tuning-knobs');
  panel.appendChild(knobsBox);
  buildKnobs(knobsBox);

  const status = el('div', 'tuning-status muted');
  const diffBox = el('div', 'tuning-diff');
  const actions = el('div', 'tuning-actions');

  const retuneBtn = button('Re-run preview', 'primary', () => void doRetune());
  const acceptBtn = button('Accept', 'accept', () => void doAccept());
  const discardBtn = button('Discard', 'ghost', () => doDiscard());
  acceptBtn.hidden = true;
  discardBtn.hidden = true;
  actions.append(retuneBtn, acceptBtn, discardBtn);
  panel.append(status, diffBox, actions);

  async function refreshDiagnostics(): Promise<void> {
    try {
      renderDiagnostics(diagBox, await api.diagnose(videoId));
    } catch (err) {
      diagBox.textContent = `Diagnostics unavailable: ${errMsg(err)}`;
    }
  }

  function buildKnobs(host: HTMLElement): void {
    host.innerHTML = '';
    for (const k of KNOBS) host.appendChild(knobRow(k));
    host.appendChild(adtofRow());
  }

  function knobRow(k: KnobDef): HTMLElement {
    const row = el('label', 'knob-row');
    const name = el('span', 'knob-label');
    name.textContent = k.label;
    name.title = k.hint;
    let input: HTMLInputElement | HTMLSelectElement;
    const current = (params[k.group]?.[k.field] as number | undefined);
    if (k.kind === 'select') {
      const sel = document.createElement('select');
      for (const opt of k.options!) {
        const o = document.createElement('option');
        o.value = String(opt);
        o.textContent = String(opt);
        if (current === opt) o.selected = true;
        sel.appendChild(o);
      }
      sel.onchange = () => setParam(k.group, k.field, Number(sel.value));
      input = sel;
    } else {
      const inp = document.createElement('input');
      inp.type = 'number';
      inp.min = String(k.min);
      inp.max = String(k.max);
      inp.step = String(k.step);
      inp.placeholder = 'default';
      if (current !== undefined) inp.value = String(current);
      inp.onchange = () => {
        if (inp.value === '') clearParam(k.group, k.field);
        else setParam(k.group, k.field, Number(inp.value));
      };
      input = inp;
    }
    row.append(name, input);
    return row;
  }

  // ADTOF thresholds as a compact 5-cell row (all-or-nothing tuple).
  function adtofRow(): HTMLElement {
    const wrap = el('div', 'knob-row knob-adtof');
    const name = el('span', 'knob-label');
    name.textContent = 'Detect sensitivity';
    name.title = 'Per-instrument onset thresholds (lower = more hits). Blank = defaults.';
    wrap.appendChild(name);
    const grid = el('div', 'adtof-grid');
    const current = (params.transcription?.thresholds as number[] | undefined) ?? [];
    ADTOF_CLASSES.forEach((cls, i) => {
      const cell = el('label', 'adtof-cell');
      const lbl = el('span', '');
      lbl.textContent = cls;
      const inp = document.createElement('input');
      inp.type = 'number';
      inp.min = '0';
      inp.max = '1';
      inp.step = '0.02';
      inp.placeholder = 'def';
      if (current[i] !== undefined) inp.value = String(current[i]);
      inp.dataset.idx = String(i);
      inp.onchange = () => updateThresholds(grid);
      cell.append(lbl, inp);
      grid.appendChild(cell);
    });
    wrap.appendChild(grid);
    return wrap;
  }

  function updateThresholds(grid: HTMLElement): void {
    const inputs = [...grid.querySelectorAll('input')] as HTMLInputElement[];
    const vals = inputs.map((i) => i.value.trim());
    // All-or-nothing: the backend takes the whole 5-tuple or uses its tuned
    // defaults. A partial tuple has no safe meaning (a blank isn't 0 — a 0
    // threshold would flood detection), so we only apply when all five are set;
    // otherwise clear and fall back to defaults. Mark partial input invalid.
    const filled = vals.filter((v) => v !== '').length;
    const partial = filled > 0 && filled < vals.length;
    grid.classList.toggle('adtof-partial', partial);
    if (filled === vals.length) {
      setParam('transcription', 'thresholds', vals.map(Number));
    } else {
      clearParam('transcription', 'thresholds');
    }
  }

  function setParam(group: string, field: string, value: unknown): void {
    (params[group] ??= {})[field] = value;
  }
  function clearParam(group: string, field: string): void {
    if (params[group]) delete params[group][field];
    if (params[group] && Object.keys(params[group]).length === 0) delete params[group];
  }

  async function doRetune(): Promise<void> {
    activeSource?.close();
    retuneBtn.disabled = true;
    acceptBtn.hidden = true;
    discardBtn.hidden = true;
    diffBox.innerHTML = '';
    status.textContent = 'Re-running…';
    let jobId: string;
    try {
      jobId = await api.startRetune(videoId, params);
    } catch (err) {
      status.textContent = `Failed to start: ${errMsg(err)}`;
      retuneBtn.disabled = false;
      return;
    }
    activeSource = api.streamRetune(jobId, {
      onProgress: (msg) => { status.textContent = msg; },
      onResult: (p) => {
        activeSource = null;
        preview = p;
        retuneBtn.disabled = false;
        status.textContent = 'Preview ready — reviewing changes below.';
        const diff = diffNotation(ctx.notation, p.notation);
        renderDiff(diffBox, diff);
        ctx.renderPreview(p.notation); // show the preview on the score
        acceptBtn.hidden = false;
        discardBtn.hidden = false;
      },
      onFailure: (error) => {
        activeSource = null;
        retuneBtn.disabled = false;
        status.textContent = `Failed: ${error}`;
      },
    });
  }

  async function doAccept(): Promise<void> {
    if (!preview) return;
    acceptBtn.disabled = true;
    status.textContent = 'Saving…';
    try {
      await api.saveNotation(videoId, preview.notation, preview.params);
      preview = null;
      ctx.reload(); // re-route so sync/edit rebind to the saved notation
    } catch (err) {
      acceptBtn.disabled = false;
      status.textContent = `Save failed: ${errMsg(err)}`;
    }
  }

  function doDiscard(): void {
    activeSource?.close();
    activeSource = null;
    preview = null;
    ctx.reload(); // reload original cleanly (undo the preview render)
  }
}

// === rendering helpers ===================================================

function renderDiagnostics(host: HTMLElement, d: Diagnostics): void {
  host.innerHTML = '';
  const line = (t: string): void => { const p = el('div', ''); p.textContent = t; host.appendChild(p); };
  line(`${d.n_notes} notes · ${d.n_bars} bars · ${d.notes_per_bar}/bar`);
  const hats = d.hats;
  if (hats.closed + hats.open > 0) {
    const pct = hats.open_fraction === null ? '' : ` (${Math.round(hats.open_fraction * 100)}% open)`;
    line(`hi-hat: ${hats.closed} closed / ${hats.open} open${pct}`);
  }
  if (d.confidence.present && d.confidence.median !== undefined) {
    line(`confidence median ${d.confidence.median}`);
  }
  for (const flag of d.flags) {
    const f = el('div', 'tuning-flag');
    f.textContent = `⚠ ${flag}`;
    host.appendChild(f);
  }
}

function renderDiff(host: HTMLElement, diff: NotationDiff): void {
  host.innerHTML = '';
  if (diff.added === 0 && diff.removed === 0) {
    host.textContent = 'No change from the current transcription.';
    return;
  }
  const head = el('div', 'diff-head');
  head.textContent = `+${diff.added} / −${diff.removed} notes across ${diff.changedBars.length} bar(s)`;
  host.appendChild(head);
  const perClass = Object.entries(diff.perClass).sort();
  for (const [cls, c] of perClass) {
    const row = el('div', 'diff-row');
    row.textContent = `${cls}: +${c.added} / −${c.removed}`;
    host.appendChild(row);
  }
}

function el(tag: string, className: string): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  return e;
}

function button(label: string, cls: string, onClick: () => void): HTMLButtonElement {
  const b = document.createElement('button');
  b.type = 'button';
  b.textContent = label;
  b.className = cls;
  b.onclick = onClick;
  return b;
}

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
