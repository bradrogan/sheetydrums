"""Drum sub-stem separation.

Conforms to `interfaces.DrumSubStemSeparator`. The real implementation
(task #8) will wrap LarsNet to produce 5 per-class audio sub-stems; the stub
returns the same buffer for every class — enough to exercise the wiring of
the class-expansion stage.
"""
from __future__ import annotations

from sheetydrums.audio import AudioBuffer
from sheetydrums.interfaces import DrumSubStems


class StubLarsNetSeparator:
    name: str = "larsnet-stub"

    def separate(self, drums: AudioBuffer) -> DrumSubStems:
        return DrumSubStems(
            kick=drums,
            snare=drums,
            hihat=drums,
            toms=drums,
            cymbals=drums,
        )
