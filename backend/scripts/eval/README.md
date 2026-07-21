# Evaluation scripts

Manual benchmarking of the sheetydrums pipeline against ground-truth drum tabs.

Why this exists, not in `tests/`: these scripts run the real pipeline against
real songs (network download, model inference, ~40 s+) and produce a *report*
for human inspection rather than a pass/fail signal. They're for debugging
accuracy regressions and validating v2 stages, not for CI.

## Structure

```
backend/scripts/eval/
├── README.md                                              this file
├── compare_treble_charger_brand_new_low.py                comparison script (one per song)
├── reference_tabs/
│   └── treble-charger-brand-new-low.txt                   the raw tab text from the user
└── baselines/
    └── treble-charger-brand-new-low.v1.events.json        v1 pipeline output snapshot for delta measurement
```

One script per song. Each script hard-codes the corresponding tab's bar-by-bar
encoding (repeat markers, alt endings, section labels) so adding a new song is
a manual transcription exercise — there's no general tab parser.

## Running

From the `backend/` directory:

```bash
# 1. Run the pipeline on the song's audio (URL or local file).
uv run sheetydrums "https://www.youtube.com/watch?v=7fcg6KeQjNI" -o /tmp/events.json

# 2. Score the output against the tab.
uv run python scripts/eval/compare_treble_charger_brand_new_low.py /tmp/events.json
```

By default the comparison scores **4 instruments** with v1's collapses
(`crash → ride`, `hihat_open → hihat_closed`). When the v2 sub-stem expansion
ships and the pipeline starts emitting `hihat_open` and `crash` as distinct
classes, run with `--full-vocab` to score the full 7-class vocab:

```bash
uv run python scripts/eval/compare_treble_charger_brand_new_low.py /tmp/events.json --full-vocab
```

## What the report shows

- **DP alignment** between tab and pipeline bars. Bars in the tab that DP
  can't match to any pipeline bar are reported as "over-expansion" — usually
  a sign that the repeat-marker interpretation in the script is wrong.
- **Per-instrument precision, recall, F1** on matched bars.
- **Per-section breakdown** so you can see whether errors concentrate in
  e.g. choruses vs. verses.
- **Tolerance sensitivity** (±0 to ±3 sixteenth-note slop) — separates real
  misses from grid-snap drift.

## Baselines (Brand New Low)

- `*.v1.events.json` — 5-class pipeline (no sub-stem branch). **F1 = 90.9%** on
  the 4-class collapsed vocab (kick 98%, snare 99%, hihat 85%, ride 85%).
  Crash was architecturally impossible (cymbal → ride at the quantizer).
- `*.v2.events.json` — 10-class pipeline with the MDX23C DrumSep sub-stem branch
  and `CheukExpander`. **v1-collapsed F1 = 90.8%** (no regression from v1).
  Full-vocab F1 lower (around 74%) — the hihat classifier on this song lands
  in the unimodal-loose bucket and labels every hat open. The tab uses
  lowercase x (closed) and uppercase X (loose) interchangeably within "Loose
  hihat" sections, which the decay-ratio feature can't separate. Future work:
  spectral-feature classifier or trained model for the hihat open/closed
  split. Crash detection works (0 → 67% F1 vs v1) and tom_low precision is
  100% on the floor toms we do detect.

- `ac-dc-back-in-black.v2.events.json` — Back in Black, no ground-truth tab
  saved. Numbers to spot-check: 100 bars at 93.8 BPM in 4/4; 100% of hihats
  labelled open (correct — Phil Rudd's iconic loose hat throughout the song);
  101 crash hits (vs 0 in v1's collapsed output); kick on beat 1 of 94/100
  bars (downbeat-spill fix from prior commit holding); modest tom presence
  (24 tom hits across 14 bars, no obvious tom fills missed).

## Adding a new song

1. Save the raw tab as `reference_tabs/<song-slug>.txt`.
2. Copy `compare_treble_charger_brand_new_low.py` to
   `compare_<song-slug>.py` and rewrite the `build_tab()` body to encode
   the new song's bars + repeats + alt endings.
3. Run the pipeline on the song's audio, save the events.json as the
   baseline under `baselines/<song-slug>.v1.events.json`.
