"""Endpoint tests for the tuning Phase 2 user/system layers (2c).

Async handlers are called directly (as in test_settings.py) — no TestClient.
"""
from __future__ import annotations

import asyncio
import json
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


# === composition is validated on the WRITE path =========================
# A frozen note can be legal on its own and illegal once composed. Before these
# checks, such a selection was accepted with a 200 and then 500'd every read of
# the project, with only DELETE /selections/{sid} — an endpoint the client can't
# discover, because listing the selections is itself a read — as recovery.

def _bad_sustain_note() -> dict[str, Any]:
    """sustain_until at/behind position: passes the selection schema (which only
    pattern-checks it) but is illegal in a composed notation."""
    return {"bar": 1, "note": {"instrument": "hihat_open", "position": "1/2",
                               "duration": "1/8", "sustain_until": "1/4"}}


def test_create_backwards_sustain_422(tmp_store: Any) -> None:
    with pytest.raises(HTTPException) as ei:
        _run(server.create_selection(VID, _body(
            lane="hihat", bar_start=1, bar_end=1, notes=[_bad_sustain_note()], ops=[])))
    assert ei.value.status_code == 422
    assert "sustain_until" in str(ei.value.detail)
    # Nothing was persisted, so the project still reads.
    assert _run(server.get_layers(VID))["selections"] == []
    assert _run(server.get_project(VID))["notation"]


def test_update_to_backwards_sustain_422_and_leaves_stored_selection_intact(tmp_store: Any) -> None:
    sel = _run(server.create_selection(VID, _body(lane="hihat", bar_start=1, bar_end=1, notes=[], ops=[])))
    with pytest.raises(HTTPException) as ei:
        _run(server.update_selection(VID, sel["selection_id"], _body(
            lane="hihat", bar_start=1, bar_end=1, notes=[_bad_sustain_note()], ops=[])))
    assert ei.value.status_code == 422
    stored = _run(server.get_layers(VID))["selections"]
    assert len(stored) == 1 and stored[0]["notes"] == []


def test_uncomposable_stored_selection_409s_and_stays_deletable(tmp_store: Any) -> None:
    """A record written around the endpoints (by hand, or by an older build) must
    degrade to a 409 that names the fix, not an unhandled 500 — and deleting it
    must work even though the layer doesn't compose."""
    project = store.load_project(VID)
    assert project is not None
    project["selections"] = [{
        "selection_id": "sel_bad", "origin": "user", "lane": "hihat",
        "bar_start": 1, "bar_end": 1, "notes": [_bad_sustain_note()], "ops": [],
        "verified": True,
    }]
    # Straight to disk: save_project would (correctly) reject this.
    store._atomic_write_text(store._path_for(VID), json.dumps(project))

    for coro in (server.get_project(VID), server.get_layers(VID), server.diagnose_project(VID)):
        with pytest.raises(HTTPException) as ei:
            _run(coro)
        assert ei.value.status_code == 409
        assert "selections/{sid}" in str(ei.value.detail)

    assert _run(server.delete_selection(VID, "sel_bad")) == {"deleted": "sel_bad"}
    assert _run(server.get_project(VID))["notation"]  # readable again


# === PUT /projects/{id} flatten guard ===================================

def test_put_effective_notation_over_layers_409(tmp_store: Any) -> None:
    _run(server.create_selection(VID, _body(lane="hihat", bar_start=1, bar_end=1, notes=[], ops=[])))
    effective = _run(server.get_project(VID))["notation"]
    with pytest.raises(HTTPException) as ei:
        _run(server.save_project(VID, {"notation": effective}))
    assert ei.value.status_code == 409
    # The base is untouched — its hi-hats are still there.
    b1 = next(b for b in _run(server.get_layers(VID))["base"]["bars"] if b["index"] == 1)
    assert [n for n in b1["notes"] if n["instrument"].startswith("hihat")]


def test_put_with_base_layer_flag_replaces_base_and_keeps_selections(tmp_store: Any) -> None:
    sel = _run(server.create_selection(VID, _body(lane="hihat", bar_start=1, bar_end=1, notes=[], ops=[])))
    fresh = {**_NOTATION, "tempo_bpm": 128.0}
    saved = _run(server.save_project(VID, {"notation": fresh, "layer": "base"}))
    assert saved["notation"]["tempo_bpm"] == 128.0
    assert [s["selection_id"] for s in saved["selections"]] == [sel["selection_id"]]


def test_put_without_layers_needs_no_flag(tmp_store: Any) -> None:
    """Unlayered projects are every project today; the guard must not touch them."""
    saved = _run(server.save_project(VID, {"notation": {**_NOTATION, "tempo_bpm": 90.0}}))
    assert saved["notation"]["tempo_bpm"] == 90.0


# === wire-level shapes ==================================================

def test_origin_map_is_keyed_by_bar_index_string_over_the_wire(tmp_store: Any) -> None:
    """`compose` returns dict[int, ...]; JSON has no integer keys, so the response
    renderer's json.dumps turns them into strings (jsonable_encoder alone does
    not — the conversion is in the dump). Pin the form a client actually
    receives: every other test here asserts on the pre-serialization dict."""
    from fastapi.encoders import jsonable_encoder
    from fastapi.responses import JSONResponse

    _run(server.create_selection(VID, _body(
        lane="hihat", bar_start=1, bar_end=1, notes=[_open_hat_at_0()], ops=[])))
    payload = _run(server.get_layers(VID))
    body = JSONResponse(content=jsonable_encoder(payload)).body
    wire = json.loads(bytes(body))
    assert sorted(wire["origin_map"]) == ["1", "2"]
    effective_bar1 = next(b for b in wire["effective"]["bars"] if b["index"] == 1)
    # Parallel to the bar's notes, in emitted order.
    assert len(wire["origin_map"]["1"]) == len(effective_bar1["notes"])
    assert "user" in wire["origin_map"]["1"]


# === system layer: no-op revert ==========================================

def test_clear_system_layer_404_when_absent(tmp_store: Any) -> None:
    """A revert with nothing to revert must not rewrite the project: save_project
    refreshes updated_at, which is the key list_projects sorts on, so a no-op
    would reorder the gallery."""
    before = store.load_project(VID)
    assert before is not None
    with pytest.raises(HTTPException) as ei:
        _run(server.clear_system_layer(VID))
    assert ei.value.status_code == 404
    after = store.load_project(VID)
    assert after is not None and after["updated_at"] == before["updated_at"]
