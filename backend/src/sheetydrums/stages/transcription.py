"""5-class drum transcription.

Conforms to `interfaces.DrumTranscriber`. The real implementation (task #7)
will wrap ADTOF; the stub emits a deterministic 2-bar rock pattern at 120 BPM
so the downstream pipeline produces a meaningful events.json without any
model installed.
"""
from __future__ import annotations

from sheetydrums.audio import AudioBuffer
from sheetydrums.interfaces import DrumHit


class StubADTOFTranscriber:
    name = "adtof-stub"
    vocabulary: tuple[str, ...] = ("kick", "snare", "hihat", "tom", "cymbal")

    def transcribe(self, drums: AudioBuffer) -> tuple[DrumHit, ...]:
        hits: list[DrumHit] = []
        # 2 bars of 4/4 at 120 BPM = 4 seconds. Beat = 0.5s. Eighth = 0.25s.
        for bar in range(2):
            bar_start = bar * 2.0
            # Kick on beats 1 and 3
            hits.append(DrumHit(bar_start + 0.0, "kick", 0.96))
            hits.append(DrumHit(bar_start + 1.0, "kick", 0.95))
            # Snare on beats 2 and 4
            hits.append(DrumHit(bar_start + 0.5, "snare", 0.94))
            hits.append(DrumHit(bar_start + 1.5, "snare", 0.93))
            # Hi-hat on every eighth note
            for eighth in range(8):
                hits.append(DrumHit(bar_start + eighth * 0.25, "hihat", 0.88))
            # A cymbal on the first downbeat of bar 2, to exercise the expansion stage
            if bar == 1:
                hits.append(DrumHit(bar_start + 0.0, "cymbal", 0.90))
        return tuple(sorted(hits, key=lambda h: (h.time, h.drum_class)))
