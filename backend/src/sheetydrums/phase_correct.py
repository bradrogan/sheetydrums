"""Downbeat-phase correction using drum-hit statistics.

Beat This! reliably detects beat positions but its downbeat phase is sometimes
wrong by 1, 2, or 3 beats — songs with sparse intros, anacruses, or strong
backbeats can mislead its downbeat head. The visible symptom is every bar
showing snare on beat 2 instead of beat 1, or kicks landing on "beat 3" of
our bar (when musically they're on beat 1).

This module corrects the phase by looking at where kicks fall and rotating
the downbeat labels so the beat with the most kicks becomes beat 1.
Reliable for genres where the kick anchors the downbeat (rock, pop, funk).
Less reliable for jazz, classical, or styles where the downbeat is implicit.
"""
from __future__ import annotations

from sheetydrums.interfaces import Beat, BeatGrid, DrumHit


def correct_downbeat_phase(
    hits: tuple[DrumHit, ...],
    grid: BeatGrid,
) -> BeatGrid:
    """Return `grid` with downbeat labels rotated to match kick distribution.

    Algorithm: for each beat-position-mod-N (where N is the numerator of the
    time signature), count how many kicks land near that beat. The position
    with the most kicks becomes the downbeat. Beats keep their times — only
    the `is_downbeat` flag changes.

    Falls back to the original grid if there aren't enough beats or kicks
    to be confident.
    """
    n: int = grid.time_signature.numerator
    if len(grid.beats) < n * 2:
        return grid

    kicks: list[DrumHit] = [h for h in hits if h.drum_class == "kick"]
    if not kicks:
        return grid

    beat_times: list[float] = [b.time for b in grid.beats]
    counts: list[int] = [0] * n
    for kick in kicks:
        idx: int = _nearest_index(beat_times, kick.time)
        counts[idx % n] += 1

    # The downbeat should be the beat-position with the most kicks. We require
    # the winner to be noticeably above uniform — at least 1.3x the mean —
    # otherwise we trust Beat This!'s original phase.
    best_position: int = counts.index(max(counts))
    mean_count: float = sum(counts) / n
    if mean_count == 0 or max(counts) < mean_count * 1.3:
        return grid

    return BeatGrid(
        beats=tuple(
            Beat(time=b.time, is_downbeat=(i % n == best_position))
            for i, b in enumerate(grid.beats)
        ),
        tempo_bpm=grid.tempo_bpm,
        time_signature=grid.time_signature,
    )


def _nearest_index(sorted_times: list[float], t: float) -> int:
    """Index of the entry in `sorted_times` closest to `t`."""
    # Linear scan is fine here — songs have hundreds of beats, hits have
    # thousands; cost is millions of comparisons but each is a float subtract.
    best_i: int = 0
    best_d: float = abs(sorted_times[0] - t)
    for i in range(1, len(sorted_times)):
        d: float = abs(sorted_times[i] - t)
        if d < best_d:
            best_i = i
            best_d = d
    return best_i
