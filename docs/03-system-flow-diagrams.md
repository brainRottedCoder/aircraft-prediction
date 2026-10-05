# 03 — System Flow Diagrams

All diagrams are Mermaid. Sequence numbers in prose refer to the steps marked in the
sequence diagrams.

---

## 1. Container architecture

```mermaid
flowchart TB
    subgraph Client["Browser — Fleet Digital Twin UI"]
        UI[React SPA]
        WSC[WebSocket client]
        GLB[Three.js / GLTF viewer]
    end

    subgraph Backend["FastAPI — single process"]
        direction TB
        MW[Request-ID + timing middleware]
        CORS[CORS middleware]
        AUTH[JWT auth + RBAC dependency]
        RT["REST routers /api/v1"]
        WSR["WebSocket /ws/fleet"]
        STAT["StaticFiles /models"]

        subgraph APP["Application layer — use cases"]
            UC1[fleet_summary]
            UC2[aircraft / engine / part detail]
            UC3[actions / schedule]
            UC4[work_orders / spares / agencies / alerts]
            UC5[telemetry_ingest]
        end

        subgraph DOM["Domain layer — PURE, no I/O"]
            R[rules.py<br/>19–26]
            H[health.py<br/>derivation + smoothing]
            A[aggregation.py<br/>series, matrices]
            S[scheduling.py<br/>back-in-service, do-by]
        end

        subgraph MLS["ML subsystem"]
            MS[model_store<br/>load once at lifespan]
            FT[features<br/>30 cycles → 29 features]
            INF[inference<br/>Booster.predict]
            ATT[attribution<br/>z-scores, top sensors]
            FB[fallback<br/>deterministic curve]
        end

        BUS[event_bus<br/>asyncio fan-out]
        REP[replay_engine<br/>1.2 s C-MAPSS loop]
    end

    subgraph Data["Storage"]
        PG[(PostgreSQL 16<br/>operational + reference + time-series)]
        ART[(data/ml/<variant>/<br/>xgboost_*.json<br/>contract + baselines)]
        FILES[/data/raw 8 Drive CSVs<br/>/data/cmapss train_FD001.txt/]
        GLBDIR[(frontend/public/models/*.glb<br/>served by nginx, not the API)]
    end

    UI -->|HTTPS /api/v1| MW
    WSC -->|WSS /ws/fleet| WSR
    GLB -->|GET /models/*.glb| STAT

    MW --> CORS --> AUTH
    AUTH --> RT
    RT --> APP
    APP --> DOM
    APP --> PG
    APP --> MLS
    REP --> MLS
    REP --> PG
    REP --> BUS
    BUS -.->|cycle.tick health.updated alert.raised| WSR
    REP -.-> reads from FILES
    MS -.-> loads from ART
    UC5 -->|POST /telemetry| REP

    STAT --> GLBDIR
    UC1 & UC2 & UC3 & UC4 -.-> read from FILES

    classDef pure fill:#e8f5e9,stroke:#2e7d32,stroke-width:2px
    classDef store fill:#e3f2fd,stroke:#1565c0
    class R,H,A,S pure
    class PG,ART,FILES,GLBDIR store
```

---

## 2. Layered request flow

```mermaid
flowchart TD
    IN([HTTP request]) --> MW["1. request_id + timing middleware"]
    MW --> CO{"2. CORS origin allowed?"}
    CO -->|no| R403a[403]
    CO -->|yes| JW["3. verify JWT"]
    JW -->|invalid/expired| R401[401]
    JW --> RB{"4. role permitted for route?"}
    RB -->|no| R403[403 ForbiddenError]
    RB -->|yes| VAL["5. Pydantic v2 validate path/query/body"]
    VAL -->|invalid| R422[422 ValidationError]
    VAL --> H["6. route handler — thin"]
    H --> AF["7. application function"]
    AF --> REPO["8. repositories — fixed query count"]
    REPO --> DBN[(PostgreSQL)]
    AF --> DOM["9. domain functions (pure)"]
    DOM --> SER["10. serialise Pydantic response"]
    SER --> OUT([200 + JSON])
    AF -.->|write only| TX[["9b. BEGIN → mutate → audit_log → COMMIT"]]
```

---

## 3. Startup and seeding

```mermaid
sequenceDiagram
    autonumber
    participant O as Operator
    participant API as FastAPI
    participant AL as Alembic
    participant PG as PostgreSQL
    participant SD as seed.run
    participant FS as /data (CSVs, C-MAPSS)
    participant MS as model_store

    O->>API: docker compose up
    API->>PG: SELECT 1 (connect retry, 30 × 1 s)
    API->>AL: alembic upgrade head
    AL->>PG: CREATE TABLE … (20 tables, enums, indexes)
    O->>API: POST /api/v1/seed/run  (or python -m app.seed)
    API->>PG: SELECT count(*) FROM aircraft
    alt already seeded
        API-->>O: 200 {"status":"skipped","aircraft":8}
    else empty
        API->>SD: execute()
        SD->>FS: read 8 Drive CSVs
        SD->>PG: COPY aircraft_ref (100), component_ref (600), flight_ops_monthly (3300)
        SD->>PG: INSERT agency (6) from maintenance_agencies.csv
        SD->>PG: INSERT spare (40) from spare_parts.csv
        SD->>PG: INSERT technical_record (1500) + snag (800)
        SD->>SD: derive health: engine←later ML, others←maintenance_burden_v1
        SD->>PG: INSERT aircraft (8: Fighter-01..08 ← AC001..AC008)
        SD->>PG: INSERT part (5) + aircraft_part (40 rows)
        SD->>FS: read train_FD001.txt
        SD->>SD: bind Fighter-0N → cmapss_unit_id (1:1)
        SD->>PG: INSERT work_order from maintenance_schedule (status mapped)
        SD->>PG: INSERT alert from risk transitions
        SD->>PG: INSERT audit_log rows for the seed actor
        SD-->>API: {"aircraft":8,"parts":40,"spares":40,"records":2300,"work_orders":600}
        API-->>O: 200 {"status":"seeded", …}
    end
    API->>MS: load data/ml/<variant>/xgboost_*.json
    alt artifact present
        MS->>MS: warmup() inference on a fixture window
        MS-->>API: {"model":"xgboost_all_full","ready":true}
    else artifact missing
        MS-->>API: {"model":"fallback","ready":true,"warn":"artifact absent"}
    end
    API->>API: start replay task if FDT_DEMO_MODE=true
    API-->>O: 200 /healthz {"db":true,"model":true,"replay":true}
```

**Seed is idempotent.** Every loader upserts on its natural key (`AC001`, `PART001`,
`MR00001`, `SCH00001`, …), so re-running never duplicates and never destroys operator
changes made at runtime. Runtime-created rows use `source_ref = NULL` and are never touched
by a re-seed.

---

## 4. Replay loop — one demo tick

This is the heart of the demo. One tick = 1.2 s = advance every active aircraft by exactly
one C-MAPSS cycle.

```mermaid
sequenceDiagram
    autonumber
    participant T as replay task
    participant FS as train_FD001.txt (in memory)
    participant INF as ML inference
    participant H as domain.health
    participant R as domain.rules
    participant PG as PostgreSQL
    participant BUS as event_bus
    participant WS as WebSocket clients

    loop every FDT_DEMO_TICK_SECONDS (1.2 s)
        T->>T: for each of 8 aircraft (staggered start offsets)
        T->>FS: read row (unit_id, cycle+1, settings×3, sensors×21)
        T->>INF: predict(window = last 30 cycles)
        INF-->>T: {rul, fan, hpc, hpt, lpt, top_sensors, model}
        T->>H: engine health = clamp(rul/125, 0, 1) → EMA(α=0.3)
        T->>R: risk_level(health)  → healthy | watch | critical
        T->>PG: INSERT engine_telemetry (aircraft_id, cycle) ON CONFLICT DO UPDATE
        T->>PG: INSERT component_health (fan,hpc,hpt,lpt)
        T->>PG: INSERT ml_prediction (rul, components, top_sensors JSONB, latency_ms)
        T->>PG: INSERT health_snapshot for engine
        alt risk changed vs previous cycle
            T->>PG: UPDATE aircraft_part SET health, risk, rul, do_by_cycle
            T->>R: recompute worst_part, mission_ready, risk on aircraft row
            T->>PG: INSERT alert if new risk ≥ watch and not already open
        end
        T->>PG: UPDATE aircraft SET current_cycle, rul, mission_ready, worst_part
        T->>BUS: publish cycle.tick   {cycle}
        T->>BUS: publish health.updated {aircraft, part, health, risk}
        T->>BUS: publish alert.raised  {aircraft, part, level, message}
        BUS->>WS: fan-out to all subscribed sockets
        T->>T: if cycle == max(unit) → wrap to ml_window (loop, no gap)<br/>Note: not cycle 1 — that yields a short feature<br/>window and a silent fallback. docs/09 §1.2
    end
```

**Ordering guarantee.** All writes for one aircraft-tick commit in a single transaction
before any event is published. A client can therefore never observe a `cycle.tick` for
cycle *n* alongside a `health.updated` still describing cycle *n−1*.

---

## 5. ML inference flow

```mermaid
flowchart TD
    A["window: last 30 cycles<br/>14 spec sensors + s6 + 3 settings"] --> B{"s6 present?"}
    B -->|no| B1["impute s6 = 21.61 (FD001 median)<br/>set s6_imputed = true"]
    B -->|yes| C
    B1 --> C["assign absolute cycle numbers<br/>from aircraft.current_cycle"]
    C --> D["regime = nearest centroid<br/>on (setting_1..3)"]
    D --> E["per-sensor z-score vs baseline<br/>median/MAD over unit's first 20 cycles"]
    E --> F["build 29-feature matrix<br/>15 sensors + 2 settings + 12 rolling mean/std"]
    F --> G{"model artifact loaded?"}
    G -->|yes| H["Booster.predict → rul_raw"]
    G -->|no| I["fallback: rul = 125 − cycle"]
    H --> J["rul = clamp(round(rul_raw), 0, 125)"]
    I --> J
    J --> K["component health from spec 47 grouping<br/>fan s8,s13 · hpc s3,s7,s11 · hpt s20,s21 · lpt s4"]
    K --> L["health = 1 − clamp(mean|z|/3.0, 0, 1)"]
    L --> M["top_sensors = blend(get_score(gain), |z|) top 5"]
    M --> N["EMA smoothing α=0.3 over cycles"]
    N --> O["return {rul, fan, hpc, hpt, lpt, top_sensors, deviation, model, latency_ms}"]
```

---

## 6. Read path — `GET /api/v1/aircraft/{id}`

```mermaid
sequenceDiagram
    autonumber
    participant FE as Frontend
    participant API as FastAPI
    participant APP as aircraft_detail()
    participant REPO as Repository
    participant PG as PostgreSQL
    participant DOM as domain (pure)

    FE->>API: GET /api/v1/aircraft/Fighter-01  (Bearer JWT)
    API->>API: request_id, CORS, JWT verify, role ∈ {viewer, officer, commander}
    API->>APP: aircraft_detail(code="Fighter-01")
    APP->>REPO: get_aircraft(code)
    REPO->>PG: SELECT * FROM aircraft WHERE code=$1
    PG-->>REPO: row
    APP->>REPO: get_parts(aircraft_id)          -- one query for all 5 parts
    REPO->>PG: SELECT * FROM aircraft_part WHERE aircraft_id=$1
    PG-->>REPO: 5 rows
    APP->>DOM: risk_level(health) per part      -- rules 19
    APP->>DOM: worst_part(parts)                -- rules 21 + tie-break
    APP->>DOM: mission_ready(rul, parts)        -- rules 20
    APP->>DOM: do_by_cycle(rul) for non-healthy -- rules 23
    APP-->>API: AircraftDetailDTO
    API-->>FE: 200 {aircraft, parts[5], worst_part, mission_ready, rul, risk}
```

**Five parts, one query.** Not five queries — the N+1 rule from §2 of the architecture doc.

---

## 7. Write path — `POST /api/v1/work-orders`

```mermaid
sequenceDiagram
    autonumber
    participant U as Maintenance officer
    participant API as FastAPI
    participant APP as create_work_order()
    participant DOM as domain
    participant REPO as Repository
    participant PG as PostgreSQL
    participant BUS as event_bus

    U->>API: POST /api/v1/work-orders {aircraft, part, due_date, priority}
    API->>API: JWT verify
    API->>API: RBAC: role ∈ {maintenance_officer, commander} else 403
    API->>API: Pydantic validate body
    API->>APP: create_work_order(payload, user)
    APP->>PG: BEGIN
    APP->>REPO: get_aircraft, get_part
    REPO->>PG: SELECT … (2 queries)
    APP->>DOM: rule 22 → action from CURRENT risk (client cannot set action)
    APP->>DOM: duplicate check — open WO already exists for (aircraft, part)?
    alt duplicate
        APP->>PG: ROLLBACK
        API-->>U: 409 ConflictError "open work order already exists"
    end
    APP->>REPO: add_work_order(...)
    REPO->>PG: INSERT … RETURNING id
    APP->>REPO: create_alert_if_needed(risk ≥ watch)
    APP->>REPO: write_audit(entity, id, action='create', actor=user, before=null, after=row)
    REPO->>PG: INSERT audit_log
    APP->>PG: COMMIT
    APP->>BUS: publish work_order.created
    API-->>U: 201 {work_order} + Location: /api/v1/work-orders/{id}
```

---

## 8. Write path — `POST /api/v1/spares/{id}/reserve`

```mermaid
sequenceDiagram
    autonumber
    participant U as Maintenance officer
    participant API as FastAPI
    participant APP as reserve_spare()
    participant PG as PostgreSQL

    U->>API: POST /api/v1/spares/PART007/reserve {work_order_id}
    API->>API: RBAC: maintenance_officer | commander
    API->>APP: reserve_spare(part_id, user)
    APP->>PG: BEGIN
    APP->>PG: SELECT stock FROM spare WHERE part_id=$1 FOR UPDATE
    PG-->>APP: stock = 0
    alt stock <= 0
        APP->>PG: ROLLBACK
        API-->>U: 409 {"code":"OUT_OF_STOCK", "lead_time_days":39}
    else stock > 0
        APP->>PG: UPDATE spare SET stock = stock − 1
        APP->>PG: INSERT stock_movement(spare_id, delta=−1, reason='reserve', user_id)
        APP->>PG: UPDATE work_order SET status='in_progress'
        APP->>PG: INSERT audit_log(entity='spare', before={stock:1}, after={stock:0})
        APP->>PG: COMMIT
        API-->>U: 200 {part_id, stock_remaining, reserved_at}
    end
```

`FOR UPDATE` is what makes the stock check safe under the concurrent reservations a live
demo produces. Without it, two officers can both read `stock = 1` and oversell to −1.

---

## 9. Alerts lifecycle

```mermaid
stateDiagram-v2
    [*] --> none: risk healthy (no alert row)

    none --> open_watch: health drops ≤ 0.70<br/>alert(level=watch)
    none --> open_critical: health drops ≤ 0.40<br/>alert(level=critical)

    open_watch --> open_critical: health falls to ≤ 0.40<br/>level escalated, same row updated
    open_watch --> acked_watch: POST /alerts/{id}/ack
    open_critical --> acked_critical: POST /alerts/{id}/ack

    acked_watch --> open_critical: health still falling, re-alert at next band
    acked_watch --> resolved: health recovers > 0.70
    acked_critical --> resolved: health recovers > 0.70
    open_critical --> resolved: health recovers > 0.70

    resolved --> open_watch: health degrades again<br/>(new row, new alert)

    note right of open_critical
        An open alert is never silently
        deleted. Acknowledgement records
        who accepted the risk and when.
    end note
```

---

## 10. WebSocket lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant FE as Frontend
    participant WS as ws endpoint
    participant REG as connection registry
    participant BUS as event_bus
    participant REP as replay task

    FE->>WS: WSS /ws/fleet?token=<JWT>
    WS->>WS: verify token from query param (browsers cannot set WS headers)
    alt invalid
        WS-->>FE: close(code=4401, reason="unauthorized")
    else valid
        WS-->>FE: {"type":"connection.ready","payload":{"aircraft":8,"demo_mode":true,"tick_seconds":1.2}}
        WS->>REG: register(socket, filters=all)
        loop every tick
            REP->>BUS: cycle.tick / health.updated / alert.raised
            BUS->>REG: fan-out
            REG->>FE: {"type":"health.updated","payload":{…}}
        end
        FE->>WS: {"type":"subscribe","payload":{"aircraft":["Fighter-01"]}}
        WS->>REG: narrow filter
        FE->>WS: {"type":"ping"}
        WS-->>FE: {"type":"pong"}
        FE--xWS: connection lost
        REG->>REG: unregister, cancel any pending sends
    end
```

---

## 11. Error and degradation flow

```mermaid
flowchart TD
    E[Exception in handler] --> K{Exception type}
    K -->|DomainError subclass| H1[map to 404 / 403 / 409 / 422]
    K -->|RequestValidationError| H2[422 with field-level detail]
    K -->|IntegrityError| H3{constraint}
    H3 -->|unique violation| H4[409 duplicate]
    H3 -->|FK violation| H5[422 unknown aircraft/part]
    H3 -->|check violation| H6[422 value out of range, e.g. health 1.4]
    K -->|ModelUnavailableError| H7[503 + Retry-After]
    K -->|Unhandled| H8[500, log traceback, hide internals from client]
    H1 & H2 & H4 & H5 & H6 & H7 & H8 --> R["{error:{code, message, request_id}}"]

    ML[ML inference fails or artifact missing] --> FB[Deterministic fallback<br/>rul = 125 − cycle]
    FB --> FLAG["response carries model:'fallback'<br/>demo continues"]
    FLAG --> OK[200 OK]
```