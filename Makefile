SHELL := /bin/bash
.DEFAULT_GOAL := help
.PHONY: help install dev backend frontend test

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install backend (uv) + frontend (pnpm) dependencies
	cd backend && uv sync
	cd frontend && pnpm install

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
