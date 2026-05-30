"""Pipeline tests demonstrating the DI pattern.

These tests inject fakes — no real Demucs / ADTOF / Beat This! / LarsNet
installation is required. They pin the orchestration contract:

- with `substem_separator=None`, the output keeps the transcriber's coarse
  vocabulary (5-class)
- with a sub-stem separator + expander, hihat/cymbal classes are refined
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from sheetydrums.audio import AudioBuffer
from sheetydrums.interfaces import BeatGrid, DrumHit, DrumSubStems
from sheetydrums.pipeline import Pipeline
from sheetydrums.stages import (
    PassThroughExpander,
    StubQuantizer,
    StubSubStemExpander,
)


class _FakeSeparator:
    name = "fake-separator"

    def separate(self, mix: AudioBuffer) -> AudioBuffer:
        return mix


class _FakeTranscriber:
    name = "fake-transcriber"
    vocabulary = ("kick", "snare", "hihat", "tom", "cymbal")

    def __init__(self, hits: tuple[DrumHit, ...]) -> None:
        self._hits = hits

    def transcribe(self, drums: AudioBuffer) -> tuple[DrumHit, ...]:
        return self._hits


class _FakeBeatTracker:
    name = "fake-beats"

    def __init__(self, grid: BeatGrid) -> None:
        self._grid = grid

    def track(self, mix: AudioBuffer) -> BeatGrid:
        return self._grid


class _FakeSubStemSeparator:
    name = "fake-substem"

    def separate(self, drums: AudioBuffer) -> DrumSubStems:
        return DrumSubStems(
            kick=drums, snare=drums, hihat=drums, toms=drums, cymbals=drums,
        )


def _make_grid() -> BeatGrid:
    return BeatGrid(
        beats=(0.0, 0.5, 1.0, 1.5),
        downbeats=(True, False, False, False),
        tempo_bpm=120.0,
        time_signature=(4, 4),
    )


def _dummy_audio(tmp_path: Path) -> Path:
    """Create a placeholder file for the CLI's existence check (contents ignored)."""
    p = tmp_path / "dummy.mp3"
    p.write_bytes(b"")
    return p


def test_pipeline_without_substems_keeps_5_class_vocab(tmp_path):
    hits = (
        DrumHit(0.0, "kick", 0.9),
        DrumHit(0.5, "snare", 0.85),
        DrumHit(0.25, "hihat", 0.8),
        DrumHit(0.75, "cymbal", 0.7),
    )
    pipeline = Pipeline(
        separator=_FakeSeparator(),
        transcriber=_FakeTranscriber(hits),
        beat_tracker=_FakeBeatTracker(_make_grid()),
        quantizer=StubQuantizer(),
        class_expander=PassThroughExpander(),
        substem_separator=None,
        verbose=False,
    )

    result = pipeline.transcribe(_dummy_audio(tmp_path))

    instruments = {note.instrument for bar in result.bars for note in bar.notes}
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


def test_pipeline_with_substems_refines_to_7_class_vocab(tmp_path):
    hits = (
        DrumHit(0.0, "kick", 0.9),
        DrumHit(0.25, "hihat", 0.8),
        DrumHit(0.375, "hihat", 0.8),  # 16th-note offset so it lands in a different position
        DrumHit(0.75, "cymbal", 0.7),
    )
    pipeline = Pipeline(
        separator=_FakeSeparator(),
        transcriber=_FakeTranscriber(hits),
        beat_tracker=_FakeBeatTracker(_make_grid()),
        quantizer=StubQuantizer(),
        class_expander=StubSubStemExpander(),
        substem_separator=_FakeSubStemSeparator(),
        verbose=False,
    )

    result = pipeline.transcribe(_dummy_audio(tmp_path))

    instruments = {note.instrument for bar in result.bars for note in bar.notes}
    # The stub expander alternates: first hihat → closed, second hihat → open
    assert "hihat_closed" in instruments
    assert "hihat_open" in instruments
    # First cymbal → ride
    assert "ride" in instruments
    # No coarse classes remain
    assert "hihat" not in instruments
    assert "cymbal" not in instruments
