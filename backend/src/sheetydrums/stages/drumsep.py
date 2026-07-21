"""Drum sub-stem separation: MDX23C 6-stem DrumSep by aufr33/jarredou.

Wraps the `audio-separator` package (which itself wraps the KUIELab MDX-Net
architecture) loaded with the jarredou DrumSep checkpoint. Output is a
DrumSubStems with kick / snare / toms / hihat / ride / crash — the v1 survey's
"Cheuk et al. recipe" stem split, with native ride/crash separation so the
class expander doesn't have to heuristic that out.

Conforms to `interfaces.DrumSubStemSeparator`.

License: model weights are CC-BY-NC-SA 4.0 — same posture as our ADTOF
dependency. Non-commercial; revisit if sheetydrums ever ships for sale.

Weights cache: ~/.cache/sheetydrums/drumsep/MDX23C-DrumSep-aufr33-jarredou.ckpt
(~700 MB, downloaded on first call).
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
import torch
from numpy.typing import NDArray

from sheetydrums.audio import AudioBuffer
from sheetydrums.device import best_device, release_to_cpu
from sheetydrums.interfaces import DrumSubStems


_MODEL_FILENAME: str = "MDX23C-DrumSep-aufr33-jarredou.ckpt"
_CACHE_DIR: Path = Path.home() / ".cache" / "sheetydrums" / "drumsep"

# The MDX23C 6-stem DrumSep model emits these stem labels (lowercase).
# Audio-separator names output files using these when we set custom_output_names.
_STEM_LABELS: tuple[str, ...] = ("kick", "snare", "toms", "hh", "ride", "crash")


class DrumSepSeparator:
    """Splits a drums stem into 6 sub-stems via MDX23C DrumSep."""
    name: str = "drumsep-mdx23c-jarredou"

    def __init__(self, device: str | None = None) -> None:
        self._device: str = device if device is not None else best_device()
        self._separator: Any = None  # lazy

    def separate(self, drums: AudioBuffer) -> DrumSubStems:
        sep: Any = self._ensure_separator()

        # audio-separator's API is file-based. Round-trip through a temp dir.
        with tempfile.TemporaryDirectory(prefix="sheetydrums-drumsep-") as tmp:
            tmp_path: Path = Path(tmp)
            in_path: Path = tmp_path / "drums.wav"
            _write_wav(drums, in_path)

            # Setting Separator.output_dir alone is insufficient: the loaded
            # model_instance was constructed with a copy of the output_dir back
            # at load_model() time. Mirror the audio-separator library's own
            # internal redirect pattern (see Separator.process_with_chunking).
            sep.output_dir = str(tmp_path)
            if getattr(sep, "model_instance", None) is not None:
                sep.model_instance.output_dir = str(tmp_path)
            sep.separate(
                str(in_path),
                custom_output_names={s: s for s in _STEM_LABELS},
            )

            stems: dict[str, AudioBuffer] = {}
            for label in _STEM_LABELS:
                path: Path = tmp_path / f"{label}.wav"
                if not path.exists():
                    raise RuntimeError(
                        f"DrumSep didn't produce expected stem {label!r} at {path}. "
                        f"Existing temp files: {sorted(p.name for p in tmp_path.iterdir())}"
                    )
                stem_samples, file_sr = sf.read(str(path), dtype="float32", always_2d=False)
                stem_arr: NDArray[np.floating] = stem_samples
                stems[label] = AudioBuffer(samples=stem_arr, sample_rate=int(file_sr))

        # Release GPU/MPS allocations the same way the upstream Demucs wrapper does.
        # The Separator wraps the model in self._separator.model_instance; release_to_cpu
        # tolerates a None or attribute-less object so the call is safe regardless.
        model: Any = getattr(self._separator, "model_instance", None)
        release_to_cpu(model)

        return DrumSubStems(
            kick=stems["kick"],
            snare=stems["snare"],
            hihat=stems["hh"],
            toms=stems["toms"],
            ride=stems["ride"],
            crash=stems["crash"],
        )

    def _ensure_separator(self) -> Any:
        if self._separator is None:
            from audio_separator.separator import Separator

            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            sep: Any = Separator(
                model_file_dir=str(_CACHE_DIR),
                log_level=logging.WARNING,
            )
            sep.load_model(model_filename=_MODEL_FILENAME)

            # The Separator auto-picks the best device. On Apple Silicon that's
            # MPS, which we want for this heavy model. If a caller explicitly
            # passes device="cpu" (e.g. to debug or avoid post-Demucs MPS
            # pollution), honour it.
            if self._device == "cpu":
                sep.torch_device = torch.device("cpu")  # pyright: ignore[reportPrivateImportUsage]

            self._separator = sep
        return self._separator


def _write_wav(buf: AudioBuffer, path: Path) -> None:
    """Write an AudioBuffer to disk as a PCM WAV. Stereo if multi-channel."""
    samples: NDArray[np.floating] = buf.samples
    sf.write(str(path), samples, buf.sample_rate, subtype="PCM_16")
