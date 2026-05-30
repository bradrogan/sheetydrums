"""sheetydrums CLI: transcribe a song's drum part to schema-valid JSON."""
import json
from pathlib import Path

import typer

from sheetydrums.pipeline import transcribe
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
) -> None:
    """Transcribe an audio file's drum part to events.json."""
    typer.echo(f"Transcribing {audio.name}…")
    events = transcribe(audio)
    validate(events)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(events, indent=2) + "\n")
    n_notes = sum(len(b["notes"]) for b in events["bars"])
    typer.echo(f"Wrote {output} ({n_notes} notes across {len(events['bars'])} bars)")


def main() -> None:
    typer.run(transcribe_command)
