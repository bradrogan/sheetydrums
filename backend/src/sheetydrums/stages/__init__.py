"""Stage implementations.

One file per stage. Real implementations land here as the corresponding task
ticket (#4, #7-#11) is worked. The current set:

- separation.py    DemucsSeparator (htdemucs_ft) — REAL (task #4)
- transcription.py ADTOFTranscriber (pytorch port) — REAL (task #7)
- substem.py       StubLarsNetSeparator — pending task #8
- expansion.py     StubSubStemExpander — pending task #9
- beats.py         BeatThisTracker — REAL (task #10)
- quantize.py      StubQuantizer — pending task #11
"""
from __future__ import annotations

from sheetydrums.stages.beats import BeatThisTracker
from sheetydrums.stages.expansion import StubSubStemExpander
from sheetydrums.stages.quantize import StubQuantizer
from sheetydrums.stages.separation import DemucsSeparator
from sheetydrums.stages.substem import StubLarsNetSeparator
from sheetydrums.stages.transcription import ADTOFTranscriber

__all__ = [
    "ADTOFTranscriber",
    "BeatThisTracker",
    "DemucsSeparator",
    "StubLarsNetSeparator",
    "StubQuantizer",
    "StubSubStemExpander",
]
