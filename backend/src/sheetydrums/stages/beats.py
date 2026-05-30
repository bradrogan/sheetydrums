"""Beat / downbeat / tempo / time-signature tracking.

Conforms to `interfaces.BeatTracker`. The real implementation (task #10)
will wrap Beat This!; the stub emits a steady 4/4 at 120 BPM grid spanning
the input audio's duration.
"""
from __future__ import annotations

from sheetydrums.audio import AudioBuffer
from sheetydrums.interfaces import BeatGrid


class StubBeatThisTracker:
    name = "beat-this-stub"

    def track(self, mix: AudioBuffer) -> BeatGrid:
        tempo_bpm = 120.0
        ibi = 60.0 / tempo_bpm  # 0.5 s
        duration = max(mix.duration_seconds, 4.0)
        n_beats = int(duration / ibi)
        beats = tuple(i * ibi for i in range(n_beats))
        downbeats = tuple(i % 4 == 0 for i in range(n_beats))
        return BeatGrid(
            beats=beats,
            downbeats=downbeats,
            tempo_bpm=tempo_bpm,
            time_signature=(4, 4),
        )
