"""The promoted bipartite matcher + P/R/F1 (sheetydrums.matching). Generic over
int (eval harness) and Fraction (Phase 3 search) positions."""
from __future__ import annotations

from fractions import Fraction

from sheetydrums.matching import bipartite_match, stats


def test_exact_match_ints() -> None:
    matched, used_t, used_p = bipartite_match({0, 4, 8}, {0, 4, 8}, 0)
    assert matched == 3
    assert used_t == {0, 4, 8} and used_p == {0, 4, 8}


def test_tolerance_matches_within_window_only() -> None:
    # tolerance 1: 5 matches 4, 9 has no truth within 1 → unmatched.
    matched, used_t, used_p = bipartite_match({4}, {5, 9}, 1)
    assert matched == 1 and used_t == {4} and used_p == {5}


def test_each_truth_used_once() -> None:
    # Two predictions near one truth: only one matches (truth consumed).
    matched, _, used_p = bipartite_match({4}, {4, 5}, 1)
    assert matched == 1 and len(used_p) == 1


def test_fraction_positions_same_slot_matches_adjacent_does_not() -> None:
    tol = Fraction(1, 32)
    # same 16th slot → matches; one 16th apart (1/16 > 1/32) → no.
    m1, _, _ = bipartite_match({Fraction(1, 4)}, {Fraction(1, 4)}, tol)
    m2, _, _ = bipartite_match({Fraction(1, 4)}, {Fraction(5, 16)}, tol)
    assert m1 == 1 and m2 == 0


def test_stats() -> None:
    assert stats(3, 0, 0) == (1.0, 1.0, 1.0)
    p, r, f = stats(1, 1, 1)
    assert p == 0.5 and r == 0.5 and abs(f - 0.5) < 1e-9
    assert stats(0, 0, 0) == (0.0, 0.0, 0.0)
