"""On-disk project store for the web app.

A *project* wraps a YouTube source with its drum transcription (`notation`),
keyed by video id. Single-user local-dev persistence: one JSON file per project
under the projects directory (default `~/.cache/sheetydrums/projects/`, but
user-configurable — see `set_projects_dir`). The notation payload is the
events.json contract verbatim and is validated on write via the same
`validate()` the pipeline uses.

The projects directory is configurable and persisted in a small config file
(`~/.cache/sheetydrums/config.json`, which lives *outside* the projects dir so
it survives moving them). Changing it can optionally move existing project files
to the new location — only the entries this store created, since a user-chosen
directory may hold unrelated files.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sheetydrums.validate import validate, validate_selections, validate_system_layer

_DEFAULT_STORE_DIR: Path = Path.home() / ".cache" / "sheetydrums" / "projects"
# Config lives outside the projects dir so the pointer survives moving projects.
_CONFIG_PATH: Path = Path.home() / ".cache" / "sheetydrums" / "config.json"


def _load_store_dir() -> Path:
    """Read the configured projects dir, falling back to the default."""
    try:
        cfg = json.loads(_CONFIG_PATH.read_text())
        d = cfg.get("projects_dir")
        if d:
            return Path(d)
    except (OSError, json.JSONDecodeError):
        pass
    return _DEFAULT_STORE_DIR


# Mutable module state, not a constant: `set_projects_dir` reassigns it, which
# is why it is lower-case. `_DEFAULT_STORE_DIR` above really is constant.
_store_dir: Path = _load_store_dir()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# === Projects-directory configuration ===================================


def default_projects_dir() -> Path:
    return _DEFAULT_STORE_DIR


def get_projects_dir() -> Path:
    return _store_dir


def set_projects_dir(new_dir: Path | str, move_existing: bool = False) -> Path:
    """Point the store at `new_dir` and persist the choice. When `move_existing`,
    first move this store's own entries from the current projects dir into the
    new one — unrelated files in a user-chosen directory are left alone.

    Raises ValueError for a non-absolute path, a path that exists as a file, or
    a path nested with the current one; FileExistsError if a moved entry would
    clobber something already in the target (we never silently overwrite the
    user's data). A move that fails partway is rolled back, so projects are
    never left split across two directories.
    """
    global _store_dir
    target = Path(new_dir).expanduser()
    if not target.is_absolute():
        raise ValueError("Projects directory must be an absolute path.")
    if target.exists() and not target.is_dir():
        raise ValueError(f"{target} exists and is not a directory.")

    old = _store_dir
    if target == old:
        _persist_store_dir(target)  # still record it (first-time explicit set)
        return target

    # Moving between nested directories is ill-defined (a parent into its own
    # child, or projects hoisted on top of the dir they came from), so refuse
    # rather than discover it halfway through the move.
    if _nested(target, old):
        raise ValueError(
            f"{target} is nested with the current projects directory ({old}) — "
            "choose a directory outside it."
        )

    target.mkdir(parents=True, exist_ok=True)
    if move_existing:
        _move_projects(old, target)
    # Switch in memory before persisting: once the files have moved, the running
    # process must follow them even if writing the config file fails.
    _store_dir = target
    try:
        _persist_store_dir(target)
    except OSError as exc:
        # The move already happened, so this is not a clean failure: the config
        # still names `old`, and after a restart `_load_store_dir()` would send
        # the app to a directory the projects have left — they'd look lost. Say
        # where they are instead of surfacing a bare errno.
        raise OSError(
            f"Projects were moved to {target}, but the new location could not be "
            f"saved to {_CONFIG_PATH} ({exc}). This session is using {target}; "
            "set the directory again to make it survive a restart."
        ) from exc
    return target


def _nested(a: Path, b: Path) -> bool:
    """True if either path sits inside the other. Compared resolved, so a
    symlinked parent can't sneak a nested pair past the check; neither path
    needs to exist."""
    ra, rb = a.resolve(), b.resolve()
    return ra.is_relative_to(rb) or rb.is_relative_to(ra)


def _known_video_ids(dir_: Path) -> set[str]:
    """Video ids of the projects stored in `dir_`. A `.json` that doesn't parse
    as a project (the user's own file, our own `config.json`) is not one."""
    return {
        path.stem for path in dir_.glob("*.json") if _read_project(path) is not None
    }


def _owned_entries(dir_: Path) -> list[Path]:
    """Entries in `dir_` that this store created — a project's JSON plus every
    `<video_id>.*` sibling (logs, stems, stage cache). The projects dir is
    user-chosen and may hold anything, so this is the only set a move touches.
    """
    if not dir_.exists():
        return []
    ids = _known_video_ids(dir_)
    # A video id never contains a dot, so the leading segment is the key.
    return sorted(p for p in dir_.iterdir() if p.name.split(".", 1)[0] in ids)


def _move_projects(old: Path, target: Path) -> None:
    """Move this store's entries from `old` into `target`, all or nothing.

    Nothing moves unless every destination is free, and a failure partway is
    rolled back (best effort) so a half-move can't strand projects in a
    directory the store no longer reads.
    """
    entries = _owned_entries(old)  # materialized once: the checks and the moves
    for entry in entries:          # must agree even if a job writes meanwhile
        dest = target / entry.name
        if dest.exists():
            raise FileExistsError(f"{dest} already exists — refusing to overwrite.")

    moved: list[tuple[Path, Path]] = []
    try:
        for entry in entries:
            dest = target / entry.name
            shutil.move(str(entry), str(dest))
            moved.append((entry, dest))
    except BaseException as exc:
        # Undo what moved. A rollback that itself fails leaves the store split,
        # which is the one outcome this function promises not to produce — so
        # name the stranded entries rather than reporting only the original
        # error and letting it look like a clean no-op.
        stranded: list[Path] = []
        for src, dest in reversed(moved):
            try:
                shutil.move(str(dest), str(src))
            except OSError:
                stranded.append(dest)
        if stranded:
            raise OSError(
                f"Move failed ({exc}) and could not be fully undone. These "
                f"entries are now in {target} while the store still reads "
                f"{old}: {', '.join(sorted(p.name for p in stranded))}. Move "
                "them back by hand, or point the store at the new directory."
            ) from exc
        raise


def _persist_store_dir(dir_: Path) -> None:
    existing: dict[str, Any] = {}
    try:
        existing = json.loads(_CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        pass
    existing["projects_dir"] = str(dir_)
    _atomic_write_text(_CONFIG_PATH, json.dumps(existing, indent=2) + "\n")


def _check_video_id(video_id: str) -> None:
    # video_id is a YouTube id ([A-Za-z0-9_-]{11}); reject anything that could
    # escape the store dir. A dot is rejected too: `_owned_entries` keys an
    # entry by the segment before its first dot, so a dotted id would disagree
    # with `_known_video_ids` (which uses `.stem`) and the project's own JSON
    # would be left behind by a move.
    if not video_id or any(c in video_id for c in "/\\."):
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
    return _store_dir / f"{video_id}.json"


def project_exists(video_id: str) -> bool:
    return _path_for(video_id).exists()


def stem_path(video_id: str) -> Path:
    """Path to a project's isolated drum-stem WAV (may not exist yet)."""
    _check_video_id(video_id)
    return _store_dir / f"{video_id}.drums.wav"


def has_stem(video_id: str) -> bool:
    return stem_path(video_id).exists()


def drumless_path(video_id: str) -> Path:
    """Path to a project's drumless backing-track WAV (may not exist yet)."""
    _check_video_id(video_id)
    return _store_dir / f"{video_id}.drumless.wav"


def has_drumless(video_id: str) -> bool:
    return drumless_path(video_id).exists()


def stages_dir(video_id: str) -> Path:
    """Directory holding a project's cached per-stage artifacts (may not exist
    yet). See `cache.StageCache`."""
    _check_video_id(video_id)
    return _store_dir / f"{video_id}.stages"


def _read_project(path: Path) -> dict[str, Any] | None:
    """Parse `path` as a project file, or None if it isn't one. The projects dir
    is user-chosen, so an unreadable file, a non-JSON file, or a JSON document
    that isn't a project is skipped rather than raised."""
    try:
        project = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(project, dict) or not project.get("video_id"):
        return None
    return project


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
    # Tuning Phase 2 layers are additive/optional — validate only when present, so
    # pre-Phase-2 projects (no selections / system_layer) keep saving unchanged.
    # The whole list goes in at once: the "no two selections own one (lane, bar)"
    # invariant is a property of the set, not of any single selection.
    validate_selections(project.get("selections") or [])
    if project.get("system_layer") is not None:
        validate_system_layer(project["system_layer"])

    existing = load_project(video_id)
    now = _now_iso()
    project = {
        **project,
        "created_at": (existing or {}).get("created_at") or project.get("created_at") or now,
        "updated_at": now,
    }

    _atomic_write_text(_path_for(video_id), json.dumps(project, indent=2) + "\n")
    return project


# === Tuning Phase 2: user layer (verified selections) ===================
# The user layer is a rewritable project-record field (not an append-only log)
# because selections mutate after creation — edge-adjust, re-verify, undo — all
# of which are ordinary field rewrites. See docs/design/phase2-plan.md §1.1.


def save_selections(video_id: str, selections: list[dict[str, Any]]) -> dict[str, Any]:
    """Replace a project's verified-selection list (the user layer), validating
    each. Returns the stored project. Raises KeyError if the project is missing
    and jsonschema.ValidationError on an invalid selection (via save_project)."""
    project = load_project(video_id)
    if project is None:
        raise KeyError(f"No project {video_id!r}")
    project["selections"] = selections
    return save_project(project)


def read_selections(video_id: str) -> list[dict[str, Any]]:
    """Return a project's verified selections, or [] if none / project missing."""
    project = load_project(video_id)
    return list((project or {}).get("selections") or [])


def append_version(video_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Append an immutable version snapshot to the append-only version log.
    Write-only in Phase 2 (nothing reads it back yet) — it is the audit trail the
    Phase 3 parameter search will score against. Returns the stored record."""
    return append_event(video_id, "versions", snapshot)


def delete_project(video_id: str) -> bool:
    """Delete a project (its JSON, append-only logs, and cached stage artifacts;
    the drum-stem/drumless audio stays). Returns True if the JSON was removed."""
    import shutil

    path = _path_for(video_id)
    for log in _store_dir.glob(f"{video_id}.*.jsonl"):
        log.unlink(missing_ok=True)
    sdir = stages_dir(video_id)
    if sdir.exists():
        shutil.rmtree(sdir, ignore_errors=True)
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
    return _store_dir / f"{video_id}.{name}.jsonl"


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
    """Return lightweight summaries, newest-updated first. Files in the projects
    dir that aren't projects are skipped — the dir is user-chosen, so a stray
    `.json` must not break the listing."""
    if not _store_dir.exists():
        return []
    summaries: list[dict[str, Any]] = [
        _summarize(project)
        for path in _store_dir.glob("*.json")
        if (project := _read_project(path)) is not None
    ]
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
