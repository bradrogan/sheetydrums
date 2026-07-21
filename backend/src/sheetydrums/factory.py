"""Pipeline factory: pick stage implementations from CLIConfig.

The single point in the codebase that decides which concrete class implements
each Protocol.
"""
from __future__ import annotations

from collections.abc import Callable

from sheetydrums.config import CLIConfig
from sheetydrums.debug import DebugSink
from sheetydrums.device import downstream_device
from sheetydrums.interfaces import SubStemBranch
from sheetydrums.pipeline import Pipeline
from sheetydrums.stages import (
    ADTOFTranscriber,
    BeatThisTracker,
    CheukExpander,
    DemucsSeparator,
    DrumSepSeparator,
    StubQuantizer,
)


def build_pipeline(
    config: CLIConfig,
    *,
    on_progress: Callable[[str], None] | None = None,
) -> Pipeline:
    """Construct a Pipeline wired with the implementations chosen by `config`.

    Demucs and DrumSep each get the best available device (MPS on Apple Silicon).
    ADTOF and Beat This! run on `downstream_device()` — explicitly CPU on Apple
    Silicon to dodge the post-Demucs MPS state issue. See sheetydrums/device.py.

    `on_progress`, if given, is fed each stage's log line; independent of
    `config.verbose` (which only controls stderr printing). The HTTP server
    uses it to stream pipeline progress over SSE.
    """
    downstream: str = downstream_device()
    substem_branch: SubStemBranch | None = None
    if config.use_drumsep:
        substem_branch = SubStemBranch(
            separator=DrumSepSeparator(),
            expander=CheukExpander(),
        )
    return Pipeline(
        separator=DemucsSeparator(progress=config.verbose),
        transcriber=ADTOFTranscriber(device=downstream),
        beat_tracker=BeatThisTracker(device=downstream),
        quantizer=StubQuantizer(),
        substem_branch=substem_branch,
        debug_sink=DebugSink(config.debug_dir),
        verbose=config.verbose,
        on_progress=on_progress,
    )
