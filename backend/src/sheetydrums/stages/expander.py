"""5→10 class expansion using sub-stem energy and spectral features.

Extends the "Cheuk et al. 2024" recipe (arXiv:2509.24853): given ADTOF's
5-class onsets and per-instrument sub-stems, refine the labels by looking at
local energy and spectrum in the relevant sub-stems:

  - For "cymbal" hits: compare RMS energy in the ride sub-stem vs the crash
    sub-stem around the hit time. Whichever has more energy wins.
  - For "hihat" hits: decay-window vs attack-window RMS ratio on the hihat
    sub-stem (a high ratio = sustained ringing = open). The decay window is
    ADAPTIVE — its end is clamped to just before the next hihat onset — so busy
    fast grooves don't alias the next stroke into the "sustain" measurement (the
    bug that flipped whole closed songs to open). Ratios are clustered/compared
    *across the whole song* so the split adapts to a recording's hihat character
    rather than an absolute cutoff. The song is split into open + closed only
    when its two ratio clusters *straddle* the open/closed threshold (one clearly
    choked, one clearly ringing); a single-character song gets one uniform label.
    NOTE (validated Aug 2026, see scripts/eval/): this works when a song's open
    hits genuinely ring longer than its closed ones (open outros, disco offbeat
    opens — split lifts open F1 0→54% on Disco Inferno, recovers Lazy Eye's open
    outro). It CANNOT separate opens that are choked as fast as the closed hats
    (fast 16th "barks" like Boogie Oogie Oogie's choruses): there the open/closed
    decay-ratios overlap and the split is noisy (open F1 only ~13%), because the
    difference is then timbral, not decay-length. Reliably catching those still
    needs a spectral/timbral feature or a trained model (v2).
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
        hihat_decay_start_seconds: float = 0.050,
        hihat_decay_max_seconds: float = 0.300,
        hihat_next_onset_guard_seconds: float = 0.010,
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
        - hihat_late_window_seconds: the OLD fixed post-attack "sustain" window.
          No longer used for classification (see below) — kept only so the
          debug dump can report the legacy ratio alongside the new one.
        - hihat_decay_start_seconds / hihat_decay_max_seconds /
          hihat_next_onset_guard_seconds: the ADAPTIVE sustain window. The
          decay ratio is measured over [decay_start, min(decay_start +
          decay_max, gap_to_next_hihat - guard)]. Anchoring the window's end to
          the next hihat onset is the key fix: a fixed 0.15-0.35 s window
          straddles the *next* stroke in any groove faster than ~3 hits/sec
          (i.e. almost all of them), so busy closed 16ths read as "still
          ringing" and the whole song flips to open. Ending the window before
          the next stroke measures each hat's own decay at any tempo. When the
          next stroke lands too soon to leave any window (very fast), the hat is
          necessarily choked -> ratio 0 (closed).
        - hihat_ratio_clip: cap the late/attack ratio at this value before
          clustering. A small minority of hits have near-zero attack energy
          and produce ratios in the dozens — those outliers would otherwise
          drag the "open" cluster center far above the rest of the data.
        - hihat_clearly_tight / hihat_clearly_loose / hihat_bimodal_min_fraction:
          DEPRECATED gate (kept only so the debug dump can still report the
          tight/loose fractions). The old "bimodal iff clearly-tight ≥15% AND
          clearly-loose ≥15%" rule missed songs whose open part is a small but
          real minority (e.g. an open outro), so the decision now uses the
          threshold-straddle test in _assign_hihat_labels instead.
        - hihat_unimodal_open_threshold: the open/closed boundary. Doubles as the
          straddle pivot — the song is treated as a genuine open/closed MIX (and
          split by cluster) only when its two ratio clusters land on opposite
          sides of this value; otherwise every hit gets one uniform label from
          whether the song's median is above it. So when the distribution is
          single-character (unimodal),
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
        self._hihat_decay_start: float = hihat_decay_start_seconds
        self._hihat_decay_max: float = hihat_decay_max_seconds
        self._hihat_next_onset_guard: float = hihat_next_onset_guard_seconds
        self._hihat_ratio_clip: float = hihat_ratio_clip
        self._hihat_clearly_tight: float = hihat_clearly_tight
        self._hihat_clearly_loose: float = hihat_clearly_loose
        self._hihat_bimodal_min_fraction: float = hihat_bimodal_min_fraction
        self._hihat_unimodal_open_threshold: float = hihat_unimodal_open_threshold
        self._tom_body_window: tuple[float, float] = tom_body_window_seconds
        self._tom_centroid_band: tuple[float, float] = tom_centroid_band_hz
        self._tom_uniform_spread_hz: float = tom_uniform_spread_hz
        self._tom_min_cluster_gap_hz: float = tom_min_cluster_gap_hz
        # Populated by the most recent expand() call; the pipeline dumps it when
        # --debug-dir is set so hi-hat ratio distributions can be inspected/tuned.
        self.last_hihat_debug: dict | None = None

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
        """Cluster every hihat hit's decay/attack RMS ratio and return {hit_index: label}.

        The decay ratio uses an ADAPTIVE sustain window whose end is clamped to
        just before the next hihat onset (see __init__), so busy fast grooves no
        longer alias the next stroke into the "sustain" measurement.

        Decision logic:
          - 0 hihat hits → empty mapping.
          - 1 hihat hit → hihat_closed (conventional default with no context).
          - Cluster the ratios via 1D k-means k=2 (on outlier-clipped ratios).
            STRADDLE test: if the two cluster centers land on opposite sides of
            `hihat_unimodal_open_threshold` (one < thr, one ≥ thr) the song
            genuinely mixes open + closed → label by cluster (low → hihat_closed,
            high → hihat_open).
          - Otherwise both clusters are on the same side → single-character hat →
            one uniform label from the median vs `hihat_unimodal_open_threshold`
            (loose songs → hihat_open, tight songs → hihat_closed).

        Also records per-hit ratios + the decision to `self.last_hihat_debug`.
        """
        a0, a1 = self._hihat_attack_window
        l0, l1 = self._hihat_late_window  # legacy fixed window, for the debug dump only
        d_start = self._hihat_decay_start
        d_max = self._hihat_decay_max
        guard = self._hihat_next_onset_guard

        hihat_indices: list[int] = [i for i, h in enumerate(hits) if h.drum_class == "hihat"]
        if not hihat_indices:
            self.last_hihat_debug = {"n": 0}
            return {}

        # Gap to the next hihat onset (a large default for the final hit so an
        # isolated last hat still gets a full decay window). Relies on hits being
        # in ascending time order — guaranteed by the transcriber, which returns
        # them sorted by (time, class); max(0, gap) below is a belt-and-braces
        # guard so an out-of-order hit degrades to "choked/closed", never crashes.
        times: list[float] = [hits[i].time for i in hihat_indices]
        DEFAULT_GAP = 1.0
        gaps: list[float] = [
            (times[k + 1] - times[k]) if k + 1 < len(times) else DEFAULT_GAP
            for k in range(len(times))
        ]

        hihat_ratios: list[float] = []
        per_hit: list[dict] = []
        for k, i in enumerate(hihat_indices):
            t = hits[i].time
            gap = max(0.0, gaps[k])
            attack: float = _rms(substems.hihat, t + a0, t + a1)
            decay_end: float = min(d_start + d_max, gap - guard)
            if attack <= 1e-9 or decay_end <= d_start:
                ratio = 0.0
                decay = 0.0
            else:
                decay = _rms(substems.hihat, t + d_start, t + decay_end)
                ratio = decay / attack
            hihat_ratios.append(ratio)
            old_late: float = _rms(substems.hihat, t + l0, t + l1)
            per_hit.append({
                "time": round(t, 4),
                "gap": round(gap, 4),
                "ratio": round(ratio, 4),
                "ratio_old_fixed": round(old_late / attack, 4) if attack > 1e-9 else 0.0,
                "decay_end": round(decay_end, 4),
            })

        if len(hihat_indices) == 1:
            self.last_hihat_debug = {"n": 1, "decision": "single->closed", "hits": per_hit}
            per_hit[0]["label"] = "hihat_closed"
            return {hihat_indices[0]: "hihat_closed"}

        raw: NDArray[np.floating] = np.asarray(hihat_ratios, dtype=np.float64)
        # tight_frac / loose_frac are retained for the debug dump only — the
        # decision no longer gates on them (see the straddle test below).
        tight_frac: float = float((raw < self._hihat_clearly_tight).mean())
        loose_frac: float = float((raw > self._hihat_clearly_loose).mean())
        median: float = float(np.median(raw))
        thr: float = self._hihat_unimodal_open_threshold

        # Cluster the ratios into two, then decide whether the song genuinely
        # MIXES open and closed by whether the clusters *straddle* the open/closed
        # threshold: one cluster in "choked/closed" territory (center < thr) and
        # the other in "ringing/open" (center >= thr). If both centers sit on the
        # same side, the hat is single-character throughout → one uniform label
        # from the median (keeps an always-loose song like Back in Black fully
        # open, and an always-tight song fully closed).
        #
        # This replaces the old "clearly-tight AND clearly-loose each ≥15%" gate,
        # which missed songs whose open part is a small but real, clearly-ringing
        # minority (e.g. an open outro): validated Aug 2026 — split lifts open F1
        # 0→54% on Disco Inferno and recovers Lazy Eye's open outro, with no
        # regression on the single-character songs. See scripts/eval/.
        clipped: NDArray[np.floating] = np.clip(raw, 0.0, self._hihat_ratio_clip)
        centers, assignments = _kmeans_1d(clipped, k=2)
        order: NDArray[np.intp] = np.argsort(centers)
        lo_center = float(centers[int(order[0])])
        hi_center = float(centers[int(order[1])])
        centers_out: list[float] = [lo_center, hi_center]
        straddles: bool = lo_center < thr <= hi_center

        labels_by_index: dict[int, str]
        decision: str
        if straddles:
            cluster_to_label: dict[int, str] = {
                int(order[0]): "hihat_closed",
                int(order[1]): "hihat_open",
            }
            decision = "bimodal-split (straddle)"
            labels_by_index = {
                hihat_indices[i]: cluster_to_label[int(a)]
                for i, a in enumerate(assignments)
            }
            for j, a in enumerate(assignments):
                per_hit[j]["label"] = cluster_to_label[int(a)]
        else:
            label: str = "hihat_open" if median > thr else "hihat_closed"
            decision = f"unimodal->{'open' if label == 'hihat_open' else 'closed'}"
            labels_by_index = {i: label for i in hihat_indices}
            for h in per_hit:
                h["label"] = label

        n_open = sum(1 for lab in labels_by_index.values() if lab == "hihat_open")
        self.last_hihat_debug = {
            "n": len(hihat_indices),
            "decision": decision,
            "median_ratio": round(median, 4),
            "tight_frac": round(tight_frac, 3),
            "loose_frac": round(loose_frac, 3),
            "kmeans_centers": [round(c, 4) for c in centers_out],
            "straddles": straddles,
            "n_open": n_open,
            "n_closed": len(hihat_indices) - n_open,
            "thresholds": {
                "clearly_tight": self._hihat_clearly_tight,
                "clearly_loose": self._hihat_clearly_loose,
                "bimodal_min_fraction": self._hihat_bimodal_min_fraction,
                "unimodal_open_threshold": self._hihat_unimodal_open_threshold,
            },
            "hits": per_hit,
        }
        return labels_by_index

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
