"""End-to-end smoke test against the committed CC fixture.

Loads `backend/tests/fixtures/eric-keyes-lost.ogg`, runs the full pipeline
(real Demucs + real ADTOF + real Beat This! + stub quantizer), serializes
to the schema, and validates. Asserts only structural properties — note
counts will change as v2 work lands and that's expected. We're proving the
pipeline doesn't blow up end-to-end, not measuring transcription quality.

Marked `slow` so the fast unit-test loop (`uv run pytest`) skips it. Run
explicitly with `uv run pytest --run-slow`. First invocation downloads
~320 MB of Demucs weights + ~77 MB of Beat This! weights into the torch
hub cache.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from sheetydrums.cli import serialize_to_schema
from sheetydrums.config import CLIConfig
from sheetydrums.factory import build_pipeline
from sheetydrums.validate import validate


_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "eric-keyes-lost.ogg"


@pytest.mark.slow
def test_pipeline_end_to_end_on_fixture(tmp_path: Path) -> None:
    assert _FIXTURE_PATH.exists(), f"fixture missing: {_FIXTURE_PATH}"

    config = CLIConfig(debug_dir=None, verbose=False)
    pipeline = build_pipeline(config)

    result = pipeline.transcribe(_FIXTURE_PATH)
    events: dict[str, Any] = serialize_to_schema(result)

    # Validates against the JSON schema and the cross-field sustain_until check
    validate(events)

    # Persist to a per-test path so a failure leaves the artifact behind for inspection
    output = tmp_path / "events.json"
    output.write_text(json.dumps(events, indent=2) + "\n")

    # Structural assertions — note counts and instruments change as v2 work
    # lands; the smoke test only pins the shape.
    assert events["audio_file"] == _FIXTURE_PATH.name
    assert events["duration_seconds"] > 25, "fixture should be roughly 30s"
    assert events["tempo_bpm"] > 0
    assert events["time_signature"]["numerator"] > 0
    assert events["time_signature"]["denominator"] > 0
    assert len(events["bars"]) >= 1
    total_notes = sum(len(b["notes"]) for b in events["bars"])
    assert total_notes >= 1, "expected at least one note across all bars"
