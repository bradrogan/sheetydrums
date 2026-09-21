"""Version snapshots + revert (safety net for re-tune). Async handlers direct."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from sheetydrums import server, store

VID = "vid00000001"


def _notation(tempo: float = 120.0, n_bars: int = 2) -> dict[str, Any]:
    return {
        "version": "1",
        "tempo_bpm": tempo,
        "time_signature": {"numerator": 4, "denominator": 4},
        "bars": [
            {"index": i, "start_seconds": float(i), "notes": [
                {"instrument": "kick", "position": "0", "duration": "1/8"},
            ]}
            for i in range(1, n_bars + 1)
        ],
    }


def _sel() -> dict[str, Any]:
    return {
        "selection_id": "sel_1", "origin": "user", "lane": "hihat",
        "bar_start": 1, "bar_end": 1,
        "notes": [{"bar": 1, "note": {"instrument": "hihat_open", "position": "0", "duration": "1/8"}}],
        "ops": [], "verified": True,
    }


@pytest.fixture()
def tmp_store(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(store, "_store_dir", tmp_path)
    store.save_project({
        "video_id": VID,
        "source": {"url": f"https://youtu.be/{VID}", "video_id": VID},
        "notation": _notation(120.0),
        "params": {"a": 1},
        "selections": [_sel()],
    })
    return tmp_path


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_retune_accept_snapshots_outgoing_base(tmp_store: Any) -> None:
    # Accept a re-tune (new tempo). The outgoing base (120) should be snapshotted.
    _run(server.save_project(VID, {"notation": _notation(140.0), "layer": "base", "keep_edits": "replay-all"}))
    vers = _run(server.list_versions(VID))["versions"]
    assert len(vers) == 1
    assert vers[0]["tempo_bpm"] == 120.0 and vers[0]["label"] == "before re-tune"
    assert vers[0]["n_selections"] == 1


def test_revert_restores_base_and_selections(tmp_store: Any) -> None:
    # Re-tune to 140 (snapshots the 120 original-ish), then revert to it.
    _run(server.save_project(VID, {"notation": _notation(140.0), "layer": "base", "keep_edits": "replay-all"}))
    proj = _run(server.get_project(VID))
    assert proj["base_notation"]["tempo_bpm"] == 140.0  # re-tune applied
    vers = _run(server.list_versions(VID))["versions"]
    vid120 = next(v["version_id"] for v in vers if v["tempo_bpm"] == 120.0)

    restored = _run(server.revert_version(VID, server.RevertRequest(version_id=vid120)))
    assert restored["notation"]["tempo_bpm"] == 120.0  # base reverted
    assert [s["selection_id"] for s in restored["selections"]] == ["sel_1"]  # user layer restored


def test_revert_snapshots_current_first(tmp_store: Any) -> None:
    _run(server.save_project(VID, {"notation": _notation(140.0), "layer": "base", "keep_edits": "replay-all"}))
    before = len(_run(server.list_versions(VID))["versions"])
    vid120 = next(v["version_id"] for v in _run(server.list_versions(VID))["versions"] if v["tempo_bpm"] == 120.0)
    _run(server.revert_version(VID, server.RevertRequest(version_id=vid120)))
    after = _run(server.list_versions(VID))["versions"]
    # Revert appended a "before revert" snapshot of the 140 state → revert is undoable.
    assert len(after) == before + 1
    assert any(v["label"] == "before revert" and v["tempo_bpm"] == 140.0 for v in after)


def test_revert_unknown_version_404(tmp_store: Any) -> None:
    with pytest.raises(HTTPException) as ei:
        _run(server.revert_version(VID, server.RevertRequest(version_id="ver_nope")))
    assert ei.value.status_code == 404


def test_versions_empty_for_untouched_project(tmp_store: Any) -> None:
    # No re-tune yet → no snapshots (the transcribe-time "original" is written by
    # the pipeline worker, not by save_project, so a store-seeded project has none).
    assert _run(server.list_versions(VID))["versions"] == []
