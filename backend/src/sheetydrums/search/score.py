"""Score a candidate notation against the user's verified selections — the Phase
3 edit-scored search objective (docs/design/phase3-plan.md §3a).

Reproduce-F1 over the **union** of the project's verified selections (D1): every
frozen note in a selection is a closed-world label, matched against the
candidate's notes in that lane × bar region with the bipartite matcher. The
aggregate F1 is the search's primary objective; the per-label ``residual`` (what
is still wrong) drives the capped loop's re-proposal (block 5c). The
"changed notes outside the verified regions" *tie-break* is **not** here — it is
minimal-parameter-movement, owned by the search (§3b, D3); this module only
supplies the reproduce score. Pure — no pipeline, no models.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from sheetydrums.anchor import DEFAULT_TOL, lane_of, parse_position
from sheetydrums.matching import bipartite_match, stats

# The 10-class schema vocabulary, mirroring schema/events.schema.json
# #/$defs/Instrument. `test_score.py` asserts it stays in sync with the schema.
SCHEMA_INSTRUMENTS: tuple[str, ...] = (
    "kick", "snare", "hihat_closed", "hihat_open", "hihat_chick",
    "ride", "crash", "tom_high", "tom_mid", "tom_low",
)


def lane_instruments(lane: str) -> tuple[str, ...]:
    """The schema instruments that belong to `lane` — the inverse of
    `anchor.lane_of` (e.g. `hihat` → closed + open; `snare` → just snare)."""
    return tuple(i for i in SCHEMA_INSTRUMENTS if lane_of(i) == lane)


@dataclass(frozen=True)
class Mismatch:
    """One still-wrong label after matching. `kind` is 'missing' (the selection
    says a hit is here, the candidate has none) or 'extra' (the candidate has a
    hit the selection does not). Feeds the capped loop's residual re-proposal."""
    selection_id: str | None
    bar: int
    instrument: str
    position: str  # canonical rational, e.g. "1/4"
    kind: str


@dataclass(frozen=True)
class ScoreResult:
    f1: float
    precision: float
    recall: float
    tp: int
    fp: int
    fn: int
    per_instrument: dict[str, tuple[int, int, int]]  # instrument -> (tp, fp, fn)
    per_selection: dict[str | None, float]  # selection_id -> F1
    residual: list[Mismatch]


def reproduce_score(
    candidate: dict[str, Any],
    selections: list[dict[str, Any]],
    tol: Fraction = DEFAULT_TOL,
) -> ScoreResult:
    """Reproduce-F1 of `candidate` (an events notation dict) against the frozen
    notes of every verified `selection`, matched per (bar, instrument) within
    `tol`. Bars are aligned by index (a selection names exact bar indices), so no
    DP bar-alignment is needed. Only the selection's lane instruments are scored,
    so a closed→open reclassify the user made registers as a miss until the
    params reproduce it."""
    bars_by_index = {b["index"]: b for b in candidate.get("bars", [])}
    tp = fp = fn = 0
    per_inst: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    per_sel: dict[str | None, list[int]] = defaultdict(lambda: [0, 0, 0])
    residual: list[Mismatch] = []

    for sel in selections:
        sid = sel.get("selection_id")
        lane = sel["lane"]
        instruments = lane_instruments(lane)
        bar_start, bar_end = sel["bar_start"], sel["bar_end"]

        # Frozen labels (ground truth) grouped by (bar, instrument).
        frozen: dict[tuple[int, str], set[Fraction]] = defaultdict(set)
        for item in sel.get("notes", []):
            note = item["note"]
            frozen[(item["bar"], note["instrument"])].add(parse_position(note["position"]))

        # Candidate notes in the region, restricted to this lane.
        cand: dict[tuple[int, str], set[Fraction]] = defaultdict(set)
        for b in range(bar_start, bar_end + 1):
            bar = bars_by_index.get(b)
            if bar is None:
                continue  # region beyond the candidate's bars → all labels miss
            for n in bar.get("notes", []):
                if lane_of(n["instrument"]) == lane:
                    cand[(b, n["instrument"])].add(parse_position(n["position"]))

        for b in range(bar_start, bar_end + 1):
            for inst in instruments:
                t = frozen.get((b, inst), set())
                p = cand.get((b, inst), set())
                matched, used_t, used_p = bipartite_match(t, p, tol)
                missing = t - used_t  # false negatives
                extra = p - used_p    # false positives
                tp += matched
                fn += len(missing)
                fp += len(extra)
                per_inst[inst][0] += matched
                per_inst[inst][1] += len(extra)
                per_inst[inst][2] += len(missing)
                per_sel[sid][0] += matched
                per_sel[sid][1] += len(extra)
                per_sel[sid][2] += len(missing)
                for pos in sorted(missing):
                    residual.append(Mismatch(sid, b, inst, str(pos), "missing"))
                for pos in sorted(extra):
                    residual.append(Mismatch(sid, b, inst, str(pos), "extra"))

    precision, recall, f1 = stats(tp, fp, fn)
    return ScoreResult(
        f1=f1,
        precision=precision,
        recall=recall,
        tp=tp,
        fp=fp,
        fn=fn,
        per_instrument={k: (v[0], v[1], v[2]) for k, v in per_inst.items()},
        per_selection={sid: stats(*c)[2] for sid, c in per_sel.items()},
        residual=residual,
    )
