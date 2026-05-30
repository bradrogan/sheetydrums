"""Stage implementations.

One file per stage, each containing the stub used during the
pipeline-infrastructure phase. Real implementations land here as the
corresponding task ticket (#4, #7-#11) is worked.
"""
from __future__ import annotations

from sheetydrums.stages.beats import StubBeatThisTracker
from sheetydrums.stages.expansion import StubSubStemExpander
from sheetydrums.stages.quantize import StubQuantizer
from sheetydrums.stages.separation import StubDemucsSeparator
from sheetydrums.stages.substem import StubLarsNetSeparator
from sheetydrums.stages.transcription import StubADTOFTranscriber

__all__ = [
    "StubADTOFTranscriber",
    "StubBeatThisTracker",
    "StubDemucsSeparator",
    "StubLarsNetSeparator",
    "StubQuantizer",
    "StubSubStemExpander",
]
