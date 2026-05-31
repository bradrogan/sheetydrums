"""Quantize drum hits onto a beat grid and emit bars + notes.

Conforms to `interfaces.Quantizer`. Strictly snaps positions to the nearest
16th note within each bar; positions are always `n/16` for some integer
`0 <= n < 16` (and simplify naturally — `0/16` → `0`, `8/16` → `1/2`, etc.).
Triplet detection is deferred to v2 (see docs/v2-backlog.md).
"""
from __future__ import annotations

from fractions import Fraction
from typing import Final

from sheetydrums.interfaces import (
    Bar,
    BeatGrid,
    DrumClass,
    DrumHit,
    Note,
    SchemaDrumClass,
)


# Subdivision the quantizer snaps onsets to. 16 = sixteenth notes, the
# default for rock/pop drum charts. Increase to 32 (or higher) if 32nd-note
# rhythmic detail becomes a v2 concern.
_SUBDIVISIONS_PER_WHOLE: Final[int] = 16


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
        whole_note_seconds: float = beat_seconds * grid.time_signature.denominator
        bar_seconds: float = beat_seconds * grid.time_signature.numerator
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
            position: Fraction = _snap_to_subdivision(
                offset_seconds, whole_note_seconds, _SUBDIVISIONS_PER_WHOLE,
            )
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


def _snap_to_subdivision(
    offset_seconds: float,
    whole_note_seconds: float,
    subdivisions_per_whole: int,
) -> Fraction:
    """Snap `offset_seconds` to the nearest n/subdivisions_per_whole position.

    Unlike Fraction.limit_denominator(N) which would happily return non-grid
    fractions like 1/3 or 3/13 when they're mathematically closer to the offset
    than any n/16, this snaps strictly to multiples of 1/subdivisions_per_whole.
    The denominator of the returned Fraction may be smaller than the subdivision
    (e.g. 8/16 simplifies to 1/2) but is never coarser — always a power-of-2
    division of the bar.
    """
    raw_subdivisions: float = offset_seconds / whole_note_seconds * subdivisions_per_whole
    snapped: int = round(raw_subdivisions)
    # Clamp to [0, subdivisions_per_whole - 1]; an onset at exactly the next
    # downbeat should belong to the next bar (handled by _find_bar), so we cap
    # at the last sixteenth of the current bar rather than letting it wrap.
    snapped = max(0, min(snapped, subdivisions_per_whole - 1))
    return Fraction(snapped, subdivisions_per_whole)


def _format_fraction(frac: Fraction) -> str:
    """Format a Fraction the way the schema expects: '0' or 'a/b'."""
    if frac.denominator == 1:
        return str(frac.numerator)
    return f"{frac.numerator}/{frac.denominator}"
