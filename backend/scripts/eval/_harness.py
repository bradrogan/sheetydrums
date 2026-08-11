"""Shared scoring harness for the per-song eval comparison scripts.

Extracted verbatim (behaviour-preserving) from
``compare_treble_charger_brand_new_low.py`` so newer songs can reuse the tab
parser, DP bar-alignment, and per-instrument precision/recall/F1 reporting
without copy-pasting ~300 lines each. The original Treble Charger script stays
self-contained; new scripts (Disco Inferno, Boogie Oogie Oogie, ...) import
``run_report`` from here and only supply their own ``build_tab()``.

Tab convention (see parse_row):
- Each line "X-|...|...|" — leading letter = instrument line, then bars of 16
  sixteenth-note cells. Any non-'-' char in a cell = a hit on that line.
- H-line lowercase 'x' = closed hat, uppercase 'X' = open hat (this is the whole
  point of the disco/funk reference songs).
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from fractions import Fraction
from pathlib import Path
from typing import Callable


# === Per-line, per-char mapping to schema instruments ====================
# Lowercase 'x' on the H line is a closed hat; uppercase 'X' is an open hat.
LINE_CHAR_TO_INST: dict[tuple[str, str], str] = {
    ("B", "o"): "kick",
    ("S", "o"): "snare",
    ("S", "g"): "snare",   # ghost note
    ("S", "f"): "snare",   # flam
    ("S", "X"): "snare",
    ("H", "x"): "hihat_closed",
    ("H", "X"): "hihat_open",
    ("H", "o"): "hihat_open",   # some tabs mark open hats with 'o' above the x
    ("C", "x"): "crash",
    ("C", "X"): "crash",
    ("R", "x"): "ride",
    ("R", "b"): "ride",   # bell
    ("R", "X"): "ride",
    ("t", "o"): "tom_high",   # rack tom
    ("F", "o"): "tom_low",    # floor tom
}


SCORED_COLLAPSED = ["kick", "snare", "hihat_closed", "ride"]
SCORED_FULL = [
    "kick", "snare",
    "hihat_closed", "hihat_open",
    "ride", "crash",
    "tom_high", "tom_mid", "tom_low",
]


def parse_row(text: str, char_map: dict[tuple[str, str], str] | None = None) -> list[dict[str, set[int]]]:
    """Parse a multi-line row of tab into per-bar {instrument: set(16th positions)} dicts."""
    cmap = char_map or LINE_CHAR_TO_INST
    lines = [l for l in text.strip().split("\n") if l.strip()]
    per_line: dict[str, list[str]] = {}
    n_bars = 0
    for line in lines:
        line = line.strip()
        if len(line) < 2 or line[1] != "-":
            continue  # not an instrument line
        letter = line[0]
        rest = line[2:]  # after "X-" the rest is "|bar1|bar2|...|"
        parts = [p for p in rest.split("|") if p]
        bars_pat = [p[:16].ljust(16, "-") for p in parts]
        per_line[letter] = bars_pat
        n_bars = max(n_bars, len(bars_pat))

    bars: list[dict[str, set[int]]] = []
    for i in range(n_bars):
        bar: dict[str, set[int]] = defaultdict(set)
        for letter, patterns in per_line.items():
            if i >= len(patterns):
                continue
            for pos, char in enumerate(patterns[i]):
                if char == "-":
                    continue
                inst = cmap.get((letter, char))
                if inst:
                    bar[inst].add(pos)
        bars.append(dict(bar))
    return bars


# === Collapse logic (mirrors the pipeline quantizer fallback) ============

def _apply_collapses(inst: str, scored: list[str], collapse_crash: bool) -> str:
    if collapse_crash and inst == "crash":
        inst = "ride"
    if inst == "hihat_open" and "hihat_open" not in scored:
        inst = "hihat_closed"
    if inst in ("tom_high", "tom_low") and inst not in scored:
        inst = "tom_mid"
    return inst


def events_bar_hits(bar: dict, scored: list[str], collapse_crash: bool) -> dict[str, set[int]]:
    hits: dict[str, set[int]] = defaultdict(set)
    for n in bar.get("notes", []):
        pos_frac = Fraction(n["position"])
        idx = int(round(float(pos_frac) * 16))
        if not (0 <= idx <= 15):
            continue
        inst = _apply_collapses(n["instrument"], scored, collapse_crash)
        if inst in scored:
            hits[inst].add(idx)
    return dict(hits)


def collapse_tab_hits(bar: dict[str, set[int]], scored: list[str], collapse_crash: bool) -> dict[str, set[int]]:
    out: dict[str, set[int]] = defaultdict(set)
    for inst, positions in bar.items():
        inst = _apply_collapses(inst, scored, collapse_crash)
        if inst in scored:
            out[inst].update(positions)
    return dict(out)


# === DP alignment ========================================================

def bipartite_match(t_pos, p_pos, tolerance: int) -> tuple[int, set[int], set[int]]:
    matched = 0
    used_t: set[int] = set()
    used_p: set[int] = set()
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


def bar_match_score(tab_bar, pl_bar, scored: list[str], tolerance: int) -> float:
    matched = 0
    tab_total = 0
    pl_total = 0
    for inst in scored:
        m, _, _ = bipartite_match(tab_bar.get(inst, set()), pl_bar.get(inst, set()), tolerance)
        matched += m
        tab_total += len(tab_bar.get(inst, set()))
        pl_total += len(pl_bar.get(inst, set()))
    return matched * 2.0 - ((tab_total - matched) + (pl_total - matched)) * 0.5


def align(tab_bars, pl_bars, scored: list[str], tolerance: int) -> list[tuple[int | None, int | None]]:
    n_t = len(tab_bars)
    n_p = len(pl_bars)
    GAP_TAB = -0.5   # over-expansion is expected; cheap to skip a tab bar
    GAP_PL = -4.0    # pipeline shouldn't have phantom bars; expensive to skip

    NEG = float("-inf")
    dp = [[NEG] * (n_p + 1) for _ in range(n_t + 1)]
    back = [[""] * (n_p + 1) for _ in range(n_t + 1)]
    dp[0][0] = 0.0
    for i in range(1, n_t + 1):
        dp[i][0] = dp[i - 1][0] + GAP_TAB
        back[i][0] = "T"
    for j in range(1, n_p + 1):
        dp[0][j] = dp[0][j - 1] + GAP_PL
        back[0][j] = "P"

    for i in range(1, n_t + 1):
        for j in range(1, n_p + 1):
            m = dp[i - 1][j - 1] + bar_match_score(tab_bars[i - 1], pl_bars[j - 1], scored, tolerance)
            t = dp[i - 1][j] + GAP_TAB
            p = dp[i][j - 1] + GAP_PL
            best = max(m, t, p)
            dp[i][j] = best
            back[i][j] = "M" if best == m else ("T" if best == t else "P")

    pairs: list[tuple[int | None, int | None]] = []
    i, j = n_t, n_p
    while i > 0 or j > 0:
        op = back[i][j]
        if op == "M":
            pairs.append((i - 1, j - 1))
            i -= 1; j -= 1
        elif op == "T":
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    pairs.reverse()
    return pairs


# === Scoring + reporting =================================================

def stats(tp: int, fp: int, fn: int) -> tuple[float, float, float]:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def aggregate(pairs, tab_bars, pl_bars, scored: list[str], tolerance: int):
    per: dict[str, list[int]] = {inst: [0, 0, 0] for inst in scored}  # tp, fp, fn
    for ti, pi in pairs:
        if ti is None or pi is None:
            continue
        for inst in scored:
            t = tab_bars[ti].get(inst, set())
            p = pl_bars[pi].get(inst, set())
            _, used_t, used_p = bipartite_match(t, p, tolerance)
            per[inst][0] += len(used_t)
            per[inst][1] += len(p - used_p)
            per[inst][2] += len(t - used_t)
    return per


BuildTab = Callable[[], "tuple[list[dict[str, set[int]]], list[tuple[str, int]]]"]


def run_report(build_tab: BuildTab, source_url: str, argv: list[str] | None = None) -> int:
    """Score an events.json against a song's ``build_tab()`` and print the report.

    ``build_tab`` returns (flat_bars, section_starts) exactly like the Treble
    Charger script's local build_tab. ``source_url`` is printed for reference.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("events_path", type=Path, help="Path to the pipeline's events.json output")
    parser.add_argument("--full-vocab", action="store_true",
                        help="Score the v2 7-class vocab (hihat_open, crash) instead of the "
                             "collapsed 4-class vocab")
    parser.add_argument("--tolerance", type=int, default=1,
                        help="Sixteenth-note tolerance for matching hits (default 1)")
    args = parser.parse_args(argv)

    scored = SCORED_FULL if args.full_vocab else SCORED_COLLAPSED
    collapse_crash = not args.full_vocab

    events = json.loads(args.events_path.read_text())
    pl_bars_raw = events["bars"]
    pl_bars = [events_bar_hits(b, scored, collapse_crash) for b in pl_bars_raw]

    flat_raw, section_starts = build_tab()
    tab_bars = [collapse_tab_hits(b, scored, collapse_crash) for b in flat_raw]

    pl_hit_count = sum(len(b['notes']) for b in pl_bars_raw)
    tab_hit_count = sum(sum(len(s) for s in b.values()) for b in tab_bars)
    print(f"Source:    {source_url}")
    print(f"Pipeline:  {len(pl_bars)} bars, {pl_hit_count} notes  ({args.events_path})")
    print(f"Tab:       {len(tab_bars)} bars, {tab_hit_count} hits in scored vocab "
          f"({'7-class full' if args.full_vocab else '4-class collapsed'})")
    print()

    pairs = align(tab_bars, pl_bars, scored, args.tolerance)
    n_matched = sum(1 for a in pairs if a[0] is not None and a[1] is not None)
    n_tab_skip = sum(1 for a in pairs if a[1] is None)
    n_pl_skip = sum(1 for a in pairs if a[0] is None)
    print(f"DP alignment: {n_matched} matched, "
          f"{n_tab_skip} tab-only (over-expansion), {n_pl_skip} pipeline-only")
    print()

    totals = aggregate(pairs, tab_bars, pl_bars, scored, args.tolerance)
    print("=" * 78)
    print(f"OVERALL  ({len(scored)} scored insts, tol +/-{args.tolerance} sixteenth)")
    print("=" * 78)
    print(f"{'instrument':<14} {'TP':>5} {'FP':>5} {'FN':>5}   {'precision':>9} {'recall':>9} {'F1':>6}")
    sum_tp = sum_fp = sum_fn = 0
    for inst in scored:
        tp, fp, fn = totals[inst]
        sum_tp += tp; sum_fp += fp; sum_fn += fn
        p, r, f = stats(tp, fp, fn)
        print(f"{inst:<14} {tp:>5} {fp:>5} {fn:>5}   {p:>9.2%} {r:>9.2%} {f:>6.2%}")
    p, r, f = stats(sum_tp, sum_fp, sum_fn)
    print(f"{'TOTAL':<14} {sum_tp:>5} {sum_fp:>5} {sum_fn:>5}   {p:>9.2%} {r:>9.2%} {f:>6.2%}")
    print()

    # Per-section
    print("=" * 78)
    print("BY SECTION")
    print("=" * 78)
    boundaries = section_starts + [("(end)", len(tab_bars))]
    print(f"{'section':<22} {'bars t->pl':>11}", end="")
    for inst in scored:
        print(f"  {inst[:10]:>16}", end="")
    print()
    for i in range(len(section_starts)):
        name, start = section_starts[i]
        end = boundaries[i + 1][1]
        pairs_in = [a for a in pairs if a[0] is not None and start <= a[0] < end]
        matched_pairs = [a for a in pairs_in if a[1] is not None]
        sec_stats = aggregate(matched_pairs, tab_bars, pl_bars, scored, args.tolerance)
        bar_range = f"{end-start}->{len(matched_pairs)}"
        print(f"{name:<22} {bar_range:>11}", end="")
        for inst in scored:
            tp, fp, fn = sec_stats[inst]
            if tp + fp + fn == 0:
                print(f"  {'-':>16}", end="")
            else:
                _, _, f1 = stats(tp, fp, fn)
                print(f"  {tp:>3}/{tp+fp:>3}/{tp+fn:>3} F={f1:>4.0%}", end="")
        print()

    # Raw counts (no alignment) — the headline number for the hihat over-firing.
    print()
    print("=" * 78)
    print("RAW HIT COUNTS (no alignment, scored vocab only)")
    print("=" * 78)
    tab_totals: dict[str, int] = defaultdict(int)
    for b in tab_bars:
        for inst, s in b.items():
            tab_totals[inst] += len(s)
    pl_totals: dict[str, int] = defaultdict(int)
    for b in pl_bars:
        for inst, s in b.items():
            pl_totals[inst] += len(s)
    print(f"{'instrument':<14}  {'tab':>6}  {'pipeline':>8}  pl/tab")
    for inst in scored:
        t = tab_totals[inst]
        p_ = pl_totals[inst]
        ratio = (p_ / t) if t else float("inf")
        print(f"{inst:<14}  {t:>6}  {p_:>8}  {ratio:.2f}")

    # Tolerance sweep
    print()
    print("=" * 78)
    print("TOLERANCE SENSITIVITY (overall F1)")
    print("=" * 78)
    for tol in (0, 1, 2, 3):
        per = aggregate(pairs, tab_bars, pl_bars, scored, tol)
        total_tp = sum(per[i][0] for i in scored)
        total_fp = sum(per[i][1] for i in scored)
        total_fn = sum(per[i][2] for i in scored)
        pr, rc, f = stats(total_tp, total_fp, total_fn)
        print(f"  +/-{tol} sixteenth:  P={pr:.1%}  R={rc:.1%}  F1={f:.1%}")
    return 0
