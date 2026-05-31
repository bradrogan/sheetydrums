# sheetydrums frontend

Vite + TypeScript + VexFlow. Loads `events.json` (produced by the Python backend) and renders an interactive drum score. Today this is a scaffold — it loads + parses + displays metadata, and renders a placeholder VexFlow stave to prove the rendering pipeline. Real drum-staff rendering is the next step.

## Setup

Requires Node ≥ 24 (for pnpm v11+) and pnpm. Install both via:

```
brew install node pnpm
```

Then in this directory:

```
pnpm install     # installs vite, typescript, vexflow, json-schema-to-typescript
pnpm run dev     # vite dev server on http://localhost:5173
pnpm run build   # generates types from schema, then tsc + vite build
```

## How types come from the schema

`schema/events.schema.json` at the repo root is the contract between the Python pipeline and this frontend. TypeScript types are **generated** from it, not hand-written:

```
pnpm run schema:gen   # writes src/generated/events.d.ts
```

This runs automatically as `prebuild`. If you change the schema, types regenerate on next build.

## Project layout

```
frontend/
├── package.json
├── pnpm-workspace.yaml   pnpm settings (esbuild build approval)
├── tsconfig.json         strict mode
├── vite.config.ts
├── index.html
├── public/
│   └── events.json       sample fixture, copied from schema/examples
└── src/
    ├── main.ts           fetch events.json, dispatch to renderer
    ├── render.ts         VexFlow rendering (currently scaffold)
    ├── style.css
    └── generated/
        └── events.d.ts   AUTO-GENERATED from the JSON schema; do not hand-edit
```

## How the static deployment works

There's no backend HTTP service. The frontend `fetch()`es a relative path:

```
fetch('./events.json') → ./events.json on the same origin
```

In dev (`pnpm run dev`), Vite serves `public/events.json` at that URL. In production you'd:

1. Run the backend CLI: `cd ../backend && uv run sheetydrums song.mp3 -o out/events.json`
2. Run `pnpm run build` here
3. Serve `dist/` and `out/events.json` side by side from any static server

## What's not done yet

- Percussion-clef rendering (kick on bottom, snare middle, hi-hat top, etc.)
- Stem-direction-by-voice handling
- Open hi-hat / ride-vs-crash glyph differentiation
- Audio playback (deferred to v2 — see `docs/v2-backlog.md` → "Manual review & editing UI")
- Click-to-seek, loop region selection (also v2)
