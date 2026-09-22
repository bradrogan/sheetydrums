"""The machine-readable knob catalog the Phase 3 search walks (phase3-plan.md
§3b). Each `KnobSpec` knows its default, a coarse search grid, which staff
**lanes** it affects (for the D4 targeted-lane delta), and how to read/write its
value in a `PipelineParams` dict — so the search treats a plain scalar
(`expander.hihat_unimodal_open_threshold`) and a tuple element
(`transcription.thresholds[hihat]`) uniformly.

Defaults mirror the stage constructors (`stages/expander.py`,
`stages/transcription.py`); grids bracket each default. Kept scalar-only for now
— the 2-tuple window knobs (`cymbal_window_seconds`, `tom_centroid_band_hz`) are
deferred, so ride/crash misclassification isn't yet reachable by the heuristic.
"""
from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# ADTOF peak-pick classes, in model order, with their tuned defaults
# (stages/transcription.py `_CLASS_AT_INDEX` / `_TUNED_THRESHOLDS`).
ADTOF_CLASSES: tuple[str, ...] = ("kick", "snare", "tom", "hihat", "cymbal")
ADTOF_DEFAULTS: tuple[float, ...] = (0.22, 0.24, 0.20, 0.16, 0.18)

# Which staff lanes each ADTOF class feeds (a threshold on 'tom' moves all three
# tom lanes; 'cymbal' feeds ride + crash).
THRESHOLD_LANES: dict[str, tuple[str, ...]] = {
    "kick": ("kick",),
    "snare": ("snare",),
    "tom": ("tom_high", "tom_mid", "tom_low"),
    "hihat": ("hihat",),
    "cymbal": ("ride", "crash"),
}

# A schema instrument → its ADTOF class (for turning a residual on a specific
# instrument into the threshold knob that governs its detection).
ADTOF_CLASS_OF: dict[str, str] = {
    "kick": "kick", "snare": "snare",
    "tom_high": "tom", "tom_mid": "tom", "tom_low": "tom",
    "hihat_closed": "hihat", "hihat_open": "hihat", "hihat_chick": "hihat",
    "ride": "cymbal", "crash": "cymbal",
}


@dataclass(frozen=True)
class KnobSpec:
    name: str                    # dotted, e.g. "transcription.thresholds[hihat]"
    lanes: tuple[str, ...]       # staff lanes this knob affects (D4 targeting)
    default: float
    grid: tuple[float, ...]      # coarse candidate values the search tries
    get: Callable[[dict[str, Any]], float]
    set: Callable[[dict[str, Any], float], dict[str, Any]]

    @property
    def span(self) -> float:
        return (max(self.grid) - min(self.grid)) or 1.0


def _scalar(name: str, group: str, field: str, lanes: tuple[str, ...],
            default: float, grid: tuple[float, ...]) -> KnobSpec:
    def get(p: dict[str, Any]) -> float:
        v = (p.get(group) or {}).get(field)
        return default if v is None else float(v)

    def setv(p: dict[str, Any], value: float) -> dict[str, Any]:
        out = copy.deepcopy(p)
        grp = out.get(group) or {}  # `or {}` — the key may be present but None
        grp[field] = value
        out[group] = grp
        return out

    return KnobSpec(name, lanes, default, grid, get, setv)


def _grid_around(default: float, offsets: tuple[float, ...], lo: float, hi: float) -> tuple[float, ...]:
    vals = sorted({round(min(hi, max(lo, default + o)), 4) for o in (0.0, *offsets)})
    return tuple(vals)


def _threshold(cls: str) -> KnobSpec:
    idx = ADTOF_CLASSES.index(cls)
    default = ADTOF_DEFAULTS[idx]
    grid = _grid_around(default, (-0.08, -0.04, 0.04, 0.08), lo=0.06, hi=0.42)

    def get(p: dict[str, Any]) -> float:
        t = (p.get("transcription") or {}).get("thresholds")
        return float(t[idx]) if t else ADTOF_DEFAULTS[idx]

    def setv(p: dict[str, Any], value: float) -> dict[str, Any]:
        out = copy.deepcopy(p)
        grp = out.get("transcription") or {}  # key may be present but None
        t = list(grp.get("thresholds") or ADTOF_DEFAULTS)
        t[idx] = value
        grp["thresholds"] = t
        out["transcription"] = grp
        return out

    return KnobSpec(f"transcription.thresholds[{cls}]", THRESHOLD_LANES[cls], default, grid, get, setv)


# The catalog. Names are the proposer's vocabulary.
KNOB_SPECS: dict[str, KnobSpec] = {
    spec.name: spec
    for spec in (
        # hi-hat open/closed classification
        _scalar("expander.hihat_unimodal_open_threshold", "expander", "hihat_unimodal_open_threshold",
                ("hihat",), 0.40, (0.20, 0.30, 0.40, 0.50, 0.60, 0.70)),
        _scalar("expander.hihat_clearly_loose", "expander", "hihat_clearly_loose",
                ("hihat",), 0.70, (0.50, 0.60, 0.70, 0.80, 0.90)),
        _scalar("expander.hihat_clearly_tight", "expander", "hihat_clearly_tight",
                ("hihat",), 0.15, (0.05, 0.10, 0.15, 0.20, 0.30)),
        _scalar("expander.hihat_bimodal_min_fraction", "expander", "hihat_bimodal_min_fraction",
                ("hihat",), 0.15, (0.05, 0.10, 0.15, 0.25)),
        # tom pitch clustering
        _scalar("expander.tom_uniform_spread_hz", "expander", "tom_uniform_spread_hz",
                ("tom_high", "tom_mid", "tom_low"), 25.0, (10.0, 25.0, 40.0, 60.0, 90.0)),
        _scalar("expander.tom_min_cluster_gap_hz", "expander", "tom_min_cluster_gap_hz",
                ("tom_high", "tom_mid", "tom_low"), 25.0, (10.0, 25.0, 40.0, 60.0)),
        # per-class detection thresholds
        _threshold("kick"), _threshold("snare"), _threshold("tom"),
        _threshold("hihat"), _threshold("cymbal"),
    )
}


def param_distance(params: dict[str, Any], knob_names: tuple[str, ...] | set[str]) -> float:
    """Normalised total movement of `knob_names` from their defaults — the D3
    tie-break (smallest wins). Each knob contributes |value − default| / span."""
    total = 0.0
    for name in knob_names:
        spec = KNOB_SPECS.get(name)
        if spec is None:
            continue
        total += abs(spec.get(params) - spec.default) / spec.span
    return total


def targeted_lanes(knob_names: tuple[str, ...] | set[str]) -> set[str]:
    """The staff lanes any of `knob_names` affects — the lanes a resulting system
    pass may write (D4)."""
    lanes: set[str] = set()
    for name in knob_names:
        spec = KNOB_SPECS.get(name)
        if spec is not None:
            lanes.update(spec.lanes)
    return lanes
