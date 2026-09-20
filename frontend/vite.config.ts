import { defineConfig } from 'vite';

// Two-process dev: `vite` serves the UI on :5173 and forwards backend calls
// to the FastAPI server on :8000 (started via `uv run sheetydrums-serve`).
// SSE streams pass through untouched — http-proxy doesn't buffer responses.
// Every backend route prefix the frontend fetches (see src/api.ts) MUST be
// listed here; anything missing falls through to Vite's SPA fallback and
// returns index.html, so `resp.json()` blows up with an opaque parse error.
export default defineConfig({
  server: {
    proxy: {
      '/transcribe': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/jobs': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/projects': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/settings': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
