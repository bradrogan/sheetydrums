import { renderScore } from './render';
import { createYouTubePlayer, type PlayerHandle } from './playback';
import { createAudioPlayer } from './audioPlayer';
import { SyncController } from './sync';
import { setupEditing, editSession } from './edit';
import * as api from './api';
import type { Project, ProjectSummary } from './api';

// === DOM references ===

const listSection = byId('list-section');
const progressSection = byId('progress-section');
const projectSection = byId('project-section');
const progressLog = byId('progress-log');
const progressTitle = byId('progress-title');
const urlForm = byId('url-form') as HTMLFormElement;
const projectGrid = byId('project-grid');
const emptyProjects = byId('empty-projects');
const cancelBtn = byId('cancel-btn');
const backBtn = byId('back-btn');
const pdfBtn = byId('pdf-btn');

function byId(id: string): HTMLElement {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Missing element #${id}`);
  return el;
}

function showOnly(section: HTMLElement): void {
  for (const s of [listSection, progressSection, projectSection]) {
    s.hidden = s !== section;
  }
}

// === Hash routing ===
// `#/`            → projects list
// `#/p/<videoId>` → project view
// The transcription-progress screen is transient imperative state, not a route.

function parseRoute(): { name: 'list' } | { name: 'project'; id: string } {
  const hash = window.location.hash.replace(/^#/, '');
  const m = /^\/p\/([^/]+)$/.exec(hash);
  if (m) return { name: 'project', id: decodeURIComponent(m[1]!) };
  return { name: 'list' };
}

async function route(): Promise<void> {
  const r = parseRoute();
  if (r.name === 'project') {
    await showProject(r.id);
  } else {
    await showList();
  }
}

// The hash we're currently showing. `reverting` guards the programmatic hash
// reset we do when the user cancels leaving an edited project.
let currentHash = window.location.hash || '#/';
let reverting = false;

// Single guard point for ALL hash navigation — in-app buttons, the sheetydrums
// home link, and the browser Back/Forward buttons all funnel through here, so
// leaving an edited project always offers Save/Discard/Cancel.
async function onHashChange(): Promise<void> {
  if (reverting) {
    reverting = false;
    return;
  }
  const target = window.location.hash || '#/';
  if (target === currentHash) return;
  if (!(await editSession.leave())) {
    // Cancelled — restore the URL without re-guarding or re-routing.
    reverting = true;
    window.location.hash = currentHash;
    return;
  }
  currentHash = target;
  await route();
}

function navigate(hash: string): void {
  const h = hash || '#/';
  if ((window.location.hash || '#/') === h) {
    void route(); // same hash — re-run manually (hashchange won't fire)
  } else {
    window.location.hash = h; // → onHashChange (guarded)
  }
}

// === Projects list ===

async function showList(): Promise<void> {
  showOnly(listSection);
  teardownPlayer();
  projectGrid.innerHTML = '';
  let projects: ProjectSummary[];
  try {
    projects = await api.listProjects();
  } catch (err) {
    projectGrid.textContent = `Failed to load projects: ${errMsg(err)}`;
    return;
  }
  emptyProjects.hidden = projects.length > 0;
  for (const p of projects) {
    projectGrid.appendChild(renderCard(p));
  }
}

function renderCard(p: ProjectSummary): HTMLElement {
  const card = document.createElement('div');
  card.className = 'project-card';

  const thumb = document.createElement('img');
  thumb.className = 'thumb';
  thumb.src = p.thumbnail;
  thumb.alt = '';
  thumb.loading = 'lazy';
  thumb.addEventListener('click', () => navigate(`#/p/${encodeURIComponent(p.video_id)}`));
  card.appendChild(thumb);

  const body = document.createElement('div');
  body.className = 'card-body';

  const title = document.createElement('div');
  title.className = 'card-title';
  title.textContent = p.title ?? p.video_id;
  title.addEventListener('click', () => navigate(`#/p/${encodeURIComponent(p.video_id)}`));
  body.appendChild(title);

  const stats = document.createElement('div');
  stats.className = 'card-stats muted';
  const bpm = p.tempo_bpm ? `${p.tempo_bpm.toFixed(0)} BPM · ` : '';
  stats.textContent = `${bpm}${p.n_bars} bars · ${p.n_notes} notes`;
  body.appendChild(stats);

  if (p.updated_at) {
    const updated = document.createElement('div');
    updated.className = 'card-updated muted';
    updated.textContent = `updated ${formatDate(p.updated_at)}`;
    body.appendChild(updated);
  }

  card.appendChild(body);

  const del = document.createElement('button');
  del.className = 'card-delete';
  del.title = 'Delete project';
  del.textContent = '✕';
  del.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!confirm(`Delete "${p.title ?? p.video_id}"? The transcription is removed; the cached audio stays.`)) return;
    try {
      await api.deleteProject(p.video_id);
      card.remove();
      if (projectGrid.children.length === 0) emptyProjects.hidden = false;
    } catch (err) {
      alert(`Delete failed: ${errMsg(err)}`);
    }
  });
  card.appendChild(del);

  return card;
}

// === New transcription ===

urlForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const url = (byId('audio-url') as HTMLInputElement).value.trim();
  if (!url) return;
  const useDrumsep = (byId('url-drumsep') as HTMLInputElement).checked;

  progressTitle.textContent = `Transcribing ${url}…`;
  progressLog.textContent = '';
  showOnly(progressSection);
  appendProgress('Submitting…');

  let resp: api.TranscribeResponse;
  try {
    resp = await api.startTranscribe(url, useDrumsep);
  } catch (err) {
    appendProgress(`Failed to start: ${errMsg(err)}`);
    return;
  }

  if (resp.status === 'exists') {
    appendProgress('Already transcribed — opening project.');
    navigate(`#/p/${encodeURIComponent(resp.project.video_id)}`);
    return;
  }

  activeSource = api.streamJob(resp.job_id, {
    onProgress: appendProgress,
    onResult: (project) => {
      activeSource = null;
      navigate(`#/p/${encodeURIComponent(project.video_id)}`);
    },
    onFailure: (error) => {
      activeSource = null;
      appendProgress(`Failed: ${error}`);
    },
  });
});

let activeSource: EventSource | null = null;

cancelBtn.addEventListener('click', () => {
  if (activeSource) {
    activeSource.close();
    activeSource = null;
  }
  appendProgress('(canceled — the backend job continues until it completes)');
  navigate('#/');
});

function appendProgress(msg: string): void {
  progressLog.textContent += msg + '\n';
  progressLog.scrollTop = progressLog.scrollHeight;
}

// === Project view ===

let currentPlayer: PlayerHandle | null = null;
let currentStemPlayer: PlayerHandle | null = null;

function teardownPlayer(): void {
  for (const p of [currentPlayer, currentStemPlayer]) p?.destroy();
  currentPlayer = null;
  currentStemPlayer = null;
}

async function showProject(videoId: string): Promise<void> {
  showOnly(projectSection);
  teardownPlayer();
  byId('meta').textContent = 'Loading…';
  byId('score').innerHTML = '';
  byId('raw').textContent = '';
  byId('player-wrap').hidden = true;

  let project: Project;
  try {
    project = await api.getProject(videoId);
  } catch (err) {
    byId('meta').textContent = `Failed to load project: ${errMsg(err)}`;
    return;
  }

  const events = project.notation;
  const ts = events.time_signature;
  const titleBits = [
    project.source?.title,
    `${events.tempo_bpm.toFixed(1)} BPM`,
    `${ts.numerator}/${ts.denominator}`,
    `${events.bars.length} bars`,
  ];
  byId('meta').textContent = titleBits.filter(Boolean).join(' · ');
  const model = renderScore(byId('score'), events);
  byId('raw').textContent = JSON.stringify(events, null, 2);

  const sync = new SyncController(model);
  setupEditing({
    project,
    notation: events,
    model,
    sync,
    editToggle: byId('edit-toggle') as HTMLButtonElement,
    saveBtn: byId('save-btn') as HTMLButtonElement,
    scoreEl: byId('score'),
  });
  await setupPlayback(project, model, sync);
}

interface LoopState {
  enabled: boolean;
  lengthSecs: number;
  start: number;
  end: number;
}

async function setupPlayback(
  project: Project,
  _model: ReturnType<typeof renderScore>,
  sync: SyncController,
): Promise<void> {
  const playBtn = byId('playpause-btn') as HTMLButtonElement;
  const back10 = byId('back10-btn') as HTMLButtonElement;
  const fwd10 = byId('fwd10-btn') as HTMLButtonElement;
  const loopToggle = byId('loop-toggle') as HTMLInputElement;
  const loopSecs = byId('loop-secs') as HTMLInputElement;
  const volume = byId('volume') as HTMLInputElement;
  const timeEl = byId('playhead-time');

  const videoId = project.source?.video_id ?? project.video_id;
  if (!videoId) return;

  byId('player-wrap').hidden = false;
  sync.onSeek = (t) => active?.seekTo(t);

  const loop: LoopState = {
    enabled: false,
    lengthSecs: Number(loopSecs.value) || 5,
    start: 0,
    end: 0,
  };

  // The currently-driving player. Task 3 swaps this between YouTube and the
  // drum-stem audio element via bindPlayer().
  let active: PlayerHandle | null = null;

  const handleTick = (t: number): void => {
    // Loop enforcement: when past the region end, jump back to its start.
    if (loop.enabled && active && t >= loop.end - 0.02) {
      active.seekTo(loop.start);
      sync.update(loop.start);
      timeEl.textContent = formatTime(loop.start);
      return;
    }
    sync.update(t);
    timeEl.textContent = formatTime(t);
  };

  function bindPlayer(player: PlayerHandle): void {
    active = player;
    player.onTick(handleTick);
    player.onStateChange((playing) => {
      playBtn.textContent = playing ? '❚❚ Pause' : '▶︎ Play';
    });
    player.setVolume(Number(volume.value));
  }

  let ytPlayer: PlayerHandle;
  try {
    ytPlayer = await createYouTubePlayer(byId('player'), videoId);
  } catch {
    // Player failed to load (offline, blocked) — score still works statically.
    return;
  }
  currentPlayer = ytPlayer;
  bindPlayer(ytPlayer);

  // Drums-only source: only offered when the stem WAV exists for this project.
  const stemRadio = document.querySelector<HTMLInputElement>('input[name="source"][value="stem"]');
  const ytRadio = document.querySelector<HTMLInputElement>('input[name="source"][value="youtube"]');
  let stemPlayer: PlayerHandle | null = null;
  if (!project.has_stem && stemRadio) {
    stemRadio.disabled = true;
    stemRadio.closest('label')?.classList.add('disabled');
  }

  function switchSource(kind: 'youtube' | 'stem'): void {
    const from = active;
    if (kind === 'stem' && !stemPlayer) {
      stemPlayer = createAudioPlayer(
        byId('stem-audio') as HTMLAudioElement,
        `/projects/${encodeURIComponent(videoId)}/drums.wav`,
      );
      currentStemPlayer = stemPlayer;
    }
    const next = kind === 'stem' ? stemPlayer! : ytPlayer;
    if (next === from) return;
    const wasPlaying = from?.isPlaying() ?? false;
    const t = from?.getCurrentTime() ?? 0;
    from?.pause();
    bindPlayer(next);
    next.seekTo(t);
    if (wasPlaying) next.play();
  }
  stemRadio?.addEventListener('change', () => stemRadio.checked && switchSource('stem'));
  ytRadio?.addEventListener('change', () => ytRadio.checked && switchSource('youtube'));

  // Enable controls.
  for (const btn of [playBtn, back10, fwd10]) btn.disabled = false;
  loopToggle.disabled = false;
  playBtn.textContent = '▶︎ Play';

  playBtn.onclick = () => active?.toggle();
  back10.onclick = () => active?.seekBy(-10);
  fwd10.onclick = () => active?.seekBy(10);

  const armLoop = (): void => {
    // Loop the X seconds ending at the current position.
    const end = active ? active.getCurrentTime() : 0;
    loop.end = end;
    loop.start = Math.max(0, end - loop.lengthSecs);
  };
  loopToggle.onchange = () => {
    loop.enabled = loopToggle.checked;
    if (loop.enabled) armLoop();
  };
  loopSecs.onchange = () => {
    loop.lengthSecs = Math.max(1, Number(loopSecs.value) || 5);
    if (loop.enabled) loop.start = Math.max(0, loop.end - loop.lengthSecs);
  };
  volume.oninput = () => active?.setVolume(Number(volume.value));
}

backBtn.addEventListener('click', () => navigate('#/'));
pdfBtn.addEventListener('click', () => window.print());

// === Helpers ===

function errMsg(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function formatTime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

// === Boot ===

// Surface uncaught errors in the console with a clear prefix — makes runtime
// failures (e.g. during playback) visible in dev logs and screenshots.
window.addEventListener('error', (e) => {
  console.error('[sheetydrums] uncaught error:', e.message, e.error?.stack ?? '');
});
window.addEventListener('unhandledrejection', (e) => {
  console.error('[sheetydrums] unhandled rejection:', e.reason);
});

// Warn on refresh / tab close / external navigation with unsaved edits. The
// browser only allows its own generic prompt here (no custom text, no async
// save), so this is a synchronous best-effort check.
window.addEventListener('beforeunload', (e) => {
  if (editSession.dirty) {
    e.preventDefault();
    e.returnValue = '';
  }
});

window.addEventListener('hashchange', () => void onHashChange());
void route();
