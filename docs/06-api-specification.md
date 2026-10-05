# 06 — API Specification

Base URL `/api/v1`. All responses JSON. Interactive docs at `/docs`, schema at
`/openapi.json`. Every response is the exact Pydantic v2 model named below — this document
and the generated OpenAPI schema must not diverge.

## Conventions

**Auth.** All endpoints except `/auth/login`, `/healthz`, `/readyz` and `/docs` require
`Authorization: Bearer <jwt>`. The WebSocket takes the token as a query parameter because
browsers cannot set headers on a WS handshake.

**Error envelope.** Every non-2xx response:

```json
{
  "error": {
    "code": "OUT_OF_STOCK",
    "message": "PART007 has no stock. Lead time is 39 days.",
    "request_id": "01JQ8X4T2M9K7P3R0S5V6W8ZAB",
    "detail": { "stock": 0, "lead_time_days": 39 }
  }
}
```

| Status | When |
|---|---|
| `400` | Malformed request that Pydantic cannot describe |
| `401` | Missing, malformed or expired token |
| `403` | Authenticated but role not permitted |
| `404` | Entity does not exist |
| `409` | State conflict — duplicate WO, no stock, bad status transition |
| `422` | Validation failure, with field paths in `detail` |
| `503` | ML artifact missing and fallback disabled |

**Roles.** `viewer` ⊂ `maintenance_officer` ⊂ `commander` for read access; mutation requires
`maintenance_officer` or `commander`.

**Numbers.** `health` is a float rounded to 3 dp, always in `[0, 1]`. `rul`,
`back_in_service_days`, `do_by_cycle` are integers. Cycles are absolute, never relative.

---

## 1. Auth

### `POST /api/v1/auth/login`

```json
// request
{ "username": "commander", "password": "••••••••" }
```

```json
// 200
{
  "access_token": "eyJhbGciOiJIUzI1NiIs…",
  "token_type": "bearer",
  "expires_in": 43200,
  "user": { "id": 1, "username": "commander", "full_name": "Cmdr. A. Rao", "role": "commander" }
}
```

`401` on bad credentials. The message is always generic — no username enumeration.

### `GET /api/v1/auth/me`

```json
{ "id": 2, "username": "officer", "full_name": "M. Iyer", "role": "maintenance_officer",
  "permissions": { "can_mutate": true, "can_manage_agencies": false } }
```

---

## 2. Fleet

### `GET /api/v1/fleet/summary` — spec 27

Roles: all. **The dashboard's primary call.**

```json
{
  "mission_ready_count": 6,
  "total_aircraft": 8,
  "critical_parts": 3,
  "average_rul": 96.4,
  "lowest_rul_aircraft": { "id": 6, "code": "Fighter-06", "rul": 24, "name": "Fighter-06" },
  "avg_availability": 0.9412,
  "risk_breakdown": { "healthy": 28, "watch": 9, "critical": 3 },
  "series": {
    "window": 60,
    "fleet_avg_health": [ { "cycle": 142, "value": 0.71 }, … ],
    "weakest_aircraft_health": [ { "cycle": 142, "value": 0.39 }, … ]
  },
  "generated_at": "2026-10-03T11:27:04Z",
  "demo_mode": true
}
```

| Field | Rule |
|---|---|
| `mission_ready_count` | rule 20 |
| `critical_parts` | count of `aircraft_part` rows with `risk_level = 'critical'` |
| `average_rul` | mean over the 8 engines, already capped at 125 (rule 26) |
| `lowest_rul_aircraft` | `null` if the fleet is empty |
| `fleet_avg_health` | mean engine health per cycle across the window |
| `weakest_aircraft_health` | engine health of the current worst aircraft, same cycles |

Both series carry the same `cycle` values so the frontend can plot them on one axis.

### `GET /api/v1/aircraft` — spec 28

```json
{
  "items": [
    { "id": 1, "code": "Fighter-01", "name": "Fighter-01",
      "tail_number": "XX-1001", "model": "Type-B", "home_base": "Base-E",
      "mission_ready": true, "rul": 118, "current_cycle": 87,
      "worst_part": "engine", "risk": "healthy",
      "engine_health": 0.94, "parts": { "engine": "healthy", "radar": "healthy",
                                        "gear": "watch", "hyd": "healthy", "fuel": "healthy" } }
  ],
  "total": 8
}
```

Query params: `risk` (`healthy|watch|critical`), `ready` (`true|false`).

### `GET /api/v1/aircraft/{id}` — spec 29

`{id}` accepts the code (`Fighter-01`) or the numeric id.

```json
{
  "id": 1, "code": "Fighter-01", "name": "Fighter-01",
  "tail_number": "XX-1001", "model": "Type-B", "home_base": "Base-E",
  "current_cycle": 87, "rul": 118, "mission_ready": true,
  "risk": "healthy", "worst_part": "engine",
  "availability": 0.9531,
  "high_stress_sorties_last_12m": 14,
  "parts": [
    { "part": "engine", "label": "Engine", "health": 0.94, "risk": "healthy",
      "rul": 118, "do_by_cycle": null, "worst_component": null,
      "simulated": false, "method": "rul_xgb_fd001", "updated_at": "2026-10-03T11:27:04Z" },
    { "part": "radar", "label": "Radar", "health": 0.81, "risk": "healthy",
      "rul": null, "do_by_cycle": null, "worst_component": null,
      "simulated": true, "method": "maintenance_burden_v1",
      "updated_at": "2026-10-03T10:02:11Z" }
  ],
  "parts_note": "radar, gear, hyd and fuel have no sensor source and are derived from maintenance history (simulated: true)."
}
```

**Always five parts**, always in `engine, radar, gear, hyd, fuel` order.

### `GET /api/v1/aircraft/{id}/engine` — spec 30

Query: `window` (default 60, range 1–500).

```json
{
  "aircraft": "Fighter-01",
  "current_cycle": 87,
  "rul": 118,
  "rul_capped": false,
  "health": 0.94,
  "risk": "healthy",
  "mission_ready": true,
  "components": { "fan": 0.96, "hpc": 0.93, "hpt": 0.95, "lpt": 0.92 },
  "weakest_component": "lpt",
  "weakest_component_sensor": "s4",
  "engine_spare": { "part_ref_id": "PART031", "item_name": "LPT Blade",
                     "stock": 6, "lead_time_days": 12, "criticality": "High" },
  "top_sensors": [
    { "sensor": "s11", "label": "Sensor 11", "contribution": 0.19, "z": 2.41, "health_impact": 0.20 },
    { "sensor": "s4",  "label": "Sensor 4",  "contribution": 0.07, "z": 1.88, "health_impact": 0.08 }
  ],
  "component_sensor_map": {
    "fan": ["s8", "s13"], "hpc": ["s3", "s7", "s9", "s11"],
    "hpt": ["s20", "s21"], "lpt": ["s4"]
  },
  "history": [
    { "cycle": 28, "health": 1.0,  "rul": 125, "fan": 1.0,  "hpc": 1.0,  "hpt": 1.0, "lpt": 1.0 },
    { "cycle": 29, "health": 0.99, "rul": 124, "fan": 1.0,  "hpc": 0.99, "hpt": 1.0, "lpt": 0.99 }
  ],
  "model": { "version": "rul_xgb_fd001", "dataset": "FD001", "mae": 11.41, "s6_imputed": false }
}
```

| Field | Meaning |
|---|---|
| `rul_capped` | `true` when the raw prediction exceeded 125 (rule 26) |
| `weakest_component` | rule 25 — drives which spare is recommended |
| `history` | exactly `window` entries, oldest first |
| `top_sensors` | ≤ 5, sorted by blended gain × |z| |
| `model.version` | `"fallback"` when the artifact is unavailable |

### `GET /api/v1/aircraft/{id}/parts/{part}` — spec 31

`{part}` ∈ `engine | radar | gear | hyd | fuel`.

```json
{
  "aircraft": "Fighter-01",
  "part": "engine",
  "label": "Engine",
  "health": 0.62,
  "risk": "watch",
  "simulated": false,
  "method": "rul_xgb_fd001",
  "rul": 118,
  "do_by_cycle": 108,
  "do_by_in_cycles": 21,
  "action": "Plan inspection",
  "components": { "fan": 0.66, "hpc": 0.61, "hpt": 0.70, "lpt": 0.59 },
  "weakest_component": "lpt",
  "records": [
    { "id": "MR00491", "date": "2022-04-28", "type": "Preventive Replacement",
      "fault": null, "action": "Preventive replacement of life-limited component",
      "agency": "MA006", "downtime_hours": 49.6, "cycles_at_event": 283, "outcome": "Resolved" },
    { "id": "MR00268", "date": "2022-09-09", "type": "Preventive Replacement",
      "fault": null, "action": "Scheduled component replacement",
      "agency": "MA006", "downtime_hours": 63.1, "cycles_at_event": 298, "outcome": "Resolved" }
  ],
  "spare": { "part_ref_id": "PART031", "item_name": "LPT Blade", "stock": 0,
             "lead_time_days": 12, "minimum_stock": 5, "criticality": "High",
             "supplier": "SkyForge Components", "in_stock": false },
  "agency": { "id": 5, "agency_ref_id": "MA005", "name": "Vector Engine Works",
              "specialisation": "engine", "free_slot_days": 2,
              "turnaround_days": 24, "location": "Base-C" },
  "back_in_service_days": 38,
  "back_in_service_breakdown": { "slot_days": 2, "turnaround_days": 24,
                                 "lead_time_days": 12, "lead_time_applied": true }
}
```

`records` is **exactly the last 2** (spec 31). `back_in_service_breakdown` is included so
the frontend can render rule 24 as `2 + 24 + 12` rather than an unexplained `38`.

For non-engine parts, `components`, `rul` and `do_by_cycle` are `null` and `spare`/`agency`
resolve from that part's own mapping (see [01 §6](01-requirements-evaluation.md)).

### `GET /api/v1/fleet/heatmap` — spec 32

```json
{
  "parts": ["engine", "radar", "gear", "hyd", "fuel"],
  "aircraft": [
    { "code": "Fighter-01", "cells": [
        { "part": "engine", "health": 0.94, "risk": "healthy", "simulated": false },
        { "part": "radar",  "health": 0.81, "risk": "healthy", "simulated": true } ] }
  ],
  "legend": { "healthy": "> 0.70", "watch": "0.40 – 0.70", "critical": "≤ 0.40" }
}
```

Always 8 × 5 = 40 cells, even when a part has no data — missing data renders as `null`
health, never as an omitted cell.

### `GET /api/v1/fleet/actions` — spec 33

Query: `limit` (default 5, max 50).

```json
{
  "items": [
    { "rank": 1, "aircraft": "Fighter-06", "aircraft_id": 6, "part": "engine",
      "health": 0.31, "risk": "critical", "action": "Replace now",
      "do_by_cycle": 14, "do_by_in_cycles": 14,
      "spare": { "part_ref_id": "PART018", "item_name": "HPT Nozzle", "stock": 0,
                 "lead_time_days": 39, "in_stock": false },
      "agency": { "name": "Vector Engine Works", "free_slot_days": 2, "turnaround_days": 24 },
      "back_in_service_days": 65, "simulated": false }
  ],
  "limit": 5
}
```

Sorted by `health` ascending — the globally worst parts, not per-aircraft worst.

### `GET /api/v1/maintenance/schedule` — spec 34

One row per aircraft (8 rows).

```json
{
  "items": [
    { "aircraft": "Fighter-06", "aircraft_id": 6, "worst_part": "engine",
      "worst_health": 0.31, "risk": "critical", "action": "Replace now",
      "do_by_cycle": 14, "do_by_date": "2026-10-17", "do_by_in_cycles": 14,
      "spare_status": "out_of_stock", "spare_item": "HPT Nozzle", "lead_time_days": 39,
      "agency": "Vector Engine Works", "back_in_service_days": 65,
      "open_work_order": "WO-0001", "mission_ready": false }
  ],
  "generated_at": "2026-10-03T11:27:04Z"
}
```

`spare_status` ∈ `in_stock | low | out_of_stock`:
`low` when `0 < stock <= minimum_stock`.

`do_by_date` converts the do-by **cycle** to a date using `flight_operations.csv` cycles per
month; `null` when the aircraft has no operations history.

---

## 3. Work orders — spec 35

### `GET /api/v1/work-orders`

Query: `status`, `aircraft`, `part`, `limit`, `offset`.

### `POST /api/v1/work-orders`

Roles: `maintenance_officer`, `commander`.

```json
// request — note: no 'action'. It is derived from current risk (rule 22).
{ "aircraft": "Fighter-06", "part": "engine", "due_date": "2026-10-20",
  "priority": "high", "notes": "HPT replacement ahead of do-by cycle 14." }
```

```json
// 201
{ "id": 42, "reference": "WO-0001", "aircraft": "Fighter-06", "part": "engine",
  "action": "Replace now", "due_date": "2026-10-20", "due_cycle": 14,
  "status": "open", "priority": "high", "notes": "…",
  "created_by": { "id": 2, "username": "officer" },
  "created_at": "2026-10-03T11:28:02Z", "updated_at": "2026-10-03T11:28:02Z",
  "started_at": null, "completed_at": null }
```

`409` if an open work order already exists for that aircraft+part (enforced by
`uq_wo_open_aircraft_part`).

### `PATCH /api/v1/work-orders/{id}`

```json
// request
{ "status": "in_progress" }
```

Optional: `priority`, `due_date`, `notes`, `action`.

```json
// 200 — same shape as POST, with started_at / completed_at populated
{ "id": 42, "reference": "WO-0001", "status": "in_progress",
  "started_at": "2026-10-03T11:31:44Z", "completed_at": null }
```

Rules:

- `open → in_progress` sets `started_at`.
- `→ done` sets `completed_at` and **releases any agency booking** for that order.
- `done → anything` is `409` — a completed work order is immutable.
- Setting `status: "done"` requires all reserved stock to have been consumed.

---

## 4. Spares — spec 36, 37

### `GET /api/v1/spares`

Query: `in_stock`, `component`, `criticality`, `q` (name search).

```json
{ "items": [
    { "part_ref_id": "PART018", "item_name": "HPT Nozzle", "component_name": "High Pressure Turbine",
      "stock": 0, "minimum_stock": 5, "reorder_quantity": 12, "supplier": "AeroParts Supply Co.",
      "lead_time_days": 39, "unit_cost_inr": 265000, "storage_location": "Base-C",
      "criticality": "High", "in_stock": false, "low_stock": true,
      "compatible_component": "High Pressure Turbine",
      "last_movement": { "delta": -1, "reason": "reserve", "at": "2026-10-03T10:44:12Z" } } ],
  "total": 40 }
```

### `PATCH /api/v1/spares/{id}`

```json
// request
{ "stock": 12, "reason": "restock", "note": "Replenishment PO-8841 received" }
```

`reason` ∈ `restock | adjust | return`. `stock` is an **absolute** value, not a delta;
`stock_movement.delta` records the difference so the audit trail stays meaningful.
`409` if the new stock would be negative.

### `POST /api/v1/spares/{id}/reserve`

```json
// request
{ "work_order_id": 42 }
```

```json
// 200
{ "part_ref_id": "PART018", "stock_before": 1, "stock_remaining": 0,
  "reserved_at": "2026-10-03T11:29:10Z", "work_order_id": 42,
  "work_order_status": "in_progress" }
```

`409 OUT_OF_STOCK` with `detail: {stock: 0, lead_time_days: 39}` when zero. On success the
work order moves to `in_progress` and a critical alert is raised if the aircraft is no
longer mission-ready.

---

## 5. Agencies — spec 38

### `GET /api/v1/agencies`

```json
{ "items": [
    { "id": 5, "agency_ref_id": "MA005", "name": "Vector Engine Works",
      "type": "Third-Party MRO", "location": "Base-C", "specialisation": "engine",
      "turnaround_days": 24, "monthly_capacity_slots": 16, "cost_multiplier": 1.45,
      "free_slot_days": 2, "open_bookings": 1,
      "handles_parts": ["engine"] } ] }
```

`handles_parts` is the reverse of the part→agency assignment (rule 15).

### `POST /api/v1/agencies/{id}/bookings`

Roles: `maintenance_officer`, `commander`.

```json
// request
{ "aircraft": "Fighter-06", "part": "engine", "work_order_id": 42 }
```

```json
// 201
{ "id": 7, "agency": "Vector Engine Works", "aircraft": "Fighter-06", "part": "engine",
  "booked_on": "2026-10-03", "slot_days": 2, "turnaround_days": 24,
  "lead_time_days": 39, "eta_date": "2027-01-04",
  "back_in_service_days": 65,
  "breakdown": { "slot": 2, "turnaround": 24, "lead_time": 39 } }
```

`eta_date = CURRENT_DATE + slot_days + turnaround_days + lead_time_days`, with
`lead_time_days` non-zero **only if** the resolved spare had zero stock (rule 24).
`409 NO_SLOT_AVAILABLE` when `free_slot_days` would drop below 1.

---

## 6. Alerts — spec 39

### `GET /api/v1/alerts`

Query: `acknowledged`, `level`, `aircraft`, `limit`.

```json
{ "items": [
    { "id": 91, "aircraft": "Fighter-06", "aircraft_id": 6, "part": "engine",
      "level": "critical", "message": "Engine health 0.31 — RUL 24. Replace now (do-by cycle 14).",
      "health": 0.31, "rul": 24, "cycle": 38, "acknowledged": false,
      "acknowledged_by": null, "acknowledged_at": null,
      "created_at": "2026-10-03T11:12:00Z" } ],
  "unacknowledged_count": 3 }
```

### `POST /api/v1/alerts/{id}/ack`

Roles: `maintenance_officer`, `commander`. Viewers get `403`.

```json
// request (optional)
{ "note": "HPT replacement scheduled; aircraft grounded until parts arrive." }
```

```json
// 200
{ "id": 91, "acknowledged": true, "acknowledged_by": { "id": 2, "username": "officer" },
  "acknowledged_at": "2026-10-03T11:28:40Z", "note": "…" }
```

Idempotent — acknowledging twice returns `200`, not `409`.

---

## 7. Telemetry — spec 40

### `POST /api/v1/telemetry`

Roles: `maintenance_officer`, `commander`. **One aircraft per batch** (spec 40).

```json
// request
{
  "aircraft": "Fighter-01",
  "rows": [
    { "cycle": 88,
      "settings": { "setting_1": 0.0012, "setting_2": -0.0004, "setting_3": 100.0 },
      "sensors": { "s2": 642.4, "s3": 1590.1, "s4": 1401.2, "s7": 553.1, "s8": 2388.2,
                    "s9": 9065.4, "s11": 521.3, "s12": 2387.9, "s13": 8131.5, "s14": 0.62,
                    "s15": 522.2, "s17": 641.2, "s20": 542.7, "s21": 2388.0 } },
    { "cycle": 89, "settings": { … }, "sensors": { … } }
  ]
}
```

```json
// 202
{ "aircraft": "Fighter-01", "rows_written": 2,
  "current_cycle": 89, "rul": 117, "health": 0.936, "risk": "healthy",
  "components": { "fan": 0.96, "hpc": 0.93, "hpt": 0.95, "lpt": 0.92 },
  "top_sensors": [ { "sensor": "s11", "contribution": 0.19 } ],
  "model": "rul_xgb_fd001", "alerts_raised": 0, "latency_ms": 4.7 }
```

`sensors` must include the 14 spec sensors. `s6` is optional — omitted it is imputed from
the FD001 median (21.61) and the response sets `model.s6_imputed: true`.

Validation failures: `422` naming the row index and the offending sensor, e.g.
`rows[3].sensors.s4: expected 1200–1600, got 42.0`.

---

## 8. Internal ML — spec 41

### `POST /api/v1/internal/ml/predict`

Roles: `maintenance_officer`, `commander`, or a service token.

```json
// request — last 30 cycles of 14 sensors + operating regime
{
  "aircraft": "Fighter-01",
  "regime": 0,
  "window": [
    { "cycle": 60, "settings": { "setting_1": 0.0, "setting_2": 0.0, "setting_3": 100.0 },
      "sensors": { "s2": 642.7, "s3": 1590.5, "s4": 1408.9, "s7": 553.4, "s8": 2388.1,
                    "s9": 9065.4, "s11": 521.9, "s12": 2387.9, "s13": 8135.1, "s14": 0.62,
                    "s15": 522.4, "s17": 641.9, "s20": 542.6, "s21": 2387.9 } }
  ],
  "persist": true
}
```

```json
// 200
{
  "aircraft": "Fighter-01", "cycle": 89,
  "rul": 117, "rul_raw": 117.4, "rul_capped": false,
  "component_health": { "fan": 0.96, "hpc": 0.93, "hpt": 0.95, "lpt": 0.92 },
  "weakest_component": "lpt",
  "deviation": { "s2": 0.08, "s3": 0.31, "s4": 0.55, "s6": 0.02, "s7": 0.19,
                 "s8": 0.11, "s9": 0.14, "s11": 0.62, "s12": 0.09, "s13": 0.07,
                 "s14": 0.21, "s15": 0.06, "s17": 0.05, "s20": 0.17, "s21": 0.10 },
  "top_sensors": [
    { "sensor": "s11", "contribution": 0.1915, "z": 2.41, "component": "hpc" },
    { "sensor": "s4",  "contribution": 0.0735, "z": 1.88, "component": "lpt" }
  ],
  "component_sensor_map": { "fan": ["s8","s13"], "hpc": ["s3","s7","s11"],
                            "hpt": ["s20","s21"], "lpt": ["s4"] },
  "model": { "version": "rul_xgb_fd001", "dataset": "FD001", "mae": 11.41,
             "s6_imputed": false, "fallback": false },
  "latency_ms": 2.4
}
```

`persist: false` runs inference without writing — used by the ML-quality test suite so
repeated calls do not pollute the time-series tables.

`window` must contain 5–30 rows; shorter windows fall back to the deterministic curve and
set `model.fallback: true`.

---

## 9. Operational endpoints

| Endpoint | Purpose |
|---|---|
| `GET /healthz` | `{status, db, model, replay}` — liveness |
| `GET /readyz` | Adds `aircraft_seeded >= 1` — readiness for traffic |
| `GET /api/v1/seed/run` | Re-run the idempotent seed (commander only) |
| `GET /api/v1/demo/status` | Replay state: tick, cycle per aircraft, elapsed, connected sockets |
| `POST /api/v1/demo/tick` | Advance one cycle manually; pauses the auto-loop (commander) |
| `POST /api/v1/demo/pause` / `resume` | Control the loop (commander) |
| `GET /api/v1/audit` | Audit trail, filterable by `entity`, `actor`, date range (commander) |

---

## 10. WebSocket — spec 42

```
wss://host/ws/fleet?token=<JWT>
```

Server → client:

```json
{ "type": "connection.ready", "payload": { "aircraft": 8, "demo_mode": true, "tick_seconds": 1.2 } }
{ "type": "cycle.tick",      "payload": { "cycle": 88, "ts": "2026-10-03T11:27:04Z" } }
{ "type": "health.updated",  "payload": { "aircraft": "Fighter-01", "part": "engine",
                                          "health": 0.936, "risk": "healthy", "rul": 117 } }
{ "type": "alert.raised",    "payload": { "id": 91, "aircraft": "Fighter-06", "part": "engine",
                                          "level": "critical", "message": "…" } }
{ "type": "work_order.created", "payload": { "reference": "WO-0001", … } }
{ "type": "pong",            "payload": {} }
```

Client → server:

```json
{ "type": "subscribe",   "payload": { "aircraft": ["Fighter-01", "Fighter-06"] } }
{ "type": "unsubscribe", "payload": { "aircraft": ["Fighter-02"] } }
{ "type": "ping",        "payload": {} }
```

Close codes: `4401` unauthorized, `4408` origin not allowed, `1011` internal error.
Full protocol in [09](09-realtime-and-demo-mode.md).

---

## 11. Static models — spec 49

```
GET /models/fighter-01.glb
GET /models/engine.glb
```

`200` with `Content-Type: model/gltf-binary`,
`Cache-Control: public, max-age=31536000, immutable`, `ETag`, `Accept-Ranges: bytes`.
`304` on conditional request. No auth required — GLBs contain no fleet data.

---

## 12. Endpoint-to-spec traceability

| Spec | Endpoint | Section |
|---|---|---|
| 27 | `GET /fleet/summary` | §2 |
| 28 | `GET /aircraft` | §2 |
| 29 | `GET /aircraft/{id}` | §2 |
| 30 | `GET /aircraft/{id}/engine` | §2 |
| 31 | `GET /aircraft/{id}/parts/{part}` | §2 |
| 32 | `GET /fleet/heatmap` | §2 |
| 33 | `GET /fleet/actions` | §2 |
| 34 | `GET /maintenance/schedule` | §2 |
| 35 | `POST /work-orders`, `PATCH /work-orders/{id}` | §3 |
| 36 | `GET /spares`, `PATCH /spares/{id}` | §4 |
| 37 | `POST /spares/{id}/reserve` | §4 |
| 38 | `GET /agencies`, `POST /agencies/{id}/bookings` | §5 |
| 39 | `GET /alerts`, `POST /alerts/{id}/ack` | §6 |
| 40 | `POST /telemetry` | §7 |
| 41 | `POST /internal/ml/predict` | §8 |
| 42 | `WS /ws/fleet` | §10 |
| 49 | `GET /models/*.glb` | §11 |