// "Fix the rest of the song for this" (Phase 3d). A launcher in the edit hub
// runs the edit-scored parameter search over the user's verified sections
// (backend POST /fix-the-rest), previews the proposed system pass through the
// edit layer (so verified sections stay overlaid + changed bars highlight, same
// as a re-tune preview), and applies it as a superseding system layer or
// discards it. See docs/design/phase3-plan.md §3d.
import * as api from './api';
import type { Project } from './api';
import type { EditHandle } from './edit';

export interface FixContext {
  project: Project;
  editHandle: EditHandle;
  /** The live verified-selections array (edit.ts mutates it in place) — the
   * search learns from these, so the launcher is a no-op with none. */
  selections: api.Selection[];
  /** Re-open the project (re-fetch + re-render) after applying/removing a pass. */
  reload: () => void;
}

export function setupFixTheRest(ctx: FixContext): void {
  const toggle = byId('fixrest-toggle');
  const panel = byId('fixrest-panel');
  panel.classList.add('fixrest-panel');
  // Reset per project (the DOM is shared across navigation).
  panel.hidden = true;
  panel.innerHTML = '';
  toggle.classList.remove('active');

  let source: EventSource | null = null;
  let preview: api.FixPreview | null = null;

  const dropPreview = (): void => {
    source?.close();
    source = null;
    if (preview) {
      ctx.editHandle.clearPreview();
      preview = null;
    }
  };
  const close = (): void => {
    dropPreview();
    panel.hidden = true;
    panel.innerHTML = '';
    toggle.classList.remove('active');
  };

  toggle.onclick = () => {
    if (!panel.hidden) {
      close();
      return;
    }
    panel.hidden = false;
    toggle.classList.add('active');
    if ((ctx.project.system_layer ?? null) !== null) renderApplied();
    else if (ctx.selections.length === 0) renderNeedsSelections();
    else void run();
  };

  // --- panel states ------------------------------------------------------

  function shell(): { status: HTMLElement; actions: HTMLElement } {
    panel.innerHTML = '';
    panel.appendChild(el('div', 'fixrest-head', 'Fix the rest of the song'));
    const status = el('div', 'fixrest-status muted');
    const actions = el('div', 'fixrest-actions');
    panel.append(status, actions);
    return { status, actions };
  }

  function renderNeedsSelections(): void {
    const { status, actions } = shell();
    status.textContent =
      'Verify a correction first (select a lane over some bars, fix it, Verify). ' +
      'Then this can learn the fix and apply it everywhere similar.';
    actions.appendChild(button('Close', 'ghost', close));
  }

  function renderApplied(): void {
    const { status, actions } = shell();
    const n = ctx.project.system_layer?.ops?.length ?? 0;
    status.textContent = `A fix is applied (${n} change${n === 1 ? '' : 's'}). Re-run to refresh it, or remove it.`;
    actions.appendChild(button('Re-run', 'primary', () => void run()));
    actions.appendChild(button('Remove fix', 'ghost', () => void doRemove()));
    actions.appendChild(button('Close', 'ghost', close));
  }

  async function run(): Promise<void> {
    dropPreview();
    const { status, actions } = shell();
    status.classList.add('running');
    status.textContent = 'Starting search…';
    actions.appendChild(button('Cancel', 'ghost', close));
    let jobId: string;
    try {
      jobId = await api.startFixTheRest(ctx.project.video_id);
    } catch (err) {
      status.classList.remove('running');
      status.textContent = `Couldn't start: ${msg(err)}`;
      return;
    }
    source = api.streamFixTheRest(jobId, {
      onProgress: (m) => { status.textContent = m; },
      onResult: (p) => {
        source = null;
        preview = p;
        status.classList.remove('running');
        const d = ctx.editHandle.preview(p.effective); // overlay + highlight changed bars, in place
        renderPreviewReady(d.added, d.removed, d.changedBars.length);
      },
      onFailure: (error) => {
        source = null;
        status.classList.remove('running');
        status.textContent = `Search failed: ${error}`;
      },
    });
  }

  function renderPreviewReady(added: number, removed: number, bars: number): void {
    if (!preview) return;
    const { status, actions } = shell();
    const lanes = preview.targeted_lanes.join(', ') || 'no';
    const fit = preview.converged ? 'matches' : 'best partial for';
    status.innerHTML = '';
    status.appendChild(el('div', '',
      `Preview — not applied yet. +${added} / −${removed} across ${bars} bar(s) in the ${lanes} lane(s).`));
    status.appendChild(el('div', 'muted',
      `This ${fit} your verified sections (F1 ${Math.round(preview.score_f1 * 100)}%). ` +
      'Changed bars are highlighted; your verified sections are kept.'));
    actions.appendChild(button('Apply', 'accept', () => void doApply()));
    actions.appendChild(button('Discard', 'ghost', () => { dropPreview(); close(); }));
  }

  async function doApply(): Promise<void> {
    if (!preview) return;
    const { status } = shell();
    status.textContent = 'Applying…';
    try {
      await api.applySystemPass(ctx.project.video_id, {
        pass_id: preview.pass_id, ops: preview.ops, params: preview.params,
      });
      preview = null; // reload rebuilds from the persisted layer
      ctx.reload();
    } catch (err) {
      status.textContent = `Apply failed: ${msg(err)}`;
    }
  }

  async function doRemove(): Promise<void> {
    const { status } = shell();
    status.textContent = 'Removing…';
    try {
      await api.clearSystemPass(ctx.project.video_id);
      ctx.reload();
    } catch (err) {
      status.textContent = `Remove failed: ${msg(err)}`;
    }
  }
}

// --- tiny DOM helpers (local, to avoid coupling to edit.ts's) -------------

function byId(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} missing`);
  return el;
}
function el(tag: string, className: string, text = ''): HTMLElement {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text) e.textContent = text;
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
function msg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
