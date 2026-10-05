#!/usr/bin/env bash
# One-shot development bootstrap: env file, datasets, model artifacts, migrations, seed.
#
# Idempotent, and safe to re-run. Intended to be the first thing a new checkout runs:
#
#   scripts/dev-up.sh
#
# It does NOT start the containers — that is `make up` (or `docker compose up`), so you
# can inspect the output before anything binds a port.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose)
if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "[dev-up] docker compose is not installed" >&2
  exit 1
fi

step() { printf '\n\033[1m[dev-up] %s\033[0m\n' "$*"; }

step "1/5 environment"
if [[ ! -f .env ]]; then
  cp .env.example .env
  secret="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  # BSD and GNU sed disagree on -i, so write through a temp file instead.
  sed "s|^FDT_JWT_SECRET=.*|FDT_JWT_SECRET=${secret}|" .env >.env.tmp && mv .env.tmp .env
  echo "created .env with a generated FDT_JWT_SECRET"
else
  echo ".env already exists, leaving it alone"
fi

step "2/6 NASA C-MAPSS telemetry"
if compgen -G "backend/data/cmapss/*_FD001.txt" >/dev/null; then
  echo "already present"
else
  scripts/fetch-cmapss.sh
fi

step "3/6 build"
"${COMPOSE[@]}" build

# The boosters are not committed and C-MAPSS has a public mirror while the boosters do
# not, so this step is the only thing that gets a real model onto a fresh checkout. It
# runs after the build because the fetcher lives in the image. A failure here is not
# fatal: the API falls back to `rul = 125 - cycle` and /healthz reports `degraded`.
step "4/6 trained model artifacts"
if compgen -G "backend/data/ml/${FDT_ML_VARIANT:-all}/*.json" >/dev/null; then
  echo "already staged"
elif "${COMPOSE[@]}" --profile bootstrap run --rm model-fetch; then
  echo "staged"
else
  echo "could not fetch the boosters — the API will serve the deterministic fallback." >&2
  echo "Set FDT_ML_ARTIFACTS_URL in .env, or copy them in by hand:" >&2
  echo "  docker compose --profile bootstrap run --rm --entrypoint sh model-fetch \\" >&2
  echo "    -c 'python -m scripts.stage_ml_artifacts --source <dir> --variant ${FDT_ML_VARIANT:-all}'" >&2
fi

step "5/6 start database"
"${COMPOSE[@]}" up -d db
for _ in $(seq 1 30); do
  if "${COMPOSE[@]}" exec -T db pg_isready -U fdt -d fdt >/dev/null 2>&1; then break; fi
  sleep 1
done

step "6/6 migrate and seed"
"${COMPOSE[@]}" run --rm --entrypoint sh api -c 'alembic upgrade head && python -m app.seed.run'

printf '\n\033[1m[dev-up] done\033[0m  run `make up` and open http://localhost:8080\n'
echo "[dev-up] demo logins: commander / officer / viewer (passwords in backend/app/seed/run.py)"