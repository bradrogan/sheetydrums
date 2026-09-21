"""Compose the base, system, and user layers into the effective notation.

`compose(base, system_layer, selections)` is the single authority for the "final
notation" the client renders/plays. Layers apply base → system → user (user last
and inviolable): a system op is dropped if its (bar, lane) falls inside a
verified selection's region, and each verified selection overwrites its
lane × [bar_start, bar_end] region with its frozen ground-truth notes. Returns
the effective notation plus an origin map (base | system | user per emitted note)
so the frontend can colour the delta without re-composing. Pure — no I/O.
See docs/design/phase2-plan.md §1.5.
"""
from __future__ import annotations

import copy
from typing import Any

from sheetydrums.anchor import (
    DEFAULT_TOL,
    find_note,
    lane_of,
    parse_position,
    region_fingerprint,
    region_status,
    replay_ops,
)
from sheetydrums.validate import check_selection_invariants, validate


def verified_regions(selections: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """The verified selections — the regions the user owns."""
    return [s for s in (selections or []) if s.get("verified")]


def region_contains(selection: dict[str, Any], bar: int, lane: str) -> bool:
    """True if (bar, lane) falls inside this selection's locked region."""
    return selection["lane"] == lane and selection["bar_start"] <= bar <= selection["bar_end"]


def _op_lanes(op: dict[str, Any]) -> set[str]:
    if op["kind"] == "reclassify":
        return {lane_of(op["from"]), lane_of(op["to"])}
    return {lane_of(op["instrument"])}


def _op_in_verified(op: dict[str, Any], regions: list[dict[str, Any]]) -> bool:
    """A system op is user-owned (and dropped) if any lane it touches, at its
    bar, is inside a verified region."""
    return any(
        region_contains(s, op["bar"], lane)
        for s in regions
        for lane in _op_lanes(op)
    )


# A working note is a mutable [note_dict, origin] pair while we compose; the
# origin travels alongside the note so we can split them at the end.


def _find_pair(
    pairs: list[list[Any]],
    position: str,
    instrument: str,
    exclude: list[Any] | None = None,
) -> list[Any] | None:
    """The pair holding the `instrument` note nearest `position`, or None.
    `exclude` skips one pair — used for destination-occupied checks, so a note
    is never considered to collide with itself."""
    candidates = [p for p in pairs if p is not exclude]
    match = find_note([p[0] for p in candidates], position, instrument, DEFAULT_TOL)
    if match is None:
        return None
    return next(p for p in candidates if p[0] is match)


def _apply_system_op(tagged: dict[int, list[list[Any]]], op: dict[str, Any]) -> None:
    """Apply one system op to the working per-bar pairs, tagging touched notes
    'system'. A system op referencing an absent bar is skipped (Phase 2 only
    feeds well-formed fixtures).

    Collision semantics mirror `anchor._apply_op` (the other applier of this op
    vocabulary): an op that would stack a second note of the same instrument at
    one position is dropped rather than applied. `events.schema.json` has no
    `uniqueItems`, so the closing `validate()` would not catch such a duplicate
    — the renderer would just get two identical noteheads."""
    pairs = tagged.get(op["bar"])
    if pairs is None:
        return
    kind = op["kind"]
    if kind == "add":
        if _find_pair(pairs, op["position"], op["instrument"]) is not None:
            return  # already sounds here
        pairs.append([
            {"instrument": op["instrument"], "position": op["position"], "duration": op["duration"]},
            "system",
        ])
    elif kind == "delete":
        p = _find_pair(pairs, op["position"], op["instrument"])
        if p is not None:
            pairs.remove(p)
    elif kind == "reclassify":
        p = _find_pair(pairs, op["position"], op["from"])
        if p is None:
            return
        if _find_pair(pairs, op["position"], op["to"], exclude=p) is not None:
            return  # target instrument already sounds here
        p[0]["instrument"] = op["to"]
        p[1] = "system"
    elif kind == "move":
        p = _find_pair(pairs, op["from_position"], op["instrument"])
        if p is None:
            return
        if _find_pair(pairs, op["to_position"], op["instrument"], exclude=p) is not None:
            return  # destination occupied
        p[0]["position"] = op["to_position"]
        p[1] = "system"


def compose(
    base: dict[str, Any],
    system_layer: dict[str, Any] | None,
    selections: list[dict[str, Any]] | None,
) -> tuple[dict[str, Any], dict[int, list[str]]]:
    """Return (effective_notation, origin_map). origin_map maps each bar index
    (an `int` here; the HTTP layer stringifies it — see `server.get_layers`) to
    the origin ('base'|'system'|'user') of each note in the effective bar's
    notes, in emitted order. Validates the composed notation before returning
    whenever a layer actually applied."""
    result = copy.deepcopy(base)
    regions = verified_regions(selections)
    # `compose` is the single authority for the final notation, so it re-checks
    # the user layer's cross-field invariants rather than trusting its caller.
    # Pure Python (no jsonschema) — cheap enough for the read path.
    check_selection_invariants(regions)

    # Start from base; every note tagged 'base'.
    tagged: dict[int, list[list[Any]]] = {
        bar["index"]: [[n, "base"] for n in bar["notes"]] for bar in result["bars"]
    }

    # System layer: apply each op unless it lands in a verified region.
    system_ops: list[dict[str, Any]] = [
        op
        for op in (system_layer or {}).get("ops", [])
        if not _op_in_verified(op, regions)
    ]
    for op in system_ops:
        _apply_system_op(tagged, op)

    # User layer: overwrite each verified region with its frozen notes (user wins).
    for sel in regions:
        lane = sel["lane"]
        by_bar: dict[int, list[dict[str, Any]]] = {}
        for item in sel["notes"]:
            by_bar.setdefault(item["bar"], []).append(item["note"])
        for b in range(sel["bar_start"], sel["bar_end"] + 1):
            if b not in tagged:
                continue  # region beyond base bars — region_status flags this on re-gen
            # Closed world: clear every existing note in this lane (even those the
            # user left — they are re-asserted by the frozen notes below).
            tagged[b] = [p for p in tagged[b] if lane_of(p[0]["instrument"]) != lane]
            for note in by_bar.get(b, []):
                tagged[b].append([copy.deepcopy(note), "user"])

    # Split working pairs back into notation + origin map, ordered by position.
    origin_map: dict[int, list[str]] = {}
    for bar in result["bars"]:
        pairs = tagged[bar["index"]]
        pairs.sort(key=lambda p: (parse_position(p[0]["position"]), p[0]["instrument"]))
        bar["notes"] = [p[0] for p in pairs]
        origin_map[bar["index"]] = [p[1] for p in pairs]

    # Validate only what the layers actually built. With nothing to layer the
    # effective notation is the base re-sorted, and `store.save_project` already
    # validated that base on write — neither the copy nor the sort can invalidate
    # it. The check is worth skipping because it dominates: on a 150-bar song a
    # compose measures ~36 ms, ~31 ms of which is this jsonschema pass, and
    # `compose` now runs on the read path (every GET of a project, its layers, or
    # its diagnostics) on the event loop.
    if regions or system_ops:
        validate(result)
    return result, origin_map


# === Retune reconciliation (accepting a re-generated base) ==============

_KEEP_EDITS_MODES = frozenset({"replay-all", "replay-with-conflict-review", "take-fresh-clean"})


def reconcile_selections(
    selections: list[dict[str, Any]],
    new_base: dict[str, Any],
    keep_edits: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Reconcile the user layer with a regenerated base (the retune-accept path).

    A verified selection is anchored to the base it was made against; when a
    retune replaces the base, each selection has to be re-checked. The frozen
    notes remain the user's ground truth (they still win at compose), so a
    selection is *kept* as long as its region still exists — only its
    base_fingerprint is re-pinned to the new base. Returns (kept, conflicts).

    `keep_edits`:
      - ``take-fresh-clean``: drop every selection (a clean slate).
      - ``replay-all``: keep every selection whose region survives (re-pinned);
        silently drop any whose bars no longer exist; surface no conflicts.
      - ``replay-with-conflict-review``: same keep/drop, but return the conflicts
        — region *missing* (bars gone), region *drifted* (the base under the
        region changed, so the user should re-check), and *op* (a recorded edit's
        anchor no longer maps) — for the user to audition and resolve.
    """
    if keep_edits not in _KEEP_EDITS_MODES:
        raise ValueError(f"Unknown keep_edits mode: {keep_edits!r}")
    if keep_edits == "take-fresh-clean":
        return [], []

    review = keep_edits == "replay-with-conflict-review"
    kept: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []

    def region_conflict(sel: dict[str, Any], reason: str) -> dict[str, Any]:
        return {
            "kind": "region", "reason": reason,
            "selection_id": sel.get("selection_id"), "lane": sel["lane"],
            "bar_start": sel["bar_start"], "bar_end": sel["bar_end"],
        }

    for sel in selections:
        status = region_status(sel, new_base)
        if status == "missing":
            # The region's bars no longer all exist — nothing to anchor to, so
            # the selection is dropped in every mode; review surfaces it.
            if review:
                conflicts.append(region_conflict(sel, "missing"))
            continue

        kept.append({
            **sel,
            "base_fingerprint": region_fingerprint(
                new_base, sel["lane"], sel["bar_start"], sel["bar_end"]
            ),
        })
        if review:
            if status == "drifted":
                conflicts.append(region_conflict(sel, "drifted"))
            _, op_conflicts = replay_ops(new_base, sel.get("ops", []), sel.get("notes", []))
            for oc in op_conflicts:
                conflicts.append({
                    "kind": "op", "reason": oc["reason"],
                    "selection_id": sel.get("selection_id"),
                    "bar": oc["op"]["bar"], "op": oc["op"],
                })

    return kept, conflicts
