"""Mix-level source separation.

Wraps Facebook's Demucs (htdemucs_ft variant) to isolate the drums stem from
a music mix. Conforms to `interfaces.MixSeparator`.

Demucs lazily downloads its ~300 MB weights into the torch hub cache on
first use; subsequent runs load from that cache. The model class loads the
weights on first `separate()` call, not at construction — so Pipeline
construction stays cheap even with the real model wired in.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import torch
from demucs.apply import apply_model
from demucs.pretrained import get_model
from numpy.typing import NDArray

from sheetydrums.audio import AudioBuffer
from sheetydrums.device import best_device, release_to_cpu


_MODEL_NAME = "htdemucs_ft"
_DRUMS_LABEL = "drums"


class DemucsSeparator:
    """Demucs htdemucs_ft mix separator. Returns the drums stem."""
    name: str = f"demucs-{_MODEL_NAME}"

    def __init__(self, device: str | None = None, progress: bool = False) -> None:
        self._device: str = device if device is not None else best_device()
        self._progress: bool = progress
        self._model: Any = None  # lazy

    def separate(self, mix: AudioBuffer) -> AudioBuffer:
        model: Any = self._ensure_model()

        mix_tensor: torch.Tensor = _audio_to_demucs_tensor(mix).to(self._device)
        with torch.no_grad():
            sources: torch.Tensor = apply_model(
                model,
                mix_tensor,
                device=self._device,
                progress=self._progress,
                split=True,
            )

        # sources shape: (batch=1, n_sources, channels, samples)
        drums_idx: int = model.sources.index(_DRUMS_LABEL)
        drums_tensor: torch.Tensor = sources[0, drums_idx]  # (channels, samples)
        drums_samples: NDArray[np.floating] = drums_tensor.cpu().numpy().T  # (samples, channels)
        result = AudioBuffer(samples=drums_samples, sample_rate=mix.sample_rate)

        # Free Demucs's GPU allocations so downstream stages have room to run.
        # See sheetydrums/device.py for why empty_cache() alone is insufficient.
        release_to_cpu(self._model)
        return result

    def _ensure_model(self) -> Any:
        if self._model is None:
            model = get_model(_MODEL_NAME)
            model.to(self._device)
            model.eval()
            self._model = model
        return self._model


def _audio_to_demucs_tensor(audio: AudioBuffer) -> torch.Tensor:
    """Convert an AudioBuffer to Demucs's expected shape: (batch=1, channels=2, samples)."""
    samples: NDArray[np.floating] = audio.samples.astype(np.float32)
    if samples.ndim == 1:
        channels_first: NDArray[np.floating] = np.stack([samples, samples], axis=0)
    else:
        channels_first = samples.T  # (channels, samples)
        if channels_first.shape[0] == 1:
            # Mono shaped as (samples, 1) — duplicate to stereo
            channels_first = np.concatenate([channels_first, channels_first], axis=0)
    # torch's type stubs don't mark this as public even though it is.
    return torch.as_tensor(channels_first).unsqueeze(0)  # pyright: ignore[reportPrivateImportUsage]


