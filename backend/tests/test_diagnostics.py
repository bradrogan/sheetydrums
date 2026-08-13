"""Unit tests for the notation diagnostics summary — pure, no models."""
from __future__ import annotations

from typing import Any

from sheetydrums.diagnostics import diagnose


def _bar(index: int, notes: list[dict[str, Any]]) -> dict[str, Any]:
    return {"index": index, "start_seconds": float(index), "notes": notes}


def _note(inst: str, pos: str = "0", conf: float | None = None) -> dict[str, Any]:
    n: dict[str, Any] = {"instrument": inst, "position": pos, "duration": "1/8"}
    if conf is not None:
        n["confidence"] = conf
    return n


def _notation(bars: list[dict[str, Any]], num: int = 4, den: int = 4) -> dict[str, Any]:
    return {
        "version": "1",
        "tempo_bpm": 120.0,
        "time_signature": {"numerator": num, "denominator": den},
        "bars": bars,
    }


def test_empty_notation() -> None:
    d = diagnose({})
    assert d["n_bars"] == 0 and d["n_notes"] == 0 and d["notes_per_bar"] == 0.0
    assert d["per_class"] == {}
    assert d["hats"]["open_fraction"] is None
    assert d["confidence"]["present"] is False
    assert d["flags"] == []
    assert d["time_signature"] is None


def test_counts_hats_and_confidence() -> None:
    d = diagnose(_notation([
        _bar(1, [
            _note("kick", "0", 0.9),
            _note("snare", "1/4", 0.8),
            _note("hihat_closed", "0", 0.7),
            _note("hihat_open", "1/8", 0.6),
        ]),
        _bar(2, []),  # empty
    ]))
    assert d["per_class"] == {"hihat_closed": 1, "hihat_open": 1, "kick": 1, "snare": 1}
    assert d["n_notes"] == 4 and d["n_bars"] == 2 and d["notes_per_bar"] == 2.0
    assert d["hats"] == {"closed": 1, "open": 1, "open_fraction": 0.5}
    assert d["confidence"]["present"] is True
    assert d["confidence"]["min"] == 0.6 and d["confidence"]["mean"] == 0.75
    assert d["time_signature"] == "4/4"
    assert "1 bar(s) with no notes" in d["flags"]


def test_flag_all_open_and_all_closed() -> None:
    allopen = diagnose(_notation([_bar(1, [_note("hihat_open"), _note("hihat_open", "1/8")])]))
    assert "hi-hat is labelled entirely open" in allopen["flags"]
    allclosed = diagnose(_notation([_bar(1, [_note("hihat_closed"), _note("hihat_closed", "1/8")])]))
    assert "hi-hat is labelled entirely closed" in allclosed["flags"]


def test_flag_offbeat_kicks() -> None:
    # 2 of 3 kicks on odd (off-beat) 16th slots → over-detection hint.
    d = diagnose(_notation([_bar(1, [
        _note("kick", "0"),      # slot 0 (on-beat)
        _note("kick", "1/16"),   # slot 1 (off-beat)
        _note("kick", "3/16"),   # slot 3 (off-beat)
    ])]))
    assert any("off-beat 16ths" in f for f in d["flags"])


def test_no_confidence_key() -> None:
    d = diagnose(_notation([_bar(1, [_note("kick"), _note("snare", "1/4")])]))
    assert d["confidence"] == {"present": False}
