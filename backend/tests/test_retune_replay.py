"""Retune reconciliation tests (tuning Phase 2f): reconcile_selections + the PUT
accept path re-anchoring the user layer onto a regenerated base."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from sheetydrums import server, store
from sheetydrums.anchor import region_fingerprint
from sheetydrums.layering import reconcile_selections

VID = "vid00000001"


def _notation(n_bars: int = 4) -> dict[str, Any]:
    bars = [
        {"index": i, "start_seconds": float(i), "notes": [
            {"instrument": "hihat_closed", "position": "0", "duration": "1/8"},
            {"instrument": "hihat_closed", "position": "1/4", "duration": "1/8"},
            {"instrument": "kick", "position": "0", "duration": "1/8"},
        ]}
        for i in range(1, n_bars + 1)
    ]
    return {
        "version": "1", "tempo_bpm": 120.0,
        "time_signature": {"numerator": 4, "denominator": 4}, "bars": bars,
    }


def _sel(sid: str = "sel_1", lane: str = "hihat", bs: int = 1, be: int = 2,
         ops: list[dict[str, Any]] | None = None, fingerprint: str = "stale") -> dict[str, Any]:
    return {
        "selection_id": sid, "origin": "user", "lane": lane,
        "bar_start": bs, "bar_end": be,
        "notes": [
            {"bar": b, "note": {"instrument": "hihat_open", "position": "0", "duration": "1/8"}}
            for b in range(bs, be + 1)
        ],
        "ops": ops if ops is not None else [
            {"kind": "reclassify", "bar": bs, "position": "0", "from": "hihat_closed", "to": "hihat_open"},
        ],
        "verified": True, "base_fingerprint": fingerprint,
    }


# === reconcile_selections (pure) ========================================

def test_reconcile_take_fresh_clean_drops_all() -> None:
    kept, conflicts = reconcile_selections([_sel()], _notation(4), "take-fresh-clean")
    assert kept == [] and conflicts == []


def test_reconcile_repins_surviving_selection() -> None:
    base = _notation(4)
    kept, _ = reconcile_selections([_sel()], base, "replay-all")
    assert len(kept) == 1
    assert kept[0]["base_fingerprint"] == region_fingerprint(base, "hihat", 1, 2)  # re-pinned
    assert kept[0]["base_fingerprint"] != "stale"


def test_reconcile_missing_region_dropped_and_reported() -> None:
    base = _notation(3)  # song got shorter than the selection's bar_end=6
    sel = _sel(bs=5, be=6)
    kept, conflicts = reconcile_selections([sel], base, "replay-with-conflict-review")
    assert kept == []
    assert len(conflicts) == 1 and conflicts[0]["kind"] == "region" and conflicts[0]["reason"] == "missing"


def test_reconcile_replay_all_drops_missing_silently() -> None:
    kept, conflicts = reconcile_selections([_sel(bs=5, be=6)], _notation(3), "replay-all")
    assert kept == [] and conflicts == []


def test_reconcile_drifted_region_kept_but_flagged() -> None:
    # base_fingerprint 'stale' won't match the new base → region exists but drifted.
    kept, conflicts = reconcile_selections([_sel()], _notation(4), "replay-with-conflict-review")
    assert len(kept) == 1  # frozen notes still win — kept
    assert any(c["kind"] == "region" and c["reason"] == "drifted" for c in conflicts)


def test_reconcile_ok_region_no_region_conflict() -> None:
    base = _notation(4)
    fp = region_fingerprint(base, "hihat", 1, 2)
    kept, conflicts = reconcile_selections([_sel(fingerprint=fp)], base, "replay-with-conflict-review")
    assert len(kept) == 1
    assert not any(c["kind"] == "region" for c in conflicts)  # fingerprint matches → not drifted


def test_reconcile_op_conflict_when_anchor_gone() -> None:
    base = _notation(4)
    fp = region_fingerprint(base, "hihat", 1, 2)
    # op deletes a hi-hat at 1/2 — the new base has none there → unmapped op.
    sel = _sel(fingerprint=fp, ops=[{"kind": "delete", "bar": 1, "position": "1/2", "instrument": "hihat_closed"}])
    _, conflicts = reconcile_selections([sel], base, "replay-with-conflict-review")
    assert any(c["kind"] == "op" and c["bar"] == 1 for c in conflicts)


# === PUT accept path ====================================================

@pytest.fixture()
def tmp_store(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(store, "_store_dir", tmp_path)
    store.save_project({
        "video_id": VID,
        "source": {"url": f"https://youtu.be/{VID}", "video_id": VID},
        "notation": _notation(4),
        "selections": [_sel()],
        "system_layer": {"pass_id": "p1", "ops": [
            {"kind": "add", "bar": 3, "position": "1/2", "instrument": "snare", "duration": "1/8", "origin": "system"},
        ]},
    })
    return tmp_path


def _accept(body: dict[str, Any]) -> Any:
    return asyncio.run(server.save_project(VID, body))


def test_accept_reanchors_and_clears_system(tmp_store: Any) -> None:
    res = _accept({"notation": _notation(4), "layer": "base", "keep_edits": "replay-with-conflict-review"})
    assert res["system_layer"] is None  # re-gen clears the system pass
    assert len(res["selections"]) == 1  # region survives → kept
    assert res["selections"][0]["base_fingerprint"] != "stale"  # re-pinned
    assert isinstance(res["conflicts"], list)
    stored = store.load_project(VID)
    assert stored is not None and stored["system_layer"] is None


def test_accept_missing_region_dropped(tmp_store: Any) -> None:
    # New base has only 1 bar; the selection's region (bars 1–2) partly vanishes.
    res = _accept({"notation": _notation(1), "layer": "base", "keep_edits": "replay-with-conflict-review"})
    assert res["selections"] == []
    assert any(c["reason"] == "missing" for c in res["conflicts"])


def test_accept_take_fresh_clean(tmp_store: Any) -> None:
    res = _accept({"notation": _notation(4), "layer": "base", "keep_edits": "take-fresh-clean"})
    assert res["selections"] == [] and res["system_layer"] is None and res["conflicts"] == []


def test_accept_invalid_keep_edits_400(tmp_store: Any) -> None:
    with pytest.raises(HTTPException) as ei:
        _accept({"notation": _notation(4), "layer": "base", "keep_edits": "nonsense"})
    assert ei.value.status_code == 400


def test_accept_without_layer_flag_409s_on_layered_project(tmp_store: Any) -> None:
    with pytest.raises(HTTPException) as ei:
        _accept({"notation": _notation(4)})  # no layer:"base" on a layered project
    assert ei.value.status_code == 409
