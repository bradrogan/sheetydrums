"""Settings endpoint tests (projects-dir config). Async handlers called directly."""
from __future__ import annotations

import asyncio
import inspect
import time
from typing import Any, cast

import pytest
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware

from sheetydrums import server, store


@pytest.fixture()
def tmp_cfg(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(store, "_store_dir", tmp_path / "projects")
    monkeypatch.setattr(store, "_CONFIG_PATH", tmp_path / "config.json")
    server._jobs.clear()
    return tmp_path


def test_get_settings(tmp_cfg: Any) -> None:
    s = asyncio.run(server.get_settings())
    assert s["projects_dir"].endswith("projects")
    assert "default_projects_dir" in s and s["project_count"] == 0


def test_update_settings_switches_dir(tmp_cfg: Any) -> None:
    new = tmp_cfg / "elsewhere"
    s = asyncio.run(server.update_settings(server.UpdateSettings(projects_dir=str(new))))
    assert s["projects_dir"] == str(new)
    assert store.get_projects_dir() == new


def test_update_settings_relative_path_400(tmp_cfg: Any) -> None:
    with pytest.raises(HTTPException) as ei:
        asyncio.run(server.update_settings(server.UpdateSettings(projects_dir="rel/dir")))
    assert ei.value.status_code == 400


def test_update_settings_empty_400(tmp_cfg: Any) -> None:
    with pytest.raises(HTTPException) as ei:
        asyncio.run(server.update_settings(server.UpdateSettings(projects_dir="   ")))
    assert ei.value.status_code == 400


def test_update_settings_conflicts_with_running_job(tmp_cfg: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Switching the store mid-job would split a project across both dirs."""
    monkeypatch.setitem(server._jobs, "running", server.JobState())  # done=False
    with pytest.raises(HTTPException) as ei:
        asyncio.run(
            server.update_settings(server.UpdateSettings(projects_dir=str(tmp_cfg / "elsewhere")))
        )
    assert ei.value.status_code == 409
    assert store.get_projects_dir() == tmp_cfg / "projects"


def test_update_settings_allowed_once_jobs_are_done(tmp_cfg: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    finished = server.JobState()
    finished.done = True
    monkeypatch.setitem(server._jobs, "finished", finished)
    new = tmp_cfg / "elsewhere"
    s = asyncio.run(server.update_settings(server.UpdateSettings(projects_dir=str(new))))
    assert s["projects_dir"] == str(new)


# === migration is atomic against job registration ======================

def test_job_cannot_register_during_a_migration(
    tmp_cfg: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard must span the whole move, not just the instant before it.

    Checking `_jobs` and then awaiting the move leaves the entire migration open
    for a new job to start in — and that job binds its paths in the old dir
    while save_project re-resolves to the new one.
    """
    order: list[str] = []
    real_set = store.set_projects_dir

    def slow_set(new_dir: Any, move_existing: bool = False) -> Any:
        order.append("migration:start")
        time.sleep(0.15)  # the window the unlocked version left open
        result = real_set(new_dir, move_existing)
        order.append("migration:end")
        return result

    real_prune = server._prune_jobs

    def recording_prune() -> None:
        order.append("job:registered")  # runs inside the lock, before _jobs[...]
        real_prune()

    monkeypatch.setattr(store, "set_projects_dir", slow_set)
    monkeypatch.setattr(server, "_prune_jobs", recording_prune)
    def no_pipeline(*_args: Any) -> None:  # don't actually run a pipeline
        return None

    monkeypatch.setattr(server, "_run_job", no_pipeline)

    async def scenario() -> None:
        migration = asyncio.create_task(
            server.update_settings(
                server.UpdateSettings(
                    projects_dir=str(tmp_cfg / "elsewhere"), move_existing=True
                )
            )
        )
        await asyncio.sleep(0.05)  # let the migration get inside the move
        job = asyncio.create_task(
            server.transcribe(
                server.TranscribeRequest(url="https://youtu.be/abc12345678")
            )
        )
        await migration
        await job

    asyncio.run(scenario())
    assert order == ["migration:start", "migration:end", "job:registered"]


def test_retune_also_registers_under_the_lock() -> None:
    # Both job-starting endpoints must take the lock, or the other one is a hole.
    src = inspect.getsource(server.retune)
    assert "_migration_lock" in src


# === CORS is not wildcarded ============================================

def test_cors_origins_are_not_wildcard() -> None:
    """`PUT /settings` moves the whole store and needs no credentials, so a
    wildcard would let any page the user has open drive it."""
    cors = [m for m in server.app.user_middleware if m.cls is CORSMiddleware]
    assert len(cors) == 1
    origins = cast("list[str]", cors[0].kwargs["allow_origins"])
    methods = cast("list[str]", cors[0].kwargs["allow_methods"])
    assert "*" not in origins
    assert all(
        o.startswith("http://localhost") or o.startswith("http://127.0.0.1")
        for o in origins
    )
    assert "*" not in methods


# === Filesystem directory picker (GET /fs/list) ===

def test_fs_list_lists_subdirs_only(tmp_cfg: Any) -> None:
    (tmp_cfg / "alpha").mkdir()
    (tmp_cfg / "beta").mkdir()
    (tmp_cfg / "song.wav").write_text("x")  # files are excluded
    res = asyncio.run(server.fs_list(str(tmp_cfg)))
    assert res["path"] == str(tmp_cfg)
    assert [e["name"] for e in res["entries"]] == ["alpha", "beta"]  # sorted, dirs only
    assert res["parent"] == str(tmp_cfg.parent)
    assert res["writable"] is True


def test_fs_list_default_resolves_to_projects_dir_ancestor(tmp_cfg: Any) -> None:
    # store._store_dir = tmp_cfg/"projects" (not created) → walk up to tmp_cfg.
    res = asyncio.run(server.fs_list(None))
    assert res["path"] == str(tmp_cfg)


def test_fs_list_relative_path_400(tmp_cfg: Any) -> None:
    with pytest.raises(HTTPException) as ei:
        asyncio.run(server.fs_list("rel/dir"))
    assert ei.value.status_code == 400


def test_fs_list_nonexistent_walks_up_to_existing_ancestor(tmp_cfg: Any) -> None:
    res = asyncio.run(server.fs_list(str(tmp_cfg / "no" / "such" / "dir")))
    assert res["path"] == str(tmp_cfg)


def test_fs_list_file_path_400(tmp_cfg: Any) -> None:
    f = tmp_cfg / "file.txt"
    f.write_text("x")
    with pytest.raises(HTTPException) as ei:
        asyncio.run(server.fs_list(str(f)))
    assert ei.value.status_code == 400
