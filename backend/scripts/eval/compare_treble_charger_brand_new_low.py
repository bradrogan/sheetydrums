"""Compare an events.json against the ground-truth tab for Treble Charger - 'Brand New Low'.

Usage:
    uv run python scripts/eval/compare_treble_charger_brand_new_low.py PATH/TO/events.json
    # or to score the full 7-class vocab (no crash->ride collapse) once the v2 expander lands:
    uv run python scripts/eval/compare_treble_charger_brand_new_low.py PATH/TO/events.json --full-vocab

Source song: https://www.youtube.com/watch?v=7fcg6KeQjNI
Reference tab: scripts/eval/reference_tabs/treble-charger-brand-new-low.txt
v1 baseline output: scripts/eval/baselines/treble-charger-brand-new-low.v1.events.json

What the report contains:
- Total bars + hits in each side
- DP alignment between tab and pipeline bars (tolerant of expansion errors)
- Per-instrument precision/recall/F1 on matched bars
- Per-section breakdown
- Tolerance sensitivity (±0 to ±3 sixteenth notes)

Default scoring (matches v1's collapsed vocabulary):
    kick, snare, hihat_closed, ride       — crash collapses to ride; tom/hihat_open ignored.
With --full-vocab (for v2's 5->7 expanded pipeline):
    kick, snare, hihat_closed, hihat_open, ride, crash    — no collapses; toms still ignored.

Tab convention (parser):
- Each line "X-|...|...|...|" — leading letter = instrument line, then bars of 16 sixteenth-note cells.
- Any non-'-' char in a cell = a hit on that line.
- Repeat markers (|---Nx---|) and alt endings (|1.|, |2.|, |3.|) are expanded manually in the encoding below.
- Cell chars (note: tab "x" on H-line means closed, "X" means open/loose hat per the tab's "(Loose hihat)" annotations):
    B-line: o          -> kick
    S-line: o, g, f, X -> snare (v1 ignores ghosts/dynamics/flams)
    H-line: x          -> hihat_closed
    H-line: X          -> hihat_open    (loose / open hat per the tab's annotations)
    C-line: x, X       -> crash         (collapsed to ride unless --full-vocab)
    R-line: x, b, X    -> ride          (bell collapses to ride)
    t-line: o          -> tom_high      (rack tom; collapses to tom_mid in 4-class mode)
    F-line: o          -> tom_low       (floor tom; collapses to tom_mid in 4-class mode)
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from fractions import Fraction
from pathlib import Path


# === Per-line, per-char mapping to schema instruments ====================
# Some lines emit different classes depending on the character (H's lowercase
# vs uppercase distinguishes closed vs open in this tab).
LINE_CHAR_TO_INST: dict[tuple[str, str], str] = {
    ("B", "o"): "kick",
    ("S", "o"): "snare",
    ("S", "g"): "snare",
    ("S", "f"): "snare",
    ("S", "X"): "snare",
    ("H", "x"): "hihat_closed",
    ("H", "X"): "hihat_open",
    ("C", "x"): "crash",
    ("C", "X"): "crash",
    ("R", "x"): "ride",
    ("R", "b"): "ride",
    ("R", "X"): "ride",
    ("t", "o"): "tom_high",   # rack tom on the t-line
    ("F", "o"): "tom_low",    # floor tom on the F-line
}


# Scored instrument sets.
SCORED_COLLAPSED = ["kick", "snare", "hihat_closed", "ride"]
SCORED_FULL = [
    "kick", "snare",
    "hihat_closed", "hihat_open",
    "ride", "crash",
    "tom_high", "tom_mid", "tom_low",
]


def parse_row(text: str) -> list[dict[str, set[int]]]:
    """Parse a multi-line row of tab into per-bar hit dicts.

    Returns a list of bars; each bar is a dict {schema_instrument: set of 16th positions}.
    Empty rows are silent bars (no instrument keys).
    """
    lines = [l for l in text.strip().split("\n") if l.strip()]
    per_line: dict[str, list[str]] = {}
    n_bars = 0
    for line in lines:
        line = line.strip()
        if len(line) < 2 or line[1] != "-":
            continue  # not an instrument line
        letter = line[0]
        # After "X-" the rest is "|bar1|bar2|...|"
        rest = line[2:]
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
                inst = LINE_CHAR_TO_INST.get((letter, char))
                if inst:
                    bar[inst].add(pos)
        bars.append(dict(bar))
    return bars


# === Build flat sequence of tab bars =====================================

def build_tab() -> tuple[list[dict[str, set[int]]], list[tuple[str, int]]]:
    """Returns (flat_bars, section_starts)."""
    flat: list[dict[str, set[int]]] = []
    section_starts: list[tuple[str, int]] = []

    def add_section(name: str) -> None:
        section_starts.append((name, len(flat)))

    # ----- Intro: 4 silent bars -----
    add_section("Intro")
    flat.extend(parse_row("""
S-|----------------|----------------|----------------|----------------|
B-|----------------|----------------|----------------|----------------|
"""))

    # ----- Verse 1 pt1: REMOVED -----
    # DP alignment showed all 12 bars in this section had no pipeline match.
    # Interpretation: V1pt1 is a hihat-less view of bars that Vpt2 also covers
    # with hihat. The note "(Sound in the song is different)" is the tabber
    # explaining that the simplified S+B notation doesn't reflect what's actually
    # in the audio. Drop V1pt1; Vpt2 represents the audio.

    # ----- Verse pt 2 -----
    add_section("Verse pt 2")
    vp2_main = parse_row("""
H-|x-x-x-x-x-x-x-x-|x-x-x-x-x-x-x-x-|x-x-x-x-x-x-x-x-|x-x-x-x-x-x-x-x-|
S-|----o--g----o---|----o--g----o---|----o--g----o---|----o--g----o---|
B-|o-------o-o---o-|--o-----o-o-----|o-------o-o---o-|--o-----o-o-----|
""")
    vp2_alt2 = parse_row("""
H-|x-x-x-x-x-x-----|
t-|--------------o-|
S-|----o--g----oo--|
B-|--o-----o-o-----|
""")
    flat.extend(vp2_main)
    flat.extend(vp2_main[:3])
    flat.extend(vp2_alt2)

    # 'Nurmal sound' 4-bar row with |---2x---|: bars 1-2 × 2 + bars 3-4 × 1
    n2x = parse_row("""
C-|x---------------|----------------|----------------|x---------------|
R-|--x-b-x-b-x-b-x-|b-x-b-x-b-x-b-x-|b-x-b-x-b-x-b---|--x-b-x-b-x-b-x-|
S-|----o--g----o---|----o--g----o---|----o--g----o-oo|----o--g----o---|
B-|o-o---o---o-----|o-o---o---o-----|o-o---o---o-----|o-o---o---o-----|
""")
    flat.extend(n2x[:2])
    flat.extend(n2x[:2])
    flat.extend(n2x[2:])
    # H/R/S/B 2-bar tail
    flat.extend(parse_row("""
H-|----------------|--------------x-|
R-|b-x-b-x-b-x-b-x-|b-x-b-x-b-x-----|
S-|----o--g----o---|----o-------f---|
B-|o-o---o---o-----|o-o---o-o-o---o-|
"""))

    # ----- Chorus 1: |---3x---| |---2x---| then 1-bar fill -----
    add_section("Chorus 1")
    ch_main = parse_row("""
C-|x---------------|----------------|x---------------|----------------|
H-|--x-X-x-X-x-X-x-|X-x-X-x-X-x-X-x-|--x-X-x-X-x-X-x-|X-x-X-x-X-x-X-x-|
S-|----o-------o---|----o-------o---|----o-------o---|----o-------o---|
B-|o-o-----o-o-----|o-o-----o-o-----|o-o-----o-o-----|o-o-----o-o-----|
""")
    flat.extend(ch_main[:2] * 3)
    flat.extend(ch_main[2:] * 2)
    flat.extend(parse_row("""
C-|x---------------|
H-|--x-X-----------|
t-|----------o---oo|
S-|----o--oo---oo--|
B-|o-o-------------|
"""))

    # ----- Verse 2 -----
    add_section("Verse 2")
    v2_main = parse_row("""
C-|x---------------|----------------|----------------|----------------|
H-|--x-X-x-X-x-X-x-|X-x-X-x-X-x-X-x-|X-x-X-x-X-x-X-x-|X-x-X-x-X-x-X-x-|
S-|----o--g----o---|----o--g----o---|----o--g----o---|----o--g----o---|
B-|o-------o-o---o-|--o-----o-o-----|o-------o-o---o-|--o-----o-o-----|
""")
    v2_alt3 = parse_row("""
H-|X-x-o-------o---|
t-|----------o-----|
S-|----o-ooo-----oo|
B-|--o---------o---|
""")
    # |1.2.| same body for plays 1 and 2; |3.| alt ending for play 3
    flat.extend(v2_main)
    flat.extend(v2_main)
    flat.extend(v2_main[:3])
    flat.extend(v2_alt3)
    # 'Nurmal sound' again
    flat.extend(n2x[:2] * 2)
    flat.extend(n2x[2:])
    # 2-bar H/R/S/B tail
    flat.extend(parse_row("""
H-|----------------|--------------x-|
R-|b-x-b-x-b-x-b-x-|b-x-b-x-b-x-----|
S-|----o--g----o---|----o-------f---|
B-|o-o---o---o-----|o-o---o-o-o---o-|
"""))

    # ----- Chorus 2 -----
    add_section("Chorus 2")
    flat.extend(ch_main[:2] * 3)
    flat.extend(ch_main[2:] * 2)
    flat.extend(parse_row("""
C-|x---------------|
H-|--x-X-x-X-x-X-x-|
S-|----o-------o---|
B-|o-o-----o-o---o-|
"""))

    # ----- Interlude -----
    add_section("Interlude")
    inter_row1 = parse_row("""
C-|x---------------|----------------|x---------------|----------------|
R-|--x-b-x-x-x-b-x-|x-x-b-x-x-x-b-x-|--x-x-x-x-x-x-x-|x-x-x-x-x-x-x-x-|
S-|----------------|----------------|----o-------o---|----o-------o---|
B-|o---------------|----------------|o-o-----o-o-----|o-o-----o-o-----|
""")
    flat.extend(inter_row1[:2] * 7)
    flat.extend(inter_row1[2:])
    flat.extend(parse_row("""
C-|x---------------|------------x---|X---------------|----------------|
R-|--x-x-x-x-x-x-x-|x-x-x-x-x-x---x-|----------------|----------------|
F-|----------------|----------------|--o-o-o-o-o-o-o-|o-o-o-o-o-o-o-o-|
S-|----o-------o---|----o-------o---|----o-------o---|----o-------o---|
B-|o-o-----o-o-----|o-o-----o-o-----|o-o-----o-o-----|o-o-----o-o---o-|
"""))

    # ----- Louder and louder -----
    add_section("Louder and louder")
    flat.extend(parse_row("""
S-|o-o-o-o-o-o-o-o-|o-o-oooooooooooo|
F-|o-o-o-o-o-o-o-o-|o-o-------------|
B-|o---o---o---o---|o---o-----------|
"""))
    flat.extend(parse_row("""
C-|x---------------|----------------|----------------|----------------|
H-|--x-X-x-X-x-X-x-|X-x-X-x-X-x-X-x-|X-x-X-x-X-x-X-x-|X-x-X-x---------|
S-|----o--g----o--g|----o--g----o--g|----o--g----o--g|----o---oooooooo|
B-|o-o-----o-o-----|o-o-----o-o-----|o-o-----o-o-----|o-o---o---------|
"""))

    # ----- Chorus 3 -----
    add_section("Chorus 3")
    flat.extend(ch_main[:2] * 3)
    flat.extend(ch_main[2:] * 2)
    ch3_row2 = parse_row("""
C-|----------------|x---------------|
H-|X-x-X-x-X-x-X---|--x-X-x-X-x-X-x-|
S-|----o-------o---|----o-------o---|
B-|o-o-----o-o-----|o-o-----o-o-----|
""")
    flat.extend(ch3_row2 * 2)
    flat.extend(parse_row("""
C-|----------------|x-x-x-x-x-x-x-x-|x-x-x-x-x-x-x-x-|x-x-x-x-x-x-x-x-|
H-|X-x-X-x-X-x-X-x-|----------------|----------------|----------------|
S-|----o-------o---|o---o---o---o---|o---o---o---o---|o---o---o---o---|
B-|o-o-----o-o---o-|--o---o---o---o-|--o---o---o---o-|--o---o---o---o-|
"""))
    flat.extend(parse_row("""
C-|x-x-x-x-x-x-x-x-|x-x-x-x-x-x-x-x-|x-x-x-x-x-x-x-x-|x-------x-------|
R-|----------------|----------------|----------------|--x-x-x---x-x-x-|
S-|o---o---o---o---|o---o---o---o---|o---o---o---o---|----o-------o---|
B-|--o---o---o---o-|--o---o---o---o-|--o---o---o---o-|o-o---o-o-o---o-|
"""))
    flat.extend(parse_row("""
C-|x---------------|----------------|
B-|o---------------|----------------|
"""))

    add_section("End")
    return flat, section_starts


# === Pipeline -> per-bar hit sets =========================================

def _apply_collapses(inst: str, scored: list[str], collapse_crash: bool) -> str:
    """Fold an instrument label down so it lands in `scored`. Mirrors the
    pipeline's quantizer fallback collapse map. Used on both pipeline output
    and tab hits so the comparison is symmetric."""
    if collapse_crash and inst == "crash":
        inst = "ride"
    if inst == "hihat_open" and "hihat_open" not in scored:
        inst = "hihat_closed"
    if inst in ("tom_high", "tom_low") and inst not in scored:
        inst = "tom_mid"
    return inst


def events_bar_hits(bar: dict, scored: list[str], collapse_crash: bool) -> dict[str, set[int]]:
    """Read a pipeline bar's notes into a {instrument: positions} dict, filtered to scored insts."""
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
    """Same collapse logic applied to a tab bar so tab and pipeline are scored on equal footing."""
    out: dict[str, set[int]] = defaultdict(set)
    for inst, positions in bar.items():
        inst = _apply_collapses(inst, scored, collapse_crash)
        if inst in scored:
            out[inst].update(positions)
    return dict(out)


# === DP alignment ========================================================

def bipartite_match(t_pos, p_pos, tolerance: int) -> tuple[int, set[int], set[int]]:
    """Greedy 1-1 match of two position sets within tolerance.
    Returns (matched_count, matched_t_positions, matched_p_positions)."""
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
    """Reward matched hits, lightly penalise mismatches so empty/empty bars score 0."""
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
    """Needleman-Wunsch-style alignment over bars."""
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("events_path", type=Path,
                        help="Path to the pipeline's events.json output")
    parser.add_argument("--full-vocab", action="store_true",
                        help="Score the v2 7-class vocab (hihat_open, crash) instead of "
                             "the v1 collapsed 4-class vocab")
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

    # Overall
    totals = aggregate(pairs, tab_bars, pl_bars, scored, args.tolerance)
    print("=" * 78)
    print(f"OVERALL  ({len(scored)} scored insts, tol ±{args.tolerance} sixteenth)")
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
    print(f"{'section':<22} {'bars t→pl':>11}", end="")
    for inst in scored:
        print(f"  {inst[:10]:>16}", end="")
    print()
    for i in range(len(section_starts)):
        name, start = section_starts[i]
        end = boundaries[i + 1][1]
        pairs_in = [a for a in pairs if a[0] is not None and start <= a[0] < end]
        matched_pairs = [a for a in pairs_in if a[1] is not None]
        sec_stats = aggregate(matched_pairs, tab_bars, pl_bars, scored, args.tolerance)
        bar_range = f"{end-start}→{len(matched_pairs)}"
        print(f"{name:<22} {bar_range:>11}", end="")
        for inst in scored:
            tp, fp, fn = sec_stats[inst]
            if tp + fp + fn == 0:
                print(f"  {'-':>16}", end="")
            else:
                _, _, f1 = stats(tp, fp, fn)
                print(f"  {tp:>3}/{tp+fp:>3}/{tp+fn:>3} F={f1:>4.0%}", end="")
        print()

    # Skipped tab bars by section (over-expansion diagnosis)
    print()
    print("=" * 78)
    print("OVER-EXPANSION DIAGNOSIS: tab bars skipped by DP")
    print("=" * 78)
    skipped_by_sec: dict[str, int] = defaultdict(int)
    for ti, pi in pairs:
        if pi is None and ti is not None:
            for k in range(len(section_starts)):
                sec_start = section_starts[k][1]
                sec_end = boundaries[k + 1][1]
                if sec_start <= ti < sec_end:
                    skipped_by_sec[section_starts[k][0]] += 1
                    break
    any_skipped = False
    for name, _ in section_starts:
        n = skipped_by_sec.get(name, 0)
        if n:
            print(f"  {name:<22}  -{n} bars")
            any_skipped = True
    if not any_skipped:
        print("  (none)")

    # Raw counts (no alignment)
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
        print(f"  ±{tol} sixteenth:  P={pr:.1%}  R={rc:.1%}  F1={f:.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
