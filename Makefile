# Gate canônico de qualidade do AI Speaking Tutor.
# Um alvo para lembrar: `make check`. Sem PATH edit, sem comando decorado.

SHELL := /bin/sh

# `uv` pode viver em ~/.local/bin sem estar no PATH; o fallback evita editar o shell.
UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
NPM ?= npm
NPX ?= npx

.DEFAULT_GOAL := help

.PHONY: help lint test e2e build check

help: ## Lista os alvos disponíveis
	@echo "AI Speaking Tutor - gate canônico"
	@echo ""
	@echo "  make lint   ruff check no backend e em ../scripts"
	@echo "  make test   pytest -q (backend) + vitest run (frontend)"
	@echo "  make e2e    playwright test (frontend, backend mockado)"
	@echo "  make build  npm run build (tsc --noEmit + vite build)"
	@echo "  make check  lint + test + e2e + build, nesta ordem"
	@echo ""
	@echo "uv: $(UV)"

lint: ## ruff check em backend/ e scripts/
	cd backend && $(UV) run ruff check . ../scripts

test: ## pytest + vitest
	cd backend && $(UV) run pytest -q
	cd frontend && $(NPX) vitest run

e2e: ## Playwright (sem backend real)
	cd frontend && $(NPX) playwright test

build: ## tsc --noEmit + vite build
	cd frontend && $(NPM) run build

check: lint test e2e build ## Tudo, na ordem
	@echo "make check: lint, test, e2e e build verdes"
