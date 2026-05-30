# sheetydrums backend pipeline — research notes (May 2026)

## 1. Executive summary

For the **drum transcription** core, the strongest open option in mid-2026 is the **ADTOF CRNN** (Zehren et al., Signals 2023) — pretrained, MIT-licensed‑adjacent (CC-BY-NC-SA on weights — see Risk 1), PyTorch port available, runs cleanly on Apple Silicon. But it only outputs **5 classes** (kick, snare, hi-hat, toms, cymbals) which is **strictly fewer than our 10**. To close the gap to our target vocabulary we should run a **drum-stem sub-separator (LarsNet or the Jarredou MDX23C "DrumSep" model)** in parallel and use its per-stem energy to disambiguate (a) open vs. closed vs. pedal hi-hat, (b) ride vs. crash, (c) high vs. mid vs. low tom — this is exactly the trick used by Cheuk et al., "Enhanced ADT via Drum Stem Source Separation" (arXiv:2509.24853, ISMIR 2024 LBD). For **beat tracking**, **Beat This!** (Foscarin/Schlüter/Widmer, ISMIR 2024, MIT, PyTorch) is the clear current SOTA and supersedes both madmom (effectively unmaintained, Python ≤3.9) and BeatNet for offline use. The pipeline should change: drop the standalone "onset detection → classification" split, since modern ADT models are joint onset+class systems. The **single biggest open question**: can a 5-class ADT + drum sub-stem post-processing reliably produce our 10 classes (especially `hihat_chick` and the three toms) at acceptable F-measure on real-world mixes, or do we need to train/fine-tune a head on STAR Drums' 18-class vocabulary?

## 2. Per-question answers

### Q1 — Drum transcription SOTA

| System | Year | Vocab | Code/Weights | License | Framework | Notes |
|---|---|---|---|---|---|---|
| **ADTOF (Zehren)** | 2021/23 | 5: kick, snare, hi-hat, tom, cymbal | yes, [github.com/MZehren/ADTOF](https://github.com/MZehren/ADTOF) + ADTOF-pytorch | CC-BY-NC-SA 4.0 | TF/Keras (PyTorch port ≈-0.2% F) | Current open SOTA on ENST/MDB at 5 classes. Vocab too narrow. |
| **Magenta OaF Drums** | 2020 | 7-9 GM MIDI bins (kick, snare, closed/open/pedal HH, low/high tom, crash, ride per E-GMD paper) | yes, E-GMD checkpoint | Apache-2.0 | **TF1** | **Magenta repo was archived Jan 6 2026** — no further maintenance. Trained on solo drums only (E-GMD), not mixes. Real-mix accuracy is weak. |
| **MT3 / Polyphonic-MT3** | ICLR 2022 | drums = "one of 128 GM MIDI", but drums are explicitly a side-task | yes | Apache-2.0 | **JAX/T5X** | Strong general transcriber, but the paper says drums "not a focus" and Slakh drum onset F1 was only ~84%. JAX install friction on Apple Silicon. |
| **YourMT3+ (YPTF.MoE+Multi)** | MLSP 2024 | Multi-instrument, drums included as one of 12 program tokens | yes, [github.com/mimbres/YourMT3](https://github.com/mimbres/YourMT3) | **GPL-3.0** (license-incompatible if we ever go closed-source) | PyTorch | ~90.1% Slakh drum onset F1 vs MT3's 83.9. Drum sub-class vocabulary is just "GM percussion" — not better-grained than MT3. |
| **Noise-to-Notes (N2N)** | arXiv Sep 2025, rev Mar 2026 | 7 (kick, snare, tom, hi-hat, crash, ride, bell) | **no code released as of May 2026** | n/a | (PyTorch implied) | Claims SOTA via diffusion + MERT features. Numbers vs ADTOF not publicly tabulated; can't verify. **Unusable until release.** |
| **Inverse Drum Machine (IDM)** | TASLP 2026, arXiv 2505.03337 | Trained for separation + transcription jointly | yes, [github.com/bernardo-torres/inverse-drum-machine](https://github.com/bernardo-torres/inverse-drum-machine) | Apache-2.0 | PyTorch (ConvNeXt+TCN) | Eval on **StemGMD only** (solo drums). Class count tied to training kit (44 train kits). Promising but unproven on full mixes. |
| **Enhanced ADT via Stem Separation** (Cheuk et al.) | arXiv 2509.24853, ISMIR 2024 LBD | 7 (splits hi-hat→open/closed and cymbal→crash/ride) | No standalone release; uses ADTOF + jarredou DrumSep | mixed | PyTorch | **Recipe, not a new model**: Demucs v4 → ADTOF (5 cls) → DrumSep sub-stems → reassign 5→7. MDB F=0.84 (+12 pts vs baseline). |
| **STAR Drums (Weber et al.)** | TISMIR Jul 2025 | **18 classes (closed/open/pedal HH, low/mid/high tom, splash/Chinese/crash/ride/bell, side-stick, clap, tambourine, cowbell, clave)** | Dataset only (no released model checkpoint) | open dataset | CRNN reference impl. | **Best vocabulary match.** Reference CRNN gets F=0.67 on MDB-18class — modest but real. Could fine-tune on this. |
| **ADT_STR (Pier et al.)** | arXiv 2601.09520 (Jan 2026) | 5-class (paper not explicit beyond that) | yes, [github.com/pier-maker92/ADT_STR](https://github.com/pier-maker92/ADT_STR) | unclear | PyTorch | Claims SOTA on ENST/MDB using synthetic one-shot data + seq2seq. No license listed; verify before depending. |

**Recommended choice:** ADTOF (PyTorch port) as the 5-class core, with the Cheuk et al. style stem-separation post-processing to expand to 7+. Use STAR Drums as a **fine-tuning corpus** when we want to push to 10/18 classes natively.

### Q2 — Does source separation help or hurt?

Evidence is **mostly positive** for ADT, with caveats:

- **Cheuk et al. 2024** ([arXiv:2509.24853](https://arxiv.org/abs/2509.24853)) report **+12 F1 pts on MDB and +10 on ENST** when running Demucs v4 to isolate drums, then ADTOF, then DrumSep for sub-stems. **BUT –2 pts on RBMA** (electronic drums) — separator artefacts hurt synthetic kits.
- **ISMIR 2025 "Understanding Performance Limitations in ADT"** ([Zenodo 17706523](https://zenodo.org/records/17706523)) confirms that for the DTM task (drums-in-mix), **melodic/vocal interference is the dominant error source** — supporting upstream separation.
- **STAR Drums (TISMIR 2025)** reports MSS quality of F=0.92 on their stems and uses separation in their pipeline.
- **Caveat:** Demucs introduces transient smearing on cymbal decays. For our `hihat_open`/`crash`/`ride` distinction this might be marginal — verify.

**Verdict:** Run Demucs (or HT-Demucs v4) upstream. Expect a real F-score gain on rock/pop and a small regression on heavily processed / electronic material.

### Q3 — Beat tracking SOTA

| System | Year | Output | License | Notes |
|---|---|---|---|---|
| **madmom** | 2016 (still cited) | beats, downbeats, tempo, key, etc. | BSD-3 | Effectively unmaintained — no PyPI release in >12 months, **broken on Python ≥3.10**. Beat This! issue #9 confirms. Avoid. |
| **BeatNet** | ISMIR 2021 | beats, downbeats, tempo, meter (online + offline) | AGPL-3.0 | Strong for real-time. Offline accuracy now beaten by Beat This! |
| **Beat Transformer** | ISMIR 2022 | beats, downbeats | MIT | Uses demixed inputs (Spleeter). Surpassed by Beat This! on most benchmarks. |
| **All-in-One Music Structure Analyzer** | ICASSP 2023 | beats, downbeats, tempo, **section labels (intro/verse/chorus/...)** | MIT | PyTorch + NATTEN. 100 fps frame resolution. No drum transcription. Excellent if we want structure boundaries for "section A/B" in the score. |
| **BEAST** | ICASSP 2024 | beats, downbeats (**online, 46 ms latency**) | research code | Online-only use case; not relevant for our offline batch. |
| **Beat This!** | ISMIR 2024 | beats + downbeats (no tempo head — derive from inter-beat interval) | MIT | PyTorch, `pip install beat-this`, 22 kHz input, MIT, pretrained weights, **doesn't need DBN**. SOTA on multiple corpora. |

**Recommended:** **Beat This!** for beats + downbeats. Derive tempo as the median 60/IBI over a windowed segment. For time-signature inference, count beats-per-bar from downbeat positions; if we want section structure later, layer All-in-One on top.

### Q4 — Multi-task / unified models

- **All-in-One (mir-aidj)** does beats+downbeats+tempo+structure jointly but **no drum transcription**.
- **MT3 / YourMT3+** are multi-instrument transcribers but **don't do beat tracking** and their drum vocabulary is coarse (general MIDI percussion).
- **Inverse Drum Machine** unifies separation + transcription for drums, but not beats.
- There is **no single 2024-2026 model** that emits beats + downbeats + a fine-grained drum vocabulary in one shot. Two-model pipeline (Beat This! + ADT) is unavoidable.

### Q5 — Vocabulary mapping to our 10 classes

| Our class | ADTOF (5) | OaF Drums (~7) | STAR (18) | N2N (7) | Cheuk 7-cls |
|---|---|---|---|---|---|
| kick | ✓ (BD) | ✓ (36) | ✓ | ✓ | ✓ |
| snare | ✓ (SD) | ✓ (38) | ✓ | ✓ | ✓ |
| hihat_closed | merged | ✓ (42) | ✓ | merged | ✓ |
| hihat_open | merged | ✓ (46) | ✓ | merged | ✓ |
| **hihat_chick** (pedal) | ✗ | ✓ (44) | ✓ | ✗ | ✗ |
| ride | merged | ✓ (51) | ✓ | ✓ | ✓ |
| crash | merged | ✓ (49) | ✓ | ✓ | ✓ |
| **tom_high** | merged | partial | ✓ | merged | partial |
| **tom_mid** | merged | partial | ✓ | merged | partial |
| **tom_low** | merged | partial | ✓ | merged | partial |

Only **STAR Drums' 18-class vocabulary natively covers all 10** of ours. The catch: there's a published reference CRNN trained on it (F=0.67 on MDB at 18 classes) but **no off-the-shelf pretrained checkpoint** — we'd have to train. OaF Drums (archived Magenta) hits all classes via GM MIDI but its TF1 stack is a dead end on Apple Silicon in 2026.

**Practical mapping** for a 5-class ADTOF baseline + LarsNet/DrumSep sub-stems:
- ADTOF hi-hat onset + LarsNet hi-hat stem decay length → closed vs. open.
- A hi-hat onset where the kick is also energetic and stick-spectrum cymbal energy is absent → likely `hihat_chick` (foot only). This is shakier; consider gating on snare/kick absence.
- ADTOF tom onset + LarsNet toms stem spectral centroid → tom_high (>200 Hz fundamental band) / tom_mid / tom_low.
- ADTOF cymbal onset + LarsNet cymbals stem long-decay envelope → ride (sustained) vs. crash (transient peak then 1-2 s decay).

### Q6 — Practical usage of top picks

**ADTOF-pytorch:**
- Input: 44.1 kHz mono, mel-spec front-end inside the model.
- Hop: 10 ms (100 fps frame predictions).
- Postprocessing: peak-picking on per-class activation; default threshold ~0.5 but **tune per class** — hi-hat threshold typically lower.
- Confidence: emit logits, snap to nearest beat-grid in a second pass.
- Watch for: cymbal-class false positives during loud vocals; mitigated by Demucs-drums-only input.

**Beat This!:**
- Input: **22.05 kHz mono** — resample if needed.
- Output: `.beats` file (TSV, time + downbeat flag). Python API returns numpy arrays.
- Use the `final0` checkpoint for general music; `small` for fast inference.
- Auto-downloads on first run. ~78 MB weights.
- Apple MPS works; ~real-time on M-series for a 4-minute song.

**Demucs v4 (HT-Demucs):**
- Use `htdemucs` model (default). Output 4 stems including drums.
- 44.1 kHz, batched in 8-second chunks with overlap.
- ~5-10× real-time on M-series MPS.

**LarsNet (or jarredou DrumSep MDX23C):**
- Input is **drums stem from Demucs**, not full mix.
- LarsNet outputs 5 sub-stems (kick, snare, toms, hi-hat, cymbals).
- License: **CC-BY-NC 4.0 on weights** — non-commercial. If sheetydrums is commercial, prefer the jarredou MDX23C model (license less restrictive, used by Cheuk et al.) or train our own on StemGMD.

## 3. Revised pipeline

```
mp3/wav (full mix, any SR)
  │
  ├─► resample 44.1 kHz, mono
  │
  ├─► Demucs v4 (htdemucs) ──► drums stem ─────────────┐
  │                                                    │
  │                                                    ├─► ADTOF-pytorch  ──► 5-class onsets + velocity
  │                                                    │     (kick, snare, hh, tom, cym)
  │                                                    │
  │                                                    └─► LarsNet / DrumSep ──► 5 sub-stems
  │                                                          │
  │                                                          └─► spectral/envelope features per onset
  │
  └─► resample 22.05 kHz, mono
        │
        └─► Beat This! ──► beat times + downbeat flags
                              │
                              └─► derive tempo (median 60/IBI) + time sig (beats/bar)

Stage: post-processing & class expansion
  - For each ADTOF onset, look up the corresponding sub-stem feature window
  - Disambiguate 5 → 10 classes via rules (see Q5)
  - Optional: train a lightweight head on STAR Drums for native 10-class output

Stage: quantization
  - Snap onsets to nearest beat subdivision (1/8, 1/16, 1/12 triplet)
  - Use beat times from Beat This! as the grid
  - Output JSON conforming to the schema/ folder
```

Stages eliminated from the old skeleton:
- "Onset detection" as a standalone stage — folded into ADTOF.
- "Classification" as a standalone stage — also folded into ADTOF.
- "Source separation" is now a **mandatory upstream stage**, not optional.

## 4. Top 3 risks / things to verify

1. **ADTOF weight license (CC-BY-NC-SA 4.0)** is non-commercial. If sheetydrums will ever monetise, we can't ship those weights. Mitigations: (a) train our own CRNN on E-GMD/Slakh/STAR Drums (permissive datasets), (b) use ADT_STR (license unclear — verify), or (c) use Inverse Drum Machine (Apache-2.0) once full-mix benchmarks land. **Verify license posture before committing.**

2. **The 5→10 class expansion is heuristic.** Pedal hi-hat (`hihat_chick`) and the three tom splits are not directly emitted by any pretrained open-weights model we can use legally. The Cheuk et al. trick only gets us to 7 classes (it splits hi-hat into open/closed and cymbals into ride/crash). For the foot hi-hat and tom splits we're either (a) writing rules over sub-stem energy/centroid — fragile — or (b) fine-tuning a 10-/18-class head on STAR Drums. **Run a vocab-expansion ablation on STAR Drums or MDB-extended before locking the architecture in.**

3. **Demucs separation artefacts on cymbals**. HT-Demucs is known to smear cymbal transients into the "other" stem. This could degrade exactly the ride/crash classes we're trying to distinguish. **A/B test ADT on full-mix vs Demucs-drums on a 20-song calibration set** that includes (a) rock with prominent cymbals, (b) electronic/synthetic kits, (c) jazz/brushes. Expect the calibration to reveal genre-specific thresholds.

Additional minor concerns:
- **madmom is dead** for Python 3.12 (our project uses 3.12). Don't reach for it as a fallback.
- **Magenta repo archived Jan 2026** — OaF Drums has no future, even though its vocabulary is appealing.
- **Beat This!** has no built-in tempo head; derive tempo + time signature from beats/downbeats. Verify this is reliable on tempo-changing songs (probably fine for a first cut — defer rubato handling).
- **Triplet/swing detection** is not solved by any of these models out of the box; a quantization-time grid search over {straight, swing, triplet} on each bar is the standard hack.

## Sources

- [ADTOF GitHub (MZehren)](https://github.com/MZehren/ADTOF) — primary repo
- [ADTOF Signals 2023 paper (MDPI)](https://www.mdpi.com/2624-6120/4/4/42) — 5-class CRNN
- [STAR Drums TISMIR 2025](https://transactions.ismir.net/articles/10.5334/tismir.244) — 18-class vocab, F=0.67 on MDB
- [Understanding Performance Limitations in ADT, ISMIR 2025](https://zenodo.org/records/17706523) — toms/cymbals are hard
- [Enhanced ADT via Drum Stem Separation, arXiv 2509.24853](https://arxiv.org/abs/2509.24853) — Demucs+ADTOF+DrumSep recipe
- [Noise-to-Notes, arXiv 2509.21739](https://arxiv.org/abs/2509.21739) — diffusion-based ADT, no code
- [Inverse Drum Machine, arXiv 2505.03337 / TASLP 2026](https://github.com/bernardo-torres/inverse-drum-machine) — Apache-2.0
- [YourMT3+, MLSP 2024](https://arxiv.org/abs/2407.04822) — GPL-3.0 multi-instrument
- [MT3 GitHub](https://github.com/magenta/mt3) — JAX, drums coarse
- [Magenta OaF Drums (archived)](https://magenta.tensorflow.org/oaf-drums)
- [Beat This! GitHub](https://github.com/CPJKU/beat_this) — MIT, ISMIR 2024 SOTA
- [Beat This! arXiv 2407.21658](https://arxiv.org/abs/2407.21658)
- [BEAST ICASSP 2024](https://arxiv.org/abs/2312.17156) — online beat tracker
- [All-in-One Music Structure Analyzer GitHub](https://github.com/mir-aidj/all-in-one) — MIT, beats+structure
- [BeatNet GitHub](https://github.com/mjhydri/BeatNet) — online CRNN
- [madmom GitHub](https://github.com/CPJKU/madmom) — unmaintained, Python ≤3.9
- [LarsNet GitHub (polimi-ispl)](https://github.com/polimi-ispl/larsnet) — CC-BY-NC, 5 drum sub-stems
- [Demucs GitHub (Facebook Research)](https://github.com/facebookresearch/demucs) — HT-Demucs v4

## Glossary

Quick lookup for acronyms and technical terms used above. For conceptual background, see `docs/mir-primer.md`.

**ablation study** — Experiment that disables one part of a model to measure its contribution. "Demucs ablation" = "what's the score if you skip Demucs?"

**ADT (Automatic Drum Transcription)** — The task: audio → list of (time, drum-class). Subtypes: ADT-DTM works on full mixes (hard), ADT-DTD works on isolated drums (easier).

**ADTOF** — Drum transcription system by Zehren et al. (Signals 2023). 5-class CRNN, current open SOTA on the DTM task. Weights are CC-BY-NC-SA.

**arXiv** — Preprint server at arxiv.org. Papers appear here months before ISMIR/ICASSP publication.

**Beat This!** — Beat tracker from CPJKU/Vienna (ISMIR 2024), MIT-licensed, current 2024 SOTA. Replaces madmom and BeatNet.

**BeatNet** — Online (real-time) beat tracker, ISMIR 2021. AGPL.

**Cheuk et al. 2024 recipe** — The trick of stacking Demucs → ADTOF → drum sub-stem separator to expand from 5 to 7 drum classes via decay/envelope features. arXiv:2509.24853.

**CC-BY-NC, CC-BY-NC-SA** — Creative Commons licenses with "Non-Commercial" restriction. Block commercial deployment. SA = derivatives must use the same license.

**CRNN (Convolutional Recurrent Neural Network)** — A CNN runs over the spectrogram per-frame to extract features; an RNN (LSTM/GRU) then captures temporal context. Workhorse architecture for ADT.

**DBN (Dynamic Bayesian Network)** — Probabilistic post-processing for older beat trackers (madmom) to enforce coherent tempo structure. Newer models (Beat This!) don't need it.

**Demucs** — Source separation model from Facebook Research. Separates a mix into 4 stems (drums, bass, vocals, other). `htdemucs` is the v4 hybrid-transformer variant; `_ft` = fine-tuned.

**diffusion model** — Class of generative ML model that progressively denoises random input into structured output. Used for image gen (Stable Diffusion) and increasingly audio.

**downbeat** — The first beat of a bar (typically beat 1 in 4/4). Beat trackers output beats + per-beat "is this a downbeat?" flags.

**DTM (Drums-in-Mix)** — ADT setting where drums are mixed with other instruments. Contrast with DTD (drums-only audio). DTM is harder.

**E-GMD** — Expanded Groove MIDI Dataset (Magenta). ~444 hours of drum audio + MIDI. Open license. Used to train OaF Drums.

**ENST-Drums** — Drum dataset from École Nationale Supérieure des Télécommunications. ~80 minutes multi-track. Long-standing ADT benchmark.

**F-measure / F1 / F-pts** — Harmonic mean of precision and recall: F1 = 2·P·R / (P+R). "F-pts" = "F1 points." "+12 F-pts" = absolute F1 improvement of 0.12.

**fps (frames per second, in audio)** — How often the model produces an output. 100 fps = one prediction per 10 ms.

**frame, hop** — Spectrograms are computed over short windows; hop = sample stride between windows. Hop = 441 at 44.1 kHz → 100 fps.

**GM MIDI (General MIDI)** — Standard mapping of drum sounds to MIDI note numbers: 36 = kick, 38 = snare, 42 = closed hat, 44 = pedal hat, 46 = open hat, 49 = crash, 51 = ride.

**htdemucs, HT-Demucs** — Hybrid Transformer Demucs. The v4 Demucs architecture; won the 2021 Music Demixing Challenge.

**IBI (Inter-Beat Interval)** — Time between consecutive beats. BPM = 60 / IBI (in seconds).

**ICASSP** — IEEE International Conference on Acoustics, Speech, and Signal Processing. Major audio venue, broader than MIR.

**Inverse Drum Machine (IDM)** — Model that jointly separates and transcribes drums. TASLP 2026, Apache-2.0. Currently evaluated only on solo drums.

**ISMIR** — International Society for Music Information Retrieval. Main academic conference for MIR.

**JAX, T5X** — Google's JAX = numerical computing library; T5X = transformer-training library on top. Less common on Apple Silicon than PyTorch.

**LarsNet** — Drum sub-stem separator from Polimi-ISPL. Splits a drums stem into 5 sub-stems (kick, snare, hi-hat, toms, cymbals). CC-BY-NC weights.

**logits** — Pre-softmax outputs of a neural net. Useful as raw confidence values before normalization to probabilities.

**madmom** — Long-running MIR library from CPJKU. Beat tracking, onset detection, key, etc. Effectively unmaintained as of 2026; broken on Python ≥3.10.

**MDB, MDB-Drums** — MedleyDB and its drum-annotated subset. ~100 multi-track songs, used as ADT benchmark.

**mel-spectrogram, mel-spec** — Spectrogram with frequency bins warped to the mel scale (matching human pitch perception). Standard ML input.

**MERT** — Music-audio Representation Transformer. A pretrained audio encoder used by some 2025 transcription models.

**MIR (Music Information Retrieval)** — The field: extracting structured information from music audio. ADT, beat tracking, key, structure all fall under MIR.

**MLSP** — Machine Learning for Signal Processing workshop. Smaller venue than ICASSP.

**MoE (Mixture of Experts)** — Neural arch with multiple sub-networks; routing chooses which to run per input. Used in some recent transcription models.

**MPS (Metal Performance Shaders)** — Apple's GPU compute framework. PyTorch's MPS backend runs models on Apple Silicon GPUs without CUDA.

**MT3** — Multi-Task Multitrack Music Transcription. Token-based transformer from Magenta, JAX-based. Transcribes multiple instruments at once but drum vocab is coarse.

**NATTEN** — Neighborhood Attention extension for PyTorch. Required by All-in-One Music Structure Analyzer.

**NMS (Non-Maximum Suppression)** — Postprocessing: when a model fires high probability across adjacent frames, keep only the peak. Standard in CV; also used for onsets.

**OaF Drums (Onset and Frames Drums)** — Magenta's 2020 drum transcriber. Trained on E-GMD. TF1 codebase; Magenta repo archived January 2026.

**onset detection** — Finding the *time* of each note attack. Output is timestamps, no labels.

**peak-picking** — Finding local maxima in a time-series prediction above a threshold and with minimum spacing.

**precision, recall** — For onset detection: precision = correct predictions / all predictions. Recall = correct predictions / all true onsets. "Correct" if within tolerance (±50 ms) and right class.

**RBMA (Red Bull Music Academy)** — Drum dataset heavy on electronic / synthetic kits. Tough ADT benchmark; separation often *hurts* on this.

**seq2seq** — Sequence-to-sequence model. Maps a sequence to a (possibly different-length) sequence.

**Slakh** — Synthesized Lakh dataset. MIDI rendered through software instruments to create labeled multi-track audio. Used to train multi-instrument transcribers.

**spectral centroid** — A feature measuring "center of mass" of a spectrum. High = bright (cymbals); low = dark (kick).

**spectrogram** — 2D representation of audio: time × frequency × magnitude. Computed via Short-Time Fourier Transform (STFT).

**STAR Drums** — A 2025 drum dataset (TISMIR Jul 2025) with 18 fine-grained drum classes including pedal hi-hat and three tom registers.

**stem, sub-stem** — Stem = one separated track from a mix (e.g., "drums stem"). Sub-stem = further-decomposed stem (e.g., "snare sub-stem from the drums stem").

**StemGMD** — Stem variant of GMD dataset. Solo drums with separated per-instrument stems.

**TASLP** — IEEE/ACM Transactions on Audio, Speech, and Language Processing. Journal.

**TF1 (TensorFlow 1.x)** — Legacy TensorFlow version with static-graph API. Largely abandoned for TF2 / PyTorch.

**TISMIR** — Transactions of ISMIR (journal). More polished version of ISMIR conference papers.

**token-based transformer** — Model that outputs a sequence of discrete symbols (tokens), one at a time. MT3 is one.

**tolerance window** — Time slack within which a predicted onset counts as correct. Standard is ±50 ms.

**transient** — The very short, high-amplitude burst at the start of any percussive sound. Drum hits are mostly transient.

**transient smearing** — Separation artifact where Demucs blurs a sharp attack into earlier frames. Hurts cymbal/hi-hat distinction.

**YourMT3+** — A 2024 multi-instrument transcriber based on MT3, GPL-3.0 licensed.
