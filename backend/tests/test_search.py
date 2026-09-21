"""Phase 3b — heuristic proposer + capped edit-scored search (search/propose.py,
search/loop.py, search/knobs.py). The pipeline is a fake `run(params)->notation`
so the loop/scoring are exercised with zero model installs (DI, like
test_pipeline.py)."""
from __future__ import annotations

from typing import Any

from sheetydrums.params import PipelineParams
from sheetydrums.search.knobs import KNOB_SPECS, param_distance, targeted_lanes
from sheetydrums.search.loop import fix_the_rest, search
from sheetydrums.search.propose import Proposal, propose
from sheetydrums.search.score import Mismatch

HAT = "expander.hihat_unimodal_open_threshold"
SNARE_THR = "transcription.thresholds[snare]"


def _notation(bars: dict[int, list[tuple[str, str]]]) -> dict[str, Any]:
    return {
        "version": "1", "tempo_bpm": 120.0,
        "time_signature": {"numerator": 4, "denominator": 4},
        "bars": [
            {"index": i, "start_seconds": float(i),
             "notes": [{"instrument": inst, "position": pos, "duration": "1/8"} for inst, pos in hits]}
            for i, hits in sorted(bars.items())
        ],
    }


def _selection(lane: str, bar: int, hits: list[tuple[str, str]], sid: str = "s") -> dict[str, Any]:
    return {
        "selection_id": sid, "origin": "user", "lane": lane,
        "bar_start": bar, "bar_end": bar,
        "notes": [{"bar": bar, "note": {"instrument": inst, "position": pos, "duration": "1/8"}}
                  for inst, pos in hits],
        "ops": [], "verified": True,
    }


def _base() -> dict[str, Any]:
    return PipelineParams().to_dict()


# === knobs ===============================================================

def test_scalar_knob_get_set() -> None:
    spec = KNOB_SPECS[HAT]
    assert spec.get(_base()) == 0.40  # default when unset
    p = spec.set(_base(), 0.55)
    assert spec.get(p) == 0.55
    assert _base()["expander"]["hihat_unimodal_open_threshold"] is None  # original untouched


def test_threshold_knob_writes_full_tuple() -> None:
    spec = KNOB_SPECS[SNARE_THR]
    assert spec.get(_base()) == 0.24
    p = spec.set(_base(), 0.16)
    t = p["transcription"]["thresholds"]
    assert t[1] == 0.16 and t[0] == 0.22 and t[3] == 0.16  # snare changed, others default


def test_param_distance_and_targeted_lanes() -> None:
    spec = KNOB_SPECS[HAT]
    p = spec.set(_base(), 0.50)
    assert param_distance(p, {HAT}) > 0
    assert param_distance(_base(), {HAT}) == 0  # at default → zero movement
    assert targeted_lanes({SNARE_THR}) == {"snare"}
    assert targeted_lanes({"transcription.thresholds[tom]"}) == {"tom_high", "tom_mid", "tom_low"}


# === proposer ============================================================

def _resid(*entries: tuple[int, str, str, str]) -> list[Mismatch]:
    # (bar, instrument, position, kind)
    return [Mismatch("s", b, inst, pos, kind) for b, inst, pos, kind in entries]


def test_propose_hihat_misclassification() -> None:
    # missing open + extra closed at the same slot → hi-hat classification knobs.
    p = propose(_resid((1, "hihat_open", "0", "missing"), (1, "hihat_closed", "0", "extra")), set())
    assert p is not None and p.knobs[0] == HAT


def test_propose_detection_threshold_for_pure_missing() -> None:
    p = propose(_resid((1, "snare", "1/2", "missing")), set())
    assert p is not None and p.knobs == (SNARE_THR,)


def test_propose_skips_tried_group_then_falls_through() -> None:
    resid = _resid((1, "hihat_open", "0", "missing"), (1, "hihat_closed", "0", "extra"))
    first = propose(resid, set())
    assert first is not None
    tried = set(first.knobs)  # all hi-hat class knobs
    nxt = propose(resid, tried)
    # hi-hat class exhausted → next is the hi-hat detection threshold.
    assert nxt is not None and nxt.knobs == ("transcription.thresholds[hihat]",)


def test_propose_none_when_exhausted() -> None:
    resid = _resid((1, "snare", "1/2", "missing"))
    assert propose(resid, {SNARE_THR}) is None


# === search + capped loop (fake pipeline) ================================

def _fake_hats(open_above: float = 0.45):
    """Hats read OPEN iff the open-threshold knob exceeds `open_above`."""
    def run(params: dict[str, Any]) -> dict[str, Any]:
        thr = KNOB_SPECS[HAT].get(params)
        inst = "hihat_open" if thr > open_above else "hihat_closed"
        return _notation({1: [(inst, "0"), (inst, "1/2")]})
    return run


def test_search_finds_the_open_threshold() -> None:
    sel = _selection("hihat", 1, [("hihat_open", "0"), ("hihat_open", "1/2")])
    best = search(Proposal((HAT,), "hats"), _base(), _fake_hats(), [sel])
    assert best.score.f1 == 1.0
    assert KNOB_SPECS[HAT].get(best.params) > 0.45


def test_tie_break_prefers_smallest_movement() -> None:
    # Every threshold > 0.45 yields F1=1.0 (grid: 0.50, 0.60, 0.70). The gentlest
    # move from the 0.40 default — 0.50 — must win (D3), not a larger one.
    sel = _selection("hihat", 1, [("hihat_open", "0"), ("hihat_open", "1/2")])
    best = search(Proposal((HAT,), "hats"), _base(), _fake_hats(), [sel])
    assert KNOB_SPECS[HAT].get(best.params) == 0.50


def test_fix_the_rest_converges_and_targets_lane() -> None:
    sel = _selection("hihat", 1, [("hihat_open", "0"), ("hihat_open", "1/2")])
    out = fix_the_rest(_base(), _fake_hats(), [sel])
    assert out.converged and out.score.f1 == 1.0 and out.rounds >= 1
    assert targeted_lanes(set(out.tried_knobs)) == {"hihat"}


def test_fix_the_rest_detection_threshold() -> None:
    # A second snare only appears once the snare threshold drops to <= 0.20.
    def run(params: dict[str, Any]) -> dict[str, Any]:
        thr = KNOB_SPECS[SNARE_THR].get(params)
        hits = [("snare", "0")] + ([("snare", "1/2")] if thr <= 0.20 else [])
        return _notation({1: hits})
    sel = _selection("snare", 1, [("snare", "0"), ("snare", "1/2")])
    out = fix_the_rest(_base(), run, [sel])
    assert out.converged and out.score.f1 == 1.0
    assert KNOB_SPECS[SNARE_THR].get(out.params) <= 0.20


def test_fix_the_rest_returns_partial_when_unreachable() -> None:
    # No parameter opens the hats → the loop returns its best partial, not an error.
    sel = _selection("hihat", 1, [("hihat_open", "0")])

    def stuck(params: dict[str, Any]) -> dict[str, Any]:
        return _notation({1: [("hihat_closed", "0")]})

    out = fix_the_rest(_base(), stuck, [sel])
    assert not out.converged and out.score.f1 < 0.90
    assert out.rounds >= 1  # it tried before giving up


def test_base_already_correct_needs_no_rounds() -> None:
    sel = _selection("snare", 1, [("snare", "0")])

    def already(params: dict[str, Any]) -> dict[str, Any]:
        return _notation({1: [("snare", "0")]})

    out = fix_the_rest(_base(), already, [sel])
    assert out.converged and out.rounds == 0 and out.tried_knobs == ()
