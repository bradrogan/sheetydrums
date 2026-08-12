"""Pipeline factory: pick stage implementations from CLIConfig.

The single point in the codebase that decides which concrete class implements
each Protocol.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

from sheetydrums.cache import StageCache, stage_key
from sheetydrums.config import CLIConfig
from sheetydrums.debug import DebugSink
from sheetydrums.device import downstream_device
from sheetydrums.interfaces import SubStemBranch
from sheetydrums.params import PipelineParams, overrides
from sheetydrums.pipeline import Pipeline
from sheetydrums.stages import (
    ADTOFTranscriber,
    BeatThisTracker,
    CheukExpander,
    DemucsSeparator,
    DrumSepSeparator,
    StubQuantizer,
)


def stage_keys(config: CLIConfig, params: PipelineParams) -> dict[str, str]:
    """The cache key for each persisted stage, chained so a change cascades to
    everything downstream (the cache dir is per-video, so the mix is implicit)."""
    drums = stage_key("drums", asdict(params.separation))
    return {
        "drums": drums,
        "substems": stage_key("substems", drums, config.use_drumsep),
        "hits_raw": stage_key("hits_raw", drums, asdict(params.transcription)),
        "grid_raw": stage_key("grid_raw"),  # + beats params once they're tunable
    }


def build_pipeline(
    config: CLIConfig,
    *,
    params: PipelineParams | None = None,
    cache_dir: Path | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> Pipeline:
    """Construct a Pipeline wired with the implementations chosen by `config`.

    Demucs and DrumSep each get the best available device (MPS on Apple Silicon).
    ADTOF and Beat This! run on `downstream_device()` — explicitly CPU on Apple
    Silicon to dodge the post-Demucs MPS state issue. See sheetydrums/device.py.

    `on_progress`, if given, is fed each stage's log line; independent of
    `config.verbose` (which only controls stderr printing). The HTTP server
    uses it to stream pipeline progress over SSE.

    `cache_dir`, if given, enables the per-stage output cache (see cache.py) so a
    param change re-runs only the affected stages. None → caching off.
    """
    params = params or PipelineParams()
    downstream: str = downstream_device()
    substem_branch: SubStemBranch | None = None
    if config.use_drumsep:
        substem_branch = SubStemBranch(
            separator=DrumSepSeparator(),
            expander=CheukExpander(**overrides(params.expander)),
        )
    return Pipeline(
        separator=DemucsSeparator(progress=config.verbose, **overrides(params.separation)),
        transcriber=ADTOFTranscriber(device=downstream, **overrides(params.transcription)),
        beat_tracker=BeatThisTracker(device=downstream),
        quantizer=StubQuantizer(**overrides(params.quantize)),
        substem_branch=substem_branch,
        debug_sink=DebugSink(config.debug_dir),
        stage_cache=StageCache(cache_dir, stage_keys(config, params)),
        verbose=config.verbose,
        on_progress=on_progress,
    )
