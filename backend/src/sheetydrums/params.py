"""Typed, JSON-serialisable pipeline tuning parameters.

`PipelineParams` captures the *tunable* knobs of each stage as **overrides**: a
field left ``None`` means "use the stage's built-in default", so an all-default
``PipelineParams()`` reproduces today's behaviour exactly — the factory passes
only the non-``None`` fields as constructor kwargs (see ``overrides``). This is
the object the AI tuning loop will set per project, persist, and search over; the
full catalog + ranges live in ``docs/design/ai-tuning-loop.md``.

Only knobs that are actually wired into a stage constructor appear here — no dead
params. More groups/fields get added as the corresponding stages are made
tunable (beat-meter constraints are the next to land).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any


@dataclass(frozen=True)
class SeparationParams:
    """Demucs. `model` = pretrained name (default htdemucs_ft); `shifts` = number
    of test-time-augmentation passes (higher = cleaner stem, slower)."""
    model: str | None = None
    shifts: int | None = None


@dataclass(frozen=True)
class TranscriptionParams:
    """ADTOF. `thresholds` = per-class peak-pick thresholds, in the model's class
    order (kick, snare, tom, hihat, cymbal). Lower = more sensitive (more recall)."""
    thresholds: tuple[float, ...] | None = None


@dataclass(frozen=True)
class ExpanderParams:
    """CheukExpander — the 5→10 class refinement knobs (cymbal ride/crash window,
    the hi-hat open/closed decay feature + straddle-split thresholds, tom pitch
    clustering). Names mirror ``CheukExpander.__init__``."""
    cymbal_window_seconds: tuple[float, float] | None = None
    hihat_attack_window_seconds: tuple[float, float] | None = None
    hihat_late_window_seconds: tuple[float, float] | None = None
    hihat_decay_start_seconds: float | None = None
    hihat_decay_max_seconds: float | None = None
    hihat_next_onset_guard_seconds: float | None = None
    hihat_ratio_clip: float | None = None
    hihat_clearly_tight: float | None = None
    hihat_clearly_loose: float | None = None
    hihat_bimodal_min_fraction: float | None = None
    hihat_unimodal_open_threshold: float | None = None
    tom_body_window_seconds: tuple[float, float] | None = None
    tom_centroid_band_hz: tuple[float, float] | None = None
    tom_uniform_spread_hz: float | None = None
    tom_min_cluster_gap_hz: float | None = None


@dataclass(frozen=True)
class QuantizeParams:
    """StubQuantizer. `subdivisions_per_whole` = the snap grid (16 = sixteenths)."""
    subdivisions_per_whole: int | None = None


@dataclass(frozen=True)
class PipelineParams:
    separation: SeparationParams = field(default_factory=SeparationParams)
    transcription: TranscriptionParams = field(default_factory=TranscriptionParams)
    expander: ExpanderParams = field(default_factory=ExpanderParams)
    quantize: QuantizeParams = field(default_factory=QuantizeParams)

    def to_dict(self) -> dict[str, Any]:
        """Full nested dict (including Nones) — round-trips via ``from_dict``."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> PipelineParams:
        """Rebuild from ``to_dict`` output (or a partial dict / None). Unknown
        keys are ignored so older persisted params stay loadable as fields grow."""
        d = d or {}

        def group(name: str, kind: type) -> Any:
            raw = d.get(name) or {}
            known = {f.name for f in fields(kind)}
            return kind(**{k: v for k, v in raw.items() if k in known})

        return cls(
            separation=group("separation", SeparationParams),
            transcription=group("transcription", TranscriptionParams),
            expander=group("expander", ExpanderParams),
            quantize=group("quantize", QuantizeParams),
        )


def overrides(group: Any) -> dict[str, Any]:
    """The non-``None`` fields of a params group, as constructor kwargs. Empty
    when nothing is overridden, so the stage falls back to its own defaults."""
    return {
        f.name: getattr(group, f.name)
        for f in fields(group)
        if getattr(group, f.name) is not None
    }
