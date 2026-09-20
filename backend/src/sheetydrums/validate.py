"""Validate emitted events against schema/events.schema.json.

Also validates the tuning Phase 2 side-schemas (`selection.schema.json`,
`system_layer.schema.json`), which `$ref` the Note definition in
`events.schema.json`. Those cross-file refs resolve offline through a
`referencing.Registry` built from every local schema — no network fetch.
"""
from __future__ import annotations

import json
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

import jsonschema
from referencing import Registry, Resource

from sheetydrums.anchor import lane_of

# In the editable dev layout the schema lives at <repo>/schema/events.schema.json.
# Path resolution: this file is at <repo>/backend/src/sheetydrums/validate.py.
SCHEMA_PATH: Final[Path] = (
    Path(__file__).resolve().parents[3] / "schema" / "events.schema.json"
)
SCHEMA_DIR: Final[Path] = SCHEMA_PATH.parent


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text())


@lru_cache(maxsize=1)
def _registry() -> Registry:
    """A referencing Registry of every local schema, keyed by its `$id`, so a
    relative `$ref` like `events.schema.json#/$defs/Note` resolves offline."""
    resources = [
        (contents["$id"], Resource.from_contents(contents))
        for contents in (
            json.loads(p.read_text()) for p in SCHEMA_DIR.glob("*.schema.json")
        )
    ]
    return Registry().with_resources(resources)


def _validate_against(instance: Any, schema_filename: str) -> None:
    schema = json.loads((SCHEMA_DIR / schema_filename).read_text())
    jsonschema.Draft7Validator(schema, registry=_registry()).validate(instance)


def validate(events: dict[str, Any]) -> None:
    """Validate the events dict. Raises jsonschema.ValidationError or ValueError on failure."""
    jsonschema.validate(events, load_schema())
    _check_sustain_until(events)


def validate_selection(selection: dict[str, Any]) -> None:
    """Validate one verified-selection record (tuning Phase 2 user layer),
    schema plus its own cross-field invariants. Raises
    jsonschema.ValidationError or ValueError on failure."""
    _validate_against(selection, "selection.schema.json")
    _check_selection_fields(selection)


def validate_selections(selections: list[dict[str, Any]]) -> None:
    """Validate a project's whole user layer: every selection individually, plus
    the cross-selection invariant that no two selections own the same lane in
    the same bar. Raises jsonschema.ValidationError or ValueError."""
    for selection in selections:
        validate_selection(selection)
    _check_no_lane_overlap(selections)


def validate_system_layer(layer: dict[str, Any]) -> None:
    """Validate the system-layer record (tuning Phase 2/3 container).
    Raises jsonschema.ValidationError on failure."""
    _validate_against(layer, "system_layer.schema.json")


# === Selection cross-field invariants ===================================
# JSON Schema can state these only as prose in a `description`. Unenforced they
# fail *silently* in `layering.compose` — a reversed bar range makes the whole
# selection inert, an out-of-region note is never emitted, and an out-of-lane
# note is appended past the lane clear as a duplicate — each of which quietly
# loses or corrupts the user's verified ground truth. So they raise here
# instead. Pure (no jsonschema), so `compose` can afford them on the read path.


def check_selection_invariants(selections: list[dict[str, Any]]) -> None:
    """Cross-field checks for a whole user layer: per-selection field coherence
    plus no two selections owning one (lane, bar). Raises ValueError. Pure —
    assumes the records are already schema-shaped."""
    for selection in selections:
        _check_selection_fields(selection)
    _check_no_lane_overlap(selections)


def _check_selection_fields(selection: dict[str, Any]) -> None:
    """One selection's own invariants: bar_end >= bar_start; every frozen note's
    bar inside [bar_start, bar_end] and its instrument in `lane`; no two frozen
    notes at one (bar, position) in the lane (a lane can't sound twice at one
    instant — otherwise the renderer draws stacked noteheads and the closed-world
    labels contradict); every op's bar inside the region (ops are edits *within*
    the selection)."""
    sid = selection.get("selection_id")
    start: int = selection["bar_start"]
    end: int = selection["bar_end"]
    if end < start:
        raise ValueError(
            f"Selection {sid!r}: bar_end ({end}) is before bar_start ({start})."
        )
    lane: str = selection["lane"]
    seen: set[tuple[int, Fraction]] = set()
    for item in selection["notes"]:
        bar: int = item["bar"]
        note = item["note"]
        instrument: str = note["instrument"]
        if not start <= bar <= end:
            raise ValueError(
                f"Selection {sid!r}: frozen note in bar {bar} is outside the "
                f"selection's region [{start}, {end}]."
            )
        if lane_of(instrument) != lane:
            raise ValueError(
                f"Selection {sid!r}: frozen note instrument {instrument!r} "
                f"belongs to lane {lane_of(instrument)!r}, not {lane!r}."
            )
        key = (bar, Fraction(note["position"]))
        if key in seen:
            raise ValueError(
                f"Selection {sid!r}: two frozen notes at bar {bar} position "
                f"{note['position']} in lane {lane!r} — a lane sounds once per instant."
            )
        seen.add(key)
    for op in selection["ops"]:
        if not start <= op["bar"] <= end:
            raise ValueError(
                f"Selection {sid!r}: op in bar {op['bar']} is outside the "
                f"selection's region [{start}, {end}]."
            )


def _check_no_lane_overlap(selections: list[dict[str, Any]]) -> None:
    """No two selections may own the same lane in the same bar. Overlap makes
    composition order-dependent: each region clears its whole lane before
    writing its frozen notes, so the later selection in list order would wipe
    the earlier one's ground truth (and drop it from the param-search labels)."""
    by_lane: dict[str, list[dict[str, Any]]] = {}
    for selection in selections:
        by_lane.setdefault(selection["lane"], []).append(selection)
    for lane, group in by_lane.items():
        ordered = sorted(group, key=lambda s: (s["bar_start"], s["bar_end"]))
        for prev, cur in zip(ordered, ordered[1:]):
            if cur["bar_start"] <= prev["bar_end"]:
                raise ValueError(
                    f"Selections {prev.get('selection_id')!r} "
                    f"([{prev['bar_start']}, {prev['bar_end']}]) and "
                    f"{cur.get('selection_id')!r} "
                    f"([{cur['bar_start']}, {cur['bar_end']}]) both own lane "
                    f"{lane!r} in bars {cur['bar_start']}.."
                    f"{min(prev['bar_end'], cur['bar_end'])}; verified regions "
                    "on one lane must not overlap."
                )


def _check_sustain_until(events: dict[str, Any]) -> None:
    """Cross-field check JSON Schema can't express: sustain_until > position. Cross-bar sustains are allowed.

    Pre: `events` MUST already be jsonschema-valid (call `validate()` rather
    than this function directly). The asserts catch direct callers; the real
    structural guarantees come from the upstream jsonschema.validate call.
    """
    assert "bars" in events, "_check_sustain_until called before jsonschema.validate()"
    for bar in events["bars"]:
        for note in bar["notes"]:
            if "sustain_until" not in note:
                continue
            pos: Fraction = Fraction(note["position"])
            until: Fraction = Fraction(note["sustain_until"])
            if until <= pos:
                raise ValueError(
                    f"Bar {bar['index']} {note['instrument']}: sustain_until={note['sustain_until']} "
                    f"must be greater than position ({note['position']})."
                )
