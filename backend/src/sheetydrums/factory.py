"""Pipeline factory: pick stage implementations from CLIConfig.

The single point in the codebase that decides which concrete class implements
each Protocol. When a real model wrapper lands (e.g. `DemucsSeparator`
replacing `StubDemucsSeparator`), the change is local to this file.
"""
from __future__ import annotations

from sheetydrums.config import CLIConfig
from sheetydrums.debug import DebugSink
from sheetydrums.pipeline import Pipeline
from sheetydrums.stages import (
    PassThroughExpander,
    StubADTOFTranscriber,
    StubBeatThisTracker,
    StubDemucsSeparator,
    StubLarsNetSeparator,
    StubQuantizer,
    StubSubStemExpander,
)


def build_pipeline(config: CLIConfig) -> Pipeline:
    """Construct a Pipeline wired with the implementations chosen by `config`."""
    substem_separator = StubLarsNetSeparator() if config.use_larsnet else None
    class_expander = StubSubStemExpander() if config.use_larsnet else PassThroughExpander()

    return Pipeline(
        separator=StubDemucsSeparator(),
        transcriber=StubADTOFTranscriber(),
        beat_tracker=StubBeatThisTracker(),
        quantizer=StubQuantizer(),
        class_expander=class_expander,
        substem_separator=substem_separator,
        debug_sink=DebugSink(config.debug_dir),
        verbose=config.verbose,
    )
