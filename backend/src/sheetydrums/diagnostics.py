"""Structured diagnostics for a transcription.

Turns a stored `notation` payload into a compact, structured summary — per-class
hit counts, the hi-hat open/closed balance, confidence distribution, empty bars,
and a few heuristic flags. This is the signal the tuning loop reads to decide
which knobs to touch (and what a human sees in the params panel); it is
deliberately computed from the emitted notation alone, so it needs no models and
no pipeline re-run.

Richer, feature-level diagnostics (e.g. the expander's per-hit decay ratios) come
from the stage cache later; this is the notation-level view.
"""
from __future__ import annotations

import statistics
from fractions import Fraction
from typing import Any

# Schema drum classes, grouped for reporting.
_HAT_CLOSED = "hihat_closed"
_HAT_OPEN = "hihat_open"


def diagnose(notation: dict[str, Any]) -> dict[str, Any]:
    """Summarise a notation dict (the events.json contract). Pure + cheap."""
    bars: list[dict[str, Any]] = notation.get("bars") or []
    ts = notation.get("time_signature") or {}
    num, den = ts.get("numerator"), ts.get("denominator")

    per_class: dict[str, int] = {}
    confidences: list[float] = []
    empty_bars = 0
    # sixteenth positions used by each class, for the "off-beat" heuristics
    kick_positions: list[int] = []

    for bar in bars:
        notes = bar.get("notes") or []
        if not notes:
            empty_bars += 1
        for n in notes:
            inst = n["instrument"]
            per_class[inst] = per_class.get(inst, 0) + 1
            c = n.get("confidence")
            if isinstance(c, (int, float)):
                confidences.append(float(c))
            if inst == "kick":
                kick_positions.append(_sixteenth(n.get("position", "0")))

    n_notes = sum(per_class.values())
    n_bars = len(bars)

    closed = per_class.get(_HAT_CLOSED, 0)
    open_ = per_class.get(_HAT_OPEN, 0)
    hat_total = closed + open_
    hats = {
        "closed": closed,
        "open": open_,
        "open_fraction": (open_ / hat_total) if hat_total else None,
    }

    confidence = {"present": bool(confidences)}
    if confidences:
        confidence |= {
            "min": round(min(confidences), 4),
            "median": round(statistics.median(confidences), 4),
            "mean": round(statistics.mean(confidences), 4),
        }

    summary: dict[str, Any] = {
        "tempo_bpm": notation.get("tempo_bpm"),
        "time_signature": f"{num}/{den}" if num and den else None,
        "n_bars": n_bars,
        "n_notes": n_notes,
        "notes_per_bar": round(n_notes / n_bars, 2) if n_bars else 0.0,
        "per_class": dict(sorted(per_class.items())),
        "hats": hats,
        "confidence": confidence,
        "empty_bars": empty_bars,
    }
    summary["flags"] = _flags(summary, kick_positions)
    return summary


def _flags(summary: dict[str, Any], kick_positions: list[int]) -> list[str]:
    """Cheap heuristic call-outs — hints, not verdicts."""
    flags: list[str] = []
    hats = summary["hats"]
    frac = hats["open_fraction"]
    if hats["closed"] + hats["open"] > 0:
        if frac == 1.0:
            flags.append("hi-hat is labelled entirely open")
        elif frac == 0.0:
            flags.append("hi-hat is labelled entirely closed")
    if summary["empty_bars"]:
        flags.append(f"{summary['empty_bars']} bar(s) with no notes")
    # Kicks landing on odd (off-beat) 16th slots suggest over-detection / bleed:
    # real kicks sit on strong subdivisions far more often than the 'e'/'a'.
    if kick_positions:
        off = sum(1 for p in kick_positions if p % 2 == 1) / len(kick_positions)
        if off > 0.4:
            flags.append(f"{off:.0%} of kicks fall on off-beat 16ths (possible over-detection)")
    return flags


def _sixteenth(position: str) -> int:
    """Position string ('0', '1/4', '3/8', ...) → nearest 16th slot index."""
    try:
        return round(float(Fraction(position)) * 16)
    except (ValueError, ZeroDivisionError):
        return 0
