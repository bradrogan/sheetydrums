"""Validate emitted events against schema/events.schema.json."""
from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

import jsonschema

# In the editable dev layout the schema lives at <repo>/schema/events.schema.json.
# Path resolution: this file is at <repo>/backend/src/sheetydrums/validate.py.
SCHEMA_PATH: Final[Path] = (
    Path(__file__).resolve().parents[3] / "schema" / "events.schema.json"
)


def load_schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text())


def validate(events: dict[str, Any]) -> None:
    """Validate the events dict. Raises jsonschema.ValidationError or ValueError on failure."""
    jsonschema.validate(events, load_schema())
    _check_sustain_until(events)


def _check_sustain_until(events: dict[str, Any]) -> None:
    """Cross-field check JSON Schema can't express: sustain_until > position. Cross-bar sustains are allowed."""
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
