"""Stage implementations.

One file per stage:

- separation.py    DemucsSeparator (htdemucs_ft) — mix → drums stem
- transcription.py ADTOFTranscriber (pytorch port) — 5-class onsets
- drumsep.py       DrumSepSeparator (MDX23C 6-stem) — drums → 6 sub-stems
- expander.py      CheukExpander — 5-class → 7-class via sub-stem energy
- beats.py         BeatThisTracker — beats + downbeats
- quantize.py      StubQuantizer + the v1 vocabulary-collapse fallback map
"""
from __future__ import annotations

from sheetydrums.stages.beats import BeatThisTracker
from sheetydrums.stages.drumsep import DrumSepSeparator
from sheetydrums.stages.expander import CheukExpander
from sheetydrums.stages.quantize import StubQuantizer
from sheetydrums.stages.separation import DemucsSeparator
from sheetydrums.stages.transcription import ADTOFTranscriber

__all__ = [
    "ADTOFTranscriber",
    "BeatThisTracker",
    "CheukExpander",
    "DemucsSeparator",
    "DrumSepSeparator",
    "StubQuantizer",
]
