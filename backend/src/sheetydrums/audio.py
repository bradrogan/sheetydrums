"""Audio buffer type and loading helper.

`load_audio` is a stub for the pipeline-infrastructure phase. The Pipeline
orchestrator and all stub stages happen to ignore the audio contents, so the
stub returns 4 seconds of silence regardless of input — enough to drive the
shape of the pipeline end-to-end. When real stages land, this becomes a thin
wrapper over `soundfile.read` (or `librosa.load`).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class AudioBuffer:
    """Immutable audio samples + their sample rate.

    `samples` is shape (n,) for mono, (n, channels) for multi-channel.
    """
    samples: NDArray[np.floating]
    sample_rate: int

    @property
    def duration_seconds(self) -> float:
        return self.samples.shape[0] / self.sample_rate

    @property
    def is_mono(self) -> bool:
        return self.samples.ndim == 1 or self.samples.shape[1] == 1


def load_audio(path: Path, target_sample_rate: int = 44100) -> AudioBuffer:
    """Load an audio file. Currently a stub that returns 4s of silence at 44.1 kHz.

    The path is accepted (and required to exist by the CLI) but its contents
    are not yet read — once a real loader is wired in this will respect the
    target sample rate and decode the actual file.
    """
    _ = path  # accepted for interface parity; not yet read
    samples: NDArray[np.floating] = np.zeros(4 * target_sample_rate, dtype=np.float32)
    return AudioBuffer(samples=samples, sample_rate=target_sample_rate)
