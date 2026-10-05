# Fleet Digital Twin — developer entry points.
#
# Everything here works from a clean checkout on a machine with Docker. Nothing needs a
# local Python or Node toolchain unless you are editing that half of the stack; see
# `make help-local` for those.

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE       ?= docker compose
WEB_PORT      ?= 8080
API_PORT      ?= 8000
PY            ?= python3
VENV          ?= backend/.venv
BIN           := $(VENV)/bin

.PHONY: help help-local up down restart logs ps rebuild migrate seed fresh setup fetch-data \
        dev-backend dev-frontend lint typecheck test test-unit test-web build-api db-test db-test-down \
        fetch-model \
        db-dump db-restore clean distclean

## ── stack ────────────────────────────────────────────────────────────────────

help: ## Show this help
	@echo ""
	@echo "  Fleet Digital Twin"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "  First run:  make setup   →   make up   →   http://localhost:$(WEB_PORT)"

help-local: ## Show the local-toolchain (no Docker) targets
	@echo "  make venv      create backend/.venv and install the API with dev extras"
	@echo "  make migrate   alembic upgrade head"
	@echo "  make seed      python -m app.seed.run"
	@echo "  make run       uvicorn --reload --workers 1 (single worker: replay is in-process)"
	@echo "  make web       vite dev server on :5173, proxying /api and /ws to the API"

## ── lifecycle ────────────────────────────────────────────────────────────────

setup: ## First-run bootstrap: .env, C-MAPSS dataset, images, migrations, seed
	@scripts/dev-up.sh

up: ## Start postgres + api + web in the background
	@$(COMPOSE) up -d --build
	@echo "web  → http://localhost:$(WEB_PORT)"
	@echo "api  → http://localhost:$(API_PORT)/docs"

down: ## Stop the stack, keeping the database volume
	@$(COMPOSE) down

restart: ## Recreate the containers
	@$(COMPOSE) up -d --force-recreate

rebuild: ## Rebuild both images from scratch and restart
	@$(COMPOSE) build --no-cache
	@$(COMPOSE) up -d

ps: ## Show container status
	@$(COMPOSE) ps

logs: ## Tail all service logs (make logs api=web to filter)
	@$(COMPOSE) logs -f --tail=100 $(SERVICE)

## ── data ─────────────────────────────────────────────────────────────────────

fetch-data: ## Download the NASA C-MAPSS telemetry into backend/data/cmapss
	@scripts/fetch-cmapss.sh

fetch-model: ## Download the trained boosters into backend/data/ml/$(FDT_ML_VARIANT)
	@$(COMPOSE) --profile bootstrap run --rm model-fetch

migrate: ## Apply database migrations
	@$(COMPOSE) run --rm --entrypoint alembic api upgrade head

seed: ## Load fleet, parts, spares, agencies, work orders and alerts (idempotent)
	@$(COMPOSE) run --rm --entrypoint sh api -c 'python -m app.seed.run'

fresh: ## Drop the database volume, then migrate and seed from scratch
	@$(COMPOSE) down -v
	@$(MAKE) up
	@$(MAKE) migrate
	@$(MAKE) seed

db-dump: ## Write a compressed SQL dump to backend/backup.sql.gz
	@$(COMPOSE) exec -T db pg_dump -U $${POSTGRES_USER:-fdt} $${POSTGRES_DB:-fdt} \
		| gzip > backend/backup.sql.gz
	@echo "wrote backend/backup.sql.gz"

db-restore: ## Restore backend/backup.sql.gz (destructive)
	@test -f backend/backup.sql.gz || { echo "no backend/backup.sql.gz — run 'make db-dump' first"; exit 1; }
	@$(COMPOSE) exec -T db psql -U $${POSTGRES_USER:-fdt} -d $${POSTGRES_DB:-fdt} \
		--clean --if-exists < <(gunzip -c backend/backup.sql.gz)
	@echo "restored"

## ── quality ──────────────────────────────────────────────────────────────────

lint: ## ruff over the API
	@$(MAKE) build-api
	@$(COMPOSE) run --rm --entrypoint ruff api check app tests scripts

typecheck: ## mypy --strict over the pure layers (core, domain, ml)
	@$(COMPOSE) build api
	@$(COMPOSE) run --rm --entrypoint mypy api app/core app/domain app/ml

# `build api` is load-bearing and must not be dropped.
#
# The api image COPYs backend/app and backend/tests (docker/backend/Dockerfile) and compose
# bind-mounts no source, so `docker compose run api pytest` executes whatever was baked in
# at the last build — not the working tree. Without this, a green suite can be reporting on
# code that no longer exists: a whole new test file was silently not collected twice while
# the count sat unchanged, and only the number moving gave it away.
build-api: ## Rebuild the api image so containers see the current source
	@$(COMPOSE) build api

test: ## Full pytest suite against a disposable database
	@$(MAKE) db-test
	@$(MAKE) build-api
	@$(COMPOSE) run --rm \
		-e FDT_TEST_DATABASE_URL='postgresql+psycopg://fdt:fdt@db-test:5432/fdt_test' \
		-e FDT_JWT_SECRET=test-secret-0123456789abcdefghijklmnop \
		-e FDT_ENVIRONMENT=development \
		-e FDT_DEMO_MODE=false \
		-e FDT_ML_FALLBACK=true \
		--entrypoint pytest api -q
	@$(MAKE) db-test-down

test-unit: ## Pure domain tests, no database
	@$(MAKE) build-api
	@$(COMPOSE) run --rm -e FDT_DEMO_MODE=false --entrypoint pytest api tests/unit -q

test-web: ## Frontend unit tests (node:test, no dependencies)
	@npm --prefix frontend test

db-test: ## Start the throwaway PostgreSQL the suite drops and recreates
	@$(COMPOSE) --profile test up -d db-test
	@for i in $$(seq 1 40); do \
		$(COMPOSE) exec -T db-test pg_isready -U fdt -d fdt_test >/dev/null 2>&1 && break; \
		sleep 1; \
	done
	@echo "throwaway database ready (compose service db-test)"

db-test-down: ## Stop the throwaway test database
	@$(COMPOSE) --profile test rm -sf db-test 2>/dev/null || true

## ── local development (no Docker) ────────────────────────────────────────────

venv: ## Create backend/.venv with the API and dev extras installed
	@$(PY) -m venv $(VENV)
	@$(BIN)/pip install --upgrade pip
	@$(BIN)/pip install -e "backend[dev]"
	@echo "activate with: source $(BIN)/activate"

dev-backend: ## uvicorn with reload (needs `make venv`; single worker by design)
	@cd backend && ../$(BIN)/uvicorn app.main:app --reload --workers 1 --port 8000

dev-frontend: ## vite dev server on :5173 proxying /api and /ws to the API
	@cd frontend && npm install && npm run dev

## ── housekeeping ─────────────────────────────────────────────────────────────

clean: ## Remove build output and caches
	@rm -rf frontend/dist
	@find backend -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache backend/seed_reconciliation.log
	@echo "cleaned"

distclean: ## clean + remove the database volume and local env files
	@$(MAKE) clean
	@$(COMPOSE) down -v --remove-orphans
	@rm -f .env backend/.env backend/backup.sql.gz
	@echo "removed volumes and local env files"