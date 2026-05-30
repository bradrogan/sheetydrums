"""Class expansion: refine 5-class drum hits into the 7-class v1 vocabulary.

Two implementations are provided up front because the choice between them is
a deployment-time decision (whether LarsNet sub-stems are available):

- `PassThroughExpander` — used when sub-stems are unavailable. Returns hits
  unchanged so downstream stages see the transcriber's native 5-class output.
- `StubSubStemExpander` — placeholder for the real envelope-feature expander.
  Maps hihat → {closed, open} and cymbal → {ride, crash} on a deterministic
  alternating schedule (so A/B comparisons differ visibly from the
  pass-through case). Tom hits collapse to `tom_mid` per the v1 vocab decision.

The real `SubStemExpander` (task #9) will replace the alternating schedule
with decay-envelope analysis on the LarsNet sub-stems but keep the same
interface signature.
"""
from __future__ import annotations

from sheetydrums.interfaces import DrumHit, DrumSubStems


class PassThroughExpander:
    name = "passthrough"

    def expand(
        self,
        hits: tuple[DrumHit, ...],
        substems: DrumSubStems | None,
    ) -> tuple[DrumHit, ...]:
        _ = substems
        return hits


class StubSubStemExpander:
    """Placeholder until real envelope-feature expansion is wired in (task #9).

    Strategy:
    - hihat → hihat_closed / hihat_open, alternating
    - cymbal → ride / crash, alternating
    - tom → tom_mid (v1 collapse; all tom hits report as tom_mid until pitch
      distinction is added in v2)
    """
    name = "substem-expander-stub"

    def expand(
        self,
        hits: tuple[DrumHit, ...],
        substems: DrumSubStems | None,
    ) -> tuple[DrumHit, ...]:
        if substems is None:
            return hits
        out: list[DrumHit] = []
        hihat_n = 0
        cymbal_n = 0
        for h in hits:
            if h.drum_class == "hihat":
                refined = "hihat_open" if hihat_n % 2 else "hihat_closed"
                out.append(DrumHit(h.time, refined, h.confidence))
                hihat_n += 1
            elif h.drum_class == "cymbal":
                refined = "crash" if cymbal_n % 2 else "ride"
                out.append(DrumHit(h.time, refined, h.confidence))
                cymbal_n += 1
            elif h.drum_class == "tom":
                out.append(DrumHit(h.time, "tom_mid", h.confidence))
            else:
                out.append(h)
        return tuple(out)
