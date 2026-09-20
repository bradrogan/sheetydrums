"""Unit tests for the pure layering engine (compose) and anchor/replay.

No I/O, no models — just base/system/user composition and op anchoring.
"""
from __future__ import annotations

from typing import Any

import jsonschema
import pytest

from sheetydrums import anchor, layering
from sheetydrums.anchor import lane_of


def _base() -> dict[str, Any]:
    return {
        "version": "1",
        "tempo_bpm": 120.0,
        "time_signature": {"numerator": 4, "denominator": 4},
        "bars": [
            {"index": 1, "start_seconds": 0.0, "notes": [
                {"instrument": "kick", "position": "0", "duration": "1/8"},
                {"instrument": "hihat_closed", "position": "0", "duration": "1/8"},
                {"instrument": "hihat_closed", "position": "1/4", "duration": "1/8"},
                {"instrument": "snare", "position": "1/4", "duration": "1/8"},
            ]},
            {"index": 2, "start_seconds": 2.0, "notes": [
                {"instrument": "kick", "position": "0", "duration": "1/8"},
                {"instrument": "hihat_closed", "position": "0", "duration": "1/8"},
            ]},
        ],
    }


def _bar(notation: dict[str, Any], index: int) -> dict[str, Any]:
    return next(b for b in notation["bars"] if b["index"] == index)


def _note_set(notation: dict[str, Any]) -> set[tuple[int, str, str]]:
    return {
        (bar["index"], n["instrument"], n["position"])
        for bar in notation["bars"]
        for n in bar["notes"]
    }


def _selection(lane: str, bar_start: int, bar_end: int, notes: list[dict[str, Any]],
               ops: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "selection_id": "s1", "origin": "user", "lane": lane,
        "bar_start": bar_start, "bar_end": bar_end,
        "notes": notes, "ops": ops or [], "verified": True,
    }


# === compose ============================================================

def test_compose_identity_no_layers() -> None:
    base = _base()
    eff, origin = layering.compose(base, None, [])
    assert _note_set(eff) == _note_set(base)
    assert all(o == "base" for origins in origin.values() for o in origins)


def test_user_selection_overwrites_region_and_tags_user() -> None:
    base = _base()
    sel = _selection("hihat", 1, 1, [
        {"bar": 1, "note": {"instrument": "hihat_open", "position": "0", "duration": "1/8"}},
    ])
    eff, origin = layering.compose(base, None, [sel])
    b1 = _bar(eff, 1)
    hats = [n for n in b1["notes"] if lane_of(n["instrument"]) == "hihat"]
    # both base closed hats replaced by the single frozen open hat
    assert [h["instrument"] for h in hats] == ["hihat_open"]
    assert hats[0]["position"] == "0"
    # non-hat notes survive untouched
    assert any(n["instrument"] == "kick" for n in b1["notes"])
    assert any(n["instrument"] == "snare" for n in b1["notes"])
    assert origin[1].count("user") == 1
    assert set(origin[1]) == {"base", "user"}


def test_verified_empty_region_clears_lane() -> None:
    # A verified selection with no notes asserts the lane is empty there (closed world).
    base = _base()
    sel = _selection("hihat", 1, 1, [])
    eff, _ = layering.compose(base, None, [sel])
    assert not [n for n in _bar(eff, 1)["notes"] if lane_of(n["instrument"]) == "hihat"]
    # bar 2 hi-hat untouched (outside the region)
    assert [n for n in _bar(eff, 2)["notes"] if lane_of(n["instrument"]) == "hihat"]


def test_system_op_dropped_in_region_applied_outside() -> None:
    base = _base()
    sel = _selection("hihat", 1, 1, [])  # user owns hi-hat in bar 1
    system = {"pass_id": "p1", "ops": [
        {"kind": "add", "bar": 1, "position": "1/2", "instrument": "hihat_closed", "duration": "1/8", "origin": "system"},
        {"kind": "add", "bar": 2, "position": "1/2", "instrument": "hihat_closed", "duration": "1/8", "origin": "system"},
    ]}
    eff, origin = layering.compose(base, system, [sel])
    # bar 1: system add landed in the verified hi-hat region -> dropped
    assert not [n for n in _bar(eff, 1)["notes"] if lane_of(n["instrument"]) == "hihat"]
    # bar 2: system add applied, tagged system
    b2 = _bar(eff, 2)
    added = [n for n in b2["notes"] if n["position"] == "1/2" and n["instrument"] == "hihat_closed"]
    assert len(added) == 1
    assert origin[2][b2["notes"].index(added[0])] == "system"


def test_user_beats_system_on_same_lane_bar() -> None:
    base = _base()
    sel = _selection("hihat", 1, 1, [
        {"bar": 1, "note": {"instrument": "hihat_open", "position": "0", "duration": "1/8"}},
    ])
    system = {"pass_id": "p1", "ops": [
        {"kind": "reclassify", "bar": 1, "position": "0", "from": "hihat_closed", "to": "ride", "origin": "system"},
    ]}
    eff, _ = layering.compose(base, system, [sel])
    b1 = _bar(eff, 1)
    # the system reclassify to ride is dropped (hi-hat bar 1 is user-owned); no ride appears
    assert not any(n["instrument"] == "ride" for n in b1["notes"])
    hats = [n for n in b1["notes"] if lane_of(n["instrument"]) == "hihat"]
    assert [h["instrument"] for h in hats] == ["hihat_open"]


def test_compose_output_valid_with_hihat_open_sustain() -> None:
    base = _base()
    sel = _selection("hihat", 1, 1, [
        {"bar": 1, "note": {"instrument": "hihat_open", "position": "0", "duration": "1/8", "sustain_until": "1/2"}},
    ])
    eff, _ = layering.compose(base, None, [sel])  # calls validate() internally; must not raise
    n = next(x for x in _bar(eff, 1)["notes"] if x["instrument"] == "hihat_open")
    assert n["sustain_until"] == "1/2"


def test_compose_rejects_invalid_sustain() -> None:
    base = _base()
    bad = _selection("hihat", 1, 1, [
        {"bar": 1, "note": {"instrument": "hihat_open", "position": "1/2", "duration": "1/8", "sustain_until": "1/4"}},
    ])
    with pytest.raises((ValueError, jsonschema.ValidationError)):
        layering.compose(base, None, [bad])


# === anchor / replay ====================================================

def test_replay_ops_match_miss_and_add() -> None:
    base = _base()
    ops = [
        {"kind": "reclassify", "bar": 1, "position": "1/4", "from": "hihat_closed", "to": "hihat_open"},
        {"kind": "delete", "bar": 1, "position": "1/2", "instrument": "snare"},  # nothing there
        {"kind": "add", "bar": 2, "position": "1/4", "instrument": "snare", "duration": "1/8"},
    ]
    new, conflicts = anchor.replay_ops(base, ops)
    assert len(conflicts) == 1 and conflicts[0]["op"]["kind"] == "delete"
    assert anchor.find_note(_bar(new, 1)["notes"], "1/4", "hihat_open") is not None
    assert anchor.find_note(_bar(new, 2)["notes"], "1/4", "snare") is not None


def test_replay_anchors_triplet_position_exactly() -> None:
    base = _base()
    _bar(base, 1)["notes"].append({"instrument": "tom_mid", "position": "1/6", "duration": "1/8"})
    ops = [{"kind": "reclassify", "bar": 1, "position": "1/6", "from": "tom_mid", "to": "tom_low"}]
    new, conflicts = anchor.replay_ops(base, ops)
    assert conflicts == []
    # exact-rational anchoring finds the 8th-triplet note; *16 rounding would have
    # snapped 1/6 -> slot 3 (=3/16) and missed it.
    assert anchor.find_note(_bar(new, 1)["notes"], "1/6", "tom_low") is not None


def test_reclassify_preserves_duration_in_place() -> None:
    base = _base()
    hat = anchor.find_note(_bar(base, 1)["notes"], "1/4", "hihat_closed")
    assert hat is not None
    hat["duration"] = "1/16"
    ops = [{"kind": "reclassify", "bar": 1, "position": "1/4", "from": "hihat_closed", "to": "hihat_open"}]
    new, _ = anchor.replay_ops(base, ops)
    out = anchor.find_note(_bar(new, 1)["notes"], "1/4", "hihat_open")
    assert out is not None and out["duration"] == "1/16"


def test_frozen_notes_overlay_sustain() -> None:
    base = _base()
    ops = [{"kind": "reclassify", "bar": 1, "position": "1/4", "from": "hihat_closed", "to": "hihat_open"}]
    frozen = [{"bar": 1, "note": {"instrument": "hihat_open", "position": "1/4", "duration": "1/8", "sustain_until": "1/2"}}]
    new, _ = anchor.replay_ops(base, ops, frozen_notes=frozen)
    out = anchor.find_note(_bar(new, 1)["notes"], "1/4", "hihat_open")
    assert out is not None and out.get("sustain_until") == "1/2"


def test_move_conflict_on_occupied_destination() -> None:
    base = _base()
    # bar 1 has hihat_closed at 0 and 1/4; move 0 -> 1/4 collides.
    ops = [{"kind": "move", "bar": 1, "from_position": "0", "to_position": "1/4", "instrument": "hihat_closed"}]
    _, conflicts = anchor.replay_ops(base, ops)
    assert len(conflicts) == 1 and "occupied" in conflicts[0]["reason"]


def test_region_status_ok_missing_drifted() -> None:
    base = _base()
    fp = anchor.region_fingerprint(base, "hihat", 1, 1)
    assert anchor.region_status(
        {"lane": "hihat", "bar_start": 1, "bar_end": 1, "base_fingerprint": fp}, base) == "ok"
    assert anchor.region_status(
        {"lane": "hihat", "bar_start": 5, "bar_end": 6, "base_fingerprint": fp}, base) == "missing"
    assert anchor.region_status(
        {"lane": "hihat", "bar_start": 1, "bar_end": 1, "base_fingerprint": "deadbeef"}, base) == "drifted"


def test_region_fingerprint_changes_with_lane_notes() -> None:
    base = _base()
    fp = anchor.region_fingerprint(base, "hihat", 1, 1)
    _bar(base, 1)["notes"].append({"instrument": "hihat_open", "position": "1/2", "duration": "1/8"})
    assert anchor.region_fingerprint(base, "hihat", 1, 1) != fp
