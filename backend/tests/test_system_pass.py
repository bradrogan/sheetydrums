"""Phase 3c — POST /projects/{id}/system (accept a 'fix the rest' pass).

The search job (`/fix-the-rest`) runs the real cached pipeline, so it's covered
by the fake-pipeline search tests (test_search.py) + the diff tests
(test_systemops.py). Here we exercise the accept/persist endpoint directly.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from sheetydrums import server, store

VID = "vid00000001"


def _notation() -> dict[str, Any]:
    return {
        "version": "1", "tempo_bpm": 120.0,
        "time_signature": {"numerator": 4, "denominator": 4},
        "bars": [
            {"index": i, "start_seconds": float(i), "notes": [
                {"instrument": "hihat_closed", "position": "0", "duration": "1/8"},
            ]}
            for i in (1, 2)
        ],
    }


@pytest.fixture()
def tmp_store(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(store, "_store_dir", tmp_path)
    store.save_project({
        "video_id": VID,
        "source": {"url": f"https://youtu.be/{VID}", "video_id": VID},
        "notation": _notation(),
        # A verified hi-hat region in bar 1 — user-owned, inviolable.
        "selections": [{
            "selection_id": "sel_1", "origin": "user", "lane": "hihat",
            "bar_start": 1, "bar_end": 1,
            "notes": [{"bar": 1, "note": {"instrument": "hihat_closed", "position": "0", "duration": "1/8"}}],
            "ops": [], "verified": True,
        }],
    })
    return tmp_path


def _apply(body: dict[str, Any]) -> Any:
    return asyncio.run(server.apply_system_pass(VID, server.SystemPassBody(**body)))


def _saved() -> dict[str, Any]:
    p = store.load_project(VID)
    assert p is not None
    return p


def test_apply_persists_layer_and_composes(tmp_store: Any) -> None:
    # Add an open hat in bar 2 (unverified) as a system pass.
    ops = [{"kind": "add", "bar": 2, "position": "1/2", "instrument": "hihat_open",
            "duration": "1/8", "origin": "system"}]
    out = _apply({"pass_id": "pass_a", "ops": ops, "params": {"expander": {}}})
    assert out["pass_id"] == "pass_a"
    # Persisted.
    saved = _saved()
    assert saved["system_layer"]["pass_id"] == "pass_a"
    assert saved["system_layer"]["params"] == {"expander": {}}  # rides along (provenance)
    # Composed effective shows the system note with a 'system' origin in bar 2.
    assert "system" in out["origin_map"]["2"]


def test_later_pass_supersedes(tmp_store: Any) -> None:
    _apply({"pass_id": "pass_a", "ops": [
        {"kind": "add", "bar": 2, "position": "1/2", "instrument": "hihat_open",
         "duration": "1/8", "origin": "system"}]})
    _apply({"pass_id": "pass_b", "ops": [
        {"kind": "add", "bar": 2, "position": "1/4", "instrument": "hihat_open",
         "duration": "1/8", "origin": "system"}]})
    saved = _saved()
    assert saved["system_layer"]["pass_id"] == "pass_b"  # wholesale replacement
    assert len(saved["system_layer"]["ops"]) == 1


def test_op_inside_verified_region_dropped_at_compose(tmp_store: Any) -> None:
    # A system op targeting bar 1's verified hi-hat is dropped — the effective
    # bar-1 hi-hat stays the user's, no 'system' origin there.
    _apply({"pass_id": "pass_a", "ops": [
        {"kind": "add", "bar": 1, "position": "1/2", "instrument": "hihat_open",
         "duration": "1/8", "origin": "system"}]})
    out = _apply({"pass_id": "pass_a", "ops": [
        {"kind": "add", "bar": 1, "position": "1/2", "instrument": "hihat_open",
         "duration": "1/8", "origin": "system"}]})
    assert "system" not in out["origin_map"]["1"]


def test_invalid_op_422(tmp_store: Any) -> None:
    with pytest.raises(HTTPException) as ei:
        _apply({"pass_id": "pass_a", "ops": [{"kind": "add", "bar": 2}]})  # missing fields
    assert ei.value.status_code == 422


def test_delete_clears_pass(tmp_store: Any) -> None:
    _apply({"pass_id": "pass_a", "ops": [
        {"kind": "add", "bar": 2, "position": "1/2", "instrument": "hihat_open",
         "duration": "1/8", "origin": "system"}]})
    asyncio.run(server.clear_system_layer(VID))
    assert _saved().get("system_layer") is None
