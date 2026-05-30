# v2 backlog — deferred from v1

Items explicitly out of scope for v1 that we want to revisit when the v1 pipeline is working end-to-end. Ordering is rough; revisit when re-scoping.

## Drum vocabulary

- **`hihat_chick` detection** — foot-pedal close with no stick hit. Schema supports it; v1 transcriber doesn't emit it. Approaches: heuristic over LarsNet hi-hat sub-stem (low stick-spectrum energy when hat closes after open), or fine-tune a classifier on STAR Drums' 18-class corpus.
- **Tom pitch distinction (`tom_high` / `tom_mid` / `tom_low`)** — v1 emits every tom hit as `tom_mid`. Approaches: spectral-centroid features from LarsNet's tom sub-stem (cheap, fragile), or fine-tune on STAR Drums' multi-tom labels.

## Notation richness

- **Triplet / tuplet detection** — schema supports tuplet groups with cross-instrument `group` IDs. v1 quantization snaps everything to straight 16ths. Approach: per-bar grid search over {straight, swing, 8th-triplet, 16th-triplet} subdivisions, pick the lowest-residual fit.
- **Ghost notes** — quieter snare hits that carry musical meaning. Detect via per-hit velocity from ADTOF logits and a low-confidence threshold. Notation: parenthesised note heads.
- **Dynamics** — accents, crescendos, mf/f sections. Mostly an emit-stage concern once we expose per-hit velocity.
- **Swing / shuffle feel** — top-level "feel" annotation on the score. Detect via IOI ratios on consecutive 8ths within a bar.

## Robustness & licensing

- **Replace ADTOF + LarsNet for commercial readiness** — both have CC-BY-NC weights. For commercial use: (a) retrain a CRNN on permissively-licensed data (E-GMD, Slakh, STAR Drums), or (b) switch to Inverse Drum Machine (Apache-2.0) once full-mix benchmarks land. See `docs/research/2026-05-pipeline-survey.md` Risk 1.
- **Genre-specific calibration** — Demucs separation artifacts vary by genre. Cheuk et al. 2024 saw +12 F1 on rock/pop but −2 on electronic kits. Calibrate confidence thresholds per genre or per-song.
- **Tempo changes / rubato** — Beat This! handles steady tempo well; rubato is harder. Defer until we see real failures on real songs.

## Cross-bar features

- **Cross-bar `sustain_until`** — schema already allows values past the originating bar (open hat ringing across bar lines). Renderer needs a sustain-tie pass to draw a tie/decoration spanning bars. Not load-bearing for v1 since the implicit "next hat event" rule covers most cases.

## Manual review & editing UI

A transcription is rarely perfect. v1 produces `events.json` and stops; v2 should let the user verify and correct against the drum-only audio.

- **Save the drum stem alongside `events.json`** — Demucs is already producing it; today we discard. Write it to `<output>.drums.wav` (or similar) so the frontend has something to play back. Cheap backend change: one file write in the separation stage's wrapper.
- **Synced audio playback in the frontend** — load both `events.json` and `<output>.drums.wav`. Highlight the current bar / note as the audio plays. Smooth-scroll the score so the playhead stays in view.
- **Click-to-seek** — clicking a note (or empty bar position) jumps audio playback to that time. Quick way to verify "does this hit really sound like a snare?"
- **Loop region selector** — drag-select a bar range; audio loops over that region so the user can listen repeatedly while editing. Essential for working out tricky fills.
- **Manual edit affordances on the score** — change a note's instrument (drag to a different staff line, or keyboard shortcut), add/remove notes, change duration. Edits flow back into `events.json` (persisted on save).
- **Edit history / undo** — every edit is a discrete operation that can be undone. Probably an `operations.json` sidecar so we don't have to diff JSONs.
- **Confidence-driven highlighting** — notes with low transcriber confidence rendered in a different color or with a `?` badge, so the user knows where to focus review effort.

This is mostly a frontend project but implies two backend changes: (a) persist the drum stem from the separation stage, and (b) possibly emit a richer events.json that retains the raw onset times alongside the quantized positions, so edits can re-quantize without re-running the pipeline.

## Notes for future-me

Whenever picking up a v2 item, re-read `docs/research/2026-05-pipeline-survey.md` first — the SOTA picture may have shifted, and what's "best available" in 2026 may not be best in 2027. The Inverse Drum Machine project is the one most worth re-checking; if its full-mix benchmarks become competitive with ADTOF, the licensing problem disappears.
