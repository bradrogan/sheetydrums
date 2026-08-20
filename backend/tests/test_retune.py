"""Endpoint tests for retune + PUT-with-params. The async handlers are called
directly (no httpx/TestClient dependency); the retune worker is stubbed so no
pipeline/models run — its real work is covered by the e2e smoke test."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from sheetydrums import server, store

_NOTATION: dict[str, Any] = {
    "version": "1",
    "tempo_bpm": 120.0,
    "time_signature": {"numerator": 4, "denominator": 4},
    "bars": [{"index": 1, "start_seconds": 0.0, "notes": [
        {"instrument": "kick", "position": "0", "duration": "1/8"},
    ]}],
}


@pytest.fixture()
def tmp_store(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(store, "_STORE_DIR", tmp_path)
    server._jobs.clear()
    return tmp_path


def _seed(vid: str = "abc12345678") -> dict[str, Any]:
    return store.save_project({
        "video_id": vid,
        "source": {"url": f"https://youtu.be/{vid}", "video_id": vid, "title": "T"},
        "notation": _NOTATION,
        "params": {"expander": {"hihat_unimodal_open_threshold": 0.4}},
    })


def test_retune_missing_project_404(tmp_store: Any) -> None:
    with pytest.raises(HTTPException) as ei:
        asyncio.run(server.retune("missing12345", server.RetuneRequest(params={})))
    assert ei.value.status_code == 404


def test_retune_spawns_job(tmp_store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed()
    captured: dict[str, Any] = {}
    def fake_worker(job: Any, video_id: str, params: dict[str, Any]) -> None:
        captured["args"] = (video_id, params)
        job.done = True
    monkeypatch.setattr(server, "_run_retune_job", fake_worker)

    resp = asyncio.run(server.retune("abc12345678", server.RetuneRequest(params={"quantize": {"subdivisions_per_whole": 32}})))
    assert resp["status"] == "job" and resp["job_id"] in server._jobs
    # The worker runs on a daemon thread; give it a moment to record its args.
    import time
    for _ in range(100):
        if "args" in captured:
            break
        time.sleep(0.01)
    assert captured["args"] == ("abc12345678", {"quantize": {"subdivisions_per_whole": 32}})


def test_put_persists_params(tmp_store: Any) -> None:
    _seed()
    new_notation = {**_NOTATION, "tempo_bpm": 128.0}
    new_params = {"quantize": {"subdivisions_per_whole": 32}}
    saved = asyncio.run(server.save_project("abc12345678", {"notation": new_notation, "params": new_params}))
    assert saved["params"] == new_params
    assert saved["notation"]["tempo_bpm"] == 128.0
    on_disk = store.load_project("abc12345678")
    assert on_disk is not None and on_disk["params"] == new_params


def test_put_without_params_preserves_existing(tmp_store: Any) -> None:
    seeded = _seed()
    saved = asyncio.run(server.save_project("abc12345678", {"notation": _NOTATION}))
    assert saved["params"] == seeded["params"]  # untouched when body omits params


def test_diagnose_endpoint(tmp_store: Any) -> None:
    _seed()
    d = asyncio.run(server.diagnose_project("abc12345678"))
    assert d["n_bars"] == 1 and d["per_class"] == {"kick": 1}

    with pytest.raises(HTTPException) as ei:
        asyncio.run(server.diagnose_project("missing12345"))
    assert ei.value.status_code == 404
