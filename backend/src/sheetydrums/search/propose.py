"""Which-stage heuristic proposer (phase3-plan.md §3b) — the Phase 3 stand-in
for the Phase 4 LLM. From the residual mismatch it picks *which* knobs to search
next, keeping the search low-dimensional. Same output shape as the future
`LLMSuggester.propose`, so Phase 4 drops in behind it.

It reads the residual the scorer produced and classifies the errors:
  - a **missing + extra pair in the same lane at one slot** → a classification
    error (open/closed hat, tom pitch) → that lane's expander knobs;
  - a leftover **missing** or **extra** for an instrument → the ADTOF detection
    threshold for that instrument's class.
Groups are returned in that priority order, skipping any whose knobs were all
tried already, so successive capped-loop rounds explore new axes (block 5c).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sheetydrums.anchor import lane_of
from sheetydrums.search.knobs import ADTOF_CLASS_OF, KNOB_SPECS
from sheetydrums.search.score import Mismatch

# Classification knobs per lane (scalar-only for now; ride/crash's tuple window
# is deferred, so a cymbal misclassification yields no classification group).
_CLASS_KNOBS: dict[str, tuple[str, ...]] = {
    "hihat": (
        "expander.hihat_unimodal_open_threshold",
        "expander.hihat_clearly_loose",
        "expander.hihat_clearly_tight",
        "expander.hihat_bimodal_min_fraction",
    ),
    "tom_high": ("expander.tom_uniform_spread_hz", "expander.tom_min_cluster_gap_hz"),
}
# tom lanes share one clustering knob group.
for _tom in ("tom_mid", "tom_low"):
    _CLASS_KNOBS[_tom] = _CLASS_KNOBS["tom_high"]


@dataclass(frozen=True)
class Proposal:
    knobs: tuple[str, ...]
    rationale: str


def _candidate_groups(residual: list[Mismatch]) -> list[Proposal]:
    """Ordered proposals implied by the residual, highest-priority first."""
    groups: list[Proposal] = []

    # 1) Classification errors: a missing + extra in the same lane at one slot.
    by_slot: dict[tuple[str | None, int, str], dict[str, set[str]]] = defaultdict(
        lambda: {"missing": set(), "extra": set()}
    )
    for m in residual:
        by_slot[(m.selection_id, m.bar, m.position)][m.kind].add(m.instrument)
    misclassified_lanes: list[str] = []
    for slot in by_slot.values():
        for miss in slot["missing"]:
            for extra in slot["extra"]:
                if lane_of(miss) == lane_of(extra) and lane_of(miss) not in misclassified_lanes:
                    misclassified_lanes.append(lane_of(miss))
    for lane in misclassified_lanes:
        knobs = _CLASS_KNOBS.get(lane)
        if knobs:
            groups.append(Proposal(knobs, f"reclassify errors in the {lane} lane"))

    # 2) Detection errors: leftover missing / extra for an instrument's class.
    classes_wrong: list[str] = []
    for m in residual:
        cls = ADTOF_CLASS_OF.get(m.instrument)
        if cls and cls not in classes_wrong:
            classes_wrong.append(cls)
    for cls in classes_wrong:
        name = f"transcription.thresholds[{cls}]"
        if name in KNOB_SPECS:
            groups.append(Proposal((name,), f"missing/extra {cls} hits → detection threshold"))

    return groups


def propose(residual: list[Mismatch], tried: set[str]) -> Proposal | None:
    """The next knob group to search, or None when the heuristic is out of ideas
    (every implied group already tried). Knobs already in `tried` are dropped
    from a group; a group emptied by that is skipped."""
    for group in _candidate_groups(residual):
        fresh = tuple(k for k in group.knobs if k not in tried)
        if fresh:
            return Proposal(fresh, group.rationale)
    return None
