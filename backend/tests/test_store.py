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


def test_save_is_atomic_no_tmp_left_behind(tmp_store: Any) -> None:
    store.save_project(_project())
    # A successful save leaves exactly the project JSON — no stray temp files.
    leftovers = [p.name for p in tmp_store.iterdir() if p.suffix == ".tmp" or ".tmp." in p.name]
    assert leftovers == []


def test_resave_preserves_old_file_on_write_failure(tmp_store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    first = store.save_project(_project(title="Original"))
    # Simulate a crash during the temp-file write; the previously-saved project
    # must remain intact (atomic write never truncates the live file).
    def boom(*_a: Any, **_k: Any) -> None:
        raise OSError("disk full")
    monkeypatch.setattr(store.os, "replace", boom)
    with pytest.raises(OSError):
        store.save_project(_project(title="Doomed"))
    loaded = store.load_project("abc12345678")
    assert loaded is not None and loaded["source"]["title"] == "Original"
    assert loaded["created_at"] == first["created_at"]
    # No half-written temp file survives the failure.
    assert [p.name for p in tmp_store.iterdir() if ".tmp." in p.name] == []


def test_append_and_read_events(tmp_store: Any) -> None:
    store.append_event("abc12345678", "edits", {"op": "reclassify", "bar": 12})
    store.append_event("abc12345678", "edits", {"op": "delete", "bar": 13})
    events = store.read_events("abc12345678", "edits")
    assert [e["op"] for e in events] == ["reclassify", "delete"]  # append order
    assert all(e["at"] for e in events)  # timestamp stamped


def test_read_events_missing_is_empty(tmp_store: Any) -> None:
    assert store.read_events("abc12345678", "edits") == []


def test_read_events_skips_torn_final_line(tmp_store: Any) -> None:
    store.append_event("abc12345678", "feedback", {"accepted": True})
    # Simulate a crash mid-append that left a truncated JSON line.
    with open(store.event_log_path("abc12345678", "feedback"), "a", encoding="utf-8") as f:
        f.write('{"accepted": fal')
    events = store.read_events("abc12345678", "feedback")
    assert len(events) == 1 and events[0]["accepted"] is True


def test_delete_removes_logs(tmp_store: Any) -> None:
    store.save_project(_project())
    store.append_event("abc12345678", "edits", {"op": "add"})
    assert store.event_log_path("abc12345678", "edits").exists()
    store.delete_project("abc12345678")
    assert not store.event_log_path("abc12345678", "edits").exists()


@pytest.mark.parametrize("bad_name", ["with-dash", "with space", "", "a.b", "../x"])
def test_invalid_log_name_rejected(tmp_store: Any, bad_name: str) -> None:
    with pytest.raises(ValueError):
        store.event_log_path("abc12345678", bad_name)


# === Projects-directory configuration ===================================

def test_set_projects_dir_persists_and_switches(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "_STORE_DIR", tmp_path / "old")
    monkeypatch.setattr(store, "_CONFIG_PATH", tmp_path / "config.json")
    new = tmp_path / "new_projects"
    returned = store.set_projects_dir(new)
    assert returned == new
    assert store.get_projects_dir() == new
    # persisted so a fresh load picks it up
    assert store._load_store_dir() == new


def test_set_projects_dir_moves_existing(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / "old"
    monkeypatch.setattr(store, "_STORE_DIR", old)
    monkeypatch.setattr(store, "_CONFIG_PATH", tmp_path / "config.json")
    store.save_project(_project("vid00000001"))
    store.append_event("vid00000001", "edits", {"op": "add"})
    (store.stages_dir("vid00000001")).mkdir(parents=True, exist_ok=True)
    (store.stages_dir("vid00000001") / "drums.wav").write_bytes(b"x")

    new = tmp_path / "moved"
    store.set_projects_dir(new, move_existing=True)

    # Everything followed to the new dir; the old dir is now empty.
    assert (new / "vid00000001.json").exists()
    assert (new / "vid00000001.edits.jsonl").exists()
    assert (new / "vid00000001.stages" / "drums.wav").exists()
    assert store.load_project("vid00000001") is not None  # readable from new dir
    assert list(old.iterdir()) == []


def test_set_projects_dir_refuses_absolute_and_collision(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / "old"
    monkeypatch.setattr(store, "_STORE_DIR", old)
    monkeypatch.setattr(store, "_CONFIG_PATH", tmp_path / "config.json")
    with pytest.raises(ValueError):
        store.set_projects_dir("relative/path")

    store.save_project(_project("vid00000001"))
    new = tmp_path / "moved"
    new.mkdir()
    (new / "vid00000001.json").write_text("{}")  # pre-existing collision
    with pytest.raises(FileExistsError):
        store.set_projects_dir(new, move_existing=True)
    # Failed move must not have switched the active dir.
    assert store.get_projects_dir() == old


def test_set_projects_dir_leaves_unrelated_files_alone(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """A user-chosen projects dir may hold anything; a move touches only ours."""
    old = tmp_path / "old"
    monkeypatch.setattr(store, "_STORE_DIR", old)
    monkeypatch.setattr(store, "_CONFIG_PATH", tmp_path / "config.json")
    store.save_project(_project("vid00000001"))
    (old / "taxes.pdf").write_bytes(b"mine")
    (old / "config.json").write_text('{"projects_dir": "/somewhere"}')  # not a project
    (old / "Photos").mkdir()

    store.set_projects_dir(tmp_path / "moved", move_existing=True)

    assert (tmp_path / "moved" / "vid00000001.json").exists()
    assert sorted(p.name for p in old.iterdir()) == ["Photos", "config.json", "taxes.pdf"]


def test_set_projects_dir_refuses_nested_target(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Nesting makes the move ill-defined, so it must be refused up front rather
    than fail halfway through and strand projects."""
    old = tmp_path / "old"
    monkeypatch.setattr(store, "_STORE_DIR", old)
    monkeypatch.setattr(store, "_CONFIG_PATH", tmp_path / "config.json")
    store.save_project(_project("vid00000001"))

    with pytest.raises(ValueError, match="nested"):
        store.set_projects_dir(old / "sub", move_existing=True)
    with pytest.raises(ValueError, match="nested"):
        store.set_projects_dir(old.parent, move_existing=True)

    assert store.get_projects_dir() == old
    assert (old / "vid00000001.json").exists()


def test_set_projects_dir_rolls_back_a_partial_move(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    old = tmp_path / "old"
    monkeypatch.setattr(store, "_STORE_DIR", old)
    monkeypatch.setattr(store, "_CONFIG_PATH", tmp_path / "config.json")
    store.save_project(_project("vid00000001"))
    store.append_event("vid00000001", "edits", {"op": "add"})
    before = sorted(p.name for p in old.iterdir())
    assert len(before) > 1  # need >1 entry for "partial" to mean anything

    real_move = store.shutil.move
    calls = {"n": 0}

    def flaky(src: str, dst: str) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:  # second forward move fails; rollback still works
            raise OSError("No space left on device")
        return real_move(src, dst)

    monkeypatch.setattr(store.shutil, "move", flaky)
    new = tmp_path / "moved"
    with pytest.raises(OSError):
        store.set_projects_dir(new, move_existing=True)

    assert sorted(p.name for p in old.iterdir()) == before
    assert list(new.iterdir()) == []
    assert store.get_projects_dir() == old


def test_list_projects_skips_non_project_json(tmp_store: Any) -> None:
    """The projects dir is user-chosen: a stray .json must not break the listing."""
    store.save_project(_project("vid00000001"))
    (tmp_store / "config.json").write_text('{"projects_dir": "/somewhere"}')
    (tmp_store / "notes.json").write_text("[1, 2, 3]")
    (tmp_store / "broken.json").write_text("{not json")

    summaries = store.list_projects()
    assert [s["video_id"] for s in summaries] == ["vid00000001"]


# === failure reporting on a projects-dir move ==========================


@pytest.fixture()
def isolated_store(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Store dir, config, and a move target all under one per-test tmp dir.

    `tmp_path.parent` is shared across the whole pytest run, so a target placed
    there leaks between tests; and the config must be patched or a successful
    move writes the developer's real ~/.cache/sheetydrums/config.json.
    """
    src = tmp_path / "store"
    src.mkdir()
    monkeypatch.setattr(store, "_STORE_DIR", src)
    monkeypatch.setattr(store, "_CONFIG_PATH", tmp_path / "config.json")
    return src, tmp_path / "moved"  # siblings: not nested, so the move is allowed


def test_config_write_failure_after_move_names_the_new_location(
    isolated_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-move config failure used to surface as a bare errno, leaving the
    user with files in the new dir, a config naming the old one, and projects
    that look lost after a restart."""
    _, target = isolated_store
    store.save_project(_project())
    real_write = store._atomic_write_text

    def failing_write(path: Any, text: str) -> None:
        if path == store._CONFIG_PATH:
            raise OSError("Read-only file system")
        real_write(path, text)

    monkeypatch.setattr(store, "_atomic_write_text", failing_write)
    with pytest.raises(OSError) as ei:
        store.set_projects_dir(target, move_existing=True)

    msg = str(ei.value)
    assert str(target) in msg          # where the files actually are
    assert "survive a restart" in msg  # and what to do about it
    assert (target / "abc12345678.json").exists()


def test_failed_rollback_names_the_stranded_entries(
    isolated_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rollback used to swallow its own failures, so a split store was reported
    as if the move had cleanly done nothing."""
    src, target = isolated_store
    store.save_project(_project())
    (src / "abc12345678.drums.wav").write_text("stem")
    real_move = store.shutil.move
    calls = {"n": 0}

    def flaky_move(s: str, d: str) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")            # fail the second move
        if calls["n"] == 3:
            raise OSError("rollback failed too")  # and fail undoing the first
        return real_move(s, d)

    monkeypatch.setattr(store.shutil, "move", flaky_move)
    with pytest.raises(OSError) as ei:
        store.set_projects_dir(target, move_existing=True)

    msg = str(ei.value)
    assert "could not be fully undone" in msg
    assert "disk full" in msg    # the original cause is still reported
    assert "abc12345678" in msg  # and which entry is stranded
    assert str(target) in msg and str(src) in msg


def test_clean_rollback_still_raises_the_original_error(
    isolated_store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    # When rollback succeeds the caller should see the real cause, not a
    # "stranded entries" message, and nothing should have moved.
    src, target = isolated_store
    store.save_project(_project())
    (src / "abc12345678.drums.wav").write_text("stem")
    real_move = store.shutil.move
    calls = {"n": 0}

    def flaky_move(s: str, d: str) -> Any:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real_move(s, d)

    monkeypatch.setattr(store.shutil, "move", flaky_move)
    with pytest.raises(OSError) as ei:
        store.set_projects_dir(target, move_existing=True)
    assert "disk full" in str(ei.value)
    assert "could not be fully undone" not in str(ei.value)
    assert sorted(p.name for p in src.iterdir()) == [
        "abc12345678.drums.wav", "abc12345678.json"
    ]
    assert store.get_projects_dir() == src  # never switched


@pytest.mark.parametrize("bad", ["a.b", "abc.json", ".", "..", ""])
def test_dotted_video_id_rejected(bad: str) -> None:
    """`_owned_entries` keys an entry by the segment before its first dot, so a
    dotted id would disagree with `_known_video_ids` and be left behind."""
    with pytest.raises(ValueError):
        store.event_log_path(bad, "edits")
