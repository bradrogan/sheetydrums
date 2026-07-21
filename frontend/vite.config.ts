import { defineConfig } from 'vite';

// Two-process dev: `vite` serves the UI on :5173 and forwards backend calls
// to the FastAPI server on :8000 (started via `uv run sheetydrums-serve`).
// SSE streams pass through untouched — http-proxy doesn't buffer responses.
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
    },
  },
});
