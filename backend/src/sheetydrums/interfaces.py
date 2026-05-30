"""Protocols and data types for the drum-transcription pipeline.

Stages are duck-typed against the Protocols below. The Pipeline accepts any
object that conforms to these shapes — production wrappers around real models
in `stages/`, in-memory fakes in tests. Concrete model libraries (Demucs,
ADTOF, LarsNet, Beat This!) appear only in stage implementation files; this
module imports none of them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sheetydrums.audio import AudioBuffer


# === Data types flowing between stages ===

@dataclass(frozen=True)
class DrumHit:
    """One detected drum strike, expressed in absolute time within the audio."""
    time: float
    drum_class: str
    confidence: float


@dataclass(frozen=True)
class BeatGrid:
    """Output of the beat-tracking stage."""
    beats: tuple[float, ...]
    downbeats: tuple[bool, ...]
    tempo_bpm: float
    time_signature: tuple[int, int]


@dataclass(frozen=True)
class DrumSubStems:
    """Per-drum-class audio sub-stems produced by a sub-stem separator (e.g. LarsNet)."""
    kick: AudioBuffer
    snare: AudioBuffer
    hihat: AudioBuffer
    toms: AudioBuffer
    cymbals: AudioBuffer


@dataclass(frozen=True)
class Note:
    """A single note in the final transcription, ready for schema emission."""
    instrument: str
    position: str
    duration: str
    confidence: float | None = None
    sustain_until: str | None = None
    tuplet: dict | None = None


@dataclass(frozen=True)
class Bar:
    """A bar containing zero or more notes."""
    index: int
    start_seconds: float
    notes: tuple[Note, ...]


@dataclass(frozen=True)
class TranscriptionResult:
    """Full pipeline output. CLI / API consumers serialize this to the schema dict."""
    audio_file: str
    duration_seconds: float
    tempo_bpm: float
    time_signature: tuple[int, int]
    bars: tuple[Bar, ...]


# === Stage protocols ===

@runtime_checkable
class MixSeparator(Protocol):
    """Separates a full music mix into its drum stem.

    Implementations may internally produce other stems (bass, vocals, other); only
    the drums stem flows downstream. The `name` attribute is used by the
    orchestrator for logging and debug-file naming.
    """
    name: str

    def separate(self, mix: AudioBuffer) -> AudioBuffer: ...


@runtime_checkable
class DrumTranscriber(Protocol):
    """Detects drum onsets in a drums stem and assigns a coarse class to each.

    The output vocabulary is implementation-defined. `vocabulary` reports the
    classes a given transcriber emits — downstream class-expansion stages should
    consult this rather than hard-coding assumptions.
    """
    name: str
    vocabulary: tuple[str, ...]

    def transcribe(self, drums: AudioBuffer) -> tuple[DrumHit, ...]: ...


@runtime_checkable
class DrumSubStemSeparator(Protocol):
    """Separates a drums stem into per-drum-class sub-stems."""
    name: str

    def separate(self, drums: AudioBuffer) -> DrumSubStems: ...


@runtime_checkable
class ClassExpander(Protocol):
    """Refines coarse drum-class labels using sub-stem features.

    When no sub-stems are available, an expander should either return hits
    unchanged (PassThroughExpander) or refuse — never silently mis-label.
    """
    name: str

    def expand(
        self,
        hits: tuple[DrumHit, ...],
        substems: DrumSubStems | None,
    ) -> tuple[DrumHit, ...]: ...


@runtime_checkable
class BeatTracker(Protocol):
    """Detects beats and downbeats and infers tempo + time signature."""
    name: str

    def track(self, mix: AudioBuffer) -> BeatGrid: ...


@runtime_checkable
class Quantizer(Protocol):
    """Groups drum hits into bars with quantized positions."""
    name: str

    def quantize(
        self,
        hits: tuple[DrumHit, ...],
        grid: BeatGrid,
    ) -> tuple[Bar, ...]: ...
