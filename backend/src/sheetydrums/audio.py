"""Audio buffer type and loading helper."""
from __future__ import annotations

from dataclasses import dataclass
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from numpy.typing import NDArray
from scipy import signal as scipy_signal


@dataclass(frozen=True)
class AudioBuffer:
    """Immutable audio samples + their sample rate.

    `samples` is shape (n,) for mono or (n, channels) for multi-channel.
    Channels are preserved end-to-end; downstream stages (e.g. ADTOF) that
    require mono should mix down at their own boundary.

    True immutability: `frozen=True` blocks reassigning the field, and
    `__post_init__` flips numpy's write flag so in-place mutation also
    fails loudly. Stages that need to modify audio must `.copy()` first
    (numpy operations like `.astype()` already copy by default).
    """
    samples: NDArray[np.floating]
    sample_rate: int

    def __post_init__(self) -> None:
        self.samples.setflags(write=False)

    @property
    def duration_seconds(self) -> float:
        return self.samples.shape[0] / self.sample_rate

    @property
    def is_mono(self) -> bool:
        return self.samples.ndim == 1 or self.samples.shape[1] == 1


def load_audio(path: Path, target_sample_rate: int = 44100) -> AudioBuffer:
    """Decode an audio file and return it at `target_sample_rate`.

    Uses libsndfile (via soundfile) for decoding, which handles wav, flac, ogg,
    and mp3 (libsndfile >= 1.1). Resamples via scipy.signal.resample_poly if the
    file's native rate doesn't match the target. Channels are preserved.
    """
    samples, sr = sf.read(str(path), dtype="float32", always_2d=False)
    samples_arr: NDArray[np.floating] = samples
    if sr != target_sample_rate:
        samples_arr = _resample(samples_arr, sr, target_sample_rate)
    return AudioBuffer(samples=samples_arr, sample_rate=target_sample_rate)


def save_audio(path: Path, buffer: AudioBuffer) -> None:
    """Write `buffer` to `path` as a WAV (16-bit PCM) via libsndfile."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), buffer.samples, buffer.sample_rate, subtype="PCM_16")


def _resample(
    samples: NDArray[np.floating],
    src_rate: int,
    dst_rate: int,
) -> NDArray[np.floating]:
    """Resample `samples` from `src_rate` to `dst_rate` using polyphase filtering."""
    g: int = gcd(src_rate, dst_rate)
    up: int = dst_rate // g
    down: int = src_rate // g
    axis: int = 0 if samples.ndim > 1 else -1
    resampled = scipy_signal.resample_poly(samples, up, down, axis=axis)
    return resampled.astype(np.float32)
