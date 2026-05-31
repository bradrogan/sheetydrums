"""5-class drum transcription.

Wraps the ADTOF-pytorch port (https://github.com/xavriley/ADTOF-pytorch) of
the ADTOF Frame-RNN CRNN (Zehren et al., Signals 2023). Pretrained weights
are CC-BY-NC-SA 4.0 — non-commercial use only. Conforms to
`interfaces.DrumTranscriber`.

The packaged weights file is ~5 MB and ships inside the adtof_pytorch
package, so the first call has no network round-trip — just a one-time
model construction and weight load.
"""
from __future__ import annotations

from math import gcd
from typing import Any

import numpy as np
import torch
from adtof_pytorch import (
    calculate_n_bins,
    create_frame_rnn_model,
    get_default_weights_path,
    load_pytorch_weights,
)
from adtof_pytorch.audio import create_adtof_processor
from adtof_pytorch.post_processing import (
    FRAME_RNN_THRESHOLDS,
    NotePeakPickingProcessor,
)
from numpy.typing import NDArray
from scipy import signal as scipy_signal

from sheetydrums.audio import AudioBuffer
from sheetydrums.device import best_device, release_to_cpu
from sheetydrums.interfaces import DrumHit, TranscriberDrumClass


# ADTOF's model emits per-frame activations at five channels in this order
# (MIDI notes 35, 38, 47, 42, 49 = kick, snare, low-mid tom, closed hi-hat,
# crash cymbal). The mapping below is the single source of truth tying
# channel index to our TranscriberDrumClass.
_CLASS_AT_INDEX: tuple[TranscriberDrumClass, ...] = (
    "kick", "snare", "tom", "hihat", "cymbal",
)
_MODEL_SAMPLE_RATE = 44100
_MODEL_FPS = 100  # one model frame every 10 ms


class ADTOFTranscriber:
    """ADTOF Frame-RNN drum transcriber, 5-class output."""

    name: str = "adtof-pytorch"
    vocabulary: tuple[TranscriberDrumClass, ...] = _CLASS_AT_INDEX

    def __init__(self, device: str | None = None) -> None:
        self._device: str = device if device is not None else best_device()
        self._model: Any = None  # lazy
        self._processor: Any = None

    def transcribe(self, drums: AudioBuffer) -> tuple[DrumHit, ...]:
        self._ensure_loaded()

        # Mono float32 at 44.1 kHz
        samples: NDArray[np.floating] = drums.samples.astype(np.float32)
        if samples.ndim > 1:
            samples = samples.mean(axis=1).astype(np.float32)
        if drums.sample_rate != _MODEL_SAMPLE_RATE:
            g: int = gcd(drums.sample_rate, _MODEL_SAMPLE_RATE)
            samples = scipy_signal.resample_poly(
                samples,
                _MODEL_SAMPLE_RATE // g,
                drums.sample_rate // g,
            ).astype(np.float32)

        # Spectrogram via ADTOF's own audio pipeline so we match the model's
        # training-time preprocessing exactly.
        stft: NDArray[np.floating] = self._processor.compute_stft(samples)
        filtered: NDArray[np.floating] = self._processor.apply_filterbank(stft)
        # filtered: (n_bins, n_frames). Model wants (batch=1, n_frames, n_bins, channels=1).
        x_np: NDArray[np.floating] = np.expand_dims(
            np.expand_dims(filtered.T, axis=0), axis=-1,
        )
        x_tensor: torch.Tensor = torch.as_tensor(x_np).to(self._device)  # pyright: ignore[reportPrivateImportUsage]

        with torch.no_grad():
            pred_tensor: torch.Tensor = self._model(x_tensor)
        pred: NDArray[np.floating] = pred_tensor.cpu().numpy()  # shape: (1, n_frames, 5)

        # Peak-pick per class with ADTOF's per-class tuned thresholds.
        # TODO(thresholds): FRAME_RNN_THRESHOLDS comes from the model authors;
        # if a class fires too often / too rarely on real songs, override via
        # constructor arg (not yet exposed) and tune empirically.
        hits: list[DrumHit] = []
        n_frames: int = pred.shape[1]
        for cls_idx, cls_name in enumerate(_CLASS_AT_INDEX):
            activation: NDArray[np.floating] = pred[0, :, cls_idx]
            picker = NotePeakPickingProcessor(
                threshold=FRAME_RNN_THRESHOLDS[cls_idx],
                fps=_MODEL_FPS,
            )
            for time_sec, _midi_placeholder in picker.process(activation):
                frame_idx: int = min(int(round(time_sec * _MODEL_FPS)), n_frames - 1)
                confidence: float = float(activation[frame_idx])
                hits.append(DrumHit(time=time_sec, drum_class=cls_name, confidence=confidence))

        # Free ADTOF's GPU allocations before downstream stages run.
        release_to_cpu(self._model)
        return tuple(sorted(hits, key=lambda h: (h.time, h.drum_class)))

    def _ensure_loaded(self) -> None:
        if self._model is None:
            n_bins: int = calculate_n_bins()
            model: Any = create_frame_rnn_model(n_bins)
            model.eval()
            weights_path: str | None = get_default_weights_path()
            if weights_path is None:
                raise RuntimeError(
                    "ADTOF pretrained weights not found in adtof_pytorch package."
                )
            model = load_pytorch_weights(model, weights_path, strict=False)
            model.to(self._device)
            self._model = model
        if self._processor is None:
            self._processor = create_adtof_processor()


