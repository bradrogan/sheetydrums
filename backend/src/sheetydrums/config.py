"""User-facing pipeline configuration, populated from CLI flags."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CLIConfig:
    """Knobs that influence pipeline construction and behaviour.

    Consumed by `factory.build_pipeline` to pick stage implementations, and
    by the orchestrator (via injected DebugSink / verbose flag) to govern
    side effects.
    """
    debug_dir: Path | None = None
    verbose: bool = True
