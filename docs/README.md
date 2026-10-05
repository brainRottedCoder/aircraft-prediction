# Fleet Digital Twin — Documentation

Design and implementation documentation for the Fleet Digital Twin predictive-maintenance
platform. Stack: **FastAPI + PostgreSQL + SQLAlchemy 2 + XGBoost (C-MAPSS)**, fully
offline.

> **Repository layout has changed.** These documents used to live in `backend/docs/`, and
> the API in `backend/backend/`. The tree is now flat: API in `backend/`, SPA in
> `frontend/`, Dockerfiles in `docker/`, and these documents at the repository root.
> References to `../docs/` still resolve from `backend/`; references to `models_artifacts/`
> in documents 10, 14 and 15 are deliberate — they describe a real past failure and the
> trap that caused it, not the current layout. See the
> [main README](../README.md) for the authoritative structure.

`05-database-design.prisma` is a **dbdiagram.io export** of the schema, kept for visual
import only. Nothing in the codebase reads it, and it is not generated from: the
authoritative schema is the Alembic migration in `backend/alembic/versions/` plus
`backend/app/models/`, both of which are enforced at runtime.

## Data sources this design is built on

| Source | Contents | Role |
|---|---|---|
| Google Drive `Project2` (8 CSVs, ~5,900 rows) | `aircraft`, `components`, `flight_operations`, `maintenance_agencies`, `maintenance_records`, `maintenance_schedule`, `snag_logs`, `spare_parts` | Fleet, parts, spares, agencies, work orders, technical records, non-engine degradation |
| NASA C-MAPSS (FD001/002/003/004) | 20,631+ cycle rows, 21 sensors, 3 settings, ground-truth RUL | Engine telemetry replay + RUL model |
| `Model_training_249.ipynb` | XGBoost RUL model (FD001 validation MAE 11.41, RMSE 16.05, R² 0.849; official test RMSE 18.37) — see [12](12-model-training-pipeline.md) and [13](13-multi-regime-training-report.md) | Trained model + artifacts on Drive |

## Documents

| # | File | Purpose |
|---|---|---|
| 01 | [Requirements Evaluation](01-requirements-evaluation.md) | Spec-vs-data gap analysis, decisions taken |
| 02 | [Backend Architecture](02-backend-architecture.md) | Layers, module layout, technology choices, request lifecycle |
| 03 | [System Flow Diagrams](03-system-flow-diagrams.md) | Architecture, ingestion, replay, ML inference, write-path sequence diagrams |
| 04 | [User Flow Diagrams](04-user-flow-diagrams.md) | Per-role journeys for commander, maintenance officer, viewer |
| 05 | [Database Design](05-database-design.md) | Full DDL, indexes, relationships, derived-column strategy |
| 06 | [API Specification](06-api-specification.md) | Every endpoint, request/response shapes, status codes |
| 07 | [Business Rules](07-business-rules.md) | Rules 19–26, exact algorithms, tie-breaks, worked examples |
| 08 | [ML Service](08-ml-service.md) | Feature contract, artifacts, component-health mapping, fallbacks |
| 09 | [Realtime & Demo Mode](09-realtime-and-demo-mode.md) | WebSocket protocol, replay engine, loop behaviour |
| 10 | [Development Plan](10-development-plan.md) | 8 phases, tasks, exit criteria, sequencing, risks |
| 11 | [Deployment & Non-Functional](11-deployment-and-nfr.md) | Docker, compose, performance budgets, observability, security |
| 12 | [Model Training Pipeline](12-model-training-pipeline.md) | `Model_training_249.ipynb` stage by stage: what it does, why, and the results |
| 13 | [Multi-Regime Training Report](13-multi-regime-training-report.md) | Phase 2: FD002–FD004 regime handling, cross-validation results, verdict |
| 14 | [**Final Status Report**](14-final-status-report.md) | **Start here.** Current state, measured numbers, open defects, next steps |
| 15 | [**Serving the Pooled Model**](15-serving-the-pooled-model.md) | The 32-column `ALL` contract, the two constant sensors, subset-aware baselines, deployment traps |

## Start here

**[14 — Final Status Report](14-final-status-report.md)** — what exists, what the numbers
are, what is broken, and what to do next. Read it before building anything: the feature
contract in [08](08-ml-service.md) changed from 22 features to 29, and several docs
described a model that no longer exists.

## Reading order for a new engineer

1. [01](01-requirements-evaluation.md) — understand what data actually exists
2. [05](05-database-design.md) — the schema everything hangs off
3. [07](07-business-rules.md) — the logic the frontend depends on
4. [02](02-backend-architecture.md) → [03](03-system-flow-diagrams.md) — how it runs
5. [06](06-api-specification.md) — the contract
6. [08](08-ml-service.md), [09](09-realtime-and-demo-mode.md) — the intelligent parts
7. [15](15-serving-the-pooled-model.md) — the multi-subset model actually being served
8. [10](10-development-plan.md), [11](11-deployment-and-nfr.md) — how to build and ship it

## Ten decisions that shape everything else

1. **ML scope is C-MAPSS only.** Drive CSVs are supporting/reference data for the UI, never model inputs.
2. **Model is native XGBoost**, persisted with `Booster.save_model`, loaded once at startup. No ONNX conversion.
3. **Fleet = 8 curated aircraft** (`Fighter-01..08` ← `AC001..AC008`), each bound 1:1 to a C-MAPSS engine unit. All 100 Drive aircraft and 600 components are retained in reference tables.
4. **Only the engine is data-driven for health.** radar / gear / hyd / fuel derive health from `maintenance_records.fault_type` and `snag_logs` and are always flagged `simulated: true`.
5. **Canonical sensor vector is 15 sensors, and `cycle` is NOT a model input** — `sensor_6` is included because the trained model uses it; `cycle` was removed because on a truncated test set it is a leakage vector, not a measurement. The FD001 contract is **29 features** (2 settings + 15 sensors + 12 rolling mean/std). The **served** model is the pooled 4-subset refit at **32 features** — it adds `regime_global` and `s10`/`s16`, which are constant in FD001 — see [15](15-serving-the-pooled-model.md).
6. **Rules 19–26 are pure functions** with no DB access, unit-tested independently.
7. **RUL is capped at 125 cycles** (spec rule 26); C-MAPSS FD001 engines actually live 128–362 cycles, so the cap is a product decision.
8. **Risk thresholds are strict `>`**: `0.7` → watch, `0.4` → critical.
9. **Every mutation writes `audit_log`** with actor, timestamp and before/after JSON.
10. **Fully offline.** No outbound network calls at runtime; C-MAPSS and Drive CSVs are bundled into the image.

## Conventions used in these docs

- Mermaid for all diagrams (renders on GitHub, VS Code, Notion).
- `snake_case` for columns, `PascalCase` for classes, `/api/v1` prefix on all routes.
- "cycle" always means a C-MAPSS flight cycle. Drive data uses "flight_cycles" (monthly aggregates) — never conflate the two.
- Cross-references use `file.md § Section`.