# MIR for engineers: a primer for sheetydrums

You're reading this because the research doc (`docs/research/2026-05-pipeline-survey.md`) assumes working knowledge of Music Information Retrieval, and that's a domain most software engineers haven't touched. This primer builds the conceptual stack you need, in the order you need it. No math; intuitions and analogies wherever possible. For specific acronyms and dataset names, the **Glossary** at the bottom of the research doc has them.

## What MIR is

Music Information Retrieval (MIR) is the field of extracting useful information from music audio: beats, tempo, key, chords, drums, structure, etc. Think of it as "computer vision but for sound": you have a signal that's rich and human-meaningful, and you want a model to surface a structured representation of what's in it.

The big difference from CV: audio is *time-series*. A song is 3–5 minutes of 44,100 samples per second per channel — millions of numbers. You can't throw a CNN at the raw waveform and get good results (people try; it's hard). You need to *featurize* it first.

## Audio basics in 60 seconds

Audio is just amplitude over time. A WAV file is at heart an array of floats from -1.0 to 1.0 at, say, 44,100 samples per second. Stereo = two parallel arrays.

For analysis, the time domain is brutal — it's hard to see anything in a raw waveform. So we move to the frequency domain via the Fourier transform: any audio chunk can be expressed as a sum of sine waves at different frequencies. The Short-Time Fourier Transform (STFT) does this on short overlapping windows (say 25 ms each), giving you a **spectrogram**: a 2D array with time on one axis, frequency on the other, magnitude as the value.

A spectrogram is to audio what a thumbnail is to a video. ML models live on spectrograms, not raw waveforms.

## The mel-spectrogram

Humans don't hear frequency linearly. A jump from 100 Hz to 200 Hz sounds like the same interval (an octave) as 1000 Hz to 2000 Hz, even though the linear gaps are different orders of magnitude. The *mel scale* warps frequency to match human perception: bins are dense at low frequencies, sparse at high.

A **mel-spectrogram** is a spectrogram with mel-scaled frequency bins. This is the standard input to audio ML models. When you read "the model takes a mel-spec," picture a wide, short image (time × 80–128 mel bins). Each column is one frame's worth of frequency content.

## The frame abstraction

Continuous audio becomes a sequence of *frames*. If the STFT uses a hop of 441 samples at 44.1 kHz, you get 100 frames per second. The model's output is also frame-aligned: at every frame, you might get "probability that a kick drum starts here" or "probability that this frame is a beat."

This is why MIR has its own time unit: frames per second (fps). 100 fps is standard. The model fires a prediction every 10 ms. When the research talks about "frame resolution" or "100 fps frame predictions," this is what it means.

## The two model families you'll see

**CRNN (Convolutional Recurrent Neural Network)**: a CNN runs over the spectrogram like a filter, extracting features at each frame. An RNN (usually LSTM or GRU) then runs over those frame-level features to add temporal context — "this frame looks like the start of a snare, *and* the previous 3 frames had quiet hi-hat." CRNN is the workhorse of MIR; most ADT systems are CRNNs.

**Transformer**: same problem shape (sequence in, sequence out), but the temporal modeling is attention-based rather than recurrent. Transformers are slowly displacing CRNNs in MIR; Beat This! is one.

You don't need to understand the internals to use them. Both ingest a spectrogram and output frame-aligned predictions. Think of them as functions: `mel_spec → per_frame_probabilities`.

## ADT: how drum transcription actually works

The task: given an audio file, produce a list of `(time, drum_class)` pairs.

**Old approach (2010s):**
1. *Onset detection* — find every place a percussive event starts. Output: list of times, no labels.
2. *Classification* — for each onset, classify what drum it is.

This works but is fragile. Errors compound: if onset detection misses a hit, the classifier never sees it. If the classifier is confused, the onset is there but mislabeled.

**New approach (2020s):**
- **Joint onset + classification**: one model outputs, at every frame, the probability of a kick / snare / hi-hat / etc. starting *here*. Post-process by peak-picking each class independently.

This is what ADTOF does. It's a CRNN that, instead of outputting one probability per frame, outputs 5 (one per drum class). The "drum was hit here" decision and the "what drum was it" decision happen in the same model. This is *the* big architectural shift that the research doc is referring to when it says "drop the standalone onset and classification stages."

## Source separation upstream

ADT models trained on solo drums perform badly on full mixes — they confuse vocals for snares, bass thumps for kicks. So in 2024 the standard is to run a *source separator* (Demucs) first, isolate the drums stem, then run ADT on the drums alone.

Demucs uses a different architecture (U-Net with a transformer middle) and was trained on a different task (recover the isolated stems from a mix). When you stack it upstream of ADT, you trade some artifacts (transient smearing on cymbals, mainly) for a massive interference reduction. Net win on rock/pop/jazz, small loss on electronic kits.

## Sub-stems: getting class richness for free

ADTOF outputs only 5 classes. It merges open + closed hi-hat into one "hi-hat" class; ride + crash into "cymbal." That's a vocabulary problem for us. But once you have the drums stem, you can run a *second* separator that splits drums into per-class sub-stems (LarsNet does this). With the per-class sub-stem available, you can answer "was this hi-hat hit open or closed?" by looking at how long it sustains in the hi-hat sub-stem: open hats ring out, closed hats decay fast.

That's the Cheuk et al. 2024 trick. It's why we're using both ADTOF and LarsNet: ADTOF for the temporal precision and onset detection, LarsNet for the class refinement. Neither model alone gets us to 7 classes; together they do.

## Beat tracking, briefly

The task: given an audio file, output a list of beat times. Ideally also flag which beats are *downbeats* (start of a bar). Same general approach as ADT: spectrogram → neural net → frame-aligned predictions → peak-pick. Beat This! is a transformer; madmom was a CRNN + DBN postprocessing (the DBN enforced coherent tempo).

Tempo falls out for free once you have beats: median of `60 / inter_beat_interval` over a window. Time signature: count beats between consecutive downbeats. (Both break on rubato and complex meters; we'll defer those.)

## How we evaluate

The standard metric is **F-measure / F1**: harmonic mean of precision and recall, calculated with a tolerance window (typically ±50 ms). A predicted onset counts as "correct" if it's within the window of a true onset *and* labeled with the right class.

F1 = 0.85 means "85% F1." Improvements are reported in "F-points": "+12 F-pts on MDB" means absolute F1 went up by 0.12 on the MDB-Drums benchmark dataset.

Precision and recall mean what they always do:
- **Precision** = (correct predicted onsets) / (all predicted onsets). "Of the hits the model said it found, how many were real?"
- **Recall** = (correct predicted onsets) / (all true onsets). "Of the real hits, how many did the model find?"

A model with high precision and low recall is conservative (says "drum hit!" rarely but is usually right). High recall, low precision is enthusiastic (says "drum hit!" often, including false alarms). F1 balances them.

## What makes this hard

Most of the unsolved or fragile bits in ADT:

- **Polyphony**: kick + snare + hi-hat hit at the same time. Joint models handle it, but small simultaneous hits (a ghost note hidden under a snare) get masked.
- **Tom pitch distinction**: a "tom" is on a continuous pitch spectrum; calling it "high/mid/low" is a quantization that depends on the kit.
- **Cymbal disambiguation**: hi-hat open vs. ride vs. crash live in similar spectral bands. Sub-stem separation helps but isn't perfect.
- **Rare events**: pedal hi-hat ("foot chick") is acoustically similar to a soft kick. Models without enough labeled training examples confuse them. This is exactly why we deferred `hihat_chick` to v2.
- **Genre variation**: an electronic kit sounds nothing like a jazz kit. Cross-genre generalization is poor; benchmark scores reported on rock/pop don't transfer.

## Mapping back to sheetydrums

Concretely, our pipeline:

| Stage | Model | What it does |
|---|---|---|
| Separation | Demucs v4 | Full mix → drums-only stem |
| Drum transcription | ADTOF | Drums stem → 5-class onsets |
| Sub-stem separation | LarsNet | Drums stem → 5 per-class sub-stems |
| Class expansion | custom rules | 5 classes + sub-stem features → 7 classes |
| Beat tracking | Beat This! | Full mix → beats + downbeats |
| Quantization | custom code | Onsets + beat grid → bars with positions |

Reading the research doc, you should now be able to interpret claims like:

- *"ADTOF gets F=0.84 on MDB at 5 classes"* — A CRNN trained on the ADTOF dataset, evaluated on MedleyDB-Drums, scores 0.84 on the F-measure metric for joint 5-class onset + classification.
- *"Demucs adds +12 F-pts but −2 on RBMA"* — Upstream separation helps on most data (rock/pop benchmarks) but hurts by 2 F-points on the electronic-kit benchmark.
- *"Beat This! doesn't need a DBN"* — The transformer's predictions are coherent enough that the old Dynamic Bayesian Network postprocessing (used by madmom) is unnecessary.

## Going deeper

If you want to go beyond intuition, the most readable starting points (no math required):

- **The original Demucs paper** (Défossez et al., 2019) — clearly written, light on math. arxiv.org/abs/1911.13254
- **The ADTOF paper** (Zehren et al., Signals 2023) — directly relevant; good explanation of CRNN ADT. mdpi.com/2624-6120/4/4/42
- **librosa documentation** — the practical Python intro to MIR audio handling. librosa.org
- **ISMIR proceedings** — free and open at ismir.net. The "tutorials" track is the most accessible entry point.

You don't need to read these to follow the project. They're for when something in the research doc nags at you and you want a deeper answer.
