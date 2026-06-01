"""Quantize drum hits onto a beat grid and emit bars + notes.

Conforms to `interfaces.Quantizer`. Strictly snaps positions to the nearest
16th note within each bar; positions are always `n/16` for some integer
`0 <= n < bar_size_in_16ths` (and simplify naturally — `0/16` → `0`,
`8/16` → `1/2`, etc.). Triplet detection is deferred to v2
(see docs/v2-backlog.md).

Two non-obvious choices that this module makes:

1. **Each bar's duration is read from the beat grid**, not derived from
   the song's median tempo. Beat This! reports tempo to the nearest 2 BPM
   (50 Hz frame rate), and within a song its detected beats drift by 10-30 ms
   per beat. Using `60/tempo` as a uniform bar length means hits near the
   end of a bar get assigned positions like `15/16` even when the drummer
   played them on the next bar's downbeat. Using per-bar actual durations
   from consecutive downbeats avoids this.

2. **Hits whose quantized position would land at the very end of a bar
   spill forward into the next bar at position 0.** Drummers play
   ahead-of-the-beat all the time — a kick at 99% of bar N is musically
   beat 1 of bar N+1 played slightly early. Without this, that kick
   renders at position 15/16 of bar N (Back In Black's iconic
   beat-1 kicks were doing exactly this, hidden as `kick@15/16`).
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
        downbeat_times: list[float] = [b.time for b in grid.beats if b.is_downbeat]
        if not downbeat_times:
            return ()

        # bar_in_whole_notes: how many whole notes one bar contains.
        # 4/4 → 1.0; 3/4 → 0.75; 6/8 → 0.75. We use this to scale "fraction
        # of bar" to "fraction of whole note" (the schema's position unit).
        bar_in_whole_notes: float = (
            grid.time_signature.numerator / grid.time_signature.denominator
        )
        # Maximum schema-valid position-in-16ths inside one bar.
        # 4/4: 16 (positions 0..15); 3/4: 12 (positions 0..11).
        max_subdivisions: int = round(bar_in_whole_notes * _SUBDIVISIONS_PER_WHOLE)

        # Per-bar actual durations from downbeat positions. The last bar has
        # no following downbeat, so we use the median of the others — a
        # robust estimate even when a few bars drift.
        bar_durations: list[float] = [
            downbeat_times[i + 1] - downbeat_times[i]
            for i in range(len(downbeat_times) - 1)
        ]
        if bar_durations:
            median_duration: float = sorted(bar_durations)[len(bar_durations) // 2]
        else:
            median_duration = 60.0 / grid.tempo_bpm * grid.time_signature.numerator
        bar_durations.append(median_duration)

        n_bars: int = len(downbeat_times)
        bar_notes: dict[int, list[Note]] = {idx: [] for idx in range(1, n_bars + 1)}

        for hit in hits:
            bar_idx: int | None = _find_bar(hit.time, downbeat_times, median_duration)
            if bar_idx is None:
                continue  # before the first downbeat — drop

            # Convert hit's offset into bar to a snapped 16th-note position.
            bar_start: float = downbeat_times[bar_idx - 1]
            bar_duration: float = bar_durations[bar_idx - 1]
            offset: float = hit.time - bar_start
            position_frac: float = offset / bar_duration  # fraction of this bar
            position_in_whole_notes: float = position_frac * bar_in_whole_notes
            snapped: int = round(position_in_whole_notes * _SUBDIVISIONS_PER_WHOLE)

            # Off-by-one fix: if the rounded position equals a full bar's worth
            # of subdivisions, the hit is musically the next bar's downbeat
            # played a few ms early. Spill it forward.
            if snapped >= max_subdivisions:
                if bar_idx + 1 in bar_notes:
                    bar_idx += 1
                    snapped = 0
                else:
                    # No next bar — clamp to the last valid position.
                    snapped = max_subdivisions - 1
            snapped = max(0, snapped)

            position: Fraction = Fraction(snapped, _SUBDIVISIONS_PER_WHOLE)
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
            for idx in range(1, n_bars + 1)
        )


def _find_bar(
    t: float,
    downbeat_times: list[float],
    last_bar_seconds: float,
) -> int | None:
    """1-based bar index containing `t`, or None if `t` is before the first
    downbeat. The last bar extends `last_bar_seconds` past its downbeat."""
    n: int = len(downbeat_times)
    for idx, start in enumerate(downbeat_times, start=1):
        end: float = downbeat_times[idx] if idx < n else start + last_bar_seconds
        if start <= t < end:
            return idx
    return None


def _format_fraction(frac: Fraction) -> str:
    """Format a Fraction the way the schema expects: '0' or 'a/b'."""
    if frac.denominator == 1:
        return str(frac.numerator)
    return f"{frac.numerator}/{frac.denominator}"
