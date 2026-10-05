# Fleet Digital Twin — Project Brief

Onboarding write-up of this repository as it exists on disk. Written from a full
read of the source, tests, and design docs on 5 October 2026. Where a document
and the code disagree, the code is treated as current and the disagreement is
called out.

Product name in the README: **Fleet Digital Twin**. Repository folder:
`predictive-aircraft-maintenance`.

---

## 1. Project Overview

### What it does

This is a predictive-maintenance console for a fictional eight-aircraft fighter
fleet. Every 1.2 seconds the backend advances each aircraft by one flight cycle
of NASA's C-MAPSS turbofan dataset, scores the last 30 cycles with an XGBoost
model, turns the remaining useful life into risk bands and maintenance actions,
and pushes the result to a browser. The frontend draws it as a 3D aircraft you
can rotate and click into.

The operational question, from `README.md` lines 49–51:

> which aircraft cannot fly next week, and why?

### Who it is for

It is a demonstration operations console, not a certified maintenance system.
Three roles are seeded (`backend/app/seed/run.py` lines 25–29):

| Role | Username | Password | What they can do |
|---|---|---|---|
| Commander | `commander` | `commander123` | Reads, writes, seed, demo controls, ML reload, audit |
| Maintenance officer | `officer` | `officer123` | Reads and mutations (work orders, spares, bookings, alert ack) |
| Viewer | `viewer` | `viewer123` | Read-only |

These passwords are committed fixtures. The README says to remove or rotate
them before any real deployment (`README.md` lines 800–811).

### Tech stack

| Layer | Choice | Where |
|---|---|---|
| API | Python 3.11+, FastAPI, Uvicorn, Pydantic v2 | `backend/pyproject.toml`, `backend/app/main.py` |
| ORM / migrations | SQLAlchemy 2, Alembic, psycopg 3 | `backend/app/db/session.py`, `backend/alembic/` |
| Database | PostgreSQL 16 | `docker-compose.yml` |
| ML | XGBoost, NumPy, local artifacts | `backend/app/ml/` |
| Auth | JWT HS256 (`pyjwt`), bcrypt (`passlib`) | `backend/app/core/security.py` |
| Frontend | React 18, Vite 5, three.js 0.128 | `frontend/package.json` |
| Edge | nginx 1.27 as a same-origin reverse proxy | `docker/frontend/` |
| Ops | Docker Compose, root Makefile (bash), GitHub Actions | `Makefile`, `.github/workflows/ci.yml` |
| Alternate deploy | Render blueprint, one Python process serves API and the built SPA | `render.yaml` |

There is no Redis, no message queue, no separate ML microservice, and no
outbound network calls at runtime. The dataset and the model are local.

### Entry points

| Process | File | What starts |
|---|---|---|
| API | `backend/app/main.py` line 221 `app = create_app()` | Factory at line 163. Lifespan at line 74 loads the model, indexes C-MAPSS, optionally seeds, starts retention, starts replay. |
| REST | `backend/app/api/v1/router.py` lines 6–11 | Mounted from `main.py` line 210 |
| WebSocket | `backend/app/realtime/ws.py` `ws_fleet` at `/ws/fleet` | Mounted from `main.py` line 211 |
| Process command | `docker/backend/Dockerfile` | `uvicorn app.main:app --workers 1` |
| SPA | `frontend/index.html` → `frontend/src/main.jsx` → `frontend/src/App.jsx` | Login gate at `App.jsx` lines 48–56. Signed-in console starts the live loop at line 23. |
| Training | `ml/notebooks/Model_training_249.ipynb` | Offline. Not imported by the API. |

`--workers 1` is a hard invariant. The replay engine is an in-process asyncio
task. A second worker would advance every aircraft twice (`README.md` lines
139 and 563–564).

---

## 2. File and Folder Structure

```
predictive-aircraft-maintenance/
├── Makefile                      # bash. make setup / up / test
├── docker-compose.yml            # db + api + web
├── docker-compose.prod.yml       # production overrides
├── render.yaml                   # single-service Render blueprint
├── .env.example                  # every FDT_* variable
├── .github/workflows/ci.yml
├── README.md
├── docker/
│   ├── backend/Dockerfile
│   └── frontend/                 # nginx image + nginx.conf.template
├── backend/
│   ├── pyproject.toml
│   ├── alembic/versions/         # one migration, 20 tables
│   ├── app/
│   │   ├── main.py
│   │   ├── api/v1/               # auth, fleet, maintenance, telemetry, ops
│   │   ├── services/
│   │   ├── repositories/
│   │   ├── domain/               # pure rules
│   │   ├── ml/
│   │   ├── realtime/
│   │   ├── models/               # SQLAlchemy
│   │   ├── schemas/              # Pydantic, API only
│   │   ├── seed/
│   │   ├── db/
│   │   └── core/
│   ├── scripts/                  # stage / fetch ML artifacts
│   ├── tests/                    # unit, integration, ml, performance
│   └── data/
│       ├── raw/                  # fleet CSVs, committed
│       ├── cmapss/               # NASA telemetry; FD001 committed, rest fetched
│       └── ml/all/               # contract, baselines, metrics committed; booster is not
├── frontend/
│   ├── src/components/           # dashboard panels
│   ├── src/state/                # store, server sync, simulation
│   ├── src/lib/                  # API client, health, plan
│   ├── src/data/                 # fleet skeleton, part catalogue
│   ├── src/three/                # WebGL scene and subsystem models
│   ├── public/models/            # ~9 MB of GLB / bin.gz, committed
│   └── tests/                    # two Node tests
├── ml/notebooks/                 # training notebook
├── docs/                         # 15 design documents
└── scripts/                      # dev-up.sh, fetch-cmapss.sh, seed.sh
```

### What each major folder is for

| Path | One line |
|---|---|
| `backend/app/` | The running system: HTTP, rules, inference, replay, persistence. |
| `backend/alembic/` | Schema history. One revision creates all 20 tables. |
| `backend/data/raw/` | The eight maintenance CSVs the seed loads. |
| `backend/data/cmapss/` | Cycle-by-cycle engine telemetry the replay reads. |
| `backend/data/ml/` | Model contract and, when staged, the XGBoost booster. |
| `backend/tests/` | Pytest against real Postgres for integration, pure functions for unit. |
| `frontend/src/` | The operations console and the 3D twin. |
| `frontend/public/models/` | Aircraft, engine, radar, and landing-gear geometry. |
| `docker/` | Images. nginx is the browser's only origin in Compose. |
| `docs/` | Design record. Several files are older than the code. See section 5. |
| `ml/notebooks/` | How the booster is trained. The API does not run this. |
| `scripts/` | First-run bootstrap. |
| `.github/workflows/` | Lint, types, tests, image builds. No deploy job. |

### Core vs supporting

**Core (the product):** `backend/app/`, `backend/alembic/`, `backend/data/raw/`,
`frontend/src/`, `frontend/public/models/`.

**Supporting:** `docs/`, `backend/tests/`, `frontend/tests/`, `docker/`,
`scripts/`, `.github/`, `ml/notebooks/`, Makefiles, compose files.

### Generated, vendored, legacy

| Item | Status |
|---|---|
| `frontend/node_modules/`, `frontend/dist/`, `backend/.venv/`, `__pycache__/` | Build output. Gitignored. |
| `backend/alembic/versions/d50c12bdb5c9_*.py` | Generated migration. It is the schema. |
| `docs/05-database-design.prisma` | dbdiagram.io export. Nothing reads it (`docs/README.md` lines 15–18). |
| `frontend/public/models/*` | Third-party 3D assets, committed on purpose (not Git LFS). |
| `backend/data/raw/*.csv` | Project dataset, committed (~584 KB). |
| NASA C-MAPSS | FD001 text files are committed. FD002–FD004 are gitignored; `make fetch-data` downloads them (`.gitignore` lines 37–44). |
| `xgboost_all_full.json` | The booster itself is **not** in the tree. Contract, baselines, and metrics under `backend/data/ml/all/` are. Without the booster the API serves the fallback curve. |
| Old paths `backend/backend/` and `models_artifacts/` | Mentioned in docs 10, 14, and 15 as history. Those directories are gone. |
| `scripts/train_rul.py` | Described in `docs/14` and `docs/15` §7. **The file is not in the repo.** Training is the notebook only. |

`frontend/src/state/simulation.js` `fallbackTick` (lines 26–37) is intentional
offline motion when the API is down. It is not dead code.

---

## 3. Architecture and How It Works

### Shape

A **modular monolith** behind an **nginx backend-for-frontend**. One browser
origin serves the SPA, the REST API, and the WebSocket, so the frontend never
bakes in an API host.

```
browser
  └─ web · nginx :8080
       /            static SPA + hashed assets
       /models/*    3D geometry (gzip off for .gz — the client gunzips)
       /api/*       ─┐
       /ws/fleet    ─┤  same origin
       /healthz     ─┘
                     ▼
       api · FastAPI :8000          uvicorn --workers 1
         api/v1      → services → repositories → PostgreSQL 16
         domain/     pure rules, no I/O
         ml/         XGBoost, loaded once at startup
         realtime/   replay task + in-process event bus
                     ▼
                  subscribers on /ws/fleet
```

On Render there is no nginx. `create_app` mounts the Vite `dist/` itself
(`main.py` `_mount_frontend`, lines 111–160) on `/`, `/assets`, and `/models`
only, so a static mount cannot swallow an API route.

Dependency direction, from the README (lines 146–161): `api` → `services` →
`repositories` → `models`. `domain/` and `ml/` are called by services and by
the replay engine. `domain/` imports only the standard library and Pydantic.
`api/` does not issue SQL.

### Startup (`lifespan`, `main.py` lines 74–108)

1. `configure_logging`
2. `model_store.load` — XGBoost once. If the booster is missing, log a warning and keep the deterministic fallback.
3. `model_store.warmup` when the handle is ready
4. `bus.bind_loop`
5. `cmapss.load` — in-memory index of the telemetry files
6. `_seed_if_empty` if `FDT_SEED_ON_BOOT` (accounts and fleet; lines 35–71)
7. `RetentionPruner.start` — deletes old telemetry so a small disk does not fill
8. `start_replay`
9. On shutdown: `stop_replay`, then stop the pruner

### The main use case: one live tick

This is the path that keeps the console moving. It is not an HTTP request.

`ReplayEngine._advance` in `backend/app/realtime/replay.py`:

1. Pick the next C-MAPSS cycle for that aircraft's bound engine (`seed/cmapss.py`, in memory, not a table).
2. Take a 30-cycle window.
3. `asyncio.to_thread(inference.predict, ...)` so XGBoost does not block the event loop.
4. Smooth engine health with an EMA, α = 0.3 (`domain/health.py` `EmaState`, `EMA_ALPHA` line 14).
5. Apply rules 19–26 (`domain/rules.py`).
6. `telemetry_repo.write_engine_prediction` writes, in **one transaction per aircraft**:
   - `engine_telemetry`
   - `component_health`
   - `ml_predictions`
   - `health_snapshots`
   - the denormalized `aircraft` / `aircraft_part` row the UI reads
7. `bus.publish` emits `cycle.tick`, `health.updated`, and `alert.raised` when the risk band changes.

A slow browser is dropped rather than allowed to stall the fleet
(`ws_queue_size` default 256 in `config.py` line 48). Close code 1013 means
the client was too slow.

### The read path (what the panels actually call)

Reads do **not** run the model. They return rows the last tick already wrote.

```
React panel
  → frontend/src/lib/api.js   Bearer JWT, base /api/v1
  → request_context middleware   main.py lines 187–196, X-Request-ID
  → current_user                 api/deps.py, role loaded from the database
  → fleet.py / maintenance.py handler
  → fleet_service / maintenance_service
  → fleet_repo
  → PostgreSQL
  → Pydantic response_model
```

`frontend/src/state/simulation.js` `startSimulation` (line 63):

- Opens `/ws/fleet` for live engine values.
- Polls fleet state on a short interval via `state/server.js`.
- Polls alerts every 10 seconds (`ALERT_POLL_MS`, line 45) so another operator's acknowledgement shows up.
- If the API is unreachable, `fallbackTick` wears the local skeleton. It refuses to touch any aircraft that already has a server RUL (line 30).

### The ingest path (production-shaped, not what the demo UI uses)

`POST /api/v1/telemetry` → `telemetry_service.ingest` (`telemetry_service.py` line 46):

- resolve aircraft, or 404
- `build_rows` (imputes sensor `s6`)
- `inference.predict`
- `telemetry_repo.write_engine_prediction`
- sync the engine alert, commit
- publish the same bus events the replay uses
- return **202** with RUL, health, model version, latency

`POST /api/v1/internal/ml/predict` scores a window and can optionally persist.
The React app does not call either route. The demo is the replay engine.

### Core modules

| Module | Responsibility |
|---|---|
| `app/main.py` `create_app`, `lifespan` | Process lifecycle, CORS, error envelope, optional SPA mount |
| `app/api/deps.py` | DB session, `CurrentUser`, `CanMutate`, `CommanderOnly` |
| `app/api/v1/auth.py` | Login, `/me` |
| `app/api/v1/fleet.py` | Fleet, aircraft, engine, part, heatmap, actions, schedule |
| `app/api/v1/maintenance.py` | Work orders, spares, agencies, alerts |
| `app/api/v1/telemetry.py` | Ingest and ad-hoc predict |
| `app/api/v1/ops.py` | `/healthz`, `/readyz`, seed, ML reload, demo controls, audit |
| `app/services/fleet_service.py` | Read use cases. `fleet_summary` line 51, `engine_detail` line 133, `maintenance_schedule` line 359 |
| `app/services/maintenance_service.py` | Writes. `create_work_order` line 54, `reserve_spare` line 203, `create_booking` line 292, `ack_alert` line 388 |
| `app/services/telemetry_service.py` | `ingest` line 46, `predict` line 121 |
| `app/services/retention.py` `RetentionPruner` | Deletes telemetry older than `FDT_TELEMETRY_RETENTION_HOURS` (default 1 h) |
| `app/repositories/telemetry_repo.py` | The single writer for engine time-series. Postgres `ON CONFLICT` upserts |
| `app/repositories/fleet_repo.py` | Fleet reads |
| `app/domain/rules.py` | Rules 19–26. See the numbering note in section 5 |
| `app/domain/health.py` | `engine_health_from_rul`, `maintenance_burden`, EMA |
| `app/domain/scheduling.py` | Agency free-slot days, ETA, cycles-to-date |
| `app/domain/aggregation.py` | Fleet series, heatmap, worst-part ranking, rollup |
| `app/ml/model_store.py` | Load booster + contract + baselines. In-process gain cache |
| `app/ml/features.py` | 30-cycle window → feature matrix |
| `app/ml/inference.py` `predict` line 37 | Score, or `_fallback_result` line 167 (`rul = 125 - cycle`) |
| `app/ml/attribution.py` | Sensor deviations → component health and top sensors |
| `app/realtime/replay.py` `ReplayEngine` | The 1.2 s clock |
| `app/realtime/bus.py` | In-process pub/sub |
| `app/realtime/ws.py` | `/ws/fleet` |
| `app/db/unit_of_work.py` | Commit + `audit_log` + `SELECT … FOR UPDATE` on stock and bookings |
| `app/seed/run.py` | Idempotent users + fleet |
| `app/seed/loaders/drive.py` | CSV → reference tables |
| `app/seed/derived.py` | The 8 aircraft, 5 parts, work orders, alerts |
| `frontend/src/App.jsx` | Auth gate and panel layout |
| `frontend/src/state/store.js` | Client state. Creates work orders and acks alerts |
| `frontend/src/state/server.js` | Maps API payloads onto the fleet skeleton. Translates API `engine` → UI `eng` |
| `frontend/src/three/` | Scene, interaction, per-subsystem models |

### HTTP surface

Style: versioned JSON REST plus one WebSocket. OpenAPI at `/docs` and
`/openapi.json`. Errors are `{ "error": { "code", "message", "request_id", "detail" } }`
via `DomainError` (`main.py` lines 198–208, `core/errors.py`).

Roles are enforced from the **database row**, not the JWT `role` claim
(`deps.py`). A role change takes effect on the next request.

**Public**

| Method | Path | Handler |
|---|---|---|
| POST | `/api/v1/auth/login` | `auth.login` |
| GET | `/healthz` | `ops.healthz` — DB, model, replay. Not in the OpenAPI schema |
| GET | `/readyz` | `ops.readyz` — fleet seeded and real model loaded. No auth |

**Any signed-in role**

| Method | Path |
|---|---|
| GET | `/api/v1/auth/me` |
| GET | `/api/v1/fleet/summary` |
| GET | `/api/v1/aircraft` |
| GET | `/api/v1/aircraft/{code_or_id}` |
| GET | `/api/v1/aircraft/{code_or_id}/engine` |
| GET | `/api/v1/aircraft/{code_or_id}/parts/{part}` |
| GET | `/api/v1/fleet/heatmap` |
| GET | `/api/v1/fleet/actions` |
| GET | `/api/v1/maintenance/schedule` |
| GET | `/api/v1/work-orders` |
| GET | `/api/v1/spares` |
| GET | `/api/v1/agencies` |
| GET | `/api/v1/alerts` |

`part` is `engine`, `radar`, `gear`, `hyd`, or `fuel`.

**Officer or commander (`CanMutate`)**

| Method | Path |
|---|---|
| POST | `/api/v1/work-orders` |
| PATCH | `/api/v1/work-orders/{id}` |
| PATCH | `/api/v1/spares/{part_ref_id}` |
| POST | `/api/v1/spares/{part_ref_id}/reserve` |
| POST | `/api/v1/agencies/{id}/bookings` |
| POST | `/api/v1/alerts/{id}/ack` |
| POST | `/api/v1/telemetry` |
| POST | `/api/v1/internal/ml/predict` |

**Commander only** — this is stricter than the README table. See section 6.

| Method | Path |
|---|---|
| POST | `/api/v1/seed/run` |
| POST | `/api/v1/ml/reload` |
| GET | `/api/v1/demo/status` |
| POST | `/api/v1/demo/pause` |
| POST | `/api/v1/demo/resume` |
| POST | `/api/v1/demo/tick` |
| GET | `/api/v1/audit` |

**WebSocket:** `/ws/fleet?token=<jwt>`. Optional `aircraft` filter. Client messages: `ping`, `subscribe`, `unsubscribe`. Origin must be on the CORS allowlist when the header is present (`ws.py`). Missing `Origin` skips that check.

The React client (`frontend/src/lib/api.js`) calls login, the fleet reads, alerts, alert ack, and work-order create. It does **not** call spares, agency booking, work-order patch, telemetry ingest, or the demo controls. Those exist for the API and the tests.

### External systems

| System | Runtime? | Configured where |
|---|---|---|
| PostgreSQL 16 | Yes | `FDT_DATABASE_URL`. Compose service `db`, host port **5433** |
| NASA C-MAPSS files | Local files, fetched once | `scripts/fetch-cmapss.sh`, `make fetch-data` |
| XGBoost artifacts | Local files | `make fetch-model` needs `FDT_ML_ARTIFACTS_URL`. Or `python -m scripts.stage_ml_artifacts` |
| Google Drive | Training-time only, in the notebook | Not used by the API |
| Render | Optional host | `render.yaml` |

No cron daemon. Two in-process loops replace one: the replay tick
(`FDT_DEMO_TICK_SECONDS`, default 1.2) and the retention sweep
(`FDT_RETENTION_INTERVAL_SECONDS`, default 300).

### Configuration

Every setting is an environment variable prefixed `FDT_`, declared on
`Settings` in `backend/app/core/config.py` and documented in `.env.example`.
`get_settings()` is `lru_cache`d.

| Variable | Default | Meaning |
|---|---|---|
| `FDT_ENVIRONMENT` | `development` | `development`, `staging`, or `production` |
| `FDT_DATABASE_URL` | `postgresql+psycopg://fdt:fdt@localhost:5432/fdt` | Bare `postgres://` URLs are rewritten to the psycopg3 scheme (`config.py` lines 116–137) |
| `FDT_JWT_SECRET` | `dev-only-insecure-secret` | Refused when environment is `production` (lines 139–158) |
| `FDT_JWT_TTL_MINUTES` | `720` | |
| `FDT_CORS_ORIGINS` | `http://localhost:5173` | Comma-separated. Never `*` |
| `FDT_DEMO_MODE` | `true` | Turns the replay engine on. **Not** rejected in production. See section 5 |
| `FDT_DEMO_TICK_SECONDS` | `1.2` | |
| `FDT_TELEMETRY_RETENTION_HOURS` | `1.0` | `0` disables pruning. Dangerous on a small disk (`config.py` lines 50–63) |
| `FDT_SEED_ON_BOOT` | `false` | Render sets this because you cannot shell in to seed |
| `FDT_RUL_CAP` | `125` | Rule 26 |
| `FDT_ML_VARIANT` | `all` | `all` (32 features), `full`, or `holdout`. Only `all` is actually staged |
| `FDT_ML_DATASET` | `FD001` | Which C-MAPSS subset the replay walks. Independent of the variant |
| `FDT_ML_FALLBACK` | `true` | Allow `rul = 125 - cycle` when the booster is missing |
| `FDT_ML_WINDOW` | `30` | |
| `FDT_DATA_DIR` | `data` | Holds `raw/`, `cmapss/`, `ml/` |
| `FDT_WEB_DIST` | `frontend/dist` | SPA mount for the no-nginx deploy |

Leave `FDT_ML_MODEL_PATH`, `FDT_ML_CONTRACT_PATH`, and `FDT_ML_SCALER_PATH`
unset. Setting only the model path splits the artifact set and the loader
falls back while a valid booster sits unused (`README.md` lines 448–451,
`docs/15` §6.1).

`.env` is gitignored. `make setup` copies `.env.example` and generates a JWT
secret. Compose requires `FDT_JWT_SECRET` to be set.

Frontend: `VITE_DEMO_MODE=true` shows the one-click commander button and is
stripped from a production bundle (`frontend/tests/bundle.test.mjs`).
`VITE_API_PROXY_TARGET` overrides the Vite dev proxy (default
`http://127.0.0.1:8000`).

### How to run, test, build, deploy

The root `Makefile` sets `SHELL := /bin/bash` (line 7). On Windows that means
**Git Bash or WSL**, not PowerShell, unless you drive Docker and the backend
venv yourself.

**Docker (documented default)**

```bash
make setup     # .env, C-MAPSS, images, migrate, seed
make up        # http://localhost:8080
```

API docs (loopback only): `http://localhost:8000/docs`.

Everyday: `make down`, `make logs`, `make logs SERVICE=api`, `make migrate`,
`make seed`, `make fresh`, `make fetch-data`, `make fetch-model`,
`make db-dump`, `make db-restore`.

**Without Docker**

```bash
make venv
docker compose up -d db
cd backend && alembic upgrade head && python -m app.seed.run
uvicorn app.main:app --reload --workers 1 --port 8000
cd frontend && npm install && npm run dev    # :5173, proxies /api and /ws
```

**Tests**

```bash
make test          # disposable Postgres, full pytest, then teardown
make test-unit
make test-web      # frontend Node tests
make lint          # ruff
make typecheck     # mypy --strict on app/core, app/domain, app/ml
```

The suite **drops and recreates the `public` schema** (`backend/tests/conftest.py`).
Point it only at a database you can lose. `make test` creates that database
for you. A direct pytest run needs `FDT_TEST_DATABASE_URL`.

**Deploy**

```bash
# set FDT_ENVIRONMENT=production, a real FDT_JWT_SECRET, a real POSTGRES_PASSWORD
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
make migrate && make seed
```

Render: `render.yaml` builds the SPA, installs the backend, fetches FD001,
then `alembic upgrade head` and `uvicorn` on `$PORT`. Health check is
`/healthz`. There is no CI deploy job.

---

## 4. Data and State

### Database

PostgreSQL 16. SQLAlchemy 2 declarative base in `backend/app/db/session.py`
line 22. One Alembic revision:

`backend/alembic/versions/d50c12bdb5c9_initial_schema_20_tables_enums_33_.py`

Docstring: "20 tables, enums, 33 indexes". `docs/05` describes a multi-revision
plan and SQL views. The repo has neither. `agency.free_slot_days` is computed
in Python (`domain/scheduling.py` `agency_free_slot_days`).

### The 20 tables

**Auth** — `backend/app/models/auth.py`

| Model | Line | Table | Notes |
|---|---|---|---|
| `User` | 20 | `users` | `commander`, `maintenance_officer`, `viewer` |
| `AuditLog` | 36 | `audit_log` | Actor, action, before/after JSONB |

**Reference, seeded, not rewritten at runtime** — `models/reference.py`

| Model | Line | Table |
|---|---|---|
| `AircraftRef` | 24 | `aircraft_ref` |
| `ComponentRef` | 51 | `component_ref` |
| `FlightOpsMonthly` | 84 | `flight_ops_monthly` |
| `TechnicalRecord` | 105 | `technical_record` |
| `Snag` | 134 | `snag` |

The Drive CSVs contain on the order of 100 aircraft and 600 components. Only
eight aircraft are promoted into the live fleet. The rest stay in these
reference tables (`docs/README.md`, decision 3).

**Fleet** — `models/fleet.py`

| Model | Line | Table | Notes |
|---|---|---|---|
| `Aircraft` | 35 | `aircraft` | `Fighter-01`…`08`, bound 1:1 to a C-MAPSS unit. Denormalized `rul`, `risk_level`, `mission_ready`, `worst_part`, `current_cycle` |
| `Part` | 90 | `part` | Five rows: engine, radar, gear, hyd, fuel |
| `AircraftPart` | 113 | `aircraft_part` | Health, risk, RUL, do-by, spare, agency, back-in-service days |

**Maintenance** — `models/maintenance.py`

| Model | Line | Table |
|---|---|---|
| `Agency` | 52 | `agency` |
| `Spare` | 76 | `spare` |
| `WorkOrder` | 110 | `work_order` |
| `AgencyBooking` | 145 | `agency_booking` |
| `StockMovement` | 177 | `stock_movement` |

**Time series** — `models/telemetry.py`. Unique on `(aircraft_id, cycle)` or `(aircraft_id, part_id, cycle)`.

| Model | Line | Table |
|---|---|---|
| `HealthSnapshot` | 28 | `health_snapshot` |
| `EngineTelemetry` | 50 | `engine_telemetry` |
| `ComponentHealth` | 88 | `component_health` |
| `MlPrediction` | 114 | `ml_prediction` |

**Alerts** — `models/alert.py` `Alert` line 25, table `alert`.

Pydantic models in `backend/app/schemas/fleet.py` and `schemas/ops.py` are the
HTTP contract. They are not the database mapping.

### How data moves

```
CSV seed (once)                C-MAPSS files (every tick)         XGBoost file (once at boot)
backend/data/raw/*.csv         backend/data/cmapss/*.txt          backend/data/ml/all/*.json
        │                              │                                  │
        ▼                              ▼                                  ▼
 seed/run.py                    in-memory index                    model_store.load
        │                              │                                  │
        ▼                              └──────────────┬───────────────────┘
 PostgreSQL reference +                               ▼
 8 aircraft, parts, spares,              inference.predict → rules → one transaction
 agencies, opening work orders                        │
                                                      ▼
                              engine_telemetry, component_health, ml_predictions,
                              health_snapshots, denormalized aircraft_parts
                                                      │
                              ┌───────────────────────┴────────────────────┐
                              ▼                                            ▼
                         REST reads                                   /ws/fleet
                     (panels, 3 s poll)                          (live engine values)
```

- Sessions: one per request, `get_db` in `session.py`.
- Mutations go through `UnitOfWork`, which writes `audit_log` in the same transaction and row-locks stock and bookings.
- Replay upserts are idempotent on `(aircraft_id, cycle)`.
- Retention deletes from the four append-only tables. Default 1 hour is about 141 MB steady state; a month unpruned was estimated at ~93 GB (`config.py` lines 51–58).
- No Redis. The only caches are denormalized columns, `get_settings()`'s `lru_cache`, and an in-process gain cache inside `model_store`.
- The browser keeps the token and user in `sessionStorage` (`fdt.token`, `fdt.user`).

### Two different "health" numbers

This is the easiest thing to misunderstand.

| Subsystem | Source | Simulated? |
|---|---|---|
| Engine | `health = rul / 125`, EMA-smoothed. RUL from XGBoost | No. This is the model |
| Fan, HPC, HPT, LPT | Sensor z-scores grouped by `COMPONENT_SENSOR_MAP` in `rules.py` lines 23–31 | Reporting split of the **one** engine model. Not four models |
| Radar, gear, hydraulics, fuel | `maintenance_burden` in `health.py` lines 52–84, from technical records and snag logs | Yes. Flagged simulated |

`FAULT_MAP` (health.py lines 18–29) assigns fault strings to parts: hydraulic
pressure → `hyd`, fuel flow → `fuel`, sensor fault → `radar`, starter fault →
`gear`. Several engine-worded faults also land on `engine`.

### Migrations

One file. Apply with `make migrate` or `alembic upgrade head` from `backend/`.
CI upgrades, downgrades, and upgrades again, then runs `alembic check`.

There is no seed migration. Data is `python -m app.seed.run`, which is
idempotent and will not duplicate rows or overwrite runtime-created work
(`seed/run.py` lines 1–5).

---

## 5. Vision vs Current State

### Stated goal

A fully offline predictive-maintenance platform: real engine RUL, deterministic
rules the UI and the database share, a visible fallback when the model is
missing, and an operations console for alerts, work orders, spares, and agency
slots. The ten decisions in `docs/README.md` lines 66–77 still match the code:

1. The model sees C-MAPSS only. The CSVs are reference data.
2. Native XGBoost, loaded once. No ONNX.
3. Eight curated aircraft, each bound to one C-MAPSS engine.
4. Only the engine is sensor-driven. The other four parts are simulated.
5. `cycle` is not a model feature (it leaked on the truncated test set). The served contract is **32 features**.
6. Rules 19–26 are pure functions.
7. RUL is capped at 125 even though FD001 engines live longer.
8. Risk uses strict `>`: above 0.7 healthy, above 0.4 watch, otherwise critical. Exactly 0.7 is watch (`rules.py` lines 52–59).
9. Every mutation writes `audit_log`.
10. No outbound calls at runtime.

### What is implemented

Roughly the whole product shell. Phases 1–7 of `docs/10-development-plan.md`
(scaffold, schema, rules, reads, writes, realtime, deploy) are in the tree and
covered by tests. Treat that plan's unchecked boxes as a historical checklist,
not a live backlog.

Working today:

- Login, three roles, JWT, audit log
- Fleet reads, heatmap, actions, schedule
- Work orders, spares, bookings, alert acknowledgement (API + tests)
- Replay engine, WebSocket, retention pruner
- 3D twin and the operations panels
- Docker Compose, nginx, CI, Render blueprint
- Fallback that reports itself on `/healthz` instead of pretending to be the model
- Pooled-model loader (`FDT_ML_VARIANT=all`)

The UI actually exercises login, fleet reads, the 3D inspector, alert ack
(`store.js` line 159), and work-order create (`store.js` line 116).

### What is incomplete

| Gap | Detail |
|---|---|
| Booster not in git | `backend/data/ml/all/` has the contract, baselines, and metrics. It does not have `xgboost_all_full.json`. A fresh clone serves `rul = 125 - cycle` until `make fetch-model` or a manual stage. `/healthz` will say `degraded`. |
| Training is a notebook | `ml/notebooks/Model_training_249.ipynb`. `scripts/train_rul.py` is documented and absent. Artifacts cannot be regenerated in CI. |
| Non-engine parts | No sensor model. Health is a recency-weighted burden. |
| Component split | Fan, HPT, and LPT were never the FD001 fault mode. HPC was. `s4` (mapped to LPT) dominates because it sits downstream of the HPC, so the LPT readout moves the most for the wrong physical reason (`docs/14` §8). `s9` has since been added to HPC in `rules.py` lines 25–28; doc 14 still says it is unassigned. |
| FD002 / FD004 | Handicapped by the FD001 sensor set. Six sensors that vary in those subsets are dropped because they are constant in FD001 (`docs/14` §5). Open research, not a code bug. |
| Regime window | The classifier assigns one regime from the latest cycle. Training assigned a regime per cycle. A window that crosses an operating-condition change is scored against one regime (`docs/15` §7). |
| Spares and agencies in the UI | Endpoints exist. The React client never calls them. |
| Demo controls in the UI | Commander-only HTTP. No button in the console. |
| Frontend lint | `npm run lint` prints a message and exits. No ESLint config. |
| mypy | Strict on `core`, `domain`, and `ml` only. Services and routers are outside that net. |
| E2E | No Playwright / browser suite. The 3D scene is untested in CI beyond "the GLB files exist in `dist/`". |

A fair reading: the **platform** is built. The **model operations** (artifact on disk, reproducible training, physically honest component health) are the unfinished part. Call that about 80% of the product vision, with the remaining 20% concentrated in ML reproducibility and the four non-engine subsystems.

### Roadmap and design docs

There is no `adr/` directory. The decisions live in `docs/README.md` ("Ten
decisions") and are expanded in 15 numbered documents. `docs/README.md` says
to start at **14**. That advice is stale. Read **15** for the model that is
actually served, and treat 14 as a snapshot from an earlier session.

| Doc | What it is now |
|---|---|
| 01 Requirements evaluation | Spec versus the CSVs. Still the reason non-engine health is simulated |
| 02 Backend architecture | Layers. Matches the code |
| 03–04 Flow diagrams | System and per-role journeys |
| 05 Database design | Full DDL discussion. The Alembic file is the authority, not the Prisma export or the nine-revision plan |
| 06 API specification | Endpoint contract. Cross-check roles against `ops.py` |
| 07 Business rules | **Authoritative rule numbering.** Matches `rules.py` |
| 08 ML service | FD001 29-feature contract. Superseded for serving by doc 15 |
| 09 Realtime | WebSocket protocol and the replay loop |
| 10 Development plan | Historical phase plan. Do not treat open checkboxes as unbuilt |
| 11 Deployment and NFRs | Budgets and topology. Some compose samples name a `postgres` service; the file uses `db` |
| 12–13 Training reports | Notebook phases and the multi-regime experiment |
| 14 Final status | Session snapshot. Integration tests have since been written and CI runs them. It still describes a `train_rul.py` and an FD001-only serving model |
| 15 Pooled model | Current serving contract: 32 columns, variant `all` |

### Doc drift you will hit on day one

1. **Rule numbers in the root README do not match the code.** `README.md` lines 307–316 list rule 20 as "worst part" and 21 as "mission ready", and they omit `recommended_action`. `docs/07` lines 15–21 and the comments in `rules.py` are the ones the tests use:

   | # | Function | Meaning |
   |---|---|---|
   | 19 | `risk_level` | > 0.70 healthy, > 0.40 watch, else critical |
   | 20 | `mission_ready` | Engine RUL > 30 and every part above watch |
   | 21 | `worst_part` | Lowest health; ties broken by engine, radar, gear, hyd, fuel |
   | 22 | `recommended_action` | Routine check / Plan inspection / Replace now |
   | 23 | `do_by_cycle` | `rul - 10` unless healthy |
   | 24 | `back_in_service_days` | free slot + turnaround + lead time if stock is 0 |
   | 25 | `engine_spare` | Weakest of fan, hpc, hpt, lpt |
   | 26 | `cap_rul` | Round and clamp to [0, 125] |

2. **README role table vs `ops.py`.** The README (lines 381 and 396) says audit is "any" role and demo pause/resume is "officer+". The handlers use `CommanderOnly` (`ops.py` lines 107–137). The code is what will 403 you.

3. **Demo mode in production.** `README.md` line 492 says the API refuses to boot if `FDT_DEMO_MODE` is true in production. `config.py` lines 147–151 say the opposite, on purpose: demo mode picks a telemetry source and is not a security check. Later README lines 705–708 agree with the code. The early sentence is leftover. `docker-compose.prod.yml` defaults demo mode to true.

4. **`docs/14` vs `docs/15`.** 14's serving model is the 29-feature FD001 booster in `data/ml/full/`. The default in `config.py` line 83 is variant `all`, 32 features. `full` and `holdout` "describe directories nothing writes any more" (config.py lines 80–82).

5. **`docs/15` §7 still talks about `scripts/train_rul.py`.** Glob of the repo finds no such file. The outstanding work (no CI-reproducible training) is real. The path is not.

6. **ML gitignore.** README says artifacts are gitignored. `.gitignore` line 46 comments that rule out (`# backend/data/ml/`). The booster is still absent; the JSON sidecars are committed.

7. **`docs/15` §7 says `data/cmapss/` is not gitignored.** `.gitignore` lines 40–44 ignore it except the FD001 subset, which is committed because public mirrors died and the Render build needs the files.

---

## 6. Known Issues, Bugs, and Bottlenecks

### TODO / FIXME / HACK / XXX / "not implemented"

A search of `*.py`, `*.js`, `*.jsx`, `*.md`, and `*.yml` found **no** `TODO`,
`FIXME`, `HACK`, `XXX`, or `NotImplemented`. The authors wrote caveats as
comments and as design docs instead of markers. The open work is in section 5,
not in comment tags.

`docs/01` notes that per-sensor deviation was not in the original notebook.
Attribution now exists in `app/ml/attribution.py`; that note is historical.

### Code smells and fragile spots

| Issue | Where | Why it matters |
|---|---|---|
| README, doc 14, and doc 15 disagree with the tree | See section 5 | A newcomer will "fix" things that were already fixed, or trust a role table that 403s |
| Rule numbers in the root README | `README.md` vs `rules.py` | The tests follow `docs/07`, not the README table |
| UI part code `eng` vs API `engine` | `frontend/src/data/fleet.js` lines 17–22 | The comment says getting this wrong is silent: the engine cell stays unknown forever. Translation is only in `state/server.js` |
| `can_manage_agencies` is commander-only on `/me`, but bookings use `CanMutate` | `auth.py` vs `maintenance.py` `create_booking` | The permission flag does not match the route |
| Viewer sees write buttons | `Alerts.jsx`, part detail | The API returns 403. The button is still there |
| Frontend lint is a stub | `frontend/package.json` | CI does not lint the SPA |
| Ruff format in CI is `\|\| true` | `.github/workflows/ci.yml` | Format drift does not fail the build |
| mypy stops at three packages | `Makefile` typecheck target | A type error in a service will not fail CI |
| `pickle.load` of the scaler | `model_store.py` | Only for the `holdout` variant, from the local artifact dir. The default `all` variant uses JSON baselines, not a pickle. Still an untrusted-artifact risk if that path is ever pointed at a downloaded file |
| Single worker, in-process replay | `replay.py`, Dockerfile | Two replicas double the fleet speed. Documented, easy to miss |
| Retention default of 1 hour | `config.py` | History charts stay populated for the demo. A longer demo, or `0`, fills the disk. The comment estimates ~36 KB/s across the fleet |

### Performance

The authors measured this and locked it in `backend/tests/performance/test_budgets.py`:

| Budget | Target |
|---|---|
| Fleet reads | < 200 ms |
| `/internal/ml/predict` | < 100 ms |
| `/healthz` | < 5 ms, and the liveness path should not hit the database for the model check |
| Query count | Must not grow with fleet size |

Inference is off the event loop (`asyncio.to_thread`). Median model latency in
doc 14 was about 1.2 ms on a synthetic run. The fleet is eight aircraft, so
N+1 would show up immediately; the performance test counts statements.

The cost that will bite is **disk**, not CPU. Four rows per aircraft per 1.2 s,
dominated by JSONB on `ml_prediction`. The pruner exists because of that
arithmetic (`config.py` lines 51–58).

3D assets are ~9 MB fetched on first paint (`rafale.glb` 3.0 MB, landing gear
2.5 MB, `engine.bin.gz` 1.9 MB, radar 1.7 MB). `engine.bin.gz` must be served
**without** `Content-Encoding: gzip`. nginx turns gzip off for `.gz` because
the client sniffs the magic bytes and gunzips itself (`README.md` lines 754–756).

### Security

| Topic | Assessment |
|---|---|
| Passwords | bcrypt. Login errors do not say whether the username exists (`auth.py`) |
| Sessions | Stateless JWT, HS256, 12-hour default. Token in `sessionStorage`, not `localStorage` |
| Production secret | Boot fails if `FDT_ENVIRONMENT=production` and the secret is still `dev-only-insecure-secret` |
| Demo accounts | **Committed** in `seed/run.py` lines 25–29. Fine for a local demo. Not fine on a public URL |
| Default DB password | `fdt` / `fdt` in `.env.example` and compose |
| WebSocket token | JWT in the query string (`api.js`). It can land in access logs |
| `/readyz` and `/docs` | Unauthenticated. `/readyz` reveals whether the fleet is seeded and whether the real model loaded |
| Login throttling | None |
| `eval` / `exec` | None in application code |
| SQL | Parameterized SQLAlchemy. The string-built SQL the README mentions is a static `COUNT` label in the performance tests |
| TLS | nginx speaks HTTP. Terminate TLS in front of it |
| CORS | Explicit list. Empty in the production overlay, because the browser is same-origin |
| Supply chain | `pickle.load` only if a scaler artifact is configured |

The old silent auto-login (every visitor became commander) was removed.
`ensureToken` no longer logs in by itself, and `bundle.test.mjs` fails the
build if the demo password survives into the production bundle.

### Tests

README claims 408 tests, 9 skipped when no booster is staged (`README.md`
lines 577–584). That count was not re-run for this write-up.

| Area | Files | What they lock |
|---|---|---|
| Rules, health, scheduling, aggregation | `tests/unit/test_rules.py`, `test_health.py`, `test_scheduling.py`, `test_aggregation.py` | The business contract |
| Features, model store, inference | `test_features.py`, `test_model_store.py`, `test_inference.py` | The 32-column contract and the fallback |
| Config guards | `test_config_guards.py` | Production refuses the dev JWT secret |
| Bus | `test_bus.py` | Slow consumers drop events |
| Artifact fetch | `test_fetch_ml_artifacts.py` | A booster whose width disagrees with its contract is refused |
| Auth | `tests/integration/test_auth_rbac.py` | Login and the role matrix |
| Fleet reads | `test_fleet_reads.py` | The panels' API |
| Work orders, spares, agencies, alerts | `test_work_orders.py`, `test_spares_agencies_alerts.py` | Writes, locks, audit |
| Telemetry | `test_telemetry_ml.py` | Ingest and predict |
| Realtime | `test_concurrency_realtime.py` | Parallel reserve/booking and the socket |
| Retention | `test_retention.py` | The pruner |
| SPA mount | `test_web_mount.py` | Static files must not shadow the API |
| Model quality | `tests/ml/test_model_quality.py` | MAE / latency against a staged booster. Skips if none is staged |
| Budgets | `tests/performance/test_budgets.py` | Latency and query-count ceilings |
| Frontend | `frontend/tests/auth.test.mjs`, `bundle.test.mjs` | No silent login; demo password tree-shaken out of production |

**Not covered:** browser flows, the three.js scene, role-gated buttons, spares
and booking from the UI (because the UI does not call them), a long WebSocket
soak, and an OpenAPI snapshot test. Doc 11 mentions a pre-commit hook; there
is no `.pre-commit-config` in the repo.

---

## 7. Things You Can Do Next

### Read this first, in order

1. `README.md` — product, diagram, commands. Ignore the rule-number table and the "demo mode refuses to boot" sentence. Both are wrong; section 5 above says how.
2. `docs/07-business-rules.md` — the rules, with the numbering the code uses.
3. `docs/15-serving-the-pooled-model.md` — the model that is actually configured. Skip doc 14 until you want the training history.
4. `backend/app/main.py` — lifespan and why the static mount is narrow.
5. `backend/app/domain/rules.py` and `domain/health.py` — the contract everything else calls.
6. `backend/app/realtime/replay.py` — the live write path.
7. `backend/app/ml/inference.py` — predict versus fallback.
8. `frontend/src/App.jsx`, `state/simulation.js`, `state/server.js`, `lib/api.js` — how the console stays honest when the API is down.
9. `docs/05-database-design.md` — relationships. When it disagrees with Alembic, believe Alembic.

Then run it. On this machine use Git Bash or WSL for `make`. Sign in as
`commander` / `commander123`. Hit `http://localhost:8000/healthz` and see
whether `model.loaded` is true. If it is false, you are looking at the
fallback, and the numbers on screen are `125 - cycle`.

### Tasks, easy to deep

1. **Good first task — hide write actions from viewers.** `Alerts.jsx` and the part-detail panel always show ack and "raise work order". `getUser()` in `frontend/src/lib/api.js` already has the role. Gate the buttons. Add a case to `frontend/tests/auth.test.mjs`. The API already returns 403; this is UI honesty.

2. **Good first task — fix the README drift.** Three concrete edits in `README.md`: the rule table (lines 307–316) should match `docs/07`; audit and demo routes (lines 381, 396) should say commander; line 492 should stop claiming production rejects demo mode. No behavior change. High leverage for the next reader.

3. **Good first task — surface model fallback in a place you will notice.** `/healthz` already returns `model.fallback` / `degraded`. `simulation.js` `trackModel` (line 50) stores it on `app.model`. Confirm the navbar or footer actually renders "fallback" when the booster is missing, and add a fixture if it does not. The whole point of the fallback design is that it must be visible (`README.md` lines 66–69).

4. **Small backend — align `can_manage_agencies` with bookings.** Either `create_booking` in `maintenance.py` should require `CommanderOnly`, or `/auth/me` should stop claiming officers cannot manage agencies. `tests/integration/test_auth_rbac.py` is the place the new matrix has to pass.

5. **Small backend — login validation.** `LoginRequest` in `schemas/ops.py` is a bare username and password. Add length bounds. There is still no rate limit; a counter in front of `auth.login` is a larger change and wants a decision about where the counter lives (there is no Redis).

6. **Needs context — call the spares and agency APIs from the console, or delete the implication that the UI does.** The maintenance plan panel shows schedule data from `GET /maintenance/schedule`. Creating a booking or reserving a spare is API-only. Either add the buttons (`maintenance_service.py` already does the transaction and the audit row) or say so in the panel. Touch `frontend/src/lib/api.js` and `components/MaintenancePlan.jsx`.

7. **Needs context — make `npm run lint` real.** The script is a stub. ESLint on `frontend/src` would be the first safety net the SPA has. CI's web job (`.github/workflows/ci.yml`) only runs `npm test` and `npm run build`.

8. **Needs the domain — component-health honesty.** `COMPONENT_SENSOR_MAP` in `rules.py` assigns `s4` to LPT even though FD001's fault is HPC. Doc 14 §8 and the comment above `hpc` in `rules.py` (lines 25–27) are the brief. Changing the map changes every engine readout and the spare chosen by rule 25. Update `tests/unit/test_rules.py` in the same change. Do not do this casually.

9. **Deep — bring the booster into a fresh clone without hand-waving.** `make fetch-model` depends on `FDT_ML_ARTIFACTS_URL`, which has no default. Document a known-good source, or commit `xgboost_all_full.json` now that `.gitignore` no longer ignores `data/ml/`. Then confirm `tests/ml/test_model_quality.py` stops skipping. Files: `backend/scripts/fetch_ml_artifacts.py`, `stage_ml_artifacts.py`, `app/ml/model_store.py`.

10. **Deep — a training entrypoint that matches the 32-column contract.** The notebook is the only producer. Doc 15 §7 describes the old 16-feature script, which is gone. A `python -m` trainer that emits `xgboost_all_full.json`, `feature_contract_all_full.json`, and `regime_baselines_all.json`, and refuses to include `cycle`, would close Phase 0. Start from `ml/notebooks/Model_training_249.ipynb` and `app/ml/features.py`. Do not reintroduce `cycle` as a feature.

### If you were maintaining this

1. **Make `/healthz` model status impossible to miss, and make a fresh clone load a real booster.** A maintenance UI on the fallback curve looks healthy. That is the failure mode the README is most worried about.
2. **Quarantine doc drift.** Either regenerate the README tables from the routers, or add a one-page "current state" note at the top of `docs/README.md` that says: served variant is `all`, rule numbers live in doc 07, demo mode is allowed in production, `train_rul.py` does not exist. Doc 14 should be labeled historical.
3. **One replica, on purpose.** Put the workers=1 and demo-mode constraint in the production compose health documentation you actually hand an operator. Scaling the API while `FDT_DEMO_MODE=true` silently doubles wear.

---

## 8. Glossary

| Term | Meaning |
|---|---|
| **Fleet Digital Twin** | This product. A live 3D and tabular view of eight aircraft. |
| **Fighter-01 … Fighter-08** | The eight live aircraft. Mapped from reference ids `AC001`…`AC008`. |
| **C-MAPSS** | NASA turbofan run-to-failure dataset. Cycle rows with 3 operating settings and 21 sensors, plus a ground-truth RUL file. |
| **FD001–FD004** | The four C-MAPSS subsets. FD001 and FD003 are one operating condition. FD002 and FD004 have six. The demo replays whichever `FDT_ML_DATASET` names, default FD001. |
| **Cycle** | One C-MAPSS flight. Not the same thing as `flight_cycles` in the monthly operations CSV. |
| **RUL** | Remaining useful life, in cycles. Capped at 125 by rule 26. |
| **Health** | A float in [0, 1]. For the engine, `rul / 125`. For the other parts, one minus a maintenance burden. |
| **Risk band** | `healthy` (> 0.7), `watch` (> 0.4), `critical` (otherwise). Strict greater-than. |
| **Mission ready** | Engine RUL > 30 and every part above the watch line. Rule 20. |
| **Do-by cycle** | The cycle by which a watch or critical part should be addressed: `rul - 10`. Rule 23. |
| **Worst part** | Lowest health on the aircraft. Ties prefer engine, then radar, gear, hyd, fuel. Rule 21. |
| **EMA** | Exponential moving average, α = 0.3, applied per aircraft so one noisy prediction does not swing the engine health. |
| **Regime** | An operating-condition bucket. The pooled model z-scores sensors against a baseline for that regime. `regime_global` is feature 32's cousin: an id, not a sensor. |
| **Pooled model / variant `all`** | XGBoost trained on FD001–FD004 together. 32 features. This is the default. |
| **Variant `full` / `holdout`** | Older FD001-only artifact layouts. Config still accepts the names. Nothing in the current pipeline writes those directories. |
| **Feature contract** | JSON that lists the 32 columns in order. The booster is positional. A contract/model mismatch is refused because it would look like a plausible wrong prediction. |
| **Fallback** | `rul = 125 - cycle` when the booster is missing, the window is short, or inference throws. `/healthz` status becomes `degraded`. |
| **s2, s3, … s21** | C-MAPSS sensor channels. `s6` and `s16` are constant in FD001 and carry no FD001 signal; the pooled contract keeps them because other subsets use them. |
| **Fan, HPC, HPT, LPT** | Fan, high-pressure compressor, high-pressure turbine, low-pressure turbine. Display groups over sensors. Not separate models. |
| **engine / radar / gear / hyd / fuel** | The five parts on each aircraft. The UI calls the engine `eng`. |
| **Snag** | A defect report (`snag` table), joined to a technical record. Input to non-engine health. |
| **Maintenance burden** | Recency-weighted sum of faults and snags, normalised and inverted into a health score. `health.py` `maintenance_burden`. |
| **Agency** | An external maintenance shop with a specialisation, turnaround, and free-slot days. |
| **Spare** | A stocked part. Rule 24 adds lead time only when stock is 0. Rule 25 picks the engine spare from the weakest component. |
| **Work order** | A maintenance job. Creating one is audited. |
| **Unit of work** | The transaction wrapper that commits the change and the audit row together. |
| **Replay / demo mode** | `FDT_DEMO_MODE`. The in-process clock that feeds C-MAPSS through the model. Off means the fleet moves only when something `POST`s `/api/v1/telemetry`. |
| **BFF** | Backend-for-frontend. Here, nginx (or the API process on Render) so the browser has one origin. |
| **Denormalized columns** | `aircraft.rul`, `mission_ready`, `worst_part`, and the `aircraft_part` health fields. Written by the tick so reads do not recompute. |
| **Retention pruner** | Deletes telemetry older than `FDT_TELEMETRY_RETENTION_HOURS` so the demo cannot fill a 1 GB disk. |
| **`FDT_`** | Prefix on every backend setting. |
| **Commander / officer / viewer** | The three roles. Officer is `maintenance_officer` in the database. |

---

## Appendix — one-screen map

```
Question: which jet cannot fly next week, and why?

Login  POST /api/v1/auth/login
         └─ JWT in sessionStorage

Every 1.2 s, inside the one API process
  C-MAPSS window → XGBoost (or 125 − cycle) → rules 19–26
  → one Postgres transaction → WebSocket

Browser
  App.jsx gates on the user
  simulation.js listens and polls
  three/ paints the aircraft
  panels read /api/v1/fleet, /aircraft, /alerts, /maintenance/schedule

If model.loaded is false, you are not looking at the trained model.
```
