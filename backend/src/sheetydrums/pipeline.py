"""Pipeline orchestrator.

Imports nothing from any audio/ML library. Stage implementations are injected
via the constructor — concrete model wrappers in `stages/` for production,
fakes in tests. To skip the LarsNet sub-stem branch, pass
`substem_separator=None` and a `PassThroughExpander` as `class_expander`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from sheetydrums.audio import load_audio
from sheetydrums.debug import DebugSink

if TYPE_CHECKING:
    from sheetydrums.interfaces import (
        BeatTracker,
        ClassExpander,
        DrumSubStemSeparator,
        DrumTranscriber,
        MixSeparator,
        Quantizer,
        TranscriptionResult,
    )

from sheetydrums.interfaces import TranscriptionResult  # noqa: E402  (re-import for runtime use)


class Pipeline:
    """Runs the drum-transcription stages in dependency order."""

    def __init__(
        self,
        *,
        separator: MixSeparator,
        transcriber: DrumTranscriber,
        beat_tracker: BeatTracker,
        quantizer: Quantizer,
        class_expander: ClassExpander,
        substem_separator: DrumSubStemSeparator | None = None,
        debug_sink: DebugSink | None = None,
        verbose: bool = True,
    ) -> None:
        self._separator = separator
        self._transcriber = transcriber
        self._beat_tracker = beat_tracker
        self._quantizer = quantizer
        self._class_expander = class_expander
        self._substem_separator = substem_separator
        self._debug = debug_sink if debug_sink is not None else DebugSink(None)
        self._verbose = verbose

    def transcribe(self, audio_path: Path) -> TranscriptionResult:
        mix = load_audio(audio_path)
        self._log(f"loaded {audio_path.name}: {mix.duration_seconds:.2f}s @ {mix.sample_rate} Hz")
        self._debug.write_audio_placeholder("input-mix", mix)

        drums = self._separator.separate(mix)
        self._log(f"[separator:{self._separator.name}] drum stem: {drums.duration_seconds:.2f}s")
        self._debug.write_audio_placeholder(f"separator-{self._separator.name}", drums)

        hits = self._transcriber.transcribe(drums)
        self._log(
            f"[transcriber:{self._transcriber.name}] {len(hits)} hits, "
            f"vocab={self._transcriber.vocabulary}"
        )
        self._debug.write_json(
            f"transcriber-{self._transcriber.name}",
            [{"time": h.time, "class": h.drum_class, "confidence": h.confidence} for h in hits],
        )

        substems = None
        if self._substem_separator is not None:
            substems = self._substem_separator.separate(drums)
            self._log(f"[substem:{self._substem_separator.name}] 5 sub-stems extracted")
            self._debug.write_text(
                f"substem-{self._substem_separator.name}",
                "kick + snare + hihat + toms + cymbals sub-stems extracted",
            )

        hits = self._class_expander.expand(hits, substems)
        expanded_vocab = sorted({h.drum_class for h in hits})
        self._log(
            f"[expander:{self._class_expander.name}] {len(hits)} hits, "
            f"vocab={tuple(expanded_vocab)}"
        )
        self._debug.write_json(
            f"expander-{self._class_expander.name}",
            [{"time": h.time, "class": h.drum_class, "confidence": h.confidence} for h in hits],
        )

        grid = self._beat_tracker.track(mix)
        self._log(
            f"[beats:{self._beat_tracker.name}] {grid.tempo_bpm:.1f} BPM, "
            f"{grid.time_signature[0]}/{grid.time_signature[1]}, {len(grid.beats)} beats"
        )
        self._debug.write_json(
            f"beats-{self._beat_tracker.name}",
            {
                "tempo_bpm": grid.tempo_bpm,
                "time_signature": list(grid.time_signature),
                "beats": list(grid.beats),
                "downbeats": list(grid.downbeats),
            },
        )

        bars = self._quantizer.quantize(hits, grid)
        n_notes = sum(len(b.notes) for b in bars)
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
