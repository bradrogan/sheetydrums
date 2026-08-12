"""Stage cache + re-run DAG. Pure disk I/O + DI fakes — no models."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf

from sheetydrums.audio import AudioBuffer
from sheetydrums.cache import StageCache
from sheetydrums.config import CLIConfig
from sheetydrums.factory import stage_keys
from sheetydrums.interfaces import Beat, BeatGrid, DrumHit, DrumSubStems, TimeSignature
from sheetydrums.params import (
    ExpanderParams,
    PipelineParams,
    SeparationParams,
    TranscriptionParams,
)
from sheetydrums.pipeline import Pipeline
from sheetydrums.stages import StubQuantizer


def _audio(v: float = 0.25, n: int = 2000) -> AudioBuffer:
    return AudioBuffer(samples=np.full(n, v, dtype=np.float32), sample_rate=44100)


def _hits() -> tuple[DrumHit, ...]:
    return (
        DrumHit(time=0.1, drum_class="kick", confidence=0.9),
        DrumHit(time=0.5, drum_class="snare", confidence=0.8),
    )


def _grid() -> BeatGrid:
    return BeatGrid(
        beats=(Beat(time=0.0, is_downbeat=True), Beat(time=0.5, is_downbeat=False)),
        tempo_bpm=120.0,
        time_signature=TimeSignature(numerator=4, denominator=4),
    )


def _substems() -> DrumSubStems:
    fields = ("kick", "snare", "hihat", "toms", "ride", "crash")
    return DrumSubStems(**{s: _audio(0.01 * (i + 1)) for i, s in enumerate(fields)})


class Counter:
    """A zero-arg compute() that records how many times it actually ran."""
    def __init__(self, value: Any) -> None:
        self.value = value
        self.n = 0

    def __call__(self) -> Any:
        self.n += 1
        return self.value


# === StageCache unit behaviour ==========================================

def test_audio_hit_miss_and_roundtrip(tmp_path: Path) -> None:
    c = Counter(_audio(0.25))
    cache = StageCache(tmp_path, {"drums": "k1"})
    a1 = cache.audio("drums", c)
    a2 = cache.audio("drums", c)
    assert c.n == 1  # second call is a cache hit — compute not re-run
    assert a2.sample_rate == 44100 and a2.samples.shape == (2000,)
    assert np.allclose(a2.samples, 0.25, atol=1e-3)  # PCM_16 round-trip
    _ = a1
    # A changed key invalidates the entry.
    StageCache(tmp_path, {"drums": "k2"}).audio("drums", c)
    assert c.n == 2


def test_disabled_cache_always_computes_and_writes_nothing(tmp_path: Path) -> None:
    c = Counter(_audio())
    cache = StageCache(None, {})
    cache.audio("drums", c)
    cache.audio("drums", c)
    assert c.n == 2
    assert list(tmp_path.iterdir()) == []


def test_missing_artifact_forces_recompute(tmp_path: Path) -> None:
    c = Counter(_audio())
    cache = StageCache(tmp_path, {"drums": "k1"})
    cache.audio("drums", c)
    assert c.n == 1
    (tmp_path / "drums.wav").unlink()  # key stays but artifact vanished
    cache.audio("drums", c)
    assert c.n == 2  # torn cache → recompute, not a stale/empty read


def test_hits_roundtrip(tmp_path: Path) -> None:
    c = Counter(_hits())
    cache = StageCache(tmp_path, {"hits_raw": "k"})
    cache.hits("hits_raw", c)
    got = cache.hits("hits_raw", c)
    assert c.n == 1 and got == _hits()


def test_grid_roundtrip(tmp_path: Path) -> None:
    c = Counter(_grid())
    cache = StageCache(tmp_path, {"grid_raw": "k"})
    cache.grid("grid_raw", c)
    got = cache.grid("grid_raw", c)
    assert c.n == 1 and got == _grid()


def test_substems_roundtrip(tmp_path: Path) -> None:
    c = Counter(_substems())
    cache = StageCache(tmp_path, {"substems": "k"})
    cache.substems("substems", c)
    got = cache.substems("substems", c)
    assert c.n == 1
    assert got.kick.samples.shape == (2000,)
    assert np.allclose(got.crash.samples, 0.06, atol=1e-3)


# === re-run DAG (key cascade) ===========================================

def test_stage_keys_cascade() -> None:
    cfg = CLIConfig(use_drumsep=True)
    base = stage_keys(cfg, PipelineParams())

    # An expander tweak changes NONE of the cached keys — the whole point:
    # Demucs/DrumSep/ADTOF/Beat-This all cache-hit, only the cheap expander re-runs.
    exp = stage_keys(cfg, PipelineParams(expander=ExpanderParams(hihat_unimodal_open_threshold=0.6)))
    assert exp == base

    # Transcription params → only hits_raw changes.
    tr = stage_keys(cfg, PipelineParams(transcription=TranscriptionParams(thresholds=(0.1,) * 5)))
    assert tr["hits_raw"] != base["hits_raw"]
    assert (tr["drums"], tr["substems"], tr["grid_raw"]) == (base["drums"], base["substems"], base["grid_raw"])

    # Separation params → drums + everything derived from it change; grid does not.
    sp = stage_keys(cfg, PipelineParams(separation=SeparationParams(shifts=2)))
    assert sp["drums"] != base["drums"]
    assert sp["substems"] != base["substems"]
    assert sp["hits_raw"] != base["hits_raw"]
    assert sp["grid_raw"] == base["grid_raw"]

    # Toggling DrumSep invalidates the sub-stems.
    off = stage_keys(CLIConfig(use_drumsep=False), PipelineParams())
    assert off["substems"] != base["substems"]


# === end-to-end: a second run skips the expensive stages ================

class _CountingSep:
    name = "sep"
    def __init__(self) -> None:
        self.calls = 0
    def separate(self, mix: AudioBuffer) -> AudioBuffer:
        self.calls += 1
        return mix


class _CountingTranscriber:
    name = "tr"
    vocabulary = ("kick", "snare", "hihat", "tom", "cymbal")
    def __init__(self, hits: tuple[DrumHit, ...]) -> None:
        self.hits = hits
        self.calls = 0
    def transcribe(self, drums: AudioBuffer) -> tuple[DrumHit, ...]:
        self.calls += 1
        return self.hits


class _CountingBeats:
    name = "beats"
    def __init__(self, grid: BeatGrid) -> None:
        self.grid = grid
        self.calls = 0
    def track(self, mix: AudioBuffer) -> BeatGrid:
        self.calls += 1
        return self.grid


def test_pipeline_reuses_cache_across_runs(tmp_path: Path) -> None:
    wav = tmp_path / "mix.wav"
    sf.write(str(wav), np.zeros(44100, dtype=np.float32), 44100)
    sep, tr, beats = _CountingSep(), _CountingTranscriber(_hits()), _CountingBeats(_grid())
    cache = StageCache(tmp_path / "stages", stage_keys(CLIConfig(use_drumsep=False), PipelineParams()))

    def run() -> Any:
        return Pipeline(
            separator=sep, transcriber=tr, beat_tracker=beats,
            quantizer=StubQuantizer(), substem_branch=None, stage_cache=cache,
        ).transcribe(wav)

    r1 = run()
    r2 = run()
    # Every expensive stage ran on the first pass and was served from cache on the second.
    assert (sep.calls, tr.calls, beats.calls) == (1, 1, 1)
    assert len(r2.bars) == len(r1.bars)
