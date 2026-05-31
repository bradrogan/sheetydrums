"""Pipeline tests demonstrating the DI pattern.

These tests inject fakes — no real Demucs / ADTOF / Beat This! installation
required. They pin the orchestration contract: with `substem_branch=None`
(the v1 default), the expander stage is skipped and the quantizer collapses
coarse classes to schema-valid defaults.

When v2 adds a real sub-stem branch, an additional test should be added that
injects a fake SubStemBranch and asserts hihat/cymbal classes are refined.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

from sheetydrums.audio import AudioBuffer
from sheetydrums.interfaces import (
    Beat,
    BeatGrid,
    DrumHit,
    TimeSignature,
    TranscriberDrumClass,
)
from sheetydrums.pipeline import Pipeline
from sheetydrums.stages import StubQuantizer


class _FakeSeparator:
    name: str = "fake-separator"

    def separate(self, mix: AudioBuffer) -> AudioBuffer:
        return mix


class _FakeTranscriber:
    name: str = "fake-transcriber"
    vocabulary: tuple[TranscriberDrumClass, ...] = ("kick", "snare", "hihat", "tom", "cymbal")

    def __init__(self, hits: tuple[DrumHit, ...]) -> None:
        self._hits: tuple[DrumHit, ...] = hits

    def transcribe(self, drums: AudioBuffer) -> tuple[DrumHit, ...]:
        _ = drums
        return self._hits


class _FakeBeatTracker:
    name: str = "fake-beats"

    def __init__(self, grid: BeatGrid) -> None:
        self._grid: BeatGrid = grid

    def track(self, mix: AudioBuffer) -> BeatGrid:
        _ = mix
        return self._grid


def _make_grid() -> BeatGrid:
    return BeatGrid(
        beats=(
            Beat(time=0.0, is_downbeat=True),
            Beat(time=0.5, is_downbeat=False),
            Beat(time=1.0, is_downbeat=False),
            Beat(time=1.5, is_downbeat=False),
        ),
        tempo_bpm=120.0,
        time_signature=TimeSignature(numerator=4, denominator=4),
    )


def _dummy_audio(tmp_path: Path) -> Path:
    """Write a tiny silent WAV so the real load_audio can decode it.

    Contents are ignored — the tests use _FakeSeparator/_FakeTranscriber which
    don't inspect the audio. We just need the file to exist and be valid.
    """
    p: Path = tmp_path / "dummy.wav"
    sf.write(str(p), np.zeros(44100, dtype=np.float32), 44100)
    return p


def test_pipeline_collapses_coarse_classes_to_schema_defaults(tmp_path: Path) -> None:
    """The v1 path: no sub-stem branch, quantizer collapses to schema defaults."""
    hits: tuple[DrumHit, ...] = (
        DrumHit(0.0, "kick", 0.9),
        DrumHit(0.5, "snare", 0.85),
        DrumHit(0.25, "hihat", 0.8),
        DrumHit(0.75, "cymbal", 0.7),
        DrumHit(1.25, "tom", 0.6),
    )
    pipeline: Pipeline = Pipeline(
        separator=_FakeSeparator(),
        transcriber=_FakeTranscriber(hits),
        beat_tracker=_FakeBeatTracker(_make_grid()),
        quantizer=StubQuantizer(),
        substem_branch=None,
        verbose=False,
    )

    result = pipeline.transcribe(_dummy_audio(tmp_path))

    instruments: set[str] = {note.instrument for bar in result.bars for note in bar.notes}
    # Coarse classes collapse to schema-valid defaults at the emit boundary:
    #   hihat → hihat_closed, cymbal → ride, tom → tom_mid
    assert "kick" in instruments
    assert "snare" in instruments
    assert "hihat_closed" in instruments
    assert "ride" in instruments
    assert "tom_mid" in instruments
    # The coarse labels never leak into output
    assert "hihat" not in instruments
    assert "cymbal" not in instruments
    assert "tom" not in instruments
