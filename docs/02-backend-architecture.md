# 02 — Backend Architecture

## 1. Architectural style

A **layered monolith** with an explicitly separated ML subsystem. Rationale:

- **Not microservices.** Eight aircraft, one Postgres instance, sub-200 ms budgets. A network
  hop between "readiness" and "rules" services would dominate latency and add failure modes
  with no benefit at this scale.
- **Not a pure CRUD API.** The value is in derived state (health, risk, RUL, actions,
  back-in-service) that must be *identical* between the REST endpoints and the WebSocket
  stream. That forces a real domain layer, which a layered monolith gives you naturally.
- **ML is a component, not a service.** XGBoost inference is ~2 ms in-process. Extracting it
  over HTTP would add more latency than the inference itself. It is a module with its own
  lifecycle, not a separate deployable.

The critical structural decision: **one rules engine, one health pipeline, called from both
the request path and the replay loop.** If the WebSocket computed risk differently from
`GET /fleet/summary`, the dashboard would visibly contradict itself within seconds.

## 2. Layer model

```
┌──────────────────────────────────────────────────────────────────────┐
│  INTERFACE LAYER                                                    │
│  REST routers (/api/v1)  ·  WebSocket /ws/fleet  ·  Static /models   │
│  Pydantic v2 request/response schemas · JWT dependency · RBAC guard  │
└───────────────────────────────┬──────────────────────────────────────┘
                                │  validated DTOs only
┌───────────────────────────────▼──────────────────────────────────────┐
│  APPLICATION LAYER  (use cases — one function per endpoint intent)   │
│  fleet_summary · aircraft_detail · engine_detail · part_detail       │
│  recommended_actions · maintenance_schedule · work_orders · spares   │
│  agencies · alerts · telemetry_ingest                               │
└───────────────────────────────┬──────────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────────┐
│  DOMAIN LAYER  (pure, no I/O, fully unit-tested)                    │
│  rules.py       — spec rules 19–26, deterministic, no DB access      │
│  health.py      — health derivation & smoothing (ML / burden paths) │
│  aggregation.py — 60-cycle series, heatmap matrices, fleet rollups  │
│  scheduling.py  — back-in-service, do-by, agency slot arithmetic    │
└───────────────┬───────────────────────────────────┬──────────────────┘
                │                                   │
┌───────────────▼──────────────┐   ┌────────────────▼──────────────────┐
│  INFRASTRUCTURE LAYER       │   │  ML SUBSYSTEM (isolated)          │
│  repositories (SQLAlchemy)  │   │  model_store   — load once        │
│  unit_of_work  — tx/session │   │  features      — window builder   │
│  audit        — audit_log   │   │  inference     — Booster.predict  │
│  event_bus    — in-proc pub │   │  attribution   — z-scores, top-N  │
│  replay_engine— C-MAPSS loop│   │  components    — sensor grouping  │
└───────────────┬──────────────┘   └───────────────────────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────────────┐
│  PostgreSQL 16  ·  SQLAlchemy 2.0 ORM  ·  Alembic migrations         │
└──────────────────────────────────────────────────────────────────────┘
```

### Layer rules (enforced in code review)

1. **Domain imports nothing.** `domain/` imports only `stdlib` and `pydantic`. No SQLAlchemy,
   no FastAPI. This is what makes rules 19–26 testable in microseconds.
2. **Routers are thin.** A router function validates, calls one application function, returns.
   No branching on business conditions, no ORM queries.
3. **Application functions never build SQL.** They call repositories or domain functions.
4. **Repositories never compute business logic.** They load and persist.
5. **Every mutation goes through `unit_of_work`**, which guarantees an `audit_log` row.

## 3. Module layout

```
backend/
├── app/
│   ├── main.py                     # FastAPI factory, lifespan, router mount
│   ├── core/
│   │   ├── config.py               # pydantic-settings, all env vars
│   │   ├── security.py             # JWT issue/verify, password hashing
│   │   ├── deps.py                 # DI: db session, current user, role guards
│   │   ├── errors.py               # domain exception → HTTP mapping
│   │   └── logging.py              # structured JSON logs, request-id middleware
│   ├── db/
│   │   ├── base.py                 # Declarative Base, naming convention
│   │   ├── session.py              # engine, SessionLocal, get_db
│   │   └── unit_of_work.py         # transaction boundary + audit helper
│   ├── models/                     # SQLAlchemy ORM, one file per table group
│   │   ├── reference.py            # aircraft_ref, component_ref, flight_ops_monthly
│   │   ├── fleet.py                # aircraft, part, aircraft_part
│   │   ├── telemetry.py            # health_snapshot, engine_telemetry, component_health
│   │   ├── ml.py                   # ml_prediction
│   │   ├── maintenance.py          # agency, spare, technical_record, snag,
│   │   │                           #   work_order, agency_booking, stock_movement
│   │   ├── alert.py                # alert
│   │   └── auth.py                 # users, audit_log
│   ├── schemas/                    # Pydantic v2 — one module per router group
│   ├── domain/                     # PURE. no I/O.
│   │   ├── rules.py                # 19–26
│   │   ├── health.py               # health derivation + EMA smoothing
│   │   ├── aggregation.py          # series, matrices, rollups
│   │   └── scheduling.py           # back-in-service, do-by
│   ├── repositories/               # one per aggregate
│   ├── services/                   # application use cases
│   ├── ml/
│   │   ├── model_store.py          # lifespan load, thread-safe singleton
│   │   ├── features.py             # window → 29-feature matrix
│   │   ├── inference.py            # rul + component health
│   │   ├── attribution.py          # baseline stats, z-scores, top sensors
│   │   └── fallback.py             # deterministic degradation curve
│   ├── realtime/
│   │   ├── ws.py                   # /ws/fleet, connection registry
│   │   ├── events.py               # event schemas
│   │   └── replay.py               # 1.2 s C-MAPSS loop, staggered offsets
│   ├── api/
│   │   ├── deps.py
│   │   └── v1/
│   │       ├── router.py           # aggregates all v1 routers
│   │       ├── auth.py             # POST /auth/login, GET /auth/me
│   │       ├── fleet.py            # /fleet/*, /aircraft/*
│   │       ├── maintenance.py      # /work-orders, /spares, /agencies, /alerts
│   │       ├── telemetry.py        # POST /telemetry
│   │       └── ml.py               # POST /internal/ml/predict
│   ├── static/                     # GLB assets mount
│   └── seed/
│       ├── run.py                  # entrypoint: python -m app.seed
│       ├── loaders/                # one loader per Drive CSV
│       ├── derived.py              # health, slots, work orders, alerts
│       └── cmapss.py               # bundle + index C-MAPSS
├── alembic/                        # migrations
├── data/
│   ├── ml/<variant>/               # staged model artifacts (gitignored)
│   ├── raw/                        # 8 Drive CSVs (vendored)
│   └── cmapss/                     # raw C-MAPSS FD001–FD004, train/test/RUL
├── tests/
│   ├── unit/                       # rules 19–26, health, scheduling — no DB
│   ├── integration/                # API + DB via Testcontainers-free SQLite fallback
│   └── fixtures/
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── alembic.ini
```

## 4. Technology decisions and rationale

| Choice | Alternative rejected | Reason |
|---|---|---|
| **FastAPI** | Flask, Django REST | Pydantic v2 validation is the contract with the frontend; async WebSocket support is native; automatic OpenAPI at `/docs` satisfies spec 6 with zero work |
| **SQLAlchemy 2.0 ORM** | Raw SQL, Tortoise | Typed `Mapped[]` models give the compiler a static view; 2.0 sessions are explicit about transaction boundaries, which the audit requirement needs |
| **Alembic** | `create_all` | Migrations are the only way a seeded DB can be rebuilt identically in CI and prod. `create_all` cannot alter a column |
| **PostgreSQL 16** | SQLite | Needs `JSONB` for `top_sensors`/`deviation`, real enum types, partial indexes for "latest snapshot per aircraft", and `TIMESTAMPTZ` for the audit trail |
| **Native XGBoost** (`Booster.save_model`) | ONNX / PyTorch / TorchServe | Per team decision. The model is already XGBoost; `save_model` is one call and needs no converter. `get_score(importance_type='gain')` gives the top-sensors attribution directly, which ONNX would not. Inference is ~2 ms, well inside the 100 ms budget |
| **ThreadPoolExecutor for inference** | Inline call | XGBoost releases the GIL. Running inference in the default executor keeps the event loop responsive during a WebSocket tick storm |
| **In-process event bus** (`asyncio.Queue` fan-out) | Redis Pub/Sub, Kafka | One API process in the compose file. Redis would be infrastructure with no consumer beyond this process |
| **Lifespan-managed singleton model store** | Reload per request | ~2 ms warm inference, but model load is ~1 s. Loaded once in `lifespan`, guarded by a lock, with `warmup()` inference so the first real request is not the slow one |
| **JWT (HS256), 3 roles** | OAuth2/OIDC, sessions | Fully offline requirement (spec 8) forbids an identity provider. HS256 with a shared secret from env is sufficient for a demo fleet |
| **SSE alternative considered** | WebSocket | Spec 42 mandates WebSocket, and client→server messages (subscribe/unsubscribe filters) benefit from duplex |

## 5. Request lifecycle

### 5.1 Read path (`GET /api/v1/fleet/summary`)

```
1  Request arrives            middleware: assign request_id, emit access log on completion
2  CORS preflight             only for browser-issued cross-origin requests
3  JWT verification           decode + verify signature + expiry  → 401 on failure
4  RBAC check                 role ∈ allowed set for the route       → 403 on failure
5  Pydantic query validation   ?window=60 coerced to int, bounds-checked
6  Route handler              thin; extracts path/query into a typed dataclass
7  Application function       fleet_summary(window)
8  Repository calls           3–4 queries, all indexed:
                                 latest aircraft_part per aircraft   (partial index)
                                 last `window` health_snapshots      (composite index)
                                 latest ml_prediction per aircraft  (partial index)
9  Domain functions           aggregation.fleet_series(...)  → rule 20 mission-ready
10 Rule application           rules.mission_ready(...), rules.worst_part(...)
11 Response serialisation     Pydantic v2 model_dump, no ORM lazy loads
12 Access log                 method, path, status, duration_ms, request_id
```

Budget: **< 5 ms** of framework overhead, < 200 ms total (§11).

**N+1 avoidance.** The dominant risk in a dashboard API is per-row queries inside a loop. The
rule is: **never query inside a comprehension.** Step 8 issues a fixed number of queries
regardless of aircraft count, then step 10 works on in-memory lists. With 8 aircraft this
matters less than with 100, but the constraint is kept so the code scales and so the
performance tests in §11 are meaningful.

### 5.2 Write path (`POST /api/v1/work-orders`)

```
1–5   same as read path, plus role guard: maintenance_officer | commander
6     Pydantic body validation
7     Route handler             thin
8     Application function      create_work_order(payload, user)
9     BEGIN                      via unit_of_work context manager
10    Domain pre-checks         part exists; aircraft exists; no duplicate open WO
11    Persist                   repository.add(...)  → flush to get the id
12    Rule 22 recomputation     action derived from current part risk, not client input
13    Alert evaluation          if risk ≥ watch and no open alert → create alert
14    Audit                     audit_log(entity='work_order', before=None, after=row)
15    Event publish             work_order.created → event_bus
16    COMMIT
17    Response 201 + Location
```

Transaction boundaries are explicit: **the audit row and the entity row commit or roll back
together.** A work order without its audit trail is treated as a bug, not a warning.

### 5.3 Write path with side tables (`POST /spares/{id}/reserve`)

Three tables in one transaction, in this order to avoid deadlock:

```
BEGIN
  SELECT ... FROM spare WHERE id = :id FOR UPDATE      ← pessimistic lock, stock check
  if stock <= 0 → ROLLBACK, 409 "no stock"
  UPDATE spare SET stock = stock - 1
  INSERT stock_movement(spare_id, delta=-1, reason='reserve', user_id)
  UPDATE work_order SET status='in_progress'
  INSERT audit_log(...)
COMMIT
```

`SELECT … FOR UPDATE` is mandatory: the demo fires reservations from multiple clients
concurrently and a read-then-write race would oversell stock.

## 6. Cross-cutting concerns

### 6.1 Configuration

All settings via `pydantic-settings`, prefix `FDT_`, read from environment with sane local
defaults. Nothing reads `os.environ` directly.

| Variable | Default | Purpose |
|---|---|---|
| `FDT_DATABASE_URL` | `postgresql+psycopg://fdt:fdt@localhost:5432/fdt` | SQLAlchemy URL |
| `FDT_JWT_SECRET` | dev-only placeholder | HS256 signing key — **must** be set in production |
| `FDT_JWT_TTL_MIN` | `720` | Token lifetime |
| `FDT_CORS_ORIGINS` | `http://localhost:5173` | Comma-separated allowlist |
| `FDT_DEMO_MODE` | `true` | Enables the replay loop |
| `FDT_DEMO_TICK_SECONDS` | `1.2` | Spec 43 interval |
| `FDT_RUL_CAP` | `125` | Spec rule 26 |
| `FDT_ML_VARIANT` | `all` | Artifact generation; resolves model + contract + baselines as a set |
| `FDT_ML_FALLBACK` | `true` | Allow deterministic fallback when artifact missing |
| `FDT_ML_DATASET` | `FD001` | Subset the demo replays (independent of the variant) |

### 6.2 Error handling

Domain exceptions, never bare `HTTPException` in application code:

| Domain exception | HTTP | Meaning |
|---|---|---|
| `NotFoundError` | 404 | Entity does not exist |
| `ConflictError` | 409 | State conflict — e.g. reserving out-of-stock |
| `ValidationError` | 422 | Business-rule violation — e.g. invalid status transition |
| `ForbiddenError` | 403 | Role not permitted |
| `ModelUnavailableError` | 503 | ML artifact missing **and** fallback disabled |

A single exception handler renders all of them as
`{"error": {"code": "...", "message": "...", "request_id": "..."}}`.
`request_id` is echoed in the `X-Request-ID` header and in the server log line, so a
frontend error report maps to exactly one backend log entry.

### 6.3 Observability

- **Structured JSON logs** — `request_id`, `route`, `status`, `duration_ms`, `user`.
- **Timing middleware** records every request; the 200 ms budget is asserted in a
  performance test (§11), not just hoped for.
- **ML latency** recorded per prediction in `ml_prediction.latency_ms`, so the 100 ms
  budget is measurable from the database after a demo run.
- **Health endpoint** `GET /healthz` — DB reachable, model loaded, replay task alive.
  `GET /readyz` additionally requires ≥ 1 aircraft seeded.

### 6.4 Security

| Concern | Mitigation |
|---|---|
| Password storage | `bcrypt`, cost 12. Plaintext never logged or returned |
| Token forgery | HS256, 24-bit minimum secret enforced at startup in non-dev mode |
| Privilege escalation | Role checked server-side per route; viewer is denied all mutations by a router-level dependency, not by per-handler `if` statements |
| SQL injection | ORM parameter binding throughout; no string-built SQL anywhere |
| CORS | Explicit origin allowlist. Never `*` with `allow_credentials=True` |
| Secrets in repo | `.env` gitignored; `JWT_SECRET` required, no committed default |
| Path traversal on `/models` | `StaticFiles` is mounted at a fixed directory; no user-controlled path segments in the mount |

## 7. Why the domain layer is pure

Spec item 52 requires unit tests for rules 19–26. If those rules were embedded in SQL or in
route handlers, the tests would need a live database and an ASGI client, and would run in
seconds rather than milliseconds. Keeping them pure means:

```python
# tests/unit/test_rules.py — no fixtures, no DB, no app
def test_watch_band_is_strictly_above_040():
    assert rules.risk_level(0.41) == "watch"
    assert rules.risk_level(0.40) == "critical"   # boundary falls through, per spec 19

def test_back_in_service_adds_lead_time_only_when_stock_zero():
    assert rules.back_in_service_days(3, 13, 39, stock=0) == 55
    assert rules.back_in_service_days(3, 13, 39, stock=4) == 16
```

The whole rules module runs in **under 10 ms**. That is only possible because it has no
imports beyond `stdlib` and `pydantic`. This constraint is what makes the test suite cheap
enough that nobody is tempted to skip it.

## 8. Transaction and concurrency model

| Operation | Isolation | Locking | Rationale |
|---|---|---|---|
| All GETs | READ COMMITTED (default) | none | Read-only; MVCC handles it |
| Replay tick writes | READ COMMITTED | row lock on the one `aircraft` row being advanced | One aircraft per tick; contention is negligible |
| `POST /telemetry` | READ COMMITTED | none on insert | Batch insert with `ON CONFLICT DO UPDATE` on `(aircraft_id, cycle)` |
| `POST /spares/{id}/reserve` | READ COMMITTED | `SELECT … FOR UPDATE` on the spare | Prevents oversell under concurrent reservations |
| `POST /agencies/{id}/bookings` | READ COMMITTED | `FOR UPDATE` on the agency | Prevents double-booking the last slot |
| Seed | READ COMMITTED | none; runs before serving | Idempotent via upsert |

**Why READ COMMITTED is sufficient.** The only multi-row invariants are stock counts and
agency slots, and both are guarded by explicit row locks. Health snapshots are append-only
and never read mid-transaction by another writer. SERIALIZABLE would add retry loops for no
correctness gain at this scale.

## 9. Static asset serving

GLB models are served by `StaticFiles` mounted at `/models`, not embedded in HTML:

```python
app.mount("/models", StaticFiles(directory="app/static/models"), name="models")
```

`StaticFiles` already emits `ETag` and honours `Last-Modified`, giving conditional GETs for
free. Because GLB content is content-addressed by filename, the mount adds an explicit
immutable cache header:

```
Cache-Control: public, max-age=31536000, immutable
```

Served in **binary** with `Content-Type: model/gltf-binary`. Nginx in front adds
`gzip` for `.glb` — worthwhile, since geometry compresses well — but **not** `brotli` at
`q=11`, which costs more CPU than it saves on a demo.

## 10. Testing strategy

| Layer | Scope | Dependencies | Target runtime |
|---|---|---|---|
| Unit | `domain/rules.py` (19–26), `domain/health.py`, `domain/scheduling.py`, `ml/features.py` | none | < 50 ms |
| Integration | Every endpoint, full request/response validation, RBAC matrix | Postgres (or SQLite for speed) | < 30 s |
| Contract | Response schemas against a committed OpenAPI snapshot | none | < 5 s |
| Performance | 200 ms / 100 ms budgets, N+1 detection via query counter | Postgres | < 60 s |
| ML quality | RUL MAE ≤ 25 on the FD001 validation split; component health in [0,1] | model artifact | < 20 s |

The query counter in the performance suite is what enforces §5.1's fixed-query rule: the
test asserts the query count is constant as the aircraft count grows, so a reintroduced N+1
fails CI rather than surfacing as a slow demo.