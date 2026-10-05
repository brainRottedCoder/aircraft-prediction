#!/usr/bin/env bash
# Migrate + seed the development database, in a one-shot container.
#
# Equivalent to `make migrate && make seed` but without needing a local Python
# environment. The seed is idempotent, so re-running is safe.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

exec docker compose run --rm --entrypoint sh api -c '
  set -e
  echo "[seed] applying migrations"
  alembic upgrade head
  echo "[seed] loading fleet data"
  python -m app.seed.run
  echo "[seed] done"
'