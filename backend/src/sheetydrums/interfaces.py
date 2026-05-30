"""Protocols and data types for the drum-transcription pipeline.

Stages are duck-typed against the Protocols below. The Pipeline accepts any
object that conforms to these shapes — production wrappers around real models
in `stages/`, in-memory fakes in tests. Concrete model libraries (Demucs,
ADTOF, LarsNet, Beat This!) appear only in stage implementation files; this
module imports none of them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict, runtime_checkable

from sheetydrums.audio import AudioBuffer


# === Drum vocabulary types ===

# The 5 classes a coarse drum transcriber (ADTOF) emits.
TranscriberDrumClass = Literal["kick", "snare", "hihat", "tom", "cymbal"]

# The vocabulary the schema accepts on disk. v1 transcriber emits 7 of these
# (no hihat_chick, no tom_high/tom_low) — see CLAUDE.md scope and docs/v2-backlog.md.
SchemaDrumClass = Literal[
    "kick", "snare",
    "hihat_closed", "hihat_open", "hihat_chick",
    "ride", "crash",
    "tom_high", "tom_mid", "tom_low",
]

# Union: a DrumHit's drum_class is one of these at any point in the pipeline.
# Pre-expansion hits hold TranscriberDrumClass values; post-expansion hits
# typically hold SchemaDrumClass values; the quantizer enforces the schema
# vocabulary at the emit boundary via `_to_schema_class`.
DrumClass = TranscriberDrumClass | SchemaDrumClass

# Written note value (durations only — tuplet ratios are conveyed separately).
Duration = Literal["1", "1/2", "1/4", "1/8", "1/16", "1/32"]


class Tuplet(TypedDict):
    """Shape of the `tuplet` field on a Note in the emitted schema."""
    actual: int
    normal: int
    group: str


# === Data types flowing between stages ===

@dataclass(frozen=True)
class DrumHit:
    """One detected drum strike, expressed in absolute time within the audio."""
    time: float
    drum_class: DrumClass
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
    instrument: SchemaDrumClass
    position: str
    duration: Duration
    confidence: float | None = None
    sustain_until: str | None = None
    tuplet: Tuplet | None = None


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
    """Separates a full music mix into its drum stem."""
    name: str

    def separate(self, mix: AudioBuffer) -> AudioBuffer: ...


@runtime_checkable
class DrumTranscriber(Protocol):
    """Detects drum onsets in a drums stem and assigns a coarse class to each."""
    name: str
    vocabulary: tuple[TranscriberDrumClass, ...]

    def transcribe(self, drums: AudioBuffer) -> tuple[DrumHit, ...]: ...


@runtime_checkable
class DrumSubStemSeparator(Protocol):
    """Separates a drums stem into per-drum-class sub-stems."""
    name: str

    def separate(self, drums: AudioBuffer) -> DrumSubStems: ...


@runtime_checkable
class ClassExpander(Protocol):
    """Refines coarse drum-class labels using sub-stem features."""
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
