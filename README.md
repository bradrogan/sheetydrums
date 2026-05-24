# sheetydrums

Listen to a song, get drum sheet music.

Takes a full song mix (mp3/wav) and produces an interactive drum-notation web view rendered with VexFlow.

## Pipeline

```
song.mp3 ─► Demucs (drums stem) ─► onset detection ─► 7-class classifier
                                                            │
                                  madmom/librosa (beats) ───┤
                                                            ▼
                                                      events.json ─► VexFlow web view
```

## Layout

- `schema/` — JSON Schema for the events the backend produces and the frontend consumes. The contract.
- `backend/` — Python pipeline: separation, onset detection, drum classification, beat tracking, quantization.
- `frontend/` — Vite + TypeScript + VexFlow web app. Loads `events.json` and renders the score.

The two halves talk only through `schema/events.schema.json`. Lock that and they can move independently.

## v1 scope

- **Input**: full song mixes
- **Output**: interactive web view (no PDF/MusicXML)
- **Drum vocab**: kick, snare, hi-hat (closed/open), ride, crash, toms (high/mid/low)
- **Out of scope for v1**: ghost notes, dynamics, swing detection
