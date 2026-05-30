"""Quantize drum hits onto a beat grid and emit bars + notes.

Conforms to `interfaces.Quantizer`. Snaps positions to the nearest 16th note
within each bar. Triplet detection is deferred to v2 (see docs/v2-backlog.md).
"""
from __future__ import annotations

from fractions import Fraction

from sheetydrums.interfaces import (
    Bar,
    BeatGrid,
    DrumClass,
    DrumHit,
    Note,
    SchemaDrumClass,
)


def _to_schema_class(c: DrumClass) -> SchemaDrumClass:
    """Narrow a DrumClass to a SchemaDrumClass.

    The 5-class transcriber emits "hihat", "tom", and "cymbal" — none of which
    appear in the schema vocab. We collapse each to a sensible default at the
    emit boundary so output is always schema-valid:
      hihat → hihat_closed (v1: open/closed distinction requires LarsNet)
      tom → tom_mid (v1: pitch distinction deferred to v2)
      cymbal → ride (v1: ride/crash distinction requires LarsNet)
    Other DrumClass values are already in SchemaDrumClass and pass through.
    """
    if c == "hihat":
        return "hihat_closed"
    if c == "tom":
        return "tom_mid"
    if c == "cymbal":
        return "ride"
    return c


class StubQuantizer:
    name: str = "16th-snap"

    def quantize(
        self,
        hits: tuple[DrumHit, ...],
        grid: BeatGrid,
    ) -> tuple[Bar, ...]:
        if not grid.beats:
            return ()

        beat_seconds: float = 60.0 / grid.tempo_bpm
        whole_note_seconds: float = beat_seconds * grid.time_signature[1]
        bar_seconds: float = beat_seconds * grid.time_signature[0]
        downbeat_times: list[float] = [b.time for b in grid.beats if b.is_downbeat]

        bar_notes: dict[int, list[Note]] = {
            idx: [] for idx in range(1, len(downbeat_times) + 1)
        }

        for hit in hits:
            bar_idx: int | None = _find_bar(hit.time, downbeat_times, bar_seconds)
            if bar_idx is None:
                continue  # hit falls outside the detected bar grid; drop
            bar_start: float = downbeat_times[bar_idx - 1]
            offset_seconds: float = hit.time - bar_start
            position: Fraction = Fraction(offset_seconds / whole_note_seconds).limit_denominator(16)
            instrument: SchemaDrumClass = _to_schema_class(hit.drum_class)
            bar_notes[bar_idx].append(
                Note(
                    instrument=instrument,
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
