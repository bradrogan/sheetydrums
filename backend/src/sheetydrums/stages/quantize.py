"""Quantize drum hits onto a beat grid and emit bars + notes.

Conforms to `interfaces.Quantizer`. Snaps positions to the nearest 16th note
within each bar. Triplet detection is deferred to v2 (see docs/v2-backlog.md).
"""
from __future__ import annotations

from fractions import Fraction

from sheetydrums.interfaces import Bar, BeatGrid, DrumHit, Note


# v1 vocabulary collapse — applied at the emit boundary so the output is always
# schema-valid regardless of which upstream stages ran:
#   - "tom" → tom_mid (pitch distinction deferred; see docs/v2-backlog.md)
#   - "hihat" → hihat_closed (default when --no-larsnet skipped the open/closed split)
#   - "cymbal" → ride (default when --no-larsnet skipped the ride/crash split)
# When the SubStemExpander runs it produces the refined labels directly; the
# coarse labels never reach this map in the LarsNet-on path.
_INSTRUMENT_MAP = {
    "tom": "tom_mid",
    "hihat": "hihat_closed",
    "cymbal": "ride",
}


class StubQuantizer:
    name = "16th-snap"

    def quantize(
        self,
        hits: tuple[DrumHit, ...],
        grid: BeatGrid,
    ) -> tuple[Bar, ...]:
        if not grid.beats:
            return ()

        beat_seconds = 60.0 / grid.tempo_bpm
        whole_note_seconds = beat_seconds * grid.time_signature[1]
        bar_seconds = beat_seconds * grid.time_signature[0]
        downbeat_times = [t for t, is_db in zip(grid.beats, grid.downbeats) if is_db]

        # Mutable accumulators per bar
        bar_notes: dict[int, list[Note]] = {idx: [] for idx in range(1, len(downbeat_times) + 1)}

        for hit in hits:
            bar_idx = _find_bar(hit.time, downbeat_times, bar_seconds)
            if bar_idx is None:
                continue  # hit falls outside the detected bar grid; drop
            bar_start = downbeat_times[bar_idx - 1]
            offset_seconds = hit.time - bar_start
            position = Fraction(offset_seconds / whole_note_seconds).limit_denominator(16)
            bar_notes[bar_idx].append(
                Note(
                    instrument=_INSTRUMENT_MAP.get(hit.drum_class, hit.drum_class),
                    position=_format_fraction(position),
                    duration="1/8",
                    confidence=hit.confidence,
                )
            )

        return tuple(
            Bar(
                index=idx,
                start_seconds=downbeat_times[idx - 1],
                notes=tuple(bar_notes[idx]),
            )
            for idx in range(1, len(downbeat_times) + 1)
        )


def _find_bar(t: float, downbeat_times: list[float], bar_seconds: float) -> int | None:
    """1-based bar index that contains `t`, or None if it falls outside the grid."""
    for idx, start in enumerate(downbeat_times, start=1):
        if start <= t < start + bar_seconds:
            return idx
    return None


def _format_fraction(frac: Fraction) -> str:
    """Format a Fraction the way the schema expects: '0' or 'a/b'."""
    if frac.denominator == 1:
        return str(frac.numerator)
    return f"{frac.numerator}/{frac.denominator}"
