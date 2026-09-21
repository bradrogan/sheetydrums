"""Grace-note / flam support (notation-only; see docs/design/grace-notes-flams.md).

Detection is out of scope — a `grace` arrives only via manual editing. These
tests pin the two load-bearing backend seams the feature touched:
  - the schema accepts an optional `grace` (instrument constrained by the shared
    enum) and rejects a bad grace instrument;
  - anchor.region_fingerprint hashes `grace` so a flam added under a verified
    region reads as drift;
  - anchor.replay_ops (via _restore_attributes) preserves a frozen note's grace
    through a replayed reclassify, and clears it when the frozen note has none.
"""
from __future__ import annotations

import copy
from typing import Any

import jsonschema
import pytest

from sheetydrums.anchor import region_fingerprint, replay_ops
from sheetydrums.validate import validate, validate_selection


def _notation() -> dict[str, Any]:
    return {
        "version": "1", "tempo_bpm": 120.0,
        "time_signature": {"numerator": 4, "denominator": 4},
        "bars": [
            {"index": 1, "start_seconds": 0.0, "notes": [
                {"instrument": "snare", "position": "1/4", "duration": "1/8"},
            ]},
        ],
    }


# === schema ==============================================================

def test_schema_accepts_flam() -> None:
    events = _notation()
    events["bars"][0]["notes"][0]["grace"] = {"instrument": "snare", "slashed": True}
    validate(events)  # must not raise


def test_schema_accepts_grace_without_slashed() -> None:
    events = _notation()
    events["bars"][0]["notes"][0]["grace"] = {"instrument": "snare"}
    validate(events)  # slashed is optional (renderer defaults to a flam)


def test_schema_rejects_bad_grace_instrument() -> None:
    events = _notation()
    events["bars"][0]["notes"][0]["grace"] = {"instrument": "cowbell"}
    with pytest.raises(jsonschema.ValidationError):
        validate(events)


def test_schema_accepts_ghost() -> None:
    events = _notation()
    events["bars"][0]["notes"][0]["ghost"] = True
    validate(events)  # must not raise


def test_selection_with_grace_note_validates() -> None:
    # The real Phase-2 persistence path: a flam lives on a verified selection's
    # frozen note. Also exercises the events.schema Note.instrument -> Instrument
    # ref-to-ref that the op's instrument $ref now resolves through.
    sel: dict[str, Any] = {
        "selection_id": "sel_1", "origin": "user", "lane": "snare",
        "bar_start": 1, "bar_end": 1,
        "notes": [{"bar": 1, "note": {
            "instrument": "snare", "position": "1/4", "duration": "1/8",
            "grace": {"instrument": "snare", "slashed": True},
        }}],
        "ops": [{"kind": "add", "bar": 1, "position": "1/4", "instrument": "snare", "duration": "1/8"}],
        "verified": True,
    }
    validate_selection(sel)  # must not raise


# === region_fingerprint drift ===========================================

def test_fingerprint_changes_when_flam_added() -> None:
    base = _notation()
    before = region_fingerprint(base, "snare", 1, 1)
    graced = copy.deepcopy(base)
    graced["bars"][0]["notes"][0]["grace"] = {"instrument": "snare", "slashed": True}
    after = region_fingerprint(graced, "snare", 1, 1)
    assert before != after  # a flam under the region must register as drift


def test_fingerprint_changes_when_ghost_added() -> None:
    base = _notation()
    before = region_fingerprint(base, "snare", 1, 1)
    ghosted = copy.deepcopy(base)
    ghosted["bars"][0]["notes"][0]["ghost"] = True
    after = region_fingerprint(ghosted, "snare", 1, 1)
    assert before != after  # a ghost under the region must register as drift


# === replay_ops / _restore_attributes ===================================

def test_replay_reclassify_keeps_frozen_flam() -> None:
    # Base has a plain tom_mid at 1/4; the user's frozen note carries a flam and
    # a reclassify tom_mid -> tom_high. Replaying the op must keep the flam.
    base: dict[str, Any] = {
        "version": "1", "tempo_bpm": 120.0,
        "time_signature": {"numerator": 4, "denominator": 4},
        "bars": [{"index": 1, "start_seconds": 0.0, "notes": [
            {"instrument": "tom_mid", "position": "1/4", "duration": "1/8"},
        ]}],
    }
    frozen = [{"bar": 1, "note": {
        "instrument": "tom_high", "position": "1/4", "duration": "1/8",
        "grace": {"instrument": "tom_high", "slashed": True}, "ghost": True,
    }}]
    ops = [{"kind": "reclassify", "bar": 1, "position": "1/4", "from": "tom_mid", "to": "tom_high"}]
    result, conflicts = replay_ops(base, ops, frozen_notes=frozen)
    assert conflicts == []
    note = result["bars"][0]["notes"][0]
    assert note["instrument"] == "tom_high"
    assert note.get("grace") == {"instrument": "tom_high", "slashed": True}
    assert note.get("ghost") is True  # ghost also rides through the replay


def test_replay_clears_grace_and_ghost_when_frozen_has_none() -> None:
    # Base note carries a flam + ghost; the frozen ground truth has neither →
    # replay drops both.
    base: dict[str, Any] = {
        "version": "1", "tempo_bpm": 120.0,
        "time_signature": {"numerator": 4, "denominator": 4},
        "bars": [{"index": 1, "start_seconds": 0.0, "notes": [
            {"instrument": "snare", "position": "1/4", "duration": "1/8",
             "grace": {"instrument": "snare", "slashed": True}, "ghost": True},
        ]}],
    }
    frozen = [{"bar": 1, "note": {"instrument": "snare", "position": "1/4", "duration": "1/8"}}]
    ops = [{"kind": "move", "bar": 1, "from_position": "1/4", "to_position": "1/4", "instrument": "snare"}]
    result, _ = replay_ops(base, ops, frozen_notes=frozen)
    assert "grace" not in result["bars"][0]["notes"][0]
    assert "ghost" not in result["bars"][0]["notes"][0]
