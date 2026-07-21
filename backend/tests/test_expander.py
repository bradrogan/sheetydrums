"""Tests for CheukExpander — focused on the per-song tom clustering logic.

The cymbal/hihat heuristics depend on real audio energy and aren't covered here
(they're exercised by the slow end-to-end smoke test). Tom clustering, by
contrast, takes synthetic centroid values and is pure logic worth pinning down.
"""
from __future__ import annotations

import numpy as np

from sheetydrums.audio import AudioBuffer
from sheetydrums.interfaces import DrumHit, DrumSubStems
from sheetydrums.stages import CheukExpander


def _silent_buffer(seconds: float = 5.0, sr: int = 44100) -> AudioBuffer:
    return AudioBuffer(samples=np.zeros(int(seconds * sr), dtype=np.float32), sample_rate=sr)


def _toms_buffer_with_pitches(pitches_hz: list[float], sr: int = 44100) -> AudioBuffer:
    """Build a tom sub-stem containing a 100 ms sine burst at each given pitch.
    Burst N starts at time 0.5 * N seconds — same schedule the test's DrumHit
    timestamps use, so each burst is the only audio in its analysis window.
    """
    duration_s = 0.5 * (len(pitches_hz) + 1) + 0.2
    n_samples = int(duration_s * sr)
    samples = np.zeros(n_samples, dtype=np.float32)
    burst_s = 0.100
    burst_n = int(burst_s * sr)
    for i, hz in enumerate(pitches_hz):
        start_t = 0.5 * (i + 1)
        start_n = int(start_t * sr)
        t = np.arange(burst_n) / sr
        # Pure sine — the band-limited centroid (50-500 Hz) of a single sine at
        # f Hz is f Hz itself, which makes assertions readable.
        samples[start_n:start_n + burst_n] = (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)
    return AudioBuffer(samples=samples, sample_rate=sr)


def _substems(toms: AudioBuffer) -> DrumSubStems:
    blank = _silent_buffer(seconds=toms.duration_seconds)
    return DrumSubStems(
        kick=blank, snare=blank, hihat=blank, toms=toms, ride=blank, crash=blank,
    )


def _tom_hits_at(n: int) -> tuple[DrumHit, ...]:
    return tuple(DrumHit(time=0.5 * (i + 1), drum_class="tom", confidence=0.9) for i in range(n))


def test_tom_single_hit_labels_as_mid():
    expander = CheukExpander()
    toms = _toms_buffer_with_pitches([100.0])
    out = expander.expand(_tom_hits_at(1), _substems(toms))
    assert out[0].drum_class == "tom_mid"


def test_tom_two_pitches_split_low_high():
    expander = CheukExpander()
    toms = _toms_buffer_with_pitches([80.0, 80.0, 80.0, 250.0, 250.0, 250.0])
    out = expander.expand(_tom_hits_at(6), _substems(toms))
    labels = [h.drum_class for h in out]
    assert labels[:3] == ["tom_low"] * 3
    assert labels[3:] == ["tom_high"] * 3


def test_tom_three_pitches_split_low_mid_high():
    expander = CheukExpander()
    toms = _toms_buffer_with_pitches([80.0, 80.0, 150.0, 150.0, 250.0, 250.0])
    out = expander.expand(_tom_hits_at(6), _substems(toms))
    labels = [h.drum_class for h in out]
    assert labels[:2] == ["tom_low"] * 2
    assert labels[2:4] == ["tom_mid"] * 2
    assert labels[4:] == ["tom_high"] * 2


def test_tom_uniform_pitches_all_mid():
    # Centroids within tom_uniform_spread_hz (default 25 Hz) should be treated
    # as a single drum rather than getting an artificial high/low split.
    expander = CheukExpander()
    toms = _toms_buffer_with_pitches([100.0, 105.0, 110.0, 95.0])
    out = expander.expand(_tom_hits_at(4), _substems(toms))
    assert all(h.drum_class == "tom_mid" for h in out)


def test_tom_close_clusters_merge_to_two():
    # Three clusters but two centers are within tom_min_cluster_gap_hz of each
    # other — the merge step should collapse them to a 2-cluster low/high split.
    expander = CheukExpander(tom_min_cluster_gap_hz=40.0)
    toms = _toms_buffer_with_pitches([80.0, 85.0, 250.0, 255.0, 260.0])
    out = expander.expand(_tom_hits_at(5), _substems(toms))
    labels = [h.drum_class for h in out]
    assert "tom_mid" not in labels
    assert set(labels) == {"tom_low", "tom_high"}


def test_non_tom_hits_unchanged_by_clustering():
    # Kick and snare hits pass through untouched even when tom clustering runs.
    expander = CheukExpander()
    blank = _silent_buffer()
    substems = _substems(_toms_buffer_with_pitches([100.0, 250.0]))
    hits = (
        DrumHit(time=0.1, drum_class="kick", confidence=0.9),
        DrumHit(time=0.5, drum_class="tom", confidence=0.9),
        DrumHit(time=0.7, drum_class="snare", confidence=0.9),
        DrumHit(time=1.0, drum_class="tom", confidence=0.9),
    )
    out = expander.expand(hits, substems)
    assert out[0].drum_class == "kick"
    assert out[2].drum_class == "snare"
    assert out[1].drum_class in {"tom_low", "tom_high"}
    assert out[3].drum_class in {"tom_low", "tom_high"}


# === Hihat clustering tests ==============================================

def _hihat_buffer(decays: list[float], sr: int = 44100) -> AudioBuffer:
    """Build a hihat sub-stem with one click per `decays` entry. Each click is
    an exponentially decaying 6 kHz tone starting at 0.5 * (i+1) seconds. The
    decay value is the e-folding time in seconds, so:
      - decay=0.005 → essentially silent by the late window (closed feel; ratio ~0)
      - decay=1.000 → late window is still close to attack level (open feel; ratio ~0.8)
    """
    sr_f = float(sr)
    duration_s = 0.5 * (len(decays) + 1) + 0.5
    samples = np.zeros(int(duration_s * sr), dtype=np.float32)
    body_n = int(0.500 * sr)
    t = np.arange(body_n) / sr_f
    base = np.sin(2 * np.pi * 6000.0 * t)
    for i, tau in enumerate(decays):
        env = np.exp(-t / max(tau, 1e-6))
        start_n = int(0.5 * (i + 1) * sr)
        samples[start_n:start_n + body_n] = (0.5 * base * env).astype(np.float32)
    return AudioBuffer(samples=samples, sample_rate=sr)


def _substems_with_hihat(hihat: AudioBuffer) -> DrumSubStems:
    blank = _silent_buffer(seconds=hihat.duration_seconds)
    return DrumSubStems(
        kick=blank, snare=blank, hihat=hihat, toms=blank, ride=blank, crash=blank,
    )


def _hihat_hits_at(n: int) -> tuple[DrumHit, ...]:
    return tuple(DrumHit(time=0.5 * (i + 1), drum_class="hihat", confidence=0.9) for i in range(n))


def test_hihat_two_decay_classes_split_open_closed():
    # Bimodal: 3 clearly-tight hits + 3 clearly-loose hits. Both clear-class
    # fractions exceed the bimodal threshold → cluster + label by rank.
    expander = CheukExpander()
    hihat = _hihat_buffer([0.005, 0.005, 0.005, 1.0, 1.0, 1.0])
    out = expander.expand(_hihat_hits_at(6), _substems_with_hihat(hihat))
    labels = [h.drum_class for h in out]
    assert labels[:3] == ["hihat_closed"] * 3
    assert labels[3:] == ["hihat_open"] * 3


def test_hihat_unimodal_loose_all_open():
    # All hits have a medium-long decay → no clearly-tight hits. Falls into
    # the unimodal path; the median ratio is above the open threshold → all open.
    expander = CheukExpander()
    hihat = _hihat_buffer([1.0] * 8)
    out = expander.expand(_hihat_hits_at(8), _substems_with_hihat(hihat))
    assert all(h.drum_class == "hihat_open" for h in out)


def test_hihat_unimodal_tight_all_closed():
    # All hits have very short decay. Unimodal path with median below threshold
    # → all labelled closed.
    expander = CheukExpander()
    hihat = _hihat_buffer([0.003] * 8)
    out = expander.expand(_hihat_hits_at(8), _substems_with_hihat(hihat))
    assert all(h.drum_class == "hihat_closed" for h in out)


def test_hihat_single_hit_defaults_closed():
    expander = CheukExpander()
    hihat = _hihat_buffer([0.150])
    out = expander.expand(_hihat_hits_at(1), _substems_with_hihat(hihat))
    assert out[0].drum_class == "hihat_closed"


def test_hihat_no_clear_loose_falls_unimodal():
    # All hits have medium-fast decay — none in the clearly-loose regime.
    # Should land in unimodal path (not get an artificial bimodal split).
    expander = CheukExpander()
    hihat = _hihat_buffer([0.080] * 10)  # all ratios well below 0.70
    out = expander.expand(_hihat_hits_at(10), _substems_with_hihat(hihat))
    # All hits get the same label (one or the other), which is the unimodal contract.
    labels = {h.drum_class for h in out}
    assert len(labels) == 1, f"expected uniform label, got {labels}"
