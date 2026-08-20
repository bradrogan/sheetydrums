// Phase 1 manual tuning panel (dev tool): show notation diagnostics, tweak a few
// pipeline params via slider+number controls, re-run (cache-accelerated preview),
// see a diff summary, accept or discard. No AI — this exercises the diagnose →
// retune → accept loop and is the plumbing the later correct-by-example ("fix the
// rest") feature reuses. Lives in a bottom drawer so the sheet stays visible.
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

// Backend stage defaults, mirrored here so a slider can start at (and reset to)
// the real default and show an "overridden" state. Keep in sync with
// stages/transcription.py (_TUNED_THRESHOLDS) + expander.py + quantize.py.
const ADTOF_CLASSES = ['kick', 'snare', 'tom', 'hihat', 'cymbal'] as const;
const ADTOF_DEFAULTS = [0.22, 0.24, 0.2, 0.16, 0.18];
const HAT_OPEN_DEFAULT = 0.4;
const SUBDIV_DEFAULT = 16;

/** One tweakable control: manages a params entry + its slider/number/reset DOM. */
interface Control {
  el: HTMLElement;
  /** Restore this control to the backend default (clears its param override). */
  reset: () => void;
}

export interface TuningContext {
  project: Project;
  notation: Notation;
  panel: HTMLElement;
  renderPreview: (n: Notation) => void;
  reload: () => void;
  onClose: () => void;
}

export function setupTuning(ctx: TuningContext): void {
  const { project, panel } = ctx;
  const videoId = project.video_id;
  const params: PipelineParams = structuredClone((project as { params?: PipelineParams }).params ?? {});
  let activeSource: EventSource | null = null;
  let preview: RetunePreview | null = null;
  const controls: Control[] = [];

  panel.innerHTML = '';
  panel.classList.add('tuning-panel');

  // Header: title + close.
  const header = el('div', 'tuning-header');
  const title = el('span', 'tuning-title');
  title.textContent = 'Tune (dev)';
  const closeBtn = iconButton('✕', 'Close', () => ctx.onClose());
  closeBtn.classList.add('tuning-close');
  header.append(title, closeBtn);

  const diagBox = el('div', 'tuning-diag muted');

  // Knobs laid out in a responsive grid (wide drawer → several across).
  const knobsBox = el('div', 'tuning-knobs');
  controls.push(numberKnob('Hi-hat open threshold', 'expander', 'hihat_unimodal_open_threshold',
    { min: 0, max: 1, step: 0.05, def: HAT_OPEN_DEFAULT,
      hint: 'Higher = more hats read as closed' }));
  controls.push(selectKnob('Grid subdivision', 'quantize', 'subdivisions_per_whole',
    { options: [16, 32], def: SUBDIV_DEFAULT, hint: '16 = sixteenths, 32 = thirty-seconds' }));
  ADTOF_CLASSES.forEach((cls, i) => {
    controls.push(adtofKnob(cls, i));
  });
  for (const c of controls) knobsBox.appendChild(c.el);

  const status = el('div', 'tuning-status muted');
  const diffBox = el('div', 'tuning-diff');

  const actions = el('div', 'tuning-actions');
  const retuneBtn = button('Re-run preview', 'primary', () => void doRetune());
  // No status text on reset — the per-knob "overridden" highlight already shows
  // state, and a transient line shifts the layout. Just clear + reset the knobs.
  const resetAllBtn = button('Reset all', 'ghost', () => { for (const c of controls) c.reset(); afterParamChange(); });
  const acceptBtn = button('Accept', 'accept', () => void doAccept());
  const discardBtn = button('Discard', 'ghost', () => doDiscard());
  acceptBtn.hidden = true;
  discardBtn.hidden = true;
  actions.append(retuneBtn, resetAllBtn, acceptBtn, discardBtn);

  panel.append(header, diagBox, knobsBox, status, diffBox, actions);
  void refreshDiagnostics();

  // === controls ==========================================================

  function numberKnob(
    label: string, group: string, field: string,
    o: { min: number; max: number; step: number; def: number; hint: string },
  ): Control {
    const cur = (params[group]?.[field] as number | undefined) ?? o.def;
    const k = sliderKnob(label, o.hint, o.min, o.max, o.step, cur);
    const apply = (v: number): void => {
      k.set(v);
      if (approxEqual(v, o.def)) clearParam(group, field); else setParam(group, field, v);
      k.mark(!approxEqual(v, o.def));
    };
    k.onChange(apply);
    k.setReset(() => { apply(o.def); afterParamChange(); });
    k.mark(!approxEqual(cur, o.def));
    return { el: k.el, reset: () => apply(o.def) };
  }

  function selectKnob(
    label: string, group: string, field: string,
    o: { options: number[]; def: number; hint: string },
  ): Control {
    const cur = params[group]?.[field] as number | undefined;
    const el0 = el('div', 'knob');
    const head = el('div', 'knob-head');
    head.append(knobLabel(label, o.hint));
    const resetBtn = resetIcon(() => { apply(''); afterParamChange(); });
    head.append(resetBtn);
    const ctl = el('div', 'knob-ctl');
    const sel = document.createElement('select');
    sel.add(new Option('(default)', ''));
    for (const opt of o.options) sel.add(new Option(String(opt), String(opt)));
    sel.value = cur === undefined ? '' : String(cur);
    ctl.append(sel);
    el0.append(head, ctl);
    const mark = markFn(el0);
    const apply = (val: string): void => {
      sel.value = val;
      if (val === '') clearParam(group, field); else setParam(group, field, Number(val));
      mark(val !== '');
    };
    sel.onchange = () => apply(sel.value);
    mark(cur !== undefined);
    return { el: el0, reset: () => apply('') };
  }

  // ADTOF per-class threshold. The tuple is all-or-nothing at the backend, so a
  // cell edit writes the full 5-tuple (defaults for untouched cells); a tuple
  // that equals the defaults exactly is dropped so it stays "unset".
  function adtofKnob(cls: string, idx: number): Control {
    const tuple = currentAdtof();
    const o = { min: 0, max: 1, step: 0.02, def: ADTOF_DEFAULTS[idx]! };
    const k = sliderKnob(`Detect: ${cls}`, 'Lower = more hits', o.min, o.max, o.step, tuple[idx]!);
    const apply = (v: number): void => {
      k.set(v);
      const next = currentAdtof();
      next[idx] = v;
      if (arraysApproxEqual(next, ADTOF_DEFAULTS)) clearParam('transcription', 'thresholds');
      else setParam('transcription', 'thresholds', next);
      k.mark(!approxEqual(v, o.def));
    };
    k.onChange(apply);
    k.setReset(() => { apply(o.def); afterParamChange(); });
    k.mark(!approxEqual(tuple[idx]!, o.def));
    return { el: k.el, reset: () => apply(o.def) };
  }

  function currentAdtof(): number[] {
    const t = params.transcription?.thresholds as number[] | undefined;
    return t ? [...t] : [...ADTOF_DEFAULTS];
  }

  // === params bookkeeping ===============================================

  function setParam(group: string, field: string, value: unknown): void {
    (params[group] ??= {})[field] = value;
  }
  function clearParam(group: string, field: string): void {
    if (params[group]) delete params[group][field];
    if (params[group] && Object.keys(params[group]).length === 0) delete params[group];
  }
  // Editing params invalidates any showing preview. Clear its status/diff so no
  // stale "Preview ready" text lingers; don't add any new text (avoids layout
  // churn — the per-knob "overridden" highlight already conveys state).
  function afterParamChange(): void {
    if (preview) {
      preview = null;
      diffBox.innerHTML = '';
      status.textContent = '';
      acceptBtn.hidden = true;
      discardBtn.hidden = true;
    }
  }

  // === diagnostics + retune =============================================

  async function refreshDiagnostics(): Promise<void> {
    try {
      renderDiagnostics(diagBox, await api.diagnose(videoId));
    } catch (err) {
      diagBox.textContent = `Diagnostics unavailable: ${errMsg(err)}`;
    }
  }

  async function doRetune(): Promise<void> {
    activeSource?.close();
    retuneBtn.disabled = true;
    acceptBtn.hidden = discardBtn.hidden = true;
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
        status.textContent = 'Preview ready — review below, then Accept or Discard.';
        renderDiff(diffBox, diffNotation(ctx.notation, p.notation));
        ctx.renderPreview(p.notation);
        acceptBtn.hidden = discardBtn.hidden = false;
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
      ctx.reload();
    } catch (err) {
      acceptBtn.disabled = false;
      status.textContent = `Save failed: ${errMsg(err)}`;
    }
  }

  function doDiscard(): void {
    activeSource?.close();
    activeSource = null;
    preview = null;
    ctx.reload();
  }
}

// === small DOM builders ==================================================

interface SliderKnob {
  el: HTMLElement;
  set: (v: number) => void;
  onChange: (fn: (v: number) => void) => void;
  setReset: (fn: () => void) => void;
  mark: (on: boolean) => void;
}

/** A stacked knob: label + reset on top, a full-width slider + number below.
 * Stacking guarantees the slider gets real width regardless of column size
 * (the earlier single-row layout squished it to unusable). */
function sliderKnob(
  label: string, hint: string, min: number, max: number, step: number, value: number,
): SliderKnob {
  const root = el('div', 'knob');
  const head = el('div', 'knob-head');
  const resetBtn = resetIcon(() => {});
  head.append(knobLabel(label, hint), resetBtn);

  const ctl = el('div', 'knob-ctl');
  const slider = document.createElement('input');
  slider.type = 'range';
  slider.min = String(min); slider.max = String(max); slider.step = String(step);
  slider.value = String(value);
  const num = document.createElement('input');
  num.type = 'number';
  num.min = String(min); num.max = String(max); num.step = String(step);
  num.value = String(value);
  num.className = 'knob-num';
  ctl.append(slider, num);
  root.append(head, ctl);

  let onChange: (v: number) => void = () => {};
  slider.oninput = () => { num.value = slider.value; };
  slider.onchange = () => onChange(Number(slider.value));
  num.onchange = () => onChange(clampNum(Number(num.value), min, max));

  return {
    el: root,
    set: (v) => { slider.value = num.value = String(v); },
    onChange: (fn) => { onChange = fn; },
    setReset: (fn) => { resetBtn.onclick = fn; },
    mark: markFn(root),
  };
}

function knobLabel(text: string, hint: string): HTMLElement {
  const s = el('span', 'knob-label');
  s.textContent = text;
  s.title = hint;
  return s;
}

/** Toggles an "overridden" (non-default) visual state on a knob row. */
function markFn(row: HTMLElement): (on: boolean) => void {
  return (on: boolean) => row.classList.toggle('knob-overridden', on);
}

function resetIcon(onClick: () => void): HTMLButtonElement {
  const b = iconButton('↺', 'Reset to default', onClick);
  b.classList.add('knob-reset');
  return b;
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
  if (d.confidence.present && d.confidence.median !== undefined) line(`confidence median ${d.confidence.median}`);
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
  for (const [cls, c] of Object.entries(diff.perClass).sort()) {
    const row = el('div', 'diff-row');
    row.textContent = `${cls}: +${c.added} / −${c.removed}`;
    host.appendChild(row);
  }
}

// === utils ===============================================================

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
function iconButton(glyph: string, title: string, onClick: () => void): HTMLButtonElement {
  const b = button(glyph, 'icon-btn', onClick);
  b.title = title;
  return b;
}
function clampNum(v: number, min: number, max: number): number {
  return Number.isNaN(v) ? min : Math.max(min, Math.min(max, v));
}
function approxEqual(a: number, b: number): boolean {
  return Math.abs(a - b) < 1e-9;
}
function arraysApproxEqual(a: number[], b: number[]): boolean {
  return a.length === b.length && a.every((v, i) => approxEqual(v, b[i]!));
}
function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
