# sheetydrums backend

Python pipeline. Takes a song, produces `events.json` matching `../schema/events.schema.json`.

## Stages

1. **Separation** — Demucs `htdemucs_ft` to extract the drums stem.
2. **Onset detection** — find every drum hit timestamp.
3. **Drum classification** — label each onset as one of the v1 vocabulary classes.
4. **Beat / tempo tracking** — establish the bar grid (madmom preferred; librosa fallback if madmom doesn't install cleanly on Apple Silicon).
5. **Quantization** — snap onset timestamps to the nearest 16th/32nd within the bar grid.
6. **Emit** — write `events.json`.

## TODO (skeleton phase)

- [ ] Pick packaging (`uv` is the current default choice)
- [ ] Walking-skeleton CLI: `sheetydrums transcribe song.mp3 -o events.json`
- [ ] Stage 1 minimal impl (Demucs) + smoke test
- [ ] Stub stages 2-5 with "everything is a snare on the downbeat" so the JSON is valid
- [ ] Real onset detection (librosa)
- [ ] Real classifier (ADTOF or train small CNN)
