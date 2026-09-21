"""The edit-scored parameter search + capped auto-loop (phase3-plan.md §3b,
block 5b/5c). The pipeline is injected as `run(params_dict) -> notation` so this
is fully testable with a fake (no models); Phase 3c wires the real cached
`build_pipeline(...).transcribe(...)` in.

- `search(proposal, base_params, run, selections)` — coordinate descent over the
  proposed knobs; each candidate is a cached re-run scored by `reproduce_score`,
  ranked by **(F1 desc, parameter-movement asc)** (D3: gentlest knob nudge wins
  a tie, so a fix propagates without over-reaching).
- `fix_the_rest(...)` — runs propose→search rounds, feeding the residual back so
  each round tries a *different* knob set, stopping at convergence, the round
  cap, a no-gain round, or when the proposer is out of ideas. Returns the best
  params/score across all rounds (block 5c).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from sheetydrums.anchor import DEFAULT_TOL
from sheetydrums.search.knobs import KNOB_SPECS, param_distance
from sheetydrums.search.propose import Proposal, propose
from sheetydrums.search.score import ScoreResult, reproduce_score

RunPipeline = Callable[[dict[str, Any]], dict[str, Any]]

# Defaults (D5).
MATCH_THRESHOLD = 0.90
ROUND_CAP = 3


@dataclass(frozen=True)
class Candidate:
    params: dict[str, Any]
    score: ScoreResult
    distance: float


@dataclass(frozen=True)
class SearchOutcome:
    params: dict[str, Any]
    score: ScoreResult
    rounds: int
    converged: bool
    tried_knobs: tuple[str, ...]


def _evaluate(
    params: dict[str, Any], run: RunPipeline, selections: list[dict[str, Any]],
    scored_knobs: set[str], tol: Fraction,
) -> Candidate:
    score = reproduce_score(run(params), selections, tol)
    return Candidate(params, score, param_distance(params, scored_knobs))


def _better(a: Candidate, b: Candidate) -> bool:
    """`a` beats `b`: higher F1, then smaller parameter movement (D3 tie-break)."""
    if a.score.f1 != b.score.f1:
        return a.score.f1 > b.score.f1
    return a.distance < b.distance


def search(
    proposal: Proposal,
    base_params: dict[str, Any],
    run: RunPipeline,
    selections: list[dict[str, Any]],
    tol: Fraction = DEFAULT_TOL,
) -> Candidate:
    """Coordinate descent over `proposal.knobs`: sweep each knob's grid holding
    the others at the running best, keep the best candidate by `_better`. Starts
    from `base_params`, so a knob left unswept keeps its current value."""
    scored = set(proposal.knobs)
    best = _evaluate(base_params, run, selections, scored, tol)
    for name in proposal.knobs:
        spec = KNOB_SPECS[name]
        for value in spec.grid:
            cand = _evaluate(spec.set(best.params, value), run, selections, scored, tol)
            if _better(cand, best):
                best = cand
    return best


def fix_the_rest(
    base_params: dict[str, Any],
    run: RunPipeline,
    selections: list[dict[str, Any]],
    *,
    match_threshold: float = MATCH_THRESHOLD,
    round_cap: int = ROUND_CAP,
    tol: Fraction = DEFAULT_TOL,
) -> SearchOutcome:
    """Propose→search rounds until converged / capped / no-gain / out-of-ideas.
    Each round feeds the current residual back to the proposer with the
    knobs-already-tried, so it explores a new axis instead of re-searching one."""
    best = _evaluate(base_params, run, selections, set(), tol)
    tried: set[str] = set()
    rounds = 0
    while rounds < round_cap and best.score.f1 < match_threshold:
        proposal = propose(best.score.residual, tried)
        if proposal is None:
            break  # heuristic out of ideas → return best partial
        tried.update(proposal.knobs)
        rounds += 1
        cand = search(proposal, best.params, run, selections, tol)
        if cand.score.f1 <= best.score.f1:
            break  # no-gain round → early stop (block 5c)
        best = cand
    return SearchOutcome(
        params=best.params,
        score=best.score,
        rounds=rounds,
        converged=best.score.f1 >= match_threshold,
        tried_knobs=tuple(sorted(tried)),
    )
