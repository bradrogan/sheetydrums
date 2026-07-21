"""Pipeline orchestrator.

Imports nothing from any audio/ML library. Stage implementations are injected
via the constructor — concrete model wrappers in `stages/` for production,
fakes in tests. To skip the sub-stem branch (LarsNet + class expander),
pass `substem_branch=None`.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from sheetydrums.audio import AudioBuffer, load_audio
from sheetydrums.debug import DebugSink
from sheetydrums.phase_correct import correct_downbeat_phase
from sheetydrums.interfaces import (
    Bar,
    BeatGrid,
    BeatTracker,
    DrumClass,
    DrumHit,
    DrumTranscriber,
    MixSeparator,
    Quantizer,
    SubStemBranch,
    TranscriptionResult,
)


class Pipeline:
    """Runs the drum-transcription stages in dependency order."""

    def __init__(
        self,
        *,
        separator: MixSeparator,
        transcriber: DrumTranscriber,
        beat_tracker: BeatTracker,
        quantizer: Quantizer,
        substem_branch: SubStemBranch | None = None,
        debug_sink: DebugSink | None = None,
        verbose: bool = True,
        on_progress: Callable[[str], None] | None = None,
    ) -> None:
        self._separator: MixSeparator = separator
        self._transcriber: DrumTranscriber = transcriber
        self._beat_tracker: BeatTracker = beat_tracker
        self._quantizer: Quantizer = quantizer
        self._substem_branch: SubStemBranch | None = substem_branch
        self._debug: DebugSink = debug_sink if debug_sink is not None else DebugSink(None)
        self._verbose: bool = verbose
        self._on_progress: Callable[[str], None] | None = on_progress

    def transcribe(
        self,
        audio_path: Path,
        drum_stem_path: Path | None = None,
    ) -> TranscriptionResult:
        mix: AudioBuffer = load_audio(audio_path)
        self._log(f"loaded {audio_path.name}: {mix.duration_seconds:.2f}s @ {mix.sample_rate} Hz")
        self._debug.write_audio_placeholder("input-mix", mix)

        drums: AudioBuffer = self._separator.separate(mix)
        self._log(f"[separator:{self._separator.name}] drum stem: {drums.duration_seconds:.2f}s")
        self._debug.write_audio_placeholder(f"separator-{self._separator.name}", drums)

        # Persist the isolated drum stem so the frontend can offer it as a
        # drums-only playback source. Written before the (slower) downstream
        # stages so it's available even if a later stage fails.
        if drum_stem_path is not None:
            from sheetydrums.audio import save_audio

            save_audio(drum_stem_path, drums)
            self._log(f"[stem] wrote drum stem → {drum_stem_path.name}")

        hits: tuple[DrumHit, ...] = self._transcriber.transcribe(drums)
        self._log(
            f"[transcriber:{self._transcriber.name}] {len(hits)} hits, "
            f"vocab={self._transcriber.vocabulary}"
        )
        self._debug.write_json(
            f"transcriber-{self._transcriber.name}",
            [{"time": h.time, "class": h.drum_class, "confidence": h.confidence} for h in hits],
        )

        if self._substem_branch is not None:
            substems = self._substem_branch.separator.separate(drums)
            self._log(f"[substem:{self._substem_branch.separator.name}] 6 sub-stems extracted")
            self._debug.write_text(
                f"substem-{self._substem_branch.separator.name}",
                "kick + snare + hihat + toms + ride + crash sub-stems extracted",
            )

            hits = self._substem_branch.expander.expand(hits, substems)
            expanded_vocab: list[DrumClass] = sorted({h.drum_class for h in hits})
            self._log(
                f"[expander:{self._substem_branch.expander.name}] {len(hits)} hits, "
                f"vocab={tuple(expanded_vocab)}"
            )
            self._debug.write_json(
                f"expander-{self._substem_branch.expander.name}",
                [{"time": h.time, "class": h.drum_class, "confidence": h.confidence} for h in hits],
            )

        grid: BeatGrid = self._beat_tracker.track(mix)
        # Downbeat-phase correction: Beat This! can place downbeats off-phase
        # in songs with sparse intros or strong backbeats. Use kick locations
        # to rotate the downbeat labels onto the beats kicks actually land on.
        # No-op when there aren't enough kicks or the kick distribution is
        # too uniform to be confident.
        n_downbeats_before: int = sum(1 for b in grid.beats if b.is_downbeat)
        grid = correct_downbeat_phase(hits, grid)
        n_downbeats_after: int = sum(1 for b in grid.beats if b.is_downbeat)
        self._log(
            f"[beats:{self._beat_tracker.name}] {grid.tempo_bpm:.1f} BPM, "
            f"{grid.time_signature.numerator}/{grid.time_signature.denominator}, "
            f"{len(grid.beats)} beats, downbeats after phase correction: "
            f"{n_downbeats_after} (was {n_downbeats_before})"
        )
        self._debug.write_json(
            f"beats-{self._beat_tracker.name}",
            {
                "tempo_bpm": grid.tempo_bpm,
                "time_signature": [grid.time_signature.numerator, grid.time_signature.denominator],
                "beats": [{"time": b.time, "downbeat": b.is_downbeat} for b in grid.beats],
            },
        )

        bars: tuple[Bar, ...] = self._quantizer.quantize(hits, grid)
        n_notes: int = sum(len(b.notes) for b in bars)
        self._log(f"[quantizer:{self._quantizer.name}] {len(bars)} bars, {n_notes} notes")

        return TranscriptionResult(
            audio_file=audio_path.name,
            duration_seconds=mix.duration_seconds,
            tempo_bpm=grid.tempo_bpm,
            time_signature=grid.time_signature,
            bars=tuple(bars),
        )

    def _log(self, msg: str) -> None:
        if self._verbose:
            print(f"  {msg}", file=sys.stderr)
        if self._on_progress is not None:
            self._on_progress(msg)
