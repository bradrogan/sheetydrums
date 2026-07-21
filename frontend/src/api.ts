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

export async function deleteProject(videoId: string): Promise<void> {
  await ok(await fetch(`/projects/${encodeURIComponent(videoId)}`, { method: 'DELETE' }));
}

// POST /transcribe returns either an already-stored project or a started job.
export type TranscribeResponse =
  | { status: 'exists'; project: Project }
  | { status: 'job'; job_id: string };

export async function startTranscribe(
  url: string,
  useDrumsep: boolean,
): Promise<TranscribeResponse> {
  const resp = await ok(
    await fetch('/transcribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url, use_drumsep: useDrumsep }),
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
