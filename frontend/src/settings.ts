// Settings dialog: choose where projects are stored, with an option to move
// existing project files to the new folder. The path is a server-side absolute
// path (the backend is local) — browsers can't hand a real filesystem path from
// a native picker to the server, so it's a text field.
import * as api from './api';

/** Open the settings modal. Resolves true if the projects dir was changed. */
export function openSettings(onChanged: () => void): void {
  const overlay = document.createElement('div');
  overlay.className = 'edit-modal-overlay';
  const box = document.createElement('div');
  box.className = 'edit-modal settings-modal';
  overlay.appendChild(box);

  box.innerHTML = '<h3>Project storage</h3><p class="muted settings-loading">Loading…</p>';
  document.body.appendChild(overlay);

  const close = (): void => overlay.remove();
  overlay.addEventListener('mousedown', (e) => {
    if (e.target === overlay) close();
  });

  void build();

  async function build(): Promise<void> {
    let settings: api.Settings;
    try {
      settings = await api.getSettings();
    } catch (err) {
      box.innerHTML = '';
      const h = el('h3', '', 'Project storage');
      const p = el('p', 'settings-error', `Couldn't load settings: ${errMsg(err)}`);
      const row = el('div', 'edit-modal-row');
      row.appendChild(button('Close', '', close));
      box.append(h, p, row);
      return;
    }

    box.innerHTML = '';
    box.appendChild(el('h3', '', 'Project storage'));
    box.appendChild(el('p', 'muted', `${settings.project_count} project(s) currently stored in:`));

    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'settings-path';
    input.value = settings.projects_dir;
    input.spellcheck = false;
    input.setAttribute('autocomplete', 'off');
    box.appendChild(input);

    const hint = el('p', 'muted settings-hint',
      `Absolute path on this machine. Default: ${settings.default_projects_dir}`);
    box.appendChild(hint);

    const moveLabel = document.createElement('label');
    moveLabel.className = 'settings-move';
    const move = document.createElement('input');
    move.type = 'checkbox';
    move.checked = true;
    moveLabel.append(move, document.createTextNode(' Move existing projects to the new folder'));
    box.appendChild(moveLabel);

    const status = el('p', 'settings-status muted', '');
    box.appendChild(status);

    const row = el('div', 'edit-modal-row');
    const cancel = button('Cancel', '', close);
    const save = button('Save', 'primary', () => void doSave());
    row.append(cancel, save);
    box.appendChild(row);

    input.focus();
    input.select();

    async function doSave(): Promise<void> {
      const dir = input.value.trim();
      if (!dir) {
        status.textContent = 'Enter a directory path.';
        status.className = 'settings-status settings-error';
        return;
      }
      if (dir === settings.projects_dir) {
        close();
        return;
      }
      save.disabled = true;
      cancel.disabled = true;
      status.className = 'settings-status muted';
      status.textContent = move.checked ? 'Moving projects…' : 'Saving…';
      try {
        await api.updateSettings(dir, move.checked);
        close();
        onChanged();
      } catch (err) {
        save.disabled = false;
        cancel.disabled = false;
        status.className = 'settings-status settings-error';
        status.textContent = errMsg(err);
      }
    }
  }
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
  if (cls) b.className = cls;
  b.onclick = onClick;
  return b;
}

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
