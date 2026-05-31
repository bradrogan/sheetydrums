"""sheetydrums CLI: transcribe a song's drum part to schema-valid JSON."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import typer

from sheetydrums.config import CLIConfig
from sheetydrums.factory import build_pipeline
from sheetydrums.interfaces import Note, TranscriptionResult
from sheetydrums.pipeline import Pipeline
from sheetydrums.validate import validate


def transcribe_command(
    audio: Path = typer.Argument(
        ...,
        exists=True,
        dir_okay=False,
        readable=True,
        help="Input audio file (mp3, wav, etc.).",
    ),
    output: Path = typer.Option(
        ...,
        "-o",
        "--output",
        help="Output path for events.json.",
    ),
    debug_dir: Path | None = typer.Option(
        None,
        "--debug-dir",
        help="Dump each stage's intermediate output into this directory for inspection.",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Suppress per-stage stat lines.",
    ),
) -> None:
    """Transcribe an audio file's drum part to events.json."""
    config: CLIConfig = CLIConfig(
        debug_dir=debug_dir,
        verbose=not quiet,
    )
    pipeline: Pipeline = build_pipeline(config)

    typer.echo(f"Transcribing {audio.name}…")
    result: TranscriptionResult = pipeline.transcribe(audio)
    events: dict[str, Any] = serialize_to_schema(result)
    validate(events)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(events, indent=2) + "\n")
    n_notes: int = sum(len(b["notes"]) for b in events["bars"])
    typer.echo(f"Wrote {output} ({n_notes} notes across {len(events['bars'])} bars)")


def serialize_to_schema(result: TranscriptionResult) -> dict[str, Any]:
    """Convert the dataclass result to a dict that matches the JSON schema."""
    return {
        "version": "1",
        "audio_file": result.audio_file,
        "duration_seconds": result.duration_seconds,
        "tempo_bpm": result.tempo_bpm,
        "time_signature": {
            "numerator": result.time_signature.numerator,
            "denominator": result.time_signature.denominator,
        },
        "bars": [
            {
                "index": bar.index,
                "start_seconds": bar.start_seconds,
                "notes": [_note_to_dict(n) for n in bar.notes],
            }
            for bar in result.bars
        ],
    }


def _note_to_dict(note: Note) -> dict[str, Any]:
    d: dict[str, Any] = {
        "instrument": note.instrument,
        "position": note.position,
        "duration": note.duration,
    }
    if note.confidence is not None:
        d["confidence"] = note.confidence
    if note.sustain_until is not None:
        d["sustain_until"] = note.sustain_until
    if note.tuplet is not None:
        d["tuplet"] = asdict(note.tuplet)
    return d


def main() -> None:
    typer.run(transcribe_command)
