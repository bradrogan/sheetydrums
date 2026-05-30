"""pytest configuration.

Adds a `--run-slow` flag so the integration tests (real models, real audio)
stay out of the fast unit-test loop. By default `uv run pytest` runs only the
fast tests; `uv run pytest --run-slow` runs everything.
"""
from __future__ import annotations

from typing import Any

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run slow/integration tests (real Demucs, real audio fixture).",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[Any],
) -> None:
    if config.getoption("--run-slow"):
        return
    skip_slow = pytest.mark.skip(reason="needs --run-slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip_slow)
