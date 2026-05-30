"""Pipeline factory: pick stage implementations from CLIConfig.

The single point in the codebase that decides which concrete class implements
each Protocol. When a real model wrapper lands (e.g. `DemucsSeparator`
replacing `StubDemucsSeparator`), the change is local to this file.
"""
from __future__ import annotations

from sheetydrums.config import CLIConfig
from sheetydrums.debug import DebugSink
from sheetydrums.interfaces import SubStemBranch
from sheetydrums.pipeline import Pipeline
from sheetydrums.stages import (
    StubADTOFTranscriber,
    StubBeatThisTracker,
    StubDemucsSeparator,
    StubLarsNetSeparator,
    StubQuantizer,
    StubSubStemExpander,
)


def build_pipeline(config: CLIConfig) -> Pipeline:
    """Construct a Pipeline wired with the implementations chosen by `config`."""
    substem_branch: SubStemBranch | None = None
    if config.use_larsnet:
        substem_branch = SubStemBranch(
            separator=StubLarsNetSeparator(),
            expander=StubSubStemExpander(),
        )

    return Pipeline(
        separator=StubDemucsSeparator(),
        transcriber=StubADTOFTranscriber(),
        beat_tracker=StubBeatThisTracker(),
        quantizer=StubQuantizer(),
        substem_branch=substem_branch,
        debug_sink=DebugSink(config.debug_dir),
        verbose=config.verbose,
    )
