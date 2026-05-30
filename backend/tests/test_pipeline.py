"""Pipeline tests demonstrating the DI pattern.

These tests inject fakes — no real Demucs / ADTOF / Beat This! / LarsNet
installation is required. They pin the orchestration contract:

- with `substem_branch=None`, the expander stage is skipped and the
  quantizer collapses coarse classes to schema-valid defaults
- with a `SubStemBranch`, the expander runs and refines hihat/cymbal classes
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
    DrumSubStems,
    SubStemBranch,
    TimeSignature,
    TranscriberDrumClass,
)
from sheetydrums.pipeline import Pipeline
from sheetydrums.stages import StubQuantizer, StubSubStemExpander


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


class _FakeSubStemSeparator:
    name: str = "fake-substem"

    def separate(self, drums: AudioBuffer) -> DrumSubStems:
        return DrumSubStems(
            kick=drums, snare=drums, hihat=drums, toms=drums, cymbals=drums,
        )


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


def test_pipeline_without_substem_branch_keeps_5_class_vocab(tmp_path: Path) -> None:
    hits: tuple[DrumHit, ...] = (
        DrumHit(0.0, "kick", 0.9),
        DrumHit(0.5, "snare", 0.85),
        DrumHit(0.25, "hihat", 0.8),
        DrumHit(0.75, "cymbal", 0.7),
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
    # Without expansion, the quantizer collapses coarse classes to schema-valid
    # defaults: hihat → hihat_closed, cymbal → ride. The *open* and *crash*
    # refinements only appear when the SubStemExpander runs.
    assert "hihat_closed" in instruments
    assert "ride" in instruments
    assert "hihat_open" not in instruments
    assert "crash" not in instruments
    # And the raw coarse labels never leak into output
    assert "hihat" not in instruments
    assert "cymbal" not in instruments


def test_pipeline_with_substem_branch_refines_to_7_class_vocab(tmp_path: Path) -> None:
    hits: tuple[DrumHit, ...] = (
        DrumHit(0.0, "kick", 0.9),
        DrumHit(0.25, "hihat", 0.8),
        DrumHit(0.375, "hihat", 0.8),  # 16th-note offset so it lands in a different position
        DrumHit(0.75, "cymbal", 0.7),
    )
    pipeline: Pipeline = Pipeline(
        separator=_FakeSeparator(),
        transcriber=_FakeTranscriber(hits),
        beat_tracker=_FakeBeatTracker(_make_grid()),
        quantizer=StubQuantizer(),
        substem_branch=SubStemBranch(
            separator=_FakeSubStemSeparator(),
            expander=StubSubStemExpander(),
        ),
        verbose=False,
    )

    result = pipeline.transcribe(_dummy_audio(tmp_path))

    instruments: set[str] = {note.instrument for bar in result.bars for note in bar.notes}
    # The stub expander alternates: first hihat → closed, second hihat → open
    assert "hihat_closed" in instruments
    assert "hihat_open" in instruments
    # First cymbal → ride
    assert "ride" in instruments
    # No coarse classes remain
    assert "hihat" not in instruments
    assert "cymbal" not in instruments
