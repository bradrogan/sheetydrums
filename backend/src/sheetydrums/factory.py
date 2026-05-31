"""Pipeline factory: pick stage implementations from CLIConfig.

The single point in the codebase that decides which concrete class implements
each Protocol. When a real model wrapper lands (e.g. v2's sub-stem class
expansion), the change is local to this file.
"""
from __future__ import annotations

from sheetydrums.config import CLIConfig
from sheetydrums.debug import DebugSink
from sheetydrums.device import downstream_device
from sheetydrums.pipeline import Pipeline
from sheetydrums.stages import (
    ADTOFTranscriber,
    BeatThisTracker,
    DemucsSeparator,
    StubQuantizer,
)


def build_pipeline(config: CLIConfig) -> Pipeline:
    """Construct a Pipeline wired with the implementations chosen by `config`.

    Demucs gets the best available device (MPS on Apple Silicon). ADTOF and
    Beat This! run on `downstream_device()` — explicitly CPU on Apple Silicon
    to dodge the post-Demucs MPS state issue. See sheetydrums/device.py for
    why. CPU is competitive for those small models, so the cost is minor.

    v1 ships without a sub-stem branch; the `SubStemBranch` slot on `Pipeline`
    stays available for v2 work (see docs/v2-backlog.md → "5 → 7 class expansion").
    """
    downstream = downstream_device()
    return Pipeline(
        separator=DemucsSeparator(progress=config.verbose),
        transcriber=ADTOFTranscriber(device=downstream),
        beat_tracker=BeatThisTracker(device=downstream),
        quantizer=StubQuantizer(),
        substem_branch=None,
        debug_sink=DebugSink(config.debug_dir),
        verbose=config.verbose,
    )
