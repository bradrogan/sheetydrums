"""On-disk project store for the web app.

A *project* wraps a YouTube source with its drum transcription (`notation`),
keyed by video id. Single-user local-dev persistence: one JSON file per project
under `~/.cache/sheetydrums/projects/<video_id>.json`. The notation payload is
the events.json contract verbatim and is validated on write via the same
`validate()` the pipeline uses.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sheetydrums.validate import validate

_STORE_DIR: Path = Path.home() / ".cache" / "sheetydrums" / "projects"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path_for(video_id: str) -> Path:
    # video_id is a YouTube id ([A-Za-z0-9_-]{11}); reject anything that could
    # escape the store dir.
    if "/" in video_id or "\\" in video_id or video_id in ("", ".", ".."):
        raise ValueError(f"Invalid video_id: {video_id!r}")
    return _STORE_DIR / f"{video_id}.json"


def project_exists(video_id: str) -> bool:
    return _path_for(video_id).exists()


def stem_path(video_id: str) -> Path:
    """Path to a project's isolated drum-stem WAV (may not exist yet)."""
    if "/" in video_id or "\\" in video_id or video_id in ("", ".", ".."):
        raise ValueError(f"Invalid video_id: {video_id!r}")
    return _STORE_DIR / f"{video_id}.drums.wav"


def has_stem(video_id: str) -> bool:
    return stem_path(video_id).exists()


def drumless_path(video_id: str) -> Path:
    """Path to a project's drumless backing-track WAV (may not exist yet)."""
    if "/" in video_id or "\\" in video_id or video_id in ("", ".", ".."):
        raise ValueError(f"Invalid video_id: {video_id!r}")
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

    _STORE_DIR.mkdir(parents=True, exist_ok=True)
    _path_for(video_id).write_text(json.dumps(project, indent=2) + "\n")
    return project


def delete_project(video_id: str) -> bool:
    """Delete a project. Returns True if a file was removed."""
    path = _path_for(video_id)
    if not path.exists():
        return False
    path.unlink()
    return True


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
