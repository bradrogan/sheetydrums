"""Beat / downbeat / tempo / time-signature tracking.

Conforms to `interfaces.BeatTracker`. The real implementation (task #10)
will wrap Beat This!; the stub emits a steady 4/4 at 120 BPM grid spanning
the input audio's duration.
"""
from __future__ import annotations

from sheetydrums.audio import AudioBuffer
from sheetydrums.interfaces import BeatGrid


class StubBeatThisTracker:
    name: str = "beat-this-stub"

    def track(self, mix: AudioBuffer) -> BeatGrid:
        tempo_bpm: float = 120.0
        ibi: float = 60.0 / tempo_bpm  # inter-beat interval, seconds
        duration: float = max(mix.duration_seconds, 4.0)
        n_beats: int = int(duration / ibi)
        beats: tuple[float, ...] = tuple(i * ibi for i in range(n_beats))
        downbeats: tuple[bool, ...] = tuple(i % 4 == 0 for i in range(n_beats))
        return BeatGrid(
            beats=beats,
            downbeats=downbeats,
            tempo_bpm=tempo_bpm,
            time_signature=(4, 4),
        )
