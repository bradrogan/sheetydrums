"""Phase 3 §3a — the edit-scored search objective (search/score.py).

Reproduce-F1 of a candidate notation against verified selections; the residual
drives the capped loop. Pure fixtures, no pipeline/models.
"""
from __future__ import annotations

from typing import Any

from sheetydrums.search.score import (
    SCHEMA_INSTRUMENTS,
    lane_instruments,
    reproduce_score,
)
from sheetydrums.validate import load_schema


def _notation(bars: dict[int, list[tuple[str, str]]]) -> dict[str, Any]:
    """Build an events notation from {bar_index: [(instrument, position), ...]}."""
    return {
        "version": "1", "tempo_bpm": 120.0,
        "time_signature": {"numerator": 4, "denominator": 4},
        "bars": [
            {"index": i, "start_seconds": float(i),
             "notes": [{"instrument": inst, "position": pos, "duration": "1/8"} for inst, pos in hits]}
            for i, hits in sorted(bars.items())
        ],
    }


def _selection(lane: str, bar: int, hits: list[tuple[str, str]], sid: str = "sel_1") -> dict[str, Any]:
    return {
        "selection_id": sid, "origin": "user", "lane": lane,
        "bar_start": bar, "bar_end": bar,
        "notes": [{"bar": bar, "note": {"instrument": inst, "position": pos, "duration": "1/8"}}
                  for inst, pos in hits],
        "ops": [], "verified": True,
    }


# === lane → instruments (inverse of anchor.lane_of) ======================

def test_lane_instruments() -> None:
    assert lane_instruments("hihat") == ("hihat_closed", "hihat_open")
    assert lane_instruments("snare") == ("snare",)
    assert lane_instruments("tom_high") == ("tom_high",)
    assert lane_instruments("hihat_chick") == ("hihat_chick",)


def test_schema_instruments_in_sync_with_schema() -> None:
    enum = load_schema()["$defs"]["Instrument"]["enum"]
    assert set(SCHEMA_INSTRUMENTS) == set(enum)


# === reproduce_score =====================================================

def test_exact_match_scores_f1_one() -> None:
    sel = _selection("snare", 1, [("snare", "1/4"), ("snare", "3/4")])
    cand = _notation({1: [("snare", "1/4"), ("snare", "3/4")]})
    r = reproduce_score(cand, [sel])
    assert r.f1 == 1.0 and r.tp == 2 and r.fp == 0 and r.fn == 0 and r.residual == []


def test_closed_vs_open_mismatch() -> None:
    # User verified an OPEN hat; candidate has a CLOSED hat at the same slot.
    sel = _selection("hihat", 1, [("hihat_open", "0")])
    cand = _notation({1: [("hihat_closed", "0")]})
    r = reproduce_score(cand, [sel])
    assert r.f1 < 1.0
    assert r.fn == 1 and r.fp == 1  # missing the open, extra the closed
    kinds = {(m.instrument, m.kind) for m in r.residual}
    assert ("hihat_open", "missing") in kinds
    assert ("hihat_closed", "extra") in kinds


def test_sweeping_fix_outside_region_not_penalised() -> None:
    # A candidate that reproduces the selection but ALSO changes many other bars
    # in the lane still scores F1=1.0 — reproduce_score only looks in-region, so a
    # fix that propagates everywhere (D3) is never penalised by the objective.
    sel = _selection("snare", 1, [("snare", "1/4")])
    cand = _notation({
        1: [("snare", "1/4")],
        2: [("snare", "0"), ("snare", "1/8"), ("snare", "1/4")],  # lots of change elsewhere
        3: [("snare", "1/2")],
    })
    r = reproduce_score(cand, [sel])
    assert r.f1 == 1.0


def test_multi_selection_aggregation() -> None:
    s1 = _selection("snare", 1, [("snare", "1/4")], sid="a")
    s2 = _selection("hihat", 2, [("hihat_open", "0"), ("hihat_open", "1/2")], sid="b")
    # candidate matches s1 fully; s2 half (one open present, one missing).
    cand = _notation({1: [("snare", "1/4")], 2: [("hihat_open", "0")]})
    r = reproduce_score(cand, [s1, s2])
    assert r.tp == 2 and r.fn == 1 and r.fp == 0
    assert r.per_selection["a"] == 1.0
    assert r.per_selection["b"] < 1.0


def test_missing_bar_all_labels_miss() -> None:
    # Selection names bar 5, candidate only has bar 1 → every label is a miss.
    sel = _selection("snare", 5, [("snare", "0"), ("snare", "1/2")])
    cand = _notation({1: [("snare", "0")]})
    r = reproduce_score(cand, [sel])
    assert r.tp == 0 and r.fn == 2 and r.recall == 0.0


def test_extra_candidate_note_is_false_positive() -> None:
    # Selection says one snare; candidate has two → one extra (FP), F1 < 1.
    sel = _selection("snare", 1, [("snare", "1/4")])
    cand = _notation({1: [("snare", "1/4"), ("snare", "3/4")]})
    r = reproduce_score(cand, [sel])
    assert r.tp == 1 and r.fp == 1 and r.fn == 0
    assert any(m.kind == "extra" and m.position == "3/4" for m in r.residual)


def test_other_lane_notes_ignored() -> None:
    # A kick in the bar doesn't affect a snare-lane selection's score.
    sel = _selection("snare", 1, [("snare", "1/4")])
    cand = _notation({1: [("snare", "1/4"), ("kick", "0")]})
    r = reproduce_score(cand, [sel])
    assert r.f1 == 1.0 and r.fp == 0
