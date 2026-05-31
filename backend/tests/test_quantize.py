"""Focused tests for the quantizer's snap-to-16ths behavior.

The previous implementation used `Fraction.limit_denominator(16)` which would
return non-16th fractions like `1/3` or `3/13` when those approximated the
offset better than any `n/16`. The strict snapper here is the regression
backstop.
"""
from __future__ import annotations

from sheetydrums.interfaces import Beat, BeatGrid, DrumHit, TimeSignature
from sheetydrums.stages.quantize import StubQuantizer


def _grid_120bpm_4_4(n_bars: int = 1) -> BeatGrid:
    """4/4 at 120 BPM: beat = 0.5 s, bar = 2.0 s, downbeat every 4 beats."""
    beats: list[Beat] = []
    for i in range(n_bars * 4):
        beats.append(Beat(time=i * 0.5, is_downbeat=(i % 4 == 0)))
    return BeatGrid(
        beats=tuple(beats),
        tempo_bpm=120.0,
        time_signature=TimeSignature(numerator=4, denominator=4),
    )


def test_downbeat_snaps_to_zero() -> None:
    bars = StubQuantizer().quantize(
        (DrumHit(0.0, "kick", 0.9),),
        _grid_120bpm_4_4(),
    )
    assert bars[0].notes[0].position == "0"


def test_beat_2_snaps_to_one_quarter() -> None:
    # 4/4, 120 BPM → beat 2 lands at 0.5 s, which is 1/4 of a whole note from the downbeat.
    bars = StubQuantizer().quantize(
        (DrumHit(0.5, "snare", 0.9),),
        _grid_120bpm_4_4(),
    )
    assert bars[0].notes[0].position == "1/4"


def test_off_grid_offset_snaps_to_nearest_16th_not_to_odd_denominator() -> None:
    # offset 0.34 s of a 2 s bar = 0.17 of a whole note. Nearest n/16:
    #   round(0.17 * 16) = round(2.72) = 3 → 3/16.
    # `Fraction.limit_denominator(16)` would have returned 1/6 (≈0.1667), which
    # is mathematically closer but useless as a drum-chart position.
    bars = StubQuantizer().quantize(
        (DrumHit(0.34, "kick", 0.5),),
        _grid_120bpm_4_4(),
    )
    assert bars[0].notes[0].position == "3/16"


def test_simplification_makes_clean_fractions() -> None:
    # 8/16 → 1/2, not "8/16". Schema accepts both but the simplified form is
    # cleaner and matches how drummers read positions.
    bars = StubQuantizer().quantize(
        (DrumHit(1.0, "kick", 0.9),),  # exactly beat 3 = 1/2 of a 4/4 bar
        _grid_120bpm_4_4(),
    )
    assert bars[0].notes[0].position == "1/2"


def test_position_never_wraps_to_next_bar() -> None:
    # An onset at 1.99 s lands in bar 1 (which spans [0.0, 2.0)). After snapping,
    # offset_in_whole_notes = 0.995 → round(0.995 * 16) = 16, which we clamp to 15.
    # Expected position: "15/16" — last sixteenth of bar 1, not "0" of bar 2.
    bars = StubQuantizer().quantize(
        (DrumHit(1.99, "snare", 0.9),),
        _grid_120bpm_4_4(),
    )
    assert bars[0].notes[0].position == "15/16"
