"""HTTP server for the sheetydrums web app (YouTube-only projects).

A *project* pairs a YouTube source with its drum transcription and is persisted
via `store.py`, keyed by video id. Endpoints:

  POST   /transcribe        {url}  → open existing project or start a job
  GET    /jobs/{id}/stream         → SSE progress, terminal event carries project
  GET    /projects                 → list summaries
  GET    /projects/{id}            → full project
  PUT    /projects/{id}            → save edited notation
  DELETE /projects/{id}            → remove

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
from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from sheetydrums import store


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


app: FastAPI = FastAPI(title="sheetydrums")
# CORS open — local-dev server, never bound off 127.0.0.1. Vite dev proxies
# requests anyway, so this only matters if you hit the API directly from a
# different origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
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
        pipeline = build_pipeline(config, on_progress=on_progress)
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


# === Project CRUD ===


@app.get("/projects")
async def list_projects() -> dict[str, Any]:
    return {"projects": store.list_projects()}


@app.get("/projects/{video_id}")
async def get_project(video_id: str) -> dict[str, Any]:
    project = store.load_project(video_id)
    if project is None:
        raise HTTPException(404, f"No project for video_id {video_id!r}")
    # Runtime-only flags (not persisted): which alternate audio tracks exist.
    return {
        **project,
        "has_stem": store.has_stem(video_id),
        "has_drumless": store.has_drumless(video_id),
    }


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
    """Save edited notation for an existing project.

    Body: {"notation": <events>}. Validates before persisting; preserves the
    project's source + created_at.
    """
    existing = store.load_project(video_id)
    if existing is None:
        raise HTTPException(404, f"No project for video_id {video_id!r}")
    notation = body.get("notation")
    if notation is None:
        raise HTTPException(400, "Body must contain `notation`.")
    try:
        project = store.save_project({**existing, "notation": notation})
    except Exception as exc:
        raise HTTPException(400, f"Invalid notation: {exc}") from exc
    return project


@app.delete("/projects/{video_id}")
async def delete_project(video_id: str) -> dict[str, Any]:
    removed = store.delete_project(video_id)
    if not removed:
        raise HTTPException(404, f"No project for video_id {video_id!r}")
    return {"deleted": video_id}


def main_serve() -> None:
    """Entry point for `sheetydrums-serve` — runs uvicorn on 127.0.0.1:8000."""
    import uvicorn

    uvicorn.run("sheetydrums.server:app", host="127.0.0.1", port=8000, reload=False)
