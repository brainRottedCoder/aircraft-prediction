# Fleet Digital Twin — Backend

**FastAPI + PostgreSQL 16 + XGBoost (NASA C-MAPSS), fully offline.** No outbound network
calls at runtime.

This is the API half of the monorepo. The supported way to run it is through Docker from
the repository root — see the [main README](../README.md). This document covers the
internals.

## Quick start

```bash
# From the repository root — no local Python needed
make setup     # .env, C-MAPSS data, images, migrations, seed
make up        # http://localhost:8080
```

Locally, with an editable install:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
docker compose up -d db          # from the repository root
alembic upgrade head
python -m app.seed.run
uvicorn app.main:app --reload --workers 1
```

Demo logins: `commander` / `officer` / `viewer`. Passwords are in `app/seed/run.py`.

> `--workers 1` is not a default to raise. The replay engine is an in-process asyncio task
> (`app/realtime/replay.py`); extra workers would run extra replay loops and double-advance
> every aircraft.

## Layout

```
app/
├── main.py            FastAPI factory, lifespan, middleware, error handler
├── core/              settings · security · errors · logging
├── db/                session · unit_of_work (transaction + audit boundary)
├── models/            20 SQLAlchemy tables, grouped by concern
├── domain/            PURE: rules · health · aggregation · scheduling
├── schemas/           Pydantic v2 request/response models
├── repositories/      data access, no business logic
├── services/          use cases, one per endpoint intent
├── ml/                model_store · features · inference · attribution · fallback
├── realtime/          event bus · replay engine · WebSocket endpoint
└── seed/              idempotent loader · C-MAPSS index · CSV loaders
```

The dependency direction is strictly downward and `domain/` imports nothing but the
standard library. `api/` never issues SQL; `repositories/` never imports `services/`.

## ML artifacts

The model is not committed. Stage it before starting:

```bash
python -m scripts.stage_ml_artifacts --source <dir-with-models/multi> --variant all
```

`FDT_ML_VARIANT` selects the generation, and the loader derives the entire artifact set
from that one value:

| Variant | Model file | Features | Preprocessing |
|---|---|---|---|
| `all` *(default)* | `xgboost_all_full.json` | 32 | Per-regime z-score |
| `full` | `xgboost_fd001_full.json` | 29 | Per-regime z-score |
| `holdout` | `xgboost_fd001_rul.json` | 29 | `StandardScaler` |

**Do not set `FDT_ML_MODEL_PATH`.** An explicit path overrides only the model while the
contract still resolves from the variant, so the two end up describing different
directories and the app degrades to the deterministic `rul = 125 − cycle` fallback with a
valid model sitting on disk unused.

Without artifacts the app still boots and answers every request; `/healthz` reports
`status: degraded` with the loader's error. Details:
[`../docs/15-serving-the-pooled-model.md`](../docs/15-serving-the-pooled-model.md).

## Data

| Path | Contents | In git |
|---|---|---|
| `data/raw/` | 7 fleet CSVs (~5,900 rows) | yes, 584 KB |
| `data/cmapss/` | NASA C-MAPSS telemetry, 43 MB | no — `make fetch-data` |
| `data/ml/` | trained boosters and contracts | no — staged from a training run |

`FDT_DATA_DIR` is resolved relative to the process CWD. The container sets it to
`/app/data` and the Dockerfile fails the build if `data/raw` is absent, because a missing
dataset degrades silently rather than raising.

## Tests

```bash
make test        # 408 tests against a disposable database it provisions
make test-unit   # 167 pure domain tests, no database
make lint        # ruff
make typecheck   # mypy --strict over app/core, app/domain, app/ml
```

> The suite **drops and recreates the `public` schema**. It must only be pointed at a
> disposable database. `make test` starts one in a container and tears it down. Running
> pytest directly requires `FDT_TEST_DATABASE_URL` to be set explicitly — there is
> deliberately no host or port default.

## Design documents

Fifteen documents in [`../docs/`](../docs/README.md) cover requirements, architecture,
the database, the API contract, business rules, the ML service, the realtime protocol and
deployment. Start with
[14 — Final Status Report](../docs/14-final-status-report.md).