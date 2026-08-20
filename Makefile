SHELL := /bin/bash
.DEFAULT_GOAL := help
.PHONY: help install dev backend frontend test verify-tuning browsers

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install backend (uv) + frontend (pnpm) dependencies
	cd backend && uv sync
	cd frontend && pnpm install

browsers: ## Install Playwright's headless Chromium (needed for `make verify-tuning`)
	cd frontend && pnpm exec playwright install chromium

dev: ## Run backend (:8000) + frontend (:5173) together — Ctrl-C stops both
	@echo "backend  → http://127.0.0.1:8000"
	@echo "frontend → http://localhost:5173"
	@trap 'kill 0' EXIT; \
	  ( cd backend && uv run sheetydrums-serve ) & \
	  ( cd frontend && pnpm run dev ) & \
	  wait

backend: ## Run just the backend API server (:8000)
	cd backend && uv run sheetydrums-serve

frontend: ## Run just the frontend dev server (:5173)
	cd frontend && pnpm run dev

test: ## Run the backend test suite
	cd backend && uv run pytest

verify-tuning: ## Visual-check the tuning panel headless (needs `make browsers` + a transcribed project)
	@echo "starting app + driving the tuning panel (screenshots → /tmp/tuning-*.png)…"
	@trap 'kill 0' EXIT; \
	  ( cd backend && uv run sheetydrums-serve >/tmp/sd_backend.log 2>&1 ) & \
	  ( cd frontend && pnpm run dev >/tmp/sd_vite.log 2>&1 ) & \
	  for i in $$(seq 1 60); do \
	    curl -sf http://127.0.0.1:8000/projects >/dev/null && \
	    curl -sf http://127.0.0.1:5173/ >/dev/null && break; sleep 1; done; \
	  cd frontend && node scripts/verify-tuning.mjs
