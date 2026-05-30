"""Pipeline orchestration: audio file → schema-compliant events dict.

Every stage is currently a stub returning trivial data — just enough to make
the data contract round-trip end to end. Real implementations replace the
private functions below one at a time.
"""
from pathlib import Path

SCHEMA_VERSION = "1"


def transcribe(audio_path: Path) -> dict:
    """Run the full pipeline and return a schema-compliant events dict."""
    drums = _separate(audio_path)
    onsets = _detect_onsets(drums)
    classified = _classify(onsets, drums)
    tempo, time_sig, beats = _track_beats(drums)
    bars = _quantize(classified, beats, tempo, time_sig)

    return {
        "version": SCHEMA_VERSION,
        "audio_file": audio_path.name,
        "duration_seconds": 2.0,
        "tempo_bpm": tempo,
        "time_signature": time_sig,
        "bars": bars,
    }


def _separate(audio_path: Path):
    """Source separation. Real version will run Demucs and return the drum stem."""
    return audio_path


def _detect_onsets(drums) -> list[float]:
    """Drum onset detection. Stub: a hit on every quarter beat at 120 BPM."""
    return [0.0, 0.5, 1.0, 1.5]


def _classify(onsets: list[float], drums) -> list[dict]:
    """Classify each onset to one of the 10 drum classes. Stub: all snare."""
    return [{"time_seconds": t, "instrument": "snare", "confidence": 0.5} for t in onsets]


def _track_beats(drums) -> tuple[float, dict, list[float]]:
    """Detect tempo, time signature, beat positions. Stub: 120 BPM, 4/4."""
    return 120.0, {"numerator": 4, "denominator": 4}, [0.0, 0.5, 1.0, 1.5]


def _quantize(classified, beats, tempo, time_sig) -> list[dict]:
    """Group classified hits into bars with positions/durations. Stub: one bar, four snare quarter notes."""
    return [
        {
            "index": 1,
            "start_seconds": 0.0,
            "notes": [
                {"instrument": "snare", "position": p, "duration": "1/4", "confidence": 0.5}
                for p in ["0", "1/4", "1/2", "3/4"]
            ],
        }
    ]
