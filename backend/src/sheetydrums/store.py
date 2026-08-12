"""On-disk project store for the web app.

A *project* wraps a YouTube source with its drum transcription (`notation`),
keyed by video id. Single-user local-dev persistence: one JSON file per project
under `~/.cache/sheetydrums/projects/<video_id>.json`. The notation payload is
the events.json contract verbatim and is validated on write via the same
`validate()` the pipeline uses.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sheetydrums.validate import validate

_STORE_DIR: Path = Path.home() / ".cache" / "sheetydrums" / "projects"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_video_id(video_id: str) -> None:
    # video_id is a YouTube id ([A-Za-z0-9_-]{11}); reject anything that could
    # escape the store dir.
    if "/" in video_id or "\\" in video_id or video_id in ("", ".", ".."):
        raise ValueError(f"Invalid video_id: {video_id!r}")


def _atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically: fully write a temp file in the same
    directory, fsync it, then os.replace() it into place. A crash mid-write
    leaves the previous file intact rather than a truncated/corrupt one (the old
    ``path.write_text`` could leave a half-written project JSON)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # atomic on the same filesystem
    except BaseException:
        try:
            os.unlink(tmp)  # never leave a stray .tmp behind
        except OSError:
            pass
        raise


def _path_for(video_id: str) -> Path:
    _check_video_id(video_id)
    return _STORE_DIR / f"{video_id}.json"


def project_exists(video_id: str) -> bool:
    return _path_for(video_id).exists()


def stem_path(video_id: str) -> Path:
    """Path to a project's isolated drum-stem WAV (may not exist yet)."""
    _check_video_id(video_id)
    return _STORE_DIR / f"{video_id}.drums.wav"


def has_stem(video_id: str) -> bool:
    return stem_path(video_id).exists()


def drumless_path(video_id: str) -> Path:
    """Path to a project's drumless backing-track WAV (may not exist yet)."""
    _check_video_id(video_id)
    return _STORE_DIR / f"{video_id}.drumless.wav"


def has_drumless(video_id: str) -> bool:
    return drumless_path(video_id).exists()


def load_project(video_id: str) -> dict[str, Any] | None:
    """Return the full project dict, or None if it doesn't exist."""
    path = _path_for(video_id)
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_project(project: dict[str, Any]) -> dict[str, Any]:
    """Validate + persist `project`, stamping timestamps. Returns the stored dict.

    `created_at` is preserved from any existing project; `updated_at` is always
    refreshed. Raises on invalid notation (jsonschema / ValueError).
    """
    video_id: str = project["video_id"]
    validate(project["notation"])

    existing = load_project(video_id)
    now = _now_iso()
    project = {
        **project,
        "created_at": (existing or {}).get("created_at") or project.get("created_at") or now,
        "updated_at": now,
    }

    _atomic_write_text(_path_for(video_id), json.dumps(project, indent=2) + "\n")
    return project


def delete_project(video_id: str) -> bool:
    """Delete a project (its JSON + any append-only logs; cached audio stays).
    Returns True if the project JSON was removed."""
    path = _path_for(video_id)
    for log in _STORE_DIR.glob(f"{video_id}.*.jsonl"):
        log.unlink(missing_ok=True)
    if not path.exists():
        return False
    path.unlink()
    return True


# === Append-only per-project logs (JSONL) ================================
# Edit history and (later) tuning feedback are append-heavy: they grow one entry
# at a time, so they live as JSON-Lines files — one record per line, appended
# rather than rewriting the whole document. Each project gets a log per `name`
# at `<video_id>.<name>.jsonl` (e.g. name="edits", "feedback").


def event_log_path(video_id: str, name: str) -> Path:
    """Path to a per-project append-only JSONL log (may not exist yet)."""
    _check_video_id(video_id)
    if not name.isidentifier():
        raise ValueError(f"Invalid log name: {name!r}")
    return _STORE_DIR / f"{video_id}.{name}.jsonl"


def append_event(video_id: str, name: str, record: dict[str, Any]) -> dict[str, Any]:
    """Append one record to a per-project log as a single JSON line, stamping
    `at` (ISO-8601) if absent. Returns the stored record. A newline in the
    serialized record would break the one-record-per-line invariant, so json's
    (newline-free) output is written verbatim with a single trailing '\\n'."""
    path = event_log_path(video_id, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {**record, "at": record.get("at") or _now_iso()}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return record


def read_events(video_id: str, name: str) -> list[dict[str, Any]]:
    """Read a per-project JSONL log in append order. Missing log → []. Blank or
    unparseable lines are skipped, so a torn final line from a crash mid-append
    never breaks the read."""
    path = event_log_path(video_id, name)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def list_projects() -> list[dict[str, Any]]:
    """Return lightweight summaries, newest-updated first."""
    if not _STORE_DIR.exists():
        return []
    summaries: list[dict[str, Any]] = []
    for path in _STORE_DIR.glob("*.json"):
        try:
            project = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        summaries.append(_summarize(project))
    summaries.sort(key=lambda s: s.get("updated_at") or "", reverse=True)
    return summaries


def _summarize(project: dict[str, Any]) -> dict[str, Any]:
    notation: dict[str, Any] = project.get("notation") or {}
    bars: list[Any] = notation.get("bars") or []
    n_notes: int = sum(len(b.get("notes") or []) for b in bars)
    video_id: str = project["video_id"]
    return {
        "video_id": video_id,
        "title": (project.get("source") or {}).get("title"),
        "url": (project.get("source") or {}).get("url"),
        "thumbnail": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
        "updated_at": project.get("updated_at"),
        "created_at": project.get("created_at"),
        "tempo_bpm": notation.get("tempo_bpm"),
        "n_bars": len(bars),
        "n_notes": n_notes,
    }
