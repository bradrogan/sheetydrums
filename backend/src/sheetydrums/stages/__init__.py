"""Stage implementations.

One file per stage. v1 ships with three real models and one stub quantizer:

- separation.py    DemucsSeparator (htdemucs_ft) — REAL
- transcription.py ADTOFTranscriber (pytorch port) — REAL
- beats.py         BeatThisTracker — REAL
- quantize.py      StubQuantizer + the v1 vocabulary-collapse map

The Protocols `DrumSubStemSeparator` and `ClassExpander` (in
`sheetydrums.interfaces`) exist for v2 work to implement against — see
`docs/v2-backlog.md` → "5 → 7 class expansion". No v1 implementations exist
because the available sub-stem separators (LarsNet, jarredou's DrumSep)
aren't packaged for downstream library use today.
"""
from __future__ import annotations

from sheetydrums.stages.beats import BeatThisTracker
from sheetydrums.stages.quantize import StubQuantizer
from sheetydrums.stages.separation import DemucsSeparator
from sheetydrums.stages.transcription import ADTOFTranscriber

__all__ = [
    "ADTOFTranscriber",
    "BeatThisTracker",
    "DemucsSeparator",
    "StubQuantizer",
]
