"""HTTP server for the sheetydrums web app (YouTube-only projects).

A *project* pairs a YouTube source with its drum transcription and is persisted
via `store.py`, keyed by video id. Endpoints:

  POST   /transcribe             {url}     → open existing project or start a job
  GET    /jobs/{id}/stream                 → SSE progress, terminal event carries project/preview
  GET    /projects                         → list summaries
  GET    /projects/{id}                    → full project (notation = composed effective layer)
  GET    /projects/{id}/layers             → {base, system_layer, selections, effective,
                                              origin_map} (origin_map keyed by bar-index string)
  GET    /projects/{id}/diagnose           → diagnostics on the effective notation
  POST   /projects/{id}/retune  {params}   → re-run with new params, stream a preview (job)
  PUT    /projects/{id}          {notation,params?,layer?} → save BASE notation
                                              (retune-accept path; needs layer="base" once
                                              the project has layers — see the handler)
  POST   /projects/{id}/selections {lane,bar_start,bar_end,notes,ops} → create a verified selection
  PUT    /projects/{id}/selections/{sid}   → edit a verified selection
  DELETE /projects/{id}/selections/{sid}   → undo a verified selection
  DELETE /projects/{id}/system             → clear the system pass (user edits untouched)
  DELETE /projects/{id}                    → remove
  GET    /settings                         → projects-dir settings
  PUT    /settings   {projects_dir, move_existing?} → change projects dir (opt. move files)

Single-user local-dev server. Job state lives in process memory; projects live
on disk. The pipeline is blocking PyTorch code, so each job runs on a dedicated
thread and its stage logs are pushed through a thread-safe queue that the SSE
generator pulls from via `asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio
import json
import queue
import re
import threading
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

import jsonschema
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from sheetydrums import store
from sheetydrums.anchor import region_fingerprint
from sheetydrums.layering import compose


# Sentinel pushed onto a job's progress queue when the worker is done.
# Distinct from None so a malformed progress line ("") can't be confused with done.
_DONE: object = object()


@dataclass
class JobState:
    progress: queue.Queue[Any] = field(default_factory=queue.Queue)
    done: bool = False
    result: dict[str, Any] | None = None  # the saved project on success
    error: str | None = None


_jobs: dict[str, JobState] = {}

# Cap on retained jobs; finished ones are evicted once we exceed this so the
# in-memory map doesn't grow forever. Generous enough that a stream can still
# reconnect to a recently-finished job.
_MAX_JOBS: int = 32

# Serializes *registering* a job against a projects-dir migration. Checking
# `_jobs` and then awaiting the move is not enough on its own: the await yields
# the loop for the whole migration (potentially a cross-volume copy of every
# stem), and a job starting in that window binds its paths in the old directory
# while `save_project` re-resolves to the new one — splitting the project across
# both. With this lock the two orderings are the only ones possible: either the
# job lands in `_jobs` first and the migration sees it and 409s, or the
# migration holds the lock and the job waits for it. Job registration holds the
# lock only briefly, so there is no meaningful contention.
_migration_lock: asyncio.Lock = asyncio.Lock()

# Guards one project's load → modify → save window, as used by the /selections
# and /system endpoints. Those handlers have no `await` between the load and the
# save today, so the event loop cannot interleave two of them and this lock is
# currently uncontended — it is here so that introducing an await (or moving the
# disk write to `asyncio.to_thread`) inside one of those windows cannot silently
# turn a read-modify-write into a lost update. A threading lock rather than an
# asyncio one because the job worker threads also write projects through
# `store`. One lock per video id touched in this process: a handful per session,
# and each is a few dozen bytes, so they are not pruned.
_project_locks: dict[str, threading.Lock] = {}
_project_locks_guard: threading.Lock = threading.Lock()


def _project_lock(video_id: str) -> threading.Lock:
    with _project_locks_guard:
        return _project_locks.setdefault(video_id, threading.Lock())


app: FastAPI = FastAPI(title="sheetydrums")
# CORS is limited to the Vite dev origins rather than "*". Binding to 127.0.0.1
# keeps other machines out but not other *pages*: nothing here is authenticated,
# and a JSON `PUT /settings` preflights — which "*" would approve — so any site
# the user happens to have open could relocate the whole project store (or
# DELETE a project). Normal use goes through the Vite proxy and is same-origin,
# so this list only matters when hitting the API directly in development.
_DEV_ORIGINS: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_DEV_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


_YT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def youtube_id(url: str) -> str | None:
    """Best-effort parse of a YouTube video id from a URL.

    Handles watch?v=, youtu.be/, /embed/, /shorts/, /live/. Returns None if the
    URL doesn't match a known shape — callers fall back to yt-dlp's id after
    download.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "youtu.be":
        candidate = parsed.path.lstrip("/").split("/", 1)[0]
        return candidate if _YT_ID_RE.match(candidate) else None
    if host in ("youtube.com", "m.youtube.com", "music.youtube.com"):
        if parsed.path == "/watch":
            vals = parse_qs(parsed.query).get("v", [])
            return vals[0] if vals and _YT_ID_RE.match(vals[0]) else None
        for prefix in ("/embed/", "/shorts/", "/live/", "/v/"):
            if parsed.path.startswith(prefix):
                candidate = parsed.path[len(prefix):].split("/", 1)[0]
                return candidate if _YT_ID_RE.match(candidate) else None
    return None


class TranscribeRequest(BaseModel):
    url: str
    use_drumsep: bool = True


@app.post("/transcribe")
async def transcribe(req: TranscribeRequest = Body(...)) -> dict[str, Any]:
    """Open an existing project for the URL, or kick off a transcription job.

    Returns one of:
      - {"status": "exists", "project": <project>}  — already transcribed
      - {"status": "job", "job_id": ..., "video_id": ...} — job started
    """
    url = req.url.strip()
    if not url:
        raise HTTPException(400, "Provide a YouTube `url`.")

    # Fast path: if we can parse the id and already have the project, skip the
    # download + pipeline entirely (no job needed).
    parsed_id = youtube_id(url)
    if parsed_id is not None:
        existing = store.load_project(parsed_id)
        if existing is not None:
            return {"status": "exists", "project": existing}

    # Otherwise start a job immediately and return. The download runs on the
    # worker thread (with streamed progress) rather than blocking this request —
    # a fresh, uncached video can take a while to fetch. The frontend gets the
    # authoritative video id from the project in the terminal `result` event.
    job_id: str = uuid.uuid4().hex
    job: JobState = JobState()
    # Registered under the migration lock so a projects-dir switch can't start
    # between this job appearing and its worker resolving store paths.
    async with _migration_lock:
        _prune_jobs()
        _jobs[job_id] = job
        threading.Thread(
            target=_run_job,
            args=(job, url, req.use_drumsep),
            daemon=True,
        ).start()

    return {"status": "job", "job_id": job_id}


def _prune_jobs() -> None:
    """Evict finished jobs so `_jobs` doesn't grow without bound across runs.
    Active (not-done) jobs are always kept."""
    if len(_jobs) < _MAX_JOBS:
        return
    for job_id in [jid for jid, j in _jobs.items() if j.done]:
        if len(_jobs) < _MAX_JOBS:
            break
        _jobs.pop(job_id, None)


def _run_job(job: JobState, url: str, use_drumsep: bool) -> None:
    """Worker thread: download, transcribe, wrap in a project, persist it."""
    # Lazy import — pulls in torch / demucs / etc. (and yt-dlp), which we don't
    # want loading on the uvicorn startup path.
    from sheetydrums.cli import serialize_to_schema
    from sheetydrums.config import CLIConfig
    from sheetydrums.factory import build_pipeline
    from sheetydrums.fetch import download_audio
    from sheetydrums.params import PipelineParams

    def on_progress(msg: str) -> None:
        job.progress.put(msg)

    try:
        on_progress("downloading audio…")
        downloaded = download_audio(url)

        # A non-parseable URL may still resolve to an already-transcribed video;
        # short-circuit once we know the authoritative id.
        existing = store.load_project(downloaded.video_id)
        if existing is not None:
            job.result = existing
            on_progress("already transcribed — opening project")
            return

        on_progress(f"loading pipeline (use_drumsep={use_drumsep})…")
        config: CLIConfig = CLIConfig(use_drumsep=use_drumsep, verbose=False)
        # Default params for now; the tuning loop will vary + persist these so a
        # project records exactly the params that produced its notation.
        params = PipelineParams()
        pipeline = build_pipeline(
            config,
            params=params,
            cache_dir=store.stages_dir(downloaded.video_id),
            on_progress=on_progress,
        )
        result = pipeline.transcribe(
            downloaded.path,
            drum_stem_path=store.stem_path(downloaded.video_id),
            drumless_path=store.drumless_path(downloaded.video_id),
        )
        notation: dict[str, Any] = serialize_to_schema(result)
        project: dict[str, Any] = store.save_project(
            {
                "video_id": downloaded.video_id,
                "source": {
                    "url": url,
                    "video_id": downloaded.video_id,
                    "title": downloaded.title,
                },
                "notation": notation,
                "params": params.to_dict(),
            }
        )
        job.result = project
        n_notes = sum(len(b["notes"]) for b in notation["bars"])
        on_progress(f"done: {n_notes} notes / {len(notation['bars'])} bars")
    except Exception as exc:
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        job.done = True
        job.progress.put(_DONE)


def _run_retune_job(job: JobState, video_id: str, params_dict: dict[str, Any]) -> None:
    """Worker thread: re-run the pipeline for an existing project with new params
    (cache-accelerated — a late-stage tweak reuses Demucs/DrumSep/ADTOF/Beat-This
    from the stage cache) and stash the *preview* notation on the job WITHOUT
    saving. The client accepts (PUT) or discards it."""
    from sheetydrums.cli import serialize_to_schema
    from sheetydrums.config import CLIConfig
    from sheetydrums.factory import build_pipeline
    from sheetydrums.fetch import download_audio
    from sheetydrums.params import PipelineParams

    def on_progress(msg: str) -> None:
        job.progress.put(msg)

    try:
        project = store.load_project(video_id)
        if project is None:  # deleted between request and worker start
            job.error = f"No project for video_id {video_id!r}"
            return
        url = (project.get("source") or {}).get("url")
        if not url:
            job.error = "Project has no source URL to re-fetch audio from."
            return

        on_progress("preparing audio…")
        downloaded = download_audio(url)  # idempotent — hits the local cache
        params = PipelineParams.from_dict(params_dict)
        # Web projects are always transcribed with DrumSep on (the /transcribe
        # default), so re-tune the same way.
        config: CLIConfig = CLIConfig(use_drumsep=True, verbose=False)
        pipeline = build_pipeline(
            config,
            params=params,
            cache_dir=store.stages_dir(video_id),
            on_progress=on_progress,
        )
        # No stem paths — the drum-stem/drumless already exist; don't rewrite them.
        result = pipeline.transcribe(downloaded.path)
        notation: dict[str, Any] = serialize_to_schema(result)
        job.result = {
            "preview": True,
            "video_id": video_id,
            "notation": notation,
            "params": params.to_dict(),
        }
        n_notes = sum(len(b["notes"]) for b in notation["bars"])
        on_progress(f"done: preview {n_notes} notes / {len(notation['bars'])} bars")
    except Exception as exc:
        job.error = f"{type(exc).__name__}: {exc}"
    finally:
        job.done = True
        job.progress.put(_DONE)


@app.get("/jobs/{job_id}/stream")
async def stream_job(job_id: str) -> StreamingResponse:
    """SSE stream of progress messages followed by a single terminal event.

    Emits:
      - `event: progress` `data: {"msg": "..."}` per stage log line.
      - `event: result`   `data: <project>` on success.
      - `event: failure`  `data: {"error": "..."}` on failure.
    """
    job: JobState | None = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job_id")

    async def gen() -> AsyncGenerator[str, None]:
        # Fast path for reconnects after the job is already finished: skip the
        # queue (a previous stream consumed it) and emit the terminal event.
        if job.done:
            yield _terminal_event(job)
            return
        while True:
            item: Any = await asyncio.to_thread(job.progress.get)
            if item is _DONE:
                break
            yield f"event: progress\ndata: {json.dumps({'msg': item})}\n\n"
        yield _terminal_event(job)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        # Disable proxy buffering — Vite's dev proxy generally doesn't buffer
        # SSE, but this is the canonical hint for any intermediary that does.
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _terminal_event(job: JobState) -> str:
    if job.error is not None:
        return f"event: failure\ndata: {json.dumps({'error': job.error})}\n\n"
    return f"event: result\ndata: {json.dumps(job.result)}\n\n"


# === Settings (projects directory) ===


def _settings_payload() -> dict[str, Any]:
    return {
        "projects_dir": str(store.get_projects_dir()),
        "default_projects_dir": str(store.default_projects_dir()),
        "project_count": len(store.list_projects()),
    }


@app.get("/settings")
async def get_settings() -> dict[str, Any]:
    # _settings_payload reads every project file for the count — off the loop.
    return await asyncio.to_thread(_settings_payload)


class UpdateSettings(BaseModel):
    projects_dir: str
    move_existing: bool = False


@app.put("/settings")
async def update_settings(req: UpdateSettings = Body(...)) -> dict[str, Any]:
    """Change where projects are stored. With `move_existing`, this store's own
    project files are moved into the new directory first (unrelated files in the
    old directory are left alone). 409s while a job is in flight."""
    path = req.projects_dir.strip()
    if not path:
        raise HTTPException(400, "Provide a `projects_dir`.")
    # A worker thread resolves some paths up front and others (save_project) at
    # the end, so switching the store mid-job would split a project across both
    # directories — and the move could race a `.stages` dir being written.
    # The lock spans the check *and* the move: checking first and then awaiting
    # would leave the whole migration open for a new job to start in.
    async with _migration_lock:
        if any(not job.done for job in _jobs.values()):
            raise HTTPException(
                409, "A job is still running — wait for it to finish before moving projects."
            )
        try:
            # Blocking I/O: moving projects can be a cross-volume copy of every
            # stem and stage artifact, which would stall in-flight SSE streams.
            await asyncio.to_thread(store.set_projects_dir, path, req.move_existing)
        except (ValueError, FileExistsError, OSError) as exc:
            raise HTTPException(400, str(exc)) from exc
    return await asyncio.to_thread(_settings_payload)


# === Project CRUD ===


def _load_or_404(video_id: str) -> dict[str, Any]:
    project = store.load_project(video_id)
    if project is None:
        raise HTTPException(404, f"No project for video_id {video_id!r}")
    return project


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compose_or_409(project: dict[str, Any]) -> tuple[dict[str, Any], dict[int, list[str]]]:
    """Compose a project's layers for a read, turning a composition failure into
    a 409 instead of an unhandled 500.

    `compose` validates the notation it builds, so a stored layer that composes
    to something invalid takes out *every* read path for the project at once —
    and the only recovery (DELETE a selection or the system layer) is then an
    endpoint the client cannot discover, because listing the layers is itself a
    read. The write paths reject such a layer up front (`_save_selections_or_422`),
    so this is the backstop for a record that predates that check or was written
    by hand; it says which endpoint clears it.
    """
    try:
        return compose(
            project.get("notation") or {},
            project.get("system_layer"),
            project.get("selections") or [],
        )
    except (jsonschema.ValidationError, ValueError) as exc:
        video_id = project.get("video_id")
        raise HTTPException(
            409,
            f"Project {video_id!r} has a layer that composes to an invalid "
            f"notation: {exc} Remove the offending selection "
            f"(DELETE /projects/{video_id}/selections/{{sid}}) or the system "
            f"layer (DELETE /projects/{video_id}/system).",
        ) from exc


@app.get("/projects")
async def list_projects() -> dict[str, Any]:
    return {"projects": store.list_projects()}


@app.get("/projects/{video_id}")
async def get_project(video_id: str) -> dict[str, Any]:
    project = _load_or_404(video_id)
    # `notation` the client renders/plays is the COMPOSED effective layer
    # (base + system + verified selections); the raw generator output stays
    # available as `base_notation` for the retune-accept path. For a legacy
    # project (no layers) effective == base, so this is behaviour-neutral.
    effective, _ = _compose_or_409(project)
    return {
        **project,
        "notation": effective,
        "base_notation": project.get("notation"),
        # Runtime-only flags (not persisted): which alternate audio tracks exist.
        "has_stem": store.has_stem(video_id),
        "has_drumless": store.has_drumless(video_id),
    }


@app.get("/projects/{video_id}/diagnose")
async def diagnose_project(video_id: str) -> dict[str, Any]:
    """Structured diagnostics for the project's EFFECTIVE notation (per-class
    counts, hi-hat balance, confidence, empty bars, heuristic flags). Runs on the
    composed layer so diagnostics reflect the user's corrections, not the raw
    generator output."""
    from sheetydrums.diagnostics import diagnose

    project = _load_or_404(video_id)
    effective, _ = _compose_or_409(project)
    return diagnose(effective)


@app.get("/projects/{video_id}/drums.wav")
async def get_drum_stem(video_id: str) -> FileResponse:
    """Serve the isolated drum-stem WAV for drums-only playback."""
    path = store.stem_path(video_id)
    if not path.exists():
        raise HTTPException(404, "No drum stem for this project.")
    return FileResponse(path, media_type="audio/wav")


@app.get("/projects/{video_id}/drumless.wav")
async def get_drumless(video_id: str) -> FileResponse:
    """Serve the drumless backing track (mix minus drums)."""
    path = store.drumless_path(video_id)
    if not path.exists():
        raise HTTPException(404, "No drumless track for this project.")
    return FileResponse(path, media_type="audio/wav")


@app.put("/projects/{video_id}")
async def save_project(video_id: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Save BASE notation for an existing project — the retune-accept path.

    Body: {"notation": <events>, "params"?: {...}, "layer"?: "base"}. This
    replaces the base layer (the generator output); user corrections live in the
    user layer via the /selections endpoints and are preserved here untouched.
    Validates before persisting; preserves the project's source + created_at +
    selections.

    `notation` on a GET response is the *composed effective* layer, so PUTting
    that back would flatten the layers into the base. Once a project has any
    layer this endpoint therefore requires an explicit `"layer": "base"` to
    confirm the body really is fresh generator output. See the 409 below.
    """
    existing = store.load_project(video_id)
    if existing is None:
        raise HTTPException(404, f"No project for video_id {video_id!r}")
    notation = body.get("notation")
    if notation is None:
        raise HTTPException(400, "Body must contain `notation`.")
    # Flatten guard. A client that GETs a project and PUTs its `notation` back is
    # writing the composed layers into the base, and every part of that is lossy
    # and silent: the raw generator output is gone, each selection's
    # base_fingerprint stops matching so `anchor.region_status` reports `drifted`
    # forever, and a system pass baked into the base can no longer be reverted by
    # DELETE /system (its ops are then dropped as "already sounds here"). The
    # closed-world overwrite re-applies cleanly on top, so nothing *looks* wrong
    # until a re-generation. Only the retune-accept path has a genuine new base,
    # so make it say so.
    if (existing.get("selections") or existing.get("system_layer") is not None) and body.get(
        "layer"
    ) != "base":
        raise HTTPException(
            409,
            f"Project {video_id!r} has user/system layers. `notation` on a GET "
            "response is the COMPOSED effective layer; PUTting it back would "
            "flatten those layers into the base irrecoverably. Edit through "
            f"POST/PUT /projects/{video_id}/selections instead, or pass "
            '{"layer": "base"} to confirm this body is fresh generator output '
            "(the retune-accept path).",
        )
    to_save: dict[str, Any] = {**existing, "notation": notation}
    # Accepting a re-tune preview also persists the params that produced it.
    if body.get("params") is not None:
        to_save["params"] = body["params"]
    try:
        project = store.save_project(to_save)
    except Exception as exc:
        raise HTTPException(400, f"Invalid notation: {exc}") from exc
    return project


class RetuneRequest(BaseModel):
    params: dict[str, Any] = {}


@app.post("/projects/{video_id}/retune")
async def retune(video_id: str, req: RetuneRequest = Body(...)) -> dict[str, Any]:
    """Re-run the pipeline for an existing project with new params and stream a
    *preview* (via GET /jobs/{id}/stream, terminal `result` event). Fast for
    late-stage tweaks thanks to the stage cache. Nothing is saved until the
    client PUTs the accepted notation (+ params)."""
    if store.load_project(video_id) is None:
        raise HTTPException(404, f"No project for video_id {video_id!r}")
    job_id: str = uuid.uuid4().hex
    job: JobState = JobState()
    async with _migration_lock:  # see `_migration_lock`
        _prune_jobs()
        _jobs[job_id] = job
        threading.Thread(
            target=_run_retune_job,
            args=(job, video_id, req.params),
            daemon=True,
        ).start()
    return {"status": "job", "job_id": job_id}


@app.delete("/projects/{video_id}")
async def delete_project(video_id: str) -> dict[str, Any]:
    removed = store.delete_project(video_id)
    if not removed:
        raise HTTPException(404, f"No project for video_id {video_id!r}")
    return {"deleted": video_id}


# === Tuning Phase 2: layers (verified selections + system pass) =========


@app.get("/projects/{video_id}/layers")
async def get_layers(video_id: str) -> dict[str, Any]:
    """The full layered view: raw base, the system pass, the user's verified
    selections, and the composed effective notation with a per-note origin map
    (base | system | user) so the client can colour the delta.

    `origin_map` is an object keyed by **bar index as a string** — JSON has no
    integer keys, so the `dict[int, ...]` `compose` returns is stringified on the
    way out (`{"1": ["base", "user"], ...}`). Its list for a bar is parallel to
    that bar's `notes` in `effective`, in emitted order.
    """
    project = _load_or_404(video_id)
    effective, origin_map = _compose_or_409(project)
    return {
        "base": project.get("notation"),
        "system_layer": project.get("system_layer"),
        "selections": project.get("selections") or [],
        "effective": effective,
        "origin_map": origin_map,
    }


class SelectionBody(BaseModel):
    lane: str
    bar_start: int
    bar_end: int
    notes: list[dict[str, Any]] = []
    ops: list[dict[str, Any]] = []


def _base_max_bar(base: dict[str, Any]) -> int:
    return max((b["index"] for b in base.get("bars") or []), default=0)


def _validate_region_in_base(base: dict[str, Any], bar_start: int, bar_end: int) -> None:
    if bar_start < 1 or bar_end < bar_start:
        raise HTTPException(400, f"Invalid bar range [{bar_start}, {bar_end}].")
    mx = _base_max_bar(base)
    if bar_end > mx:
        raise HTTPException(400, f"bar_end {bar_end} exceeds the song's {mx} bars.")


def _build_selection(
    sid: str, body: SelectionBody, base: dict[str, Any], created_at: str
) -> dict[str, Any]:
    """Assemble a stored verified-selection record from a request body, stamping
    the server-owned fields. base_fingerprint pins the base under the region at
    verify time so re-generation can later detect drift (2f)."""
    return {
        "selection_id": sid,
        "origin": "user",
        "lane": body.lane,
        "bar_start": body.bar_start,
        "bar_end": body.bar_end,
        "notes": body.notes,
        "ops": body.ops,
        "verified": True,
        "base_fingerprint": region_fingerprint(base, body.lane, body.bar_start, body.bar_end),
        "created_at": created_at,
    }


def _save_selections_or_422(
    project: dict[str, Any],
    selections: list[dict[str, Any]],
    *,
    check_composition: bool = True,
) -> None:
    """Persist a project's user layer, rejecting anything that fails validation
    *or composition*.

    Composing before the write is what keeps a schema-valid selection from
    bricking the read path. `store.save_selections` validates the base notation
    and each selection record in isolation; it never merges them. A frozen note
    can be individually legal and illegal once composed — a `sustain_until` at or
    before its `position` is the concrete case — and `compose` is what catches
    that, on every subsequent GET. Doing it here means the request that
    introduces the bad layer is the request that fails.

    `check_composition=False` is for the *removal* path. Deleting a selection can
    only shrink what gets layered, so gating it on a clean compose would be
    backwards: with two bad records, neither could ever be deleted, and deletion
    is exactly the recovery `_compose_or_409` tells the client to use.
    """
    try:
        if check_composition:
            compose(project.get("notation") or {}, project.get("system_layer"), selections)
        store.save_selections(project["video_id"], selections)
    except (jsonschema.ValidationError, ValueError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/projects/{video_id}/selections")
async def create_selection(video_id: str, body: SelectionBody = Body(...)) -> dict[str, Any]:
    """Create a verified selection (the user layer). The server stamps the id,
    origin, verified flag, created_at, and base_fingerprint; the whole user layer
    is then re-validated (schema + field + no-lane-overlap invariants) on save."""
    with _project_lock(video_id):
        project = _load_or_404(video_id)
        base = project.get("notation") or {}
        _validate_region_in_base(base, body.bar_start, body.bar_end)
        sel = _build_selection("sel_" + uuid.uuid4().hex, body, base, _now_iso())
        _save_selections_or_422(project, [*(project.get("selections") or []), sel])
    return sel


@app.put("/projects/{video_id}/selections/{sid}")
async def update_selection(
    video_id: str, sid: str, body: SelectionBody = Body(...)
) -> dict[str, Any]:
    """Edit a verified selection (adjust its region / notes / re-verify).
    base_fingerprint is recomputed against the current base for the (possibly
    new) region; created_at is preserved."""
    with _project_lock(video_id):
        project = _load_or_404(video_id)
        selections = project.get("selections") or []
        idx = next((i for i, s in enumerate(selections) if s["selection_id"] == sid), None)
        if idx is None:
            raise HTTPException(404, f"No selection {sid!r}")
        base = project.get("notation") or {}
        _validate_region_in_base(base, body.bar_start, body.bar_end)
        updated = _build_selection(sid, body, base, selections[idx].get("created_at") or _now_iso())
        _save_selections_or_422(project, [*selections[:idx], updated, *selections[idx + 1 :]])
    return updated


@app.delete("/projects/{video_id}/selections/{sid}")
async def delete_selection(video_id: str, sid: str) -> dict[str, Any]:
    """Undo a verified selection as a unit."""
    with _project_lock(video_id):
        project = _load_or_404(video_id)
        selections = project.get("selections") or []
        remaining = [s for s in selections if s["selection_id"] != sid]
        if len(remaining) == len(selections):
            raise HTTPException(404, f"No selection {sid!r}")
        # No compose check: see `_save_selections_or_422`. Removal must stay
        # available even when the layer currently composes to nothing valid.
        _save_selections_or_422(project, remaining, check_composition=False)
    return {"deleted": sid}


@app.delete("/projects/{video_id}/system")
async def clear_system_layer(video_id: str) -> dict[str, Any]:
    """Undo the current 'fix the rest' system pass as a unit. Reverts only the
    system layer — never a user edit. 404s when there is no pass to clear, so a
    no-op revert doesn't rewrite the project: `store.save_project` refreshes
    `updated_at`, which is the key `store.list_projects` sorts on, and a revert
    that changed nothing should not reorder the gallery. (Phase 2 has nothing
    that sets the layer yet; owning the revert path is Phase 2's obligation.)"""
    with _project_lock(video_id):
        project = _load_or_404(video_id)
        layer = project.get("system_layer")
        if layer is None:
            raise HTTPException(404, f"Project {video_id!r} has no system layer to clear.")
        project["system_layer"] = None
        try:
            store.save_project(project)
        except (jsonschema.ValidationError, ValueError, OSError) as exc:
            raise HTTPException(400, f"Could not clear the system layer: {exc}") from exc
    return {"cleared": layer.get("pass_id")}


def main_serve() -> None:
    """Entry point for `sheetydrums-serve` — runs uvicorn on 127.0.0.1:8000."""
    import uvicorn

    uvicorn.run("sheetydrums.server:app", host="127.0.0.1", port=8000, reload=False)
