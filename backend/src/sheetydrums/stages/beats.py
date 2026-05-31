"""Beat / downbeat / tempo / time-signature tracking.

Wraps Beat This! (Foscarin/Schlüter/Widmer, ISMIR 2024, MIT-licensed). Replaces
madmom's role for offline beat tracking — madmom is unmaintained on Python ≥3.10.
Conforms to `interfaces.BeatTracker`.

Lazy-loads the model on first `track()` call. First run downloads ~77 MB
of weights to `~/.cache/torch/hub/checkpoints/`; subsequent runs use the
cache.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import torch
from beat_this.inference import Audio2Beats
from numpy.typing import NDArray

from sheetydrums.audio import AudioBuffer
from sheetydrums.interfaces import Beat, BeatGrid, TimeSignature


_DEFAULT_CHECKPOINT = "final0"
# Numerators that map to a recognised simple/compound time signature; anything
# else falls back to 4/4 (the v1 default). 4 covers 4/4 (most rock/pop); 3
# covers 3/4 (waltz); 6 covers 6/8 (compound duple, written with denom=4 still
# in this stage — denominator refinement is v2 work).
_RECOGNISED_BEATS_PER_BAR = {2, 3, 4, 6, 8, 12}


class BeatThisTracker:
    """Beat This! beat + downbeat tracker."""

    name: str = f"beat-this-{_DEFAULT_CHECKPOINT}"

    def __init__(
        self,
        checkpoint: str = _DEFAULT_CHECKPOINT,
        device: str | None = None,
    ) -> None:
        self._checkpoint: str = checkpoint
        self._device: str = device if device is not None else _best_device()
        self._tracker: Any = None  # lazy

    def track(self, mix: AudioBuffer) -> BeatGrid:
        tracker = self._ensure_tracker()

        # Beat This! takes a 1-D mono float32 numpy array + sample rate.
        samples: NDArray[np.floating] = mix.samples.astype(np.float32)
        if samples.ndim > 1:
            samples = samples.mean(axis=1).astype(np.float32)

        beat_times_arr, downbeat_times_arr = tracker(samples, mix.sample_rate)
        beat_times: NDArray[np.floating] = np.asarray(beat_times_arr, dtype=np.float32)
        downbeat_times: NDArray[np.floating] = np.asarray(downbeat_times_arr, dtype=np.float32)

        beats: tuple[Beat, ...] = _build_beats(beat_times, downbeat_times)
        tempo_bpm: float = _derive_tempo(beat_times)
        time_signature: TimeSignature = _derive_time_signature(beats)

        return BeatGrid(
            beats=beats,
            tempo_bpm=tempo_bpm,
            time_signature=time_signature,
        )

    def _ensure_tracker(self) -> Any:
        if self._tracker is None:
            self._tracker = Audio2Beats(
                checkpoint_path=self._checkpoint,
                device=self._device,
            )
        return self._tracker


def _build_beats(
    beat_times: NDArray[np.floating],
    downbeat_times: NDArray[np.floating],
) -> tuple[Beat, ...]:
    """Build the Beat tuple. Beat This! emits downbeat times as a subset of
    beat times (each downbeat is also a beat), but minor float drift between
    the two lists means we match with a 5 ms tolerance rather than exact eq."""
    if downbeat_times.size == 0:
        return tuple(Beat(time=float(t), is_downbeat=False) for t in beat_times)
    return tuple(
        Beat(
            time=float(t),
            is_downbeat=bool(np.any(np.isclose(downbeat_times, t, atol=0.005))),
        )
        for t in beat_times
    )


def _derive_tempo(beat_times: NDArray[np.floating]) -> float:
    """Tempo as median(60 / inter-beat-interval). Falls back to 120 BPM if
    there aren't enough beats to estimate."""
    if beat_times.size < 2:
        return 120.0
    ibis: NDArray[np.floating] = np.diff(beat_times)
    median_ibi: float = float(np.median(ibis))
    if median_ibi <= 0:
        return 120.0
    return 60.0 / median_ibi


def _derive_time_signature(beats: tuple[Beat, ...]) -> TimeSignature:
    """Time signature from beats-between-consecutive-downbeats.

    Denominator stays at 4 in v1 (most music). Compound-meter denominator
    inference (e.g. 6/8 vs 6/4) is deferred to v2.
    """
    downbeat_positions: list[int] = [i for i, b in enumerate(beats) if b.is_downbeat]
    if len(downbeat_positions) < 2:
        return TimeSignature(numerator=4, denominator=4)
    spans: list[int] = [
        downbeat_positions[i + 1] - downbeat_positions[i]
        for i in range(len(downbeat_positions) - 1)
    ]
    most_common, _count = Counter(spans).most_common(1)[0]
    if most_common in _RECOGNISED_BEATS_PER_BAR:
        return TimeSignature(numerator=most_common, denominator=4)
    return TimeSignature(numerator=4, denominator=4)


def _best_device() -> str:
    """Pick a PyTorch device. Beat This! is plain PyTorch so MPS works."""
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"
