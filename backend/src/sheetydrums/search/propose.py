"""Which-stage heuristic proposer (phase3-plan.md §3b) — the Phase 3 stand-in
for the Phase 4 LLM. Same output shape as the future `LLMSuggester.propose`, so
Phase 4 drops in behind it.

**Ops-driven** (per ai-tuning-loop.md block 5a, which feeds the proposer "the
user's correction (edit ops)"). The edit *type* already encodes intent, so we
read it directly rather than re-inferring it by pairing missing/extra in the
reproduce-residual — that inference was lossy across lanes/families (a
tom_high↔tom_mid or crash↔hihat confusion lives in two lane-selections and never
paired). An op says exactly what happened:
  - `reclassify` from→to, **same ADTOF family** (open↔closed, tom pitch,
    ride↔crash) → that family's expander split knobs;
  - `reclassify` **across families** (crash↔hihat) → an ADTOF detection
    confusion → nudge both classes' thresholds (the best available; ADTOF has no
    knob to truly move an onset between its classes);
  - `add` / `delete` → the ADTOF detection threshold for that instrument's class;
  - `move` → a timing/grid problem (quantize knob deferred → not yet mapped).

The residual is the *satisfaction oracle*: an op is only proposed while the
current best candidate hasn't reproduced it (its target note is still
missing/extra), so a fix already achieved isn't re-proposed and rounds target
what's left. Groups are returned reclassify-first (most specific intent), then
detection, skipping any whose knobs were all tried (block 5c).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sheetydrums.anchor import parse_position
from sheetydrums.search.knobs import ADTOF_CLASS_OF, KNOB_SPECS
from sheetydrums.search.score import Mismatch

# Split knobs per ADTOF family (scalar-only for now; the cymbal ride/crash window
# is a 2-tuple knob, still deferred — a cymbal split yields no group and falls to
# the detection thresholds / out-of-ideas).
_CLASS_KNOBS: dict[str, tuple[str, ...]] = {
    "hihat": (
        "expander.hihat_unimodal_open_threshold",
        "expander.hihat_clearly_loose",
        "expander.hihat_clearly_tight",
        "expander.hihat_bimodal_min_fraction",
    ),
    "tom": ("expander.tom_uniform_spread_hz", "expander.tom_min_cluster_gap_hz"),
}


@dataclass(frozen=True)
class Proposal:
    knobs: tuple[str, ...]
    rationale: str


def _canon(position: str) -> str:
    return str(parse_position(position))


def _op_unsatisfied(op: dict[str, Any], residual: set[tuple[str, int, str, str]]) -> bool:
    """True while the current best candidate hasn't reproduced this op's intent —
    i.e. the note the op should have produced is still `missing`, or the note it
    should have removed is still `extra`, in the reproduce-residual."""
    bar, kind = op["bar"], op["kind"]
    if kind == "add":
        return ("missing", bar, op["instrument"], _canon(op["position"])) in residual
    if kind == "delete":
        return ("extra", bar, op["instrument"], _canon(op["position"])) in residual
    if kind == "reclassify":
        pos = _canon(op["position"])
        return ("missing", bar, op["to"], pos) in residual or ("extra", bar, op["from"], pos) in residual
    if kind == "move":
        return (
            ("missing", bar, op["instrument"], _canon(op["to_position"])) in residual
            or ("extra", bar, op["instrument"], _canon(op["from_position"])) in residual
        )
    return False


def _reclassify_group(op: dict[str, Any]) -> Proposal | None:
    fam_from = ADTOF_CLASS_OF.get(op["from"])
    fam_to = ADTOF_CLASS_OF.get(op["to"])
    if fam_from is None or fam_to is None:
        return None
    if fam_from == fam_to:  # within-family split (open/closed, tom pitch, ride/crash)
        knobs = _CLASS_KNOBS.get(fam_from)
        return Proposal(knobs, f"reclassify within the {fam_from} family") if knobs else None
    # cross-family: an ADTOF class confusion — nudge both classes' thresholds.
    knobs = tuple(
        n for n in (f"transcription.thresholds[{fam_to}]", f"transcription.thresholds[{fam_from}]")
        if n in KNOB_SPECS
    )
    return Proposal(knobs, f"{fam_from}->{fam_to} confusion (ADTOF class)") if knobs else None


def _detection_group(op: dict[str, Any]) -> Proposal | None:
    cls = ADTOF_CLASS_OF.get(op["instrument"])
    name = f"transcription.thresholds[{cls}]" if cls else None
    return Proposal((name,), f"{op['kind']} {op['instrument']} → detection threshold") if name in KNOB_SPECS else None


def _candidate_groups(ops: list[dict[str, Any]], residual: list[Mismatch]) -> list[Proposal]:
    """Knob groups implied by the still-unsatisfied ops, reclassify-first."""
    index = {(m.kind, m.bar, m.instrument, m.position) for m in residual}
    unsatisfied = [op for op in ops if _op_unsatisfied(op, index)]
    groups: list[Proposal] = []
    for op in unsatisfied:
        if op["kind"] == "reclassify":
            g = _reclassify_group(op)
            if g:
                groups.append(g)
    for op in unsatisfied:
        if op["kind"] in ("add", "delete"):
            g = _detection_group(op)
            if g:
                groups.append(g)
    return groups


def propose(
    ops: list[dict[str, Any]], residual: list[Mismatch], tried: set[str]
) -> Proposal | None:
    """The next knob group to search, or None when the heuristic is out of ideas
    (every group implied by an unsatisfied op is already tried). Knobs in `tried`
    are dropped from a group; a group emptied by that is skipped."""
    for group in _candidate_groups(ops, residual):
        fresh = tuple(k for k in group.knobs if k not in tried)
        if fresh:
            return Proposal(fresh, group.rationale)
    return None
