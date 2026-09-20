"""Bar-relative anchoring + op replay for the tuning user layer.

Positions are exact rationals (`Fraction`), never rounded to a sixteenth grid,
so triplet/tuplet edits anchor exactly (decision Q3). All coordinate logic lives
here, so a future switch to time-based anchoring is a single-file change. See
docs/design/phase2-plan.md §1.4.
"""
from __future__ import annotations

import copy
import hashlib
import json
from fractions import Fraction
from typing import Any

# Match tolerance in whole-note units. A 16th grid spaces notes 1/16 apart, so
# their half-distance is 1/32; matching with a strictly-less-than 1/32 window
# means a note nudged by re-quantization still snaps to its anchor while adjacent
# 16th slots never cross-match. When both are candidates, the NEAREST wins, which
# also disambiguates a triplet position from a neighbouring straight slot. On a
# mixed straight+triplet grid the safe tolerance is smaller (1/12 and 1/16 sit
# only 1/48 apart) — nearest-wins covers the common case; see plan §4 risk 2.
DEFAULT_TOL: Fraction = Fraction(1, 32)


def lane_of(instrument: str) -> str:
    """Map a drum instrument to its staff lane. hihat_closed/hihat_open share the
    `hihat` lane (open/closed is a per-note property, not a separate lane);
    everything else — including hihat_chick — is its own lane. Mirrors the
    frontend editor's laneOf (edit.ts)."""
    if instrument in ("hihat_closed", "hihat_open"):
        return "hihat"
    return instrument


def parse_position(position: str | Fraction) -> Fraction:
    return position if isinstance(position, Fraction) else Fraction(position)


def find_note(
    notes: list[dict[str, Any]],
    position: str | Fraction,
    instrument: str,
    tol: Fraction = DEFAULT_TOL,
) -> dict[str, Any] | None:
    """Return the `instrument` note in `notes` whose position is nearest
    `position` within `tol`, or None. Exact-rational, grid-agnostic match."""
    target = parse_position(position)
    best: dict[str, Any] | None = None
    best_dist: Fraction | None = None
    for n in notes:
        if n["instrument"] != instrument:
            continue
        dist = abs(parse_position(n["position"]) - target)
        if dist < tol and (best_dist is None or dist < best_dist):
            best, best_dist = n, dist
    return best


def region_fingerprint(
    notation: dict[str, Any], lane: str, bar_start: int, bar_end: int
) -> str:
    """Stable hash of the base notes in `lane` across bars [bar_start, bar_end].
    Recorded at verify time and re-checked on re-generation to detect that the
    base under a verified region changed (region drift)."""
    items: list[list[Any]] = []
    for bar in notation["bars"]:
        if bar_start <= bar["index"] <= bar_end:
            for n in bar["notes"]:
                if lane_of(n["instrument"]) == lane:
                    items.append(
                        [bar["index"], n["position"], n["instrument"],
                         n.get("duration"), n.get("sustain_until")]
                    )
    items.sort(key=lambda it: (it[0], str(it[1]), it[2]))
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()


def region_status(selection: dict[str, Any], base_notation: dict[str, Any]) -> str:
    """Classify a verified selection against a (possibly regenerated) base:
    'missing' if the region's bars no longer all exist, 'drifted' if they exist
    but the base under the region changed since verify time (base_fingerprint
    mismatch), else 'ok'."""
    indices = {b["index"] for b in base_notation["bars"]}
    for b in range(selection["bar_start"], selection["bar_end"] + 1):
        if b not in indices:
            return "missing"
    fp = selection.get("base_fingerprint")
    if fp is not None and fp != region_fingerprint(
        base_notation, selection["lane"], selection["bar_start"], selection["bar_end"]
    ):
        return "drifted"
    return "ok"


def _apply_op(
    bars_by_index: dict[int, dict[str, Any]], op: dict[str, Any], tol: Fraction
) -> tuple[bool, dict[str, Any] | None, str | None]:
    """Apply one op in place to the bar map. Returns (applied, resulting_note,
    reason). applied=False with a reason string signals a conflict; the op is
    a no-op in that case."""
    bar = bars_by_index.get(op["bar"])
    if bar is None:
        return False, None, f"bar {op['bar']} not in base"
    notes = bar["notes"]
    kind = op["kind"]
    if kind == "add":
        if find_note(notes, op["position"], op["instrument"], tol) is not None:
            return False, None, f"add: {op['instrument']} already present near {op['position']} in bar {op['bar']}"
        note = {"instrument": op["instrument"], "position": op["position"], "duration": op["duration"]}
        notes.append(note)
        return True, note, None
    if kind == "delete":
        n = find_note(notes, op["position"], op["instrument"], tol)
        if n is None:
            return False, None, f"delete: no {op['instrument']} near {op['position']} in bar {op['bar']}"
        notes.remove(n)
        return True, None, None
    if kind == "reclassify":
        n = find_note(notes, op["position"], op["from"], tol)
        if n is None:
            return False, None, f"reclassify: no {op['from']} near {op['position']} in bar {op['bar']}"
        n["instrument"] = op["to"]  # in place — preserves duration/sustain_until
        return True, n, None
    if kind == "move":
        n = find_note(notes, op["from_position"], op["instrument"], tol)
        if n is None:
            return False, None, f"move: no {op['instrument']} near {op['from_position']} in bar {op['bar']}"
        if find_note(notes, op["to_position"], op["instrument"], tol) is not None:
            return False, None, f"move: destination {op['to_position']} occupied in bar {op['bar']}"
        n["position"] = op["to_position"]  # in place — preserves duration/sustain_until
        return True, n, None
    return False, None, f"unknown op kind {kind!r}"


def _frozen_lookup(frozen_notes: list[dict[str, Any]]) -> dict[tuple[int, str], list[dict[str, Any]]]:
    idx: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for item in frozen_notes:
        note = item["note"]
        idx.setdefault((item["bar"], note["instrument"]), []).append(note)
    return idx


def _restore_attributes(
    note: dict[str, Any], bar: int, frozen: dict[tuple[int, str], list[dict[str, Any]]], tol: Fraction
) -> None:
    """Overlay a matching frozen note's full attributes (duration, sustain_until)
    onto an applied note — so e.g. a reclassify to hihat_open restores the sustain
    the user set on the frozen ground truth."""
    match = find_note(frozen.get((bar, note["instrument"]), []), note["position"], note["instrument"], tol)
    if match is None:
        return
    if "duration" in match:
        note["duration"] = match["duration"]
    if "sustain_until" in match:
        note["sustain_until"] = match["sustain_until"]
    elif "sustain_until" in note:
        del note["sustain_until"]


def replay_ops(
    base_notation: dict[str, Any],
    ops: list[dict[str, Any]],
    frozen_notes: list[dict[str, Any]] | None = None,
    tol: Fraction = DEFAULT_TOL,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Apply user ops onto a fresh copy of `base_notation`, returning
    (new_notation, conflicts). Each conflict is {"op": op, "reason": str} for an
    op whose anchor no longer maps. reclassify/move mutate in place, so a base
    note's duration/sustain_until are preserved; when `frozen_notes` (RegionNote
    {bar, note} list) is given, an applied note additionally inherits the frozen
    note's attributes. Diagnostic/best-effort — actual note placement on re-gen
    is done by `layering.compose` from the frozen notes; this surfaces conflicts."""
    result = copy.deepcopy(base_notation)
    bars_by_index = {b["index"]: b for b in result["bars"]}
    frozen = _frozen_lookup(frozen_notes) if frozen_notes else {}
    conflicts: list[dict[str, Any]] = []
    for op in ops:
        applied, note, reason = _apply_op(bars_by_index, op, tol)
        if not applied:
            conflicts.append({"op": op, "reason": reason})
        elif note is not None and frozen:
            _restore_attributes(note, op["bar"], frozen, tol)
    return result, conflicts
