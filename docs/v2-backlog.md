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

## Notes for future-me

Whenever picking up a v2 item, re-read `docs/research/2026-05-pipeline-survey.md` first — the SOTA picture may have shifted, and what's "best available" in 2026 may not be best in 2027. The Inverse Drum Machine project is the one most worth re-checking; if its full-mix benchmarks become competitive with ADTOF, the licensing problem disappears.
