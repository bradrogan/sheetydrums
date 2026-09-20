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
    """Validate one verified-selection record (tuning Phase 2 user layer).
    Raises jsonschema.ValidationError on failure."""
    _validate_against(selection, "selection.schema.json")


def validate_system_layer(layer: dict[str, Any]) -> None:
    """Validate the system-layer record (tuning Phase 2/3 container).
    Raises jsonschema.ValidationError on failure."""
    _validate_against(layer, "system_layer.schema.json")


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
