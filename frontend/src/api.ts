// Typed wrappers around the backend project API. The backend proxies through
// Vite's dev server (see vite.config.ts), so these are same-origin fetches.
import type { DrumProjectV1 } from './generated/project';
import type { DrumTranscriptionEventsV1Draft } from './generated/events';

export type Project = DrumProjectV1;
export type Notation = DrumTranscriptionEventsV1Draft;

export interface ProjectSummary {
  video_id: string;
  title?: string;
  url?: string;
  thumbnail: string;
  updated_at?: string;
  created_at?: string;
  tempo_bpm?: number;
  n_bars: number;
  n_notes: number;
}

async function ok(resp: Response): Promise<Response> {
  if (!resp.ok) {
    const body = await resp.text().catch(() => '');
    throw new Error(`${resp.status} ${resp.statusText}${body ? `: ${body}` : ''}`);
  }
  return resp;
}

export async function listProjects(): Promise<ProjectSummary[]> {
  const resp = await ok(await fetch('/projects'));
  const { projects } = (await resp.json()) as { projects: ProjectSummary[] };
  return projects;
}

export async function getProject(videoId: string): Promise<Project> {
  const resp = await ok(await fetch(`/projects/${encodeURIComponent(videoId)}`));
  return (await resp.json()) as Project;
}

// Pipeline tuning params — a nested overrides object (backend PipelineParams).
// Kept loose (partial, arbitrary groups) so the frontend needn't restate the
// full catalog; the backend ignores unknown keys and fills defaults.
export type PipelineParams = Record<string, Record<string, unknown>>;

// PUT /projects/{id} writes the project's BASE layer. `project.notation` from a
// GET is the *composed effective* layer (base + system pass + verified
// selections), so these two entry points are deliberately separate rather than
// one function with a flag — sending the composed notation back would flatten
// the layers into the base, and the backend 409s on exactly that.
//
// Use for genuinely new generator output only: a retune preview the user
// accepted. `layer: 'base'` is the backend's required acknowledgement of that.
export async function acceptRetune(
  videoId: string,
  notation: Notation,
  params: PipelineParams,
): Promise<Project> {
  const resp = await ok(
    await fetch(`/projects/${encodeURIComponent(videoId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notation, params, layer: 'base' }),
    }),
  );
  return (await resp.json()) as Project;
}

// Use for hand edits to the score. Sends no `layer` acknowledgement, so once a
// project has verified selections or a system pass the backend rejects it and
// the edit has to go through the /selections endpoints instead (Phase 2d) —
// which is the point: it fails loudly rather than silently flattening.
export async function saveNotation(videoId: string, notation: Notation): Promise<Project> {
  const resp = await ok(
    await fetch(`/projects/${encodeURIComponent(videoId)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notation }),
    }),
  );
  return (await resp.json()) as Project;
}

// Notation diagnostics (GET /projects/{id}/diagnose). Shape mirrors
// diagnostics.diagnose(); kept loose since it's display-only.
export interface Diagnostics {
  tempo_bpm: number | null;
  time_signature: string | null;
  n_bars: number;
  n_notes: number;
  notes_per_bar: number;
  per_class: Record<string, number>;
  hats: { closed: number; open: number; open_fraction: number | null };
  confidence: { present: boolean; min?: number; median?: number; mean?: number };
  empty_bars: number;
  flags: string[];
}

export async function diagnose(videoId: string): Promise<Diagnostics> {
  const resp = await ok(await fetch(`/projects/${encodeURIComponent(videoId)}/diagnose`));
  return (await resp.json()) as Diagnostics;
}

// A re-tune preview (terminal `result` event of a retune job). NOT yet saved —
// the caller renders the diff and either accepts (acceptRetune) or discards.
export interface RetunePreview {
  preview: true;
  video_id: string;
  notation: Notation;
  params: PipelineParams;
}

// POST /projects/{id}/retune → a job whose stream carries a RetunePreview.
export async function startRetune(videoId: string, params: PipelineParams): Promise<string> {
  const resp = await ok(
    await fetch(`/projects/${encodeURIComponent(videoId)}/retune`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ params }),
    }),
  );
  const { job_id } = (await resp.json()) as { job_id: string };
  return job_id;
}

export interface RetuneCallbacks {
  onProgress: (msg: string) => void;
  onResult: (preview: RetunePreview) => void;
  onFailure: (error: string) => void;
}

// Same SSE shape as streamJob, but the terminal `result` is a preview payload.
export function streamRetune(jobId: string, cb: RetuneCallbacks): EventSource {
  return streamJob(jobId, {
    onProgress: cb.onProgress,
    onResult: (payload) => cb.onResult(payload as unknown as RetunePreview),
    onFailure: cb.onFailure,
  });
}

export async function deleteProject(videoId: string): Promise<void> {
  await ok(await fetch(`/projects/${encodeURIComponent(videoId)}`, { method: 'DELETE' }));
}

// === Tuning Phase 2: verified selections (user layer) + layered read ===

type SchemaClass = Notation['bars'][number]['notes'][number]['instrument'];
export type NoteObj = Notation['bars'][number]['notes'][number];
/** A frozen note plus the bar it belongs to (a within-bar position is ambiguous
 * across a multi-bar region). Mirrors the backend RegionNote. */
export interface RegionNote {
  bar: number;
  note: NoteObj;
}

/** One anchored edit. Positions are exact rational strings ("1/4"), never
 * sixteenth indices, matching the backend Op vocabulary. */
export type Op =
  | { kind: 'add'; bar: number; position: string; instrument: SchemaClass; duration: string }
  | { kind: 'delete'; bar: number; position: string; instrument: SchemaClass }
  | { kind: 'reclassify'; bar: number; position: string; from: SchemaClass; to: SchemaClass }
  | { kind: 'move'; bar: number; from_position: string; to_position: string; instrument: SchemaClass };

export interface Selection {
  selection_id: string;
  origin: 'user';
  lane: string;
  bar_start: number;
  bar_end: number;
  notes: RegionNote[];
  ops: Op[];
  verified: boolean;
  base_fingerprint?: string;
  created_at?: string;
}

/** origin_map is keyed by bar index; JSON object keys are strings even though
 * the backend keys them by int, so read `origin_map[String(barIndex)]`. */
export type OriginMap = Record<string, ('base' | 'system' | 'user')[]>;

export interface Layers {
  base: Notation;
  system_layer: { pass_id: string; ops: Op[] } | null;
  selections: Selection[];
  effective: Notation;
  origin_map: OriginMap;
}

export interface SelectionInput {
  lane: string;
  bar_start: number;
  bar_end: number;
  notes: RegionNote[];
  ops: Op[];
}

export async function getLayers(videoId: string): Promise<Layers> {
  const resp = await ok(await fetch(`/projects/${encodeURIComponent(videoId)}/layers`));
  return (await resp.json()) as Layers;
}

export async function createSelection(videoId: string, body: SelectionInput): Promise<Selection> {
  const resp = await ok(
    await fetch(`/projects/${encodeURIComponent(videoId)}/selections`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    }),
  );
  return (await resp.json()) as Selection;
}

export async function updateSelection(
  videoId: string,
  selectionId: string,
  body: SelectionInput,
): Promise<Selection> {
  const resp = await ok(
    await fetch(
      `/projects/${encodeURIComponent(videoId)}/selections/${encodeURIComponent(selectionId)}`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      },
    ),
  );
  return (await resp.json()) as Selection;
}

export async function deleteSelection(videoId: string, selectionId: string): Promise<void> {
  await ok(
    await fetch(
      `/projects/${encodeURIComponent(videoId)}/selections/${encodeURIComponent(selectionId)}`,
      { method: 'DELETE' },
    ),
  );
}

export async function clearSystemPass(videoId: string): Promise<void> {
  await ok(await fetch(`/projects/${encodeURIComponent(videoId)}/system`, { method: 'DELETE' }));
}

// === Settings: where projects are stored ===
export interface Settings {
  projects_dir: string;
  default_projects_dir: string;
  project_count: number;
}

export async function getSettings(): Promise<Settings> {
  const resp = await ok(await fetch('/settings'));
  return (await resp.json()) as Settings;
}

// Read-only directory listing for the projects-dir picker (GET /fs/list).
export interface DirListing {
  path: string;
  parent: string | null;
  entries: { name: string; path: string }[];
  writable: boolean;
}

export async function listDir(path?: string): Promise<DirListing> {
  const q = path ? `?path=${encodeURIComponent(path)}` : '';
  const resp = await ok(await fetch(`/fs/list${q}`));
  return (await resp.json()) as DirListing;
}

export async function updateSettings(projectsDir: string, moveExisting: boolean): Promise<Settings> {
  const resp = await ok(
    await fetch('/settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ projects_dir: projectsDir, move_existing: moveExisting }),
    }),
  );
  return (await resp.json()) as Settings;
}

// POST /transcribe returns either an already-stored project or a started job.
export type TranscribeResponse =
  | { status: 'exists'; project: Project }
  | { status: 'job'; job_id: string };

export async function startTranscribe(url: string): Promise<TranscribeResponse> {
  const resp = await ok(
    await fetch('/transcribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }), // DrumSep is always on; backend defaults use_drumsep=true
    }),
  );
  return (await resp.json()) as TranscribeResponse;
}

export interface JobCallbacks {
  onProgress: (msg: string) => void;
  onResult: (project: Project) => void;
  onFailure: (error: string) => void;
}

// Open an SSE stream for a running job. Returns the EventSource so the caller
// can .close() it (e.g. on cancel). Terminal events close it internally.
export function streamJob(jobId: string, cb: JobCallbacks): EventSource {
  const es = new EventSource(`/jobs/${jobId}/stream`);
  let settled = false;
  const finish = (): void => {
    if (settled) return;
    settled = true;
    es.close();
  };

  es.addEventListener('progress', (e) => {
    const { msg } = JSON.parse((e as MessageEvent).data) as { msg: string };
    cb.onProgress(msg);
  });
  es.addEventListener('result', (e) => {
    const project = JSON.parse((e as MessageEvent).data) as Project;
    cb.onResult(project);
    finish();
  });
  es.addEventListener('failure', (e) => {
    const { error } = JSON.parse((e as MessageEvent).data) as { error: string };
    cb.onFailure(error);
    finish();
  });
  es.onerror = () => {
    // EventSource auto-reconnects on transient blips. Only treat a fully closed
    // stream as a hard error if we haven't already settled on a terminal event.
    if (es.readyState === EventSource.CLOSED && !settled) {
      cb.onFailure('Stream closed unexpectedly.');
      finish();
    }
  };
  return es;
}
