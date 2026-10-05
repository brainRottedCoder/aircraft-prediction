# Fleet Digital Twin — backend-local targets.
#
# The repository-root Makefile is the supported entry point: it runs everything through
# Docker so no local Python environment is required. This file exists for the case where
# you are iterating on the API with an editable install already active.

PY      ?= python3
PIP     ?= $(PY) -m pip

.PHONY: help venv install migrate seed run test test-unit test-all lint format typecheck check clean

help: ## Show this help
	@grep -E '^[a-z-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-12s %s\n", $$1, $$2}'

venv: ## Create .venv with dev extras
	$(PY) -m venv .venv

install: ## Editable install with dev extras
	$(PIP) install -e ".[dev]"

migrate: ## Apply migrations
	alembic upgrade head

seed: ## Idempotent seed (safe to re-run)
	$(PY) -m app.seed.run

run: ## Dev server (single worker: the replay engine is in-process)
	uvicorn app.main:app --reload --workers 1 --port 8000

test: ## Full suite (needs a reachable disposable PostgreSQL — see conftest.py)
	pytest -q

test-unit: ## Pure domain tests, no database
	pytest tests/unit -q

test-all: ## Everything CI runs
	$(MAKE) lint typecheck test

lint: ## ruff
	ruff check app tests scripts

format: ## ruff formatter
	ruff format app tests scripts

typecheck: ## mypy --strict on the pure layers
	mypy app/core app/domain app/ml

check: lint typecheck test   ## everything CI runs

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache seed_reconciliation.log