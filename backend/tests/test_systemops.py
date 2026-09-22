"""Phase 3c §3c — the system-layer delta (layering.notation_to_system_ops).

Diff a search-winner candidate against the base into system ops, restricted to
the targeted lanes and excluding verified regions. The load-bearing property is
the round-trip: compose(base, {ops}) reproduces the candidate within those lanes.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any

from sheetydrums.anchor import lane_of, parse_position
from sheetydrums.layering import compose, notation_to_system_ops


def _notation(bars: dict[int, list[tuple[str, str]]]) -> dict[str, Any]:
    return {
        "version": "1", "tempo_bpm": 120.0,
        "time_signature": {"numerator": 4, "denominator": 4},
        "bars": [
            {"index": i, "start_seconds": float(i),
             "notes": [{"instrument": inst, "position": pos, "duration": "1/8"} for inst, pos in hits]}
            for i, hits in sorted(bars.items())
        ],
    }


def _lane_notes(notation: dict[str, Any], lanes: set[str]) -> set[tuple[int, str, Fraction]]:
    """(bar, instrument, position) of every note in `lanes` — for set comparison."""
    return {
        (b["index"], n["instrument"], parse_position(n["position"]))
        for b in notation["bars"] for n in b["notes"]
        if lane_of(n["instrument"]) in lanes
    }


def _selection(lane: str, bar: int, hits: list[tuple[str, str]], sid: str = "s") -> dict[str, Any]:
    return {
        "selection_id": sid, "origin": "user", "lane": lane,
        "bar_start": bar, "bar_end": bar,
        "notes": [{"bar": bar, "note": {"instrument": inst, "position": pos, "duration": "1/8"}}
                  for inst, pos in hits],
        "ops": [], "verified": True,
    }


def _roundtrip(base: dict[str, Any], candidate: dict[str, Any], lanes: set[str],
               selections: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ops = notation_to_system_ops(base, candidate, lanes, selections)
    effective, _ = compose(base, {"pass_id": "p1", "ops": ops}, selections)
    return ops, effective


# === basic diffs =========================================================

def test_add_only() -> None:
    base = _notation({1: [("hihat_closed", "0")]})
    cand = _notation({1: [("hihat_closed", "0"), ("hihat_closed", "1/2")]})
    ops, eff = _roundtrip(base, cand, {"hihat"})
    assert [o["kind"] for o in ops] == ["add"]
    assert ops[0]["origin"] == "system"
    assert _lane_notes(eff, {"hihat"}) == _lane_notes(cand, {"hihat"})


def test_delete_only() -> None:
    base = _notation({1: [("hihat_closed", "0"), ("hihat_closed", "1/2")]})
    cand = _notation({1: [("hihat_closed", "0")]})
    ops, eff = _roundtrip(base, cand, {"hihat"})
    assert [o["kind"] for o in ops] == ["delete"]
    assert _lane_notes(eff, {"hihat"}) == _lane_notes(cand, {"hihat"})


def test_same_slot_change_is_reclassify() -> None:
    base = _notation({1: [("hihat_closed", "0")]})
    cand = _notation({1: [("hihat_open", "0")]})
    ops, eff = _roundtrip(base, cand, {"hihat"})
    assert len(ops) == 1 and ops[0]["kind"] == "reclassify"
    assert ops[0]["from"] == "hihat_closed" and ops[0]["to"] == "hihat_open"
    assert _lane_notes(eff, {"hihat"}) == _lane_notes(cand, {"hihat"})


def test_cross_family_reclassify_when_both_lanes_targeted() -> None:
    # crash → hihat_closed at the same slot, with both lanes in the target set.
    base = _notation({1: [("crash", "0")]})
    cand = _notation({1: [("hihat_closed", "0")]})
    ops, eff = _roundtrip(base, cand, {"crash", "hihat"})
    assert len(ops) == 1 and ops[0]["kind"] == "reclassify"
    assert ops[0]["from"] == "crash" and ops[0]["to"] == "hihat_closed"
    assert _lane_notes(eff, {"crash", "hihat"}) == _lane_notes(cand, {"crash", "hihat"})


# === lane restriction (D4) ==============================================

def test_untargeted_lane_change_is_ignored() -> None:
    # The candidate also gained a kick, but the pass targets only hihat → no
    # kick op, and the composed kick stays as the base had it (absent).
    base = _notation({1: [("hihat_closed", "0")]})
    cand = _notation({1: [("hihat_open", "0"), ("kick", "0")]})
    ops, eff = _roundtrip(base, cand, {"hihat"})
    assert all(lane_of(str(o.get("instrument") or o.get("to"))) == "hihat" for o in ops)
    assert _lane_notes(eff, {"kick"}) == set()  # kick untouched (base had none)
    assert _lane_notes(eff, {"hihat"}) == _lane_notes(cand, {"hihat"})


# === verified regions excluded ==========================================

def test_op_inside_verified_region_omitted() -> None:
    base = _notation({1: [("hihat_closed", "0")], 2: [("hihat_closed", "0")]})
    cand = _notation({1: [("hihat_open", "0")], 2: [("hihat_open", "0")]})
    # Bar 1's hihat is user-verified → the pass must not touch it.
    sel = _selection("hihat", 1, [("hihat_open", "0")])
    ops = notation_to_system_ops(base, cand, {"hihat"}, [sel])
    assert all(o["bar"] != 1 for o in ops)  # nothing proposed inside the verified bar
    assert any(o["bar"] == 2 for o in ops)  # bar 2 (unverified) still fixed


# === edges ===============================================================

def test_bar_absent_from_candidate_yields_no_ops() -> None:
    base = _notation({1: [("hihat_closed", "0")], 2: [("hihat_closed", "0")]})
    cand = _notation({1: [("hihat_open", "0")]})  # candidate dropped bar 2
    ops = notation_to_system_ops(base, cand, {"hihat"})
    assert all(o["bar"] == 1 for o in ops)


def test_identical_notation_is_empty_delta() -> None:
    base = _notation({1: [("snare", "0"), ("snare", "1/2")]})
    assert notation_to_system_ops(base, base, {"snare"}) == []
