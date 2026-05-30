"""Mix-level source separation.

Conforms to `interfaces.MixSeparator`. The real implementation (task #4)
will wrap Demucs htdemucs and isolate the drums stem; the stub returns the
input mix unchanged so downstream stub stages can run.
"""
from __future__ import annotations

from sheetydrums.audio import AudioBuffer


class StubDemucsSeparator:
    name = "demucs-stub"

    def separate(self, mix: AudioBuffer) -> AudioBuffer:
        return mix
