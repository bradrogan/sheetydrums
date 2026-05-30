"""Per-stage artifact dumping for `--debug-dir`."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sheetydrums.audio import AudioBuffer


class DebugSink:
    """Optionally writes each stage's intermediate output to a directory.

    Numbered prefixes (01-, 02-, ...) preserve pipeline order in the dump,
    making the directory listing a natural narrative of the run. When the
    sink is constructed with `dir=None`, every method is a no-op — callers
    don't have to check.
    """

    def __init__(self, dir: Path | None) -> None:
        self._dir: Path | None = dir
        if self._dir is not None:
            self._dir.mkdir(parents=True, exist_ok=True)
        self._step: int = 0

    @property
    def enabled(self) -> bool:
        return self._dir is not None

    def write_json(self, stage_name: str, data: Any) -> None:
        if self._dir is None:
            return
        path: Path = self._next_path(stage_name, "json")
        path.write_text(json.dumps(data, indent=2, default=str) + "\n")

    def write_text(self, stage_name: str, text: str) -> None:
        if self._dir is None:
            return
        path: Path = self._next_path(stage_name, "txt")
        path.write_text(text + "\n")

    def write_audio_placeholder(self, stage_name: str, audio: AudioBuffer) -> None:
        """Stub: until soundfile is wired in, dump a one-line description instead of audio."""
        self.write_text(
            stage_name,
            f"<placeholder for audio: {audio.duration_seconds:.2f}s @ {audio.sample_rate} Hz>",
        )

    def _next_path(self, stage_name: str, ext: str) -> Path:
        self._step += 1
        assert self._dir is not None  # guarded by the early returns above
        return self._dir / f"{self._step:02d}-{stage_name}.{ext}"
