"""5→10 class expansion using sub-stem energy and spectral features.

Extends the "Cheuk et al. 2024" recipe (arXiv:2509.24853): given ADTOF's
5-class onsets and per-instrument sub-stems, refine the labels by looking at
local energy and spectrum in the relevant sub-stems:

  - For "cymbal" hits: compare RMS energy in the ride sub-stem vs the crash
    sub-stem around the hit time. Whichever has more energy wins.
  - For "hihat" hits: late-window vs attack-window RMS ratio on the hihat
    sub-stem (a high ratio = sustained ringing = open). Like the toms, the
    ratios are clustered *across the whole song* — the per-song split adapts
    to a recording's hihat character rather than imposing an absolute Hz/ratio
    cutoff that would over- or under-classify depending on the kit miking.
  - For "tom" hits: band-limited (50–500 Hz) spectral centroid of the tom
    sub-stem in the hit's body window, clustered *across the whole song*. The
    highest cluster's hits become tom_high, the lowest become tom_low, the
    middle (if any) becomes tom_mid. Per-song clustering avoids the kit-tuning
    problem: a metal kit's "floor tom" centroid can be the same Hz as a jazz
    kit's "mid tom", so absolute thresholds don't generalize — but within one
    recording the relative ordering does.

Kick and snare pass through unchanged — ADTOF already labels them definitively
and there's no schema-level sub-split for them.

Conforms to `interfaces.ClassExpander`.
"""
from __future__ import annotations

import numpy as np
from numpy.typing import NDArray

from sheetydrums.audio import AudioBuffer
from sheetydrums.interfaces import DrumHit, DrumSubStems


class CheukExpander:
    name: str = "cheuk-recipe"

    def __init__(
        self,
        *,
        cymbal_window_seconds: tuple[float, float] = (0.01, 0.20),
        hihat_attack_window_seconds: tuple[float, float] = (0.005, 0.050),
        hihat_late_window_seconds: tuple[float, float] = (0.15, 0.35),
        hihat_ratio_clip: float = 3.0,
        hihat_clearly_tight: float = 0.15,
        hihat_clearly_loose: float = 0.70,
        hihat_bimodal_min_fraction: float = 0.15,
        hihat_unimodal_open_threshold: float = 0.40,
        tom_body_window_seconds: tuple[float, float] = (0.010, 0.120),
        tom_centroid_band_hz: tuple[float, float] = (50.0, 500.0),
        tom_uniform_spread_hz: float = 25.0,
        tom_min_cluster_gap_hz: float = 25.0,
    ) -> None:
        """
        - cymbal_window_seconds: how long around a cymbal hit to measure
          ride/crash energy. (-pre, +post) seconds from the hit.
        - hihat_attack_window_seconds: the immediate attack window for a
          hihat hit (used as the baseline for the open/closed ratio).
        - hihat_late_window_seconds: the post-attack "sustain" window. High
          energy here vs the attack baseline means the hat is ringing.
        - hihat_ratio_clip: cap the late/attack ratio at this value before
          clustering. A small minority of hits have near-zero attack energy
          and produce ratios in the dozens — those outliers would otherwise
          drag the "open" cluster center far above the rest of the data.
        - hihat_clearly_tight / hihat_clearly_loose: ratios that are
          unambiguously "choked" or "ringing" respectively. Used for
          shape-based bimodality detection (below).
        - hihat_bimodal_min_fraction: a song is treated as bimodal only when
          BOTH the clearly-tight fraction AND the clearly-loose fraction
          exceed this. Otherwise the song's hihat is single-character
          throughout (e.g. Back in Black, always loose) and we use the
          unimodal fallback. Empirically, k-means cluster-gap can't separate
          these cases because the long-tail outliers from low-energy attacks
          fool k-means into a fake-bimodal split.
        - hihat_unimodal_open_threshold: when the distribution is unimodal,
          if the song's median ratio exceeds this we call every hat open;
          otherwise every hat closed. Set near the natural midpoint of
          plausible ratios so a "tight" song's median (~0.1) stays closed
          and a "loose" song's median (~0.4–0.5) flips to open.
        - tom_body_window_seconds: window after each tom onset where the tom's
          fundamental is most cleanly audible.
        - tom_centroid_band_hz: frequency range the centroid is computed over.
          Restricted to (50, 500) Hz so the centroid reflects the tom's
          fundamental rather than broadband stick-attack noise.
        - tom_uniform_spread_hz: if the total span of the song's tom centroids
          is smaller than this, treat them all as a single tom (label tom_mid).
          Prevents single-tom kits from getting an artificial high/low split.
        - tom_min_cluster_gap_hz: cluster centers closer than this get merged
          back together. Catches k-means over-splitting when only 2 toms are
          present in a song but we ran with k=3.
        """
        self._cymbal_window: tuple[float, float] = cymbal_window_seconds
        self._hihat_attack_window: tuple[float, float] = hihat_attack_window_seconds
        self._hihat_late_window: tuple[float, float] = hihat_late_window_seconds
        self._hihat_ratio_clip: float = hihat_ratio_clip
        self._hihat_clearly_tight: float = hihat_clearly_tight
        self._hihat_clearly_loose: float = hihat_clearly_loose
        self._hihat_bimodal_min_fraction: float = hihat_bimodal_min_fraction
        self._hihat_unimodal_open_threshold: float = hihat_unimodal_open_threshold
        self._tom_body_window: tuple[float, float] = tom_body_window_seconds
        self._tom_centroid_band: tuple[float, float] = tom_centroid_band_hz
        self._tom_uniform_spread_hz: float = tom_uniform_spread_hz
        self._tom_min_cluster_gap_hz: float = tom_min_cluster_gap_hz

    def expand(
        self,
        hits: tuple[DrumHit, ...],
        substems: DrumSubStems,
    ) -> tuple[DrumHit, ...]:
        # Two-pass for toms AND hihat: collect features over the whole song,
        # cluster across the song's population, then label by cluster rank.
        # Avoids absolute thresholds that wouldn't generalize across kits.
        tom_label_by_idx: dict[int, str] = self._assign_tom_labels(hits, substems)
        hihat_label_by_idx: dict[int, str] = self._assign_hihat_labels(hits, substems)

        out: list[DrumHit] = []
        for i, h in enumerate(hits):
            if h.drum_class == "cymbal":
                refined: str = self._classify_cymbal(h.time, substems)
                out.append(DrumHit(time=h.time, drum_class=refined, confidence=h.confidence))  # type: ignore[arg-type]
            elif h.drum_class == "hihat":
                out.append(DrumHit(time=h.time, drum_class=hihat_label_by_idx[i], confidence=h.confidence))  # type: ignore[arg-type]
            elif h.drum_class == "tom":
                out.append(DrumHit(time=h.time, drum_class=tom_label_by_idx[i], confidence=h.confidence))  # type: ignore[arg-type]
            else:
                out.append(h)
        return tuple(out)

    def _classify_cymbal(self, t: float, ss: DrumSubStems) -> str:
        pre, post = self._cymbal_window
        ride_e: float = _rms(ss.ride, t - pre, t + post)
        crash_e: float = _rms(ss.crash, t - pre, t + post)
        return "crash" if crash_e > ride_e else "ride"

    def _assign_hihat_labels(
        self,
        hits: tuple[DrumHit, ...],
        substems: DrumSubStems,
    ) -> dict[int, str]:
        """Cluster every hihat hit's late/attack RMS ratio and return {hit_index: label}.

        Decision logic:
          - 0 hihat hits → empty mapping.
          - 1 hihat hit → hihat_closed (conventional default with no context).
          - Bimodality test: the song has BOTH clearly-tight (ratio < ε_tight)
            and clearly-loose (ratio > ε_loose) populations, each at least
            `hihat_bimodal_min_fraction` of the total. If both are present →
            cluster via 1D k-means k=2 (on outlier-clipped ratios), low
            cluster → hihat_closed, high cluster → hihat_open.
          - Otherwise the song's hihat is single-character. Pick a uniform
            label from the median ratio vs `hihat_unimodal_open_threshold`
            (loose songs land on hihat_open; tight songs on hihat_closed).
        """
        a0, a1 = self._hihat_attack_window
        l0, l1 = self._hihat_late_window

        hihat_indices: list[int] = []
        hihat_ratios: list[float] = []
        for i, h in enumerate(hits):
            if h.drum_class != "hihat":
                continue
            attack: float = _rms(substems.hihat, h.time + a0, h.time + a1)
            if attack <= 1e-9:
                hihat_indices.append(i)
                hihat_ratios.append(0.0)
                continue
            late: float = _rms(substems.hihat, h.time + l0, h.time + l1)
            hihat_indices.append(i)
            hihat_ratios.append(late / attack)

        if not hihat_indices:
            return {}
        if len(hihat_indices) == 1:
            return {hihat_indices[0]: "hihat_closed"}

        raw: NDArray[np.floating] = np.asarray(hihat_ratios, dtype=np.float64)
        tight_frac: float = float((raw < self._hihat_clearly_tight).mean())
        loose_frac: float = float((raw > self._hihat_clearly_loose).mean())

        is_bimodal: bool = (
            tight_frac >= self._hihat_bimodal_min_fraction
            and loose_frac >= self._hihat_bimodal_min_fraction
        )

        if not is_bimodal:
            median: float = float(np.median(raw))
            label: str = (
                "hihat_open" if median > self._hihat_unimodal_open_threshold
                else "hihat_closed"
            )
            return {i: label for i in hihat_indices}

        clipped: NDArray[np.floating] = np.clip(raw, 0.0, self._hihat_ratio_clip)
        centers, assignments = _kmeans_1d(clipped, k=2)
        order: NDArray[np.intp] = np.argsort(centers)
        cluster_to_label: dict[int, str] = {
            int(order[0]): "hihat_closed",
            int(order[1]): "hihat_open",
        }
        return {
            hihat_indices[i]: cluster_to_label[int(a)]
            for i, a in enumerate(assignments)
        }

    def _assign_tom_labels(
        self,
        hits: tuple[DrumHit, ...],
        substems: DrumSubStems,
    ) -> dict[int, str]:
        """Cluster every tom hit's centroid and return {hit_index: label}.

        Decision logic:
          - 0 tom hits → empty mapping.
          - 1 tom hit  → tom_mid (no kit context to rank against).
          - Overall span < uniform-spread → single drum, all tom_mid.
          - Otherwise: 1D k-means with k=3 (or k=2 if only 2 hits exist), then
            merge any cluster centers within min_cluster_gap. Surviving
            clusters are ranked by center: lowest → tom_low, highest →
            tom_high, middle (if a 3-cluster survives the merge) → tom_mid.
        """
        b0, b1 = self._tom_body_window
        f_lo, f_hi = self._tom_centroid_band

        tom_indices: list[int] = []
        tom_centroids: list[float] = []
        for i, h in enumerate(hits):
            if h.drum_class != "tom":
                continue
            c: float = _spectral_centroid(substems.toms, h.time + b0, h.time + b1, band_hz=(f_lo, f_hi))
            tom_indices.append(i)
            tom_centroids.append(c)

        if not tom_indices:
            return {}
        if len(tom_indices) == 1:
            return {tom_indices[0]: "tom_mid"}

        values: NDArray[np.floating] = np.asarray(tom_centroids, dtype=np.float64)
        spread: float = float(values.max() - values.min())
        if spread < self._tom_uniform_spread_hz:
            return {i: "tom_mid" for i in tom_indices}

        # Run 1D k-means with k=3 (or k=2 when there are exactly 2 hits).
        k: int = 3 if len(values) >= 3 else 2
        centers, assignments = _kmeans_1d(values, k)

        # Sort clusters by center, then merge adjacent ones that are closer
        # than `min_cluster_gap`. Carries each original cluster id to a final
        # rank (0 = lowest pitch).
        order: NDArray[np.intp] = np.argsort(centers)
        sorted_centers: NDArray[np.floating] = centers[order]
        rank_of_sorted_pos: list[int] = [0]
        next_rank: int = 0
        for i in range(1, len(sorted_centers)):
            if (sorted_centers[i] - sorted_centers[i - 1]) < self._tom_min_cluster_gap_hz:
                rank_of_sorted_pos.append(next_rank)
            else:
                next_rank += 1
                rank_of_sorted_pos.append(next_rank)
        n_final_clusters: int = next_rank + 1

        cluster_to_rank: dict[int, int] = {
            int(order[i]): rank_of_sorted_pos[i] for i in range(len(order))
        }
        rank_to_label: dict[int, str] = _tom_rank_labels(n_final_clusters)

        return {
            tom_indices[i]: rank_to_label[cluster_to_rank[int(a)]]
            for i, a in enumerate(assignments)
        }


def _tom_rank_labels(n_clusters: int) -> dict[int, str]:
    """Map a final rank (0..n-1, ordered low→high pitch) to a tom label."""
    if n_clusters == 1:
        return {0: "tom_mid"}
    if n_clusters == 2:
        return {0: "tom_low", 1: "tom_high"}
    # 3 (or more, defensively — extra get folded into tom_mid as a fallback)
    out: dict[int, str] = {0: "tom_low", n_clusters - 1: "tom_high"}
    for r in range(1, n_clusters - 1):
        out[r] = "tom_mid"
    return out


def _kmeans_1d(
    values: NDArray[np.floating],
    k: int,
    max_iter: int = 30,
) -> tuple[NDArray[np.floating], NDArray[np.intp]]:
    """1D k-means. Returns (cluster_centers, per-point assignments)."""
    # Quantile init places initial centers across the distribution — stable
    # and converges in a few iterations on 1D data.
    quantiles: NDArray[np.floating] = np.linspace(0.0, 1.0, k + 2)[1:-1]
    centers: NDArray[np.floating] = np.quantile(values, quantiles).astype(np.float64)
    assignments: NDArray[np.intp] = np.zeros(len(values), dtype=np.intp)
    for _ in range(max_iter):
        dists: NDArray[np.floating] = np.abs(values[:, None] - centers[None, :])
        new_assignments: NDArray[np.intp] = dists.argmin(axis=1).astype(np.intp)
        if (new_assignments == assignments).all() and _ > 0:
            break
        assignments = new_assignments
        for c in range(k):
            mask: NDArray[np.bool_] = assignments == c
            if mask.any():
                centers[c] = float(values[mask].mean())
    return centers, assignments


def _rms(buf: AudioBuffer, start_s: float, end_s: float) -> float:
    """RMS energy over [start_s, end_s) of a sub-stem buffer. Mono-mixes if stereo."""
    if end_s <= start_s:
        return 0.0
    sr: int = buf.sample_rate
    s0: int = max(0, int(start_s * sr))
    s1: int = min(buf.samples.shape[0], int(end_s * sr))
    if s1 <= s0:
        return 0.0
    samples: NDArray[np.floating] = buf.samples[s0:s1]
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples * samples)))


def _spectral_centroid(
    buf: AudioBuffer,
    start_s: float,
    end_s: float,
    band_hz: tuple[float, float] | None = None,
) -> float:
    """Spectral centroid (Hz) of the buffer in [start_s, end_s). Mono-mixes if stereo.

    If `band_hz` is supplied, the centroid is computed over only that frequency
    band. Useful when broadband content (e.g. stick-attack noise on a tom)
    would otherwise dominate and obscure a lower-frequency fundamental.

    Returns 0.0 for silent / empty windows so the caller's threshold logic
    falls into the lowest bucket.
    """
    if end_s <= start_s:
        return 0.0
    sr: int = buf.sample_rate
    s0: int = max(0, int(start_s * sr))
    s1: int = min(buf.samples.shape[0], int(end_s * sr))
    if s1 <= s0:
        return 0.0
    samples: NDArray[np.floating] = buf.samples[s0:s1]
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if samples.size == 0:
        return 0.0
    spectrum: NDArray[np.floating] = np.abs(np.fft.rfft(samples))
    freqs: NDArray[np.floating] = np.fft.rfftfreq(samples.size, d=1.0 / sr)
    if band_hz is not None:
        mask = (freqs >= band_hz[0]) & (freqs <= band_hz[1])
        spectrum = spectrum[mask]
        freqs = freqs[mask]
    total: float = float(spectrum.sum())
    if total <= 1e-9:
        return 0.0
    return float((spectrum * freqs).sum() / total)
