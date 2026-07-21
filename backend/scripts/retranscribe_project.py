"""Re-transcribe a cached project in place: apply the current pipeline
(tuned thresholds) + regenerate the drum stem, then overwrite the project's
notation. One-off dev utility for iterating on detection without re-downloading.

Usage: uv run python scripts/retranscribe_project.py <video_id>
"""
from __future__ import annotations

import sys
from pathlib import Path

from sheetydrums import store
from sheetydrums.cli import serialize_to_schema
from sheetydrums.config import CLIConfig
from sheetydrums.factory import build_pipeline


def main() -> None:
    video_id = sys.argv[1] if len(sys.argv) > 1 else "EjoyMB2ehsc"
    wav = Path.home() / ".cache" / "sheetydrums" / "youtube" / f"{video_id}.wav"
    if not wav.exists():
        raise SystemExit(f"cached audio not found: {wav}")
    project = store.load_project(video_id)
    if project is None:
        raise SystemExit(f"no project for {video_id}")

    pipeline = build_pipeline(CLIConfig(use_drumsep=True, verbose=True))
    result = pipeline.transcribe(wav, drum_stem_path=store.stem_path(video_id))
    project["notation"] = serialize_to_schema(result)
    store.save_project(project)

    n = sum(len(b["notes"]) for b in project["notation"]["bars"])
    print(f"updated {video_id}: {n} notes, stem={store.has_stem(video_id)}")


if __name__ == "__main__":
    main()
