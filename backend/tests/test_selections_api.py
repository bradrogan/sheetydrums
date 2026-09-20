"""Endpoint tests for the tuning Phase 2 user/system layers (2c).

Async handlers are called directly (as in test_settings.py) — no TestClient.
"""
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
    "bars": [
        {"index": 1, "start_seconds": 0.0, "notes": [
            {"instrument": "hihat_closed", "position": "0", "duration": "1/8"},
            {"instrument": "hihat_closed", "position": "1/4", "duration": "1/8"},
            {"instrument": "kick", "position": "0", "duration": "1/8"},
        ]},
        {"index": 2, "start_seconds": 2.0, "notes": [
            {"instrument": "hihat_closed", "position": "0", "duration": "1/8"},
        ]},
    ],
}

VID = "vid00000001"


@pytest.fixture()
def tmp_store(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(store, "_store_dir", tmp_path)
    store.save_project({
        "video_id": VID,
        "source": {"url": f"https://youtu.be/{VID}", "video_id": VID},
        "notation": _NOTATION,
    })
    return tmp_path


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _body(**kw: Any) -> server.SelectionBody:
    return server.SelectionBody(**kw)


def _open_hat_at_0() -> dict[str, Any]:
    return {"bar": 1, "note": {"instrument": "hihat_open", "position": "0", "duration": "1/8"}}


# === create + layers ====================================================

def test_create_selection_and_layers(tmp_store: Any) -> None:
    sel = _run(server.create_selection(VID, _body(
        lane="hihat", bar_start=1, bar_end=1,
        notes=[_open_hat_at_0()],
        ops=[{"kind": "reclassify", "bar": 1, "position": "0", "from": "hihat_closed", "to": "hihat_open"}],
    )))
    assert sel["selection_id"].startswith("sel_")
    assert sel["verified"] is True and sel["origin"] == "user" and sel["base_fingerprint"]

    layers = _run(server.get_layers(VID))
    assert len(layers["selections"]) == 1
    b1 = next(b for b in layers["effective"]["bars"] if b["index"] == 1)
    hats = [n for n in b1["notes"] if n["instrument"].startswith("hihat")]
    assert [h["instrument"] for h in hats] == ["hihat_open"]  # closed-world overwrite
    assert "user" in layers["origin_map"][1]
    # base is preserved unchanged
    b1_base = next(b for b in layers["base"]["bars"] if b["index"] == 1)
    assert sum(1 for n in b1_base["notes"] if n["instrument"] == "hihat_closed") == 2


def test_get_project_serves_effective_with_base_alongside(tmp_store: Any) -> None:
    _run(server.create_selection(VID, _body(lane="hihat", bar_start=1, bar_end=1, notes=[], ops=[])))
    proj = _run(server.get_project(VID))
    b1_eff = next(b for b in proj["notation"]["bars"] if b["index"] == 1)
    assert not [n for n in b1_eff["notes"] if n["instrument"].startswith("hihat")]  # verified empty
    b1_base = next(b for b in proj["base_notation"]["bars"] if b["index"] == 1)
    assert [n for n in b1_base["notes"] if n["instrument"].startswith("hihat")]  # base untouched


# === validation =========================================================

def test_create_region_beyond_bars_400(tmp_store: Any) -> None:
    with pytest.raises(HTTPException) as ei:
        _run(server.create_selection(VID, _body(lane="hihat", bar_start=1, bar_end=9, notes=[], ops=[])))
    assert ei.value.status_code == 400


def test_create_note_wrong_lane_422(tmp_store: Any) -> None:
    with pytest.raises(HTTPException) as ei:
        _run(server.create_selection(VID, _body(
            lane="hihat", bar_start=1, bar_end=1,
            notes=[{"bar": 1, "note": {"instrument": "kick", "position": "0", "duration": "1/8"}}], ops=[])))
    assert ei.value.status_code == 422


def test_create_duplicate_position_422(tmp_store: Any) -> None:
    with pytest.raises(HTTPException) as ei:
        _run(server.create_selection(VID, _body(
            lane="hihat", bar_start=1, bar_end=1,
            notes=[
                {"bar": 1, "note": {"instrument": "hihat_closed", "position": "0", "duration": "1/8"}},
                {"bar": 1, "note": {"instrument": "hihat_open", "position": "0", "duration": "1/8"}},
            ], ops=[])))
    assert ei.value.status_code == 422


def test_overlapping_selections_422(tmp_store: Any) -> None:
    _run(server.create_selection(VID, _body(lane="hihat", bar_start=1, bar_end=2, notes=[], ops=[])))
    with pytest.raises(HTTPException) as ei:
        _run(server.create_selection(VID, _body(lane="hihat", bar_start=2, bar_end=2, notes=[], ops=[])))
    assert ei.value.status_code == 422


# === update / delete ====================================================

def test_update_selection_preserves_created_at(tmp_store: Any) -> None:
    sel = _run(server.create_selection(VID, _body(lane="hihat", bar_start=1, bar_end=1, notes=[], ops=[])))
    sid = sel["selection_id"]
    updated = _run(server.update_selection(VID, sid, _body(lane="hihat", bar_start=1, bar_end=2, notes=[], ops=[])))
    assert updated["selection_id"] == sid and updated["bar_end"] == 2
    assert updated["created_at"] == sel["created_at"]
    assert _run(server.get_layers(VID))["selections"][0]["bar_end"] == 2


def test_update_missing_404(tmp_store: Any) -> None:
    with pytest.raises(HTTPException) as ei:
        _run(server.update_selection(VID, "sel_nope", _body(lane="hihat", bar_start=1, bar_end=1, notes=[], ops=[])))
    assert ei.value.status_code == 404


def test_delete_selection(tmp_store: Any) -> None:
    sel = _run(server.create_selection(VID, _body(lane="hihat", bar_start=1, bar_end=1, notes=[], ops=[])))
    out = _run(server.delete_selection(VID, sel["selection_id"]))
    assert out["deleted"] == sel["selection_id"]
    assert _run(server.get_layers(VID))["selections"] == []


def test_delete_missing_404(tmp_store: Any) -> None:
    with pytest.raises(HTTPException) as ei:
        _run(server.delete_selection(VID, "sel_nope"))
    assert ei.value.status_code == 404


# === system layer =======================================================

def _inject_system_layer(ops: list[dict[str, Any]]) -> None:
    project = store.load_project(VID)
    assert project is not None
    project["system_layer"] = {"pass_id": "pass_1", "ops": ops}
    store.save_project(project)


def test_system_op_in_effective_then_cleared(tmp_store: Any) -> None:
    _inject_system_layer([
        {"kind": "add", "bar": 2, "position": "1/4", "instrument": "snare", "duration": "1/8", "origin": "system"},
    ])
    layers = _run(server.get_layers(VID))
    b2 = next(b for b in layers["effective"]["bars"] if b["index"] == 2)
    assert any(n["instrument"] == "snare" and n["position"] == "1/4" for n in b2["notes"])
    assert layers["origin_map"][2].count("system") == 1

    out = _run(server.clear_system_layer(VID))
    assert out["cleared"] == "pass_1"
    layers2 = _run(server.get_layers(VID))
    assert layers2["system_layer"] is None
    b2b = next(b for b in layers2["effective"]["bars"] if b["index"] == 2)
    assert not any(n["instrument"] == "snare" for n in b2b["notes"])


def test_system_op_dropped_under_verified_region(tmp_store: Any) -> None:
    _run(server.create_selection(VID, _body(lane="hihat", bar_start=1, bar_end=1, notes=[], ops=[])))
    _inject_system_layer([
        {"kind": "add", "bar": 1, "position": "1/2", "instrument": "hihat_closed", "duration": "1/8", "origin": "system"},
    ])
    layers = _run(server.get_layers(VID))
    b1 = next(b for b in layers["effective"]["bars"] if b["index"] == 1)
    assert not [n for n in b1["notes"] if n["instrument"].startswith("hihat")]  # system add dropped


# === diagnose on effective ==============================================

def test_diagnose_reflects_user_edit(tmp_store: Any) -> None:
    before = _run(server.diagnose_project(VID))
    assert before["hats"]["closed"] == 3 and before["n_notes"] == 4
    _run(server.create_selection(VID, _body(lane="hihat", bar_start=1, bar_end=2, notes=[], ops=[])))
    after = _run(server.diagnose_project(VID))
    assert after["hats"]["closed"] == 0 and after["n_notes"] == 1  # only the kick survives
