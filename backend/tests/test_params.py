"""PipelineParams + factory threading. Stage construction is lazy (no model
loads), so building the pipeline here is cheap."""
from __future__ import annotations

from sheetydrums.config import CLIConfig
from sheetydrums.factory import build_pipeline
from sheetydrums.params import (
    ExpanderParams,
    PipelineParams,
    QuantizeParams,
    SeparationParams,
    TranscriptionParams,
    overrides,
)
from sheetydrums.stages.transcription import _TUNED_THRESHOLDS
from sheetydrums.stages.quantize import _SUBDIVISIONS_PER_WHOLE
from sheetydrums.stages.separation import _MODEL_NAME


def test_defaults_are_all_none_overrides_empty() -> None:
    p = PipelineParams()
    for group in (p.separation, p.transcription, p.expander, p.quantize):
        assert overrides(group) == {}  # nothing overridden → stages use own defaults


def test_overrides_returns_only_non_none() -> None:
    e = ExpanderParams(hihat_unimodal_open_threshold=0.55, hihat_ratio_clip=2.0)
    assert overrides(e) == {"hihat_unimodal_open_threshold": 0.55, "hihat_ratio_clip": 2.0}


def test_roundtrip_from_dict_to_dict() -> None:
    p = PipelineParams(
        transcription=TranscriptionParams(thresholds=(0.2, 0.24, 0.2, 0.16, 0.18)),
        expander=ExpanderParams(hihat_unimodal_open_threshold=0.5),
        quantize=QuantizeParams(subdivisions_per_whole=32),
        separation=SeparationParams(model="htdemucs", shifts=2),
    )
    assert PipelineParams.from_dict(p.to_dict()) == p


def test_from_dict_tolerates_partial_none_and_unknown_keys() -> None:
    assert PipelineParams.from_dict(None) == PipelineParams()
    assert PipelineParams.from_dict({}) == PipelineParams()
    # unknown keys (e.g. an older/newer persisted schema) are ignored
    p = PipelineParams.from_dict({"quantize": {"subdivisions_per_whole": 24, "bogus": 1}})
    assert p.quantize.subdivisions_per_whole == 24


def test_factory_threads_overrides_into_stages() -> None:
    params = PipelineParams(
        transcription=TranscriptionParams(thresholds=(0.1, 0.1, 0.1, 0.1, 0.1)),
        expander=ExpanderParams(hihat_unimodal_open_threshold=0.55),
        quantize=QuantizeParams(subdivisions_per_whole=32),
        separation=SeparationParams(model="htdemucs", shifts=3),
    )
    p = build_pipeline(CLIConfig(use_drumsep=True), params=params)
    assert p._transcriber._thresholds == (0.1, 0.1, 0.1, 0.1, 0.1)
    assert p._substem_branch is not None
    assert p._substem_branch.expander._hihat_unimodal_open_threshold == 0.55
    assert p._quantizer._subdivisions_per_whole == 32
    assert p._separator._model_name == "htdemucs"
    assert p._separator._shifts == 3


def test_factory_defaults_match_stage_builtins() -> None:
    # No params → every stage falls back to its own built-in defaults (the
    # drift guard: default PipelineParams must reproduce today's behaviour).
    p = build_pipeline(CLIConfig(use_drumsep=True))
    assert p._transcriber._thresholds == _TUNED_THRESHOLDS
    assert p._quantizer._subdivisions_per_whole == _SUBDIVISIONS_PER_WHOLE
    assert p._separator._model_name == _MODEL_NAME
    assert p._separator._shifts is None
