"""Unit tests for server helpers that don't need the pipeline/models."""
from __future__ import annotations

import pytest

from sheetydrums.server import youtube_id


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=EjoyMB2ehsc", "EjoyMB2ehsc"),
        ("https://youtube.com/watch?v=EjoyMB2ehsc&t=30s", "EjoyMB2ehsc"),
        ("https://m.youtube.com/watch?v=EjoyMB2ehsc", "EjoyMB2ehsc"),
        ("https://music.youtube.com/watch?v=EjoyMB2ehsc", "EjoyMB2ehsc"),
        ("https://youtu.be/EjoyMB2ehsc", "EjoyMB2ehsc"),
        ("https://youtu.be/EjoyMB2ehsc?t=30", "EjoyMB2ehsc"),
        ("https://www.youtube.com/shorts/EjoyMB2ehsc", "EjoyMB2ehsc"),
        ("https://www.youtube.com/embed/EjoyMB2ehsc", "EjoyMB2ehsc"),
        ("https://www.youtube.com/live/EjoyMB2ehsc", "EjoyMB2ehsc"),
        ("https://www.youtube.com/v/EjoyMB2ehsc", "EjoyMB2ehsc"),
        # Non-matches → None (caller falls back to yt-dlp's id after download).
        ("https://example.com/watch?v=EjoyMB2ehsc", None),
        ("https://www.youtube.com/watch?v=tooshort", None),  # not 11 chars
        ("https://youtu.be/", None),
        ("https://www.youtube.com/", None),
        ("not a url at all", None),
    ],
)
def test_youtube_id(url: str, expected: str | None) -> None:
    assert youtube_id(url) == expected
