"""Settings endpoint tests (projects-dir config). Async handlers called directly."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from sheetydrums import server, store


@pytest.fixture()
def tmp_cfg(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(store, "_STORE_DIR", tmp_path / "projects")
    monkeypatch.setattr(store, "_CONFIG_PATH", tmp_path / "config.json")
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
