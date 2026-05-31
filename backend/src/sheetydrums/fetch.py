"""Resolve a user-supplied audio source to a local file path.

Supports two input shapes:
- Local file path → returned unchanged after existence check.
- Public URL (YouTube and anything else yt-dlp handles) → downloaded as WAV
  to a content-addressed cache, then the cache path is returned.

Cache layout: `~/.cache/sheetydrums/youtube/<video_id>.wav`. A second call with
the same URL is a no-op (yt-dlp sees the file exists and skips re-download).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse


_CACHE_DIR: Path = Path.home() / ".cache" / "sheetydrums" / "youtube"


def resolve_audio_source(audio_input: str) -> Path:
    """Return a local Path to the audio for `audio_input` (a path or URL).

    Raises FileNotFoundError for missing local paths and yt_dlp.DownloadError
    (or its subclasses) for unavailable / DRM-locked / network-failed URLs.
    """
    if _looks_like_url(audio_input):
        return _download_audio(audio_input)
    path = Path(audio_input)
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")
    return path


def _looks_like_url(s: str) -> bool:
    """True if `s` is an http/https URL. yt-dlp handles a lot more (s3, ftp,
    site-specific extractors) but http/https covers our practical surface."""
    parsed = urlparse(s)
    return parsed.scheme in ("http", "https")


def _download_audio(url: str) -> Path:
    """Download `url`'s audio via yt-dlp, return the cached WAV path."""
    # Imported lazily so a `sheetydrums --help` doesn't pay the yt-dlp import
    # cost (which transitively pulls in a lot of extractor modules).
    import yt_dlp  # type: ignore[import-untyped]

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)

    output_template: str = str(_CACHE_DIR / "%(id)s.%(ext)s")
    # ydl_opts and info_dict are kept as `Any` at the yt-dlp boundary —
    # yt-dlp's TypedDicts are nominal-typed and not great to match against
    # plain dict literals. The yt-dlp call is the type firewall.
    ydl_opts: Any = {
        "format": "bestaudio/best",
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        # ffmpeg postprocessor extracts audio + transcodes to WAV (16-bit PCM)
        # so soundfile can read it without M4A/AAC headaches.
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "wav",
            },
        ],
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info: Any = ydl.extract_info(url, download=True)
        video_id: str = info["id"]

    cached: Path = _CACHE_DIR / f"{video_id}.wav"
    if not cached.exists():
        raise RuntimeError(
            f"yt-dlp completed but cached file is missing: {cached}. "
            f"Check that ffmpeg is on PATH (needed for audio extraction)."
        )
    return cached
