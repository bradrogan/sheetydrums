"""Unit tests for the project store — pure disk I/O, no models."""
from __future__ import annotations

from typing import Any

import pytest

from sheetydrums import store

_NOTATION: dict[str, Any] = {
    "version": "1",
    "tempo_bpm": 120.0,
    "time_signature": {"numerator": 4, "denominator": 4},
    "bars": [
        {"index": 1, "start_seconds": 0.0, "notes": [
            {"instrument": "kick", "position": "0", "duration": "1/8"},
            {"instrument": "snare", "position": "1/4", "duration": "1/8"},
        ]},
    ],
}


def _project(vid: str = "abc12345678", title: str = "Title") -> dict[str, Any]:
    return {
        "video_id": vid,
        "source": {"url": f"https://youtu.be/{vid}", "video_id": vid, "title": title},
        "notation": _NOTATION,
    }


@pytest.fixture()
def tmp_store(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(store, "_STORE_DIR", tmp_path)
    return tmp_path


def test_save_and_load_roundtrip(tmp_store: Any) -> None:
    saved = store.save_project(_project())
    assert saved["created_at"] and saved["updated_at"]
    assert store.project_exists("abc12345678")
    loaded = store.load_project("abc12345678")
    assert loaded is not None
    assert loaded["notation"] == _NOTATION
    assert loaded["source"]["title"] == "Title"


def test_created_at_preserved_on_resave(tmp_store: Any) -> None:
    first = store.save_project(_project())
    second = store.save_project(_project(title="Retitled"))
    assert second["created_at"] == first["created_at"]  # preserved across saves
    assert second["updated_at"] >= first["updated_at"]
    loaded = store.load_project("abc12345678")
    assert loaded is not None and loaded["source"]["title"] == "Retitled"


def test_load_missing_returns_none(tmp_store: Any) -> None:
    assert store.load_project("missing12345") is None
    assert store.project_exists("missing12345") is False


def test_list_projects_summary(tmp_store: Any) -> None:
    store.save_project(_project("vid00000001"))
    store.save_project(_project("vid00000002"))
    summaries = store.list_projects()
    assert {s["video_id"] for s in summaries} == {"vid00000001", "vid00000002"}
    s = summaries[0]
    assert s["thumbnail"].endswith(".jpg")
    assert s["n_bars"] == 1 and s["n_notes"] == 2
    assert s["tempo_bpm"] == 120.0


def test_delete_project(tmp_store: Any) -> None:
    store.save_project(_project())
    assert store.delete_project("abc12345678") is True
    assert store.delete_project("abc12345678") is False  # already gone
    assert store.load_project("abc12345678") is None


def test_stem_helpers(tmp_store: Any) -> None:
    assert store.has_stem("abc12345678") is False
    assert store.stem_path("abc12345678").name == "abc12345678.drums.wav"


@pytest.mark.parametrize("bad_id", ["../evil", "a/b", "a\\b", "", ".", ".."])
def test_invalid_video_id_rejected(tmp_store: Any, bad_id: str) -> None:
    with pytest.raises(ValueError):
        store.stem_path(bad_id)


def test_save_invalid_notation_raises(tmp_store: Any) -> None:
    bad = _project()
    bad["notation"] = {"version": "1"}  # missing required tempo_bpm/bars/time_signature
    with pytest.raises(Exception):
        store.save_project(bad)
    assert store.load_project("abc12345678") is None  # nothing persisted
