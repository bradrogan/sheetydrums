"""Per-project stage-output cache + re-run DAG.

Persists each expensive pipeline stage's output keyed by a hash of (its params +
its inputs' keys), so a parameter change re-runs only the affected stages instead
of the whole pipeline — a hi-hat/expander tweak becomes seconds instead of
minutes because Demucs + DrumSep + ADTOF + Beat This! all cache-hit.

Constructed with `dir=None` (the default) the cache is disabled: every stage
recomputes and nothing is written — i.e. today's behaviour. The factory computes
the per-stage keys from `PipelineParams` (it knows them) and hands them here; the
pipeline just calls the typed helpers around each stage.

Cache-key chain (built in `factory.stage_keys`) — the cache dir is per-video, so
the mix identity is implicit:
    drums     = H(separation params)
    substems  = H(drums, drumsep on/off)
    hits_raw  = H(drums, transcription params)
    grid_raw  = H(beats params)              # Beat This! output, pre phase-correct

The cheap stages (expander, downbeat phase-correction, quantize) always recompute
from the cached inputs — no need to persist them.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import soundfile as sf

from sheetydrums.audio import AudioBuffer, save_audio
from sheetydrums.interfaces import Beat, BeatGrid, DrumHit, DrumSubStems, TimeSignature

# Bump to invalidate every cached artifact when a serialization format changes.
_CACHE_VERSION = "v1"


def stage_key(*parts: Any) -> str:
    """Stable short hash of params + parent keys. Dataclasses are hashed via
    their repr (`default=str`), which is deterministic for frozen param groups."""
    blob = json.dumps([_CACHE_VERSION, *parts], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class StageCache:
    """Loads a stage's cached output when its key matches, else computes + stores
    it. Disabled (dir=None) → always compute, never write."""

    def __init__(self, dir: Path | None, keys: dict[str, str]) -> None:
        self._dir: Path | None = dir
        self._keys: dict[str, str] = keys

    @property
    def enabled(self) -> bool:
        return self._dir is not None

    # --- key bookkeeping --------------------------------------------------
    def _fresh(self, name: str, *artifacts: Path) -> bool:
        """True if the stored key for `name` matches the expected key and all
        artifact files exist."""
        if self._dir is None:
            return False
        keyfile = self._dir / f"{name}.key"
        if not keyfile.exists() or not all(a.exists() for a in artifacts):
            return False
        return keyfile.read_text().strip() == self._keys.get(name, "")

    def _commit(self, name: str) -> None:
        # Write the key AFTER the artifact(s) so a crash mid-write never leaves a
        # valid-looking key pointing at a partial artifact.
        assert self._dir is not None
        (self._dir / f"{name}.key").write_text(self._keys[name])

    # --- typed helpers ----------------------------------------------------
    def audio(self, name: str, compute: Callable[[], AudioBuffer]) -> AudioBuffer:
        if self._dir is None:
            return compute()
        path = self._dir / f"{name}.wav"
        if self._fresh(name, path):
            return _read_audio(path)
        val = compute()
        save_audio(path, val)
        self._commit(name)
        return val

    def substems(self, name: str, compute: Callable[[], DrumSubStems]) -> DrumSubStems:
        if self._dir is None:
            return compute()
        paths = {s: self._dir / f"{name}_{s}.wav" for s in _SUBSTEM_FIELDS}
        if self._fresh(name, *paths.values()):
            return DrumSubStems(**{s: _read_audio(p) for s, p in paths.items()})
        val = compute()
        self._dir.mkdir(parents=True, exist_ok=True)
        for s, p in paths.items():
            save_audio(p, getattr(val, s))
        self._commit(name)
        return val

    def hits(self, name: str, compute: Callable[[], tuple[DrumHit, ...]]) -> tuple[DrumHit, ...]:
        if self._dir is None:
            return compute()
        path = self._dir / f"{name}.json"
        if self._fresh(name, path):
            return _read_hits(path)
        val = compute()
        _write_hits(path, val)
        self._commit(name)
        return val

    def grid(self, name: str, compute: Callable[[], BeatGrid]) -> BeatGrid:
        if self._dir is None:
            return compute()
        path = self._dir / f"{name}.json"
        if self._fresh(name, path):
            return _read_grid(path)
        val = compute()
        _write_grid(path, val)
        self._commit(name)
        return val


_SUBSTEM_FIELDS = ("kick", "snare", "hihat", "toms", "ride", "crash")


# === serialization =======================================================

def _read_audio(path: Path) -> AudioBuffer:
    """Read a cached WAV at its native sample rate (the pipeline runs at 44.1 kHz
    end-to-end, so no resample is needed)."""
    samples, sr = sf.read(str(path), dtype="float32", always_2d=False)
    return AudioBuffer(samples=samples, sample_rate=int(sr))


def _write_hits(path: Path, hits: tuple[DrumHit, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        [{"time": h.time, "drum_class": h.drum_class, "confidence": h.confidence} for h in hits]
    ))


def _read_hits(path: Path) -> tuple[DrumHit, ...]:
    return tuple(
        DrumHit(time=d["time"], drum_class=d["drum_class"], confidence=d["confidence"])
        for d in json.loads(path.read_text())
    )


def _write_grid(path: Path, grid: BeatGrid) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "tempo_bpm": grid.tempo_bpm,
        "time_signature": [grid.time_signature.numerator, grid.time_signature.denominator],
        "beats": [[b.time, b.is_downbeat] for b in grid.beats],
    }))


def _read_grid(path: Path) -> BeatGrid:
    d = json.loads(path.read_text())
    num, den = d["time_signature"]
    return BeatGrid(
        beats=tuple(Beat(time=t, is_downbeat=bool(db)) for t, db in d["beats"]),
        tempo_bpm=d["tempo_bpm"],
        time_signature=TimeSignature(numerator=num, denominator=den),
    )
