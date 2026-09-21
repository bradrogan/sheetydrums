"""Bipartite hit-matching + precision/recall/F1 — the one scorer shared by the
offline eval harness (``scripts/eval``) and the Phase 3 edit-scored parameter
search (``search/score.py``).

Positions are numeric and comparable, matched within ``tolerance`` in the same
units: the eval tab matcher passes sixteenth-note **ints** (tolerance ``1``); the
search's notation matcher passes exact **Fractions** (tolerance
``anchor.DEFAULT_TOL`` = ``1/32``). The algorithm only sorts, subtracts, and
compares, so it is agnostic to which.
"""
from __future__ import annotations

from typing import Any


def bipartite_match(
    t_pos: set[Any], p_pos: set[Any], tolerance: Any
) -> tuple[int, set[Any], set[Any]]:
    """Greedily match predicted positions ``p_pos`` to truth positions ``t_pos``
    within ``tolerance`` (each truth position used at most once). Returns
    ``(matched_count, used_truth_positions, used_pred_positions)``; the unmatched
    remainders are ``t_pos - used_t`` (false negatives) and ``p_pos - used_p``
    (false positives)."""
    matched = 0
    used_t: set[Any] = set()
    used_p: set[Any] = set()
    for pi in sorted(p_pos):
        for ti in sorted(t_pos):
            if ti in used_t:
                continue
            if abs(pi - ti) <= tolerance:
                matched += 1
                used_t.add(ti)
                used_p.add(pi)
                break
    return matched, used_t, used_p


def stats(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    """(precision, recall, F1) from tp/fp/fn; 0.0 for any undefined ratio."""
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f
