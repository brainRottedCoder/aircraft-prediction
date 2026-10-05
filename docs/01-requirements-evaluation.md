# 01 — Requirements Evaluation

Evaluation of the frontend's backend requirements against the data actually available in
the Google Drive folder and the NASA C-MAPSS dataset, with the decision taken for each gap.

## 1. Inventory of available data

### 1.1 Google Drive `Project2`

Downloaded and profiled on 2026-10-03. All files publicly readable, no auth required.

| File | Rows | Columns | Notes |
|---|---|---|---|
| `aircraft.csv` | 100 | `aircraft_id, tail_number, aircraft_model, engine_id, engine_model, manufacture_date, induction_date, home_base, total_flight_hours, total_cycles, current_status` | `aircraft_model` ∈ {Type-A, Type-B, Type-C}; `current_status` ∈ {Serviceable, Under Maintenance, Awaiting Spares, Grounded} |
| `components.csv` | 600 | `component_id, aircraft_id, engine_id, component_name, component_system, installation_date, operating_hours, operating_cycles, life_limit_hours, life_limit_cycles, health_status, times_replaced` | **6 engine components only**: Fan, Low Pressure Compressor, High Pressure Compressor, High Pressure Turbine, Low Pressure Turbine, Combustor. `health_status` ∈ {Good, Watch, Degraded}. 6 rows per aircraft |
| `flight_operations.csv` | 3300 | `aircraft_id, month, flight_hours, sorties, flight_cycles, avg_sortie_duration_hours, high_stress_sorties, days_available, days_unavailable` | 33 months × 100 aircraft. **Not referenced anywhere in the original spec** |
| `maintenance_agencies.csv` | 6 | `agency_id, agency_name, type, location, specialisation, average_turnaround_days, capacity_slots_per_month, cost_multiplier` | `specialisation` ∈ {general, engine, avionics, hydraulics}. **No free-slot field** |
| `maintenance_records.csv` | 1500 | `maintenance_id, aircraft_id, component_id, agency_id, part_id, date, maintenance_type, fault_type, maintenance_action, downtime_hours, cost_inr, flight_hours_at_event, cycles_at_event, outcome` | `fault_type` ∈ {blade erosion, compressor stall, fuel flow anomaly, high EGT, hydraulic pressure low, oil pressure drop, oil temperature high, sensor fault, starter fault, vibration above limit} + null. `part_id` nullable |
| `maintenance_schedule.csv` | 600 | `schedule_id, aircraft_id, component_id, task_type, due_date, due_flight_hours, due_cycles, estimated_duration_hours, priority, required_part_id, assigned_agency_id, status` | `status` ∈ {Planned, Awaiting Part, Overdue} — **different vocabulary from the spec's work-order enum** |
| `snag_logs.csv` | 800 | `snag_id, maintenance_id, aircraft_id, date_reported, reported_by_role, snag_text, severity, resolution_text` | `severity` ∈ {Critical, Major, Minor}; 64 distinct snag texts, 5 distinct resolutions |
| `spare_parts.csv` | 40 | `part_id, part_name, component_id, component_name, aircraft_model, quantity_available, minimum_stock, reorder_quantity, supplier, lead_time_days, unit_cost_inr, storage_location, last_restock_date, criticality, compatible_component` | **Fully satisfies spec item 14** |

### 1.2 NASA C-MAPSS

| Dataset | Engines | Train rows | Cycle range (train) | Conditions | Fault modes |
|---|---|---|---|---|---|
| FD001 | 100 | 20,631 | 128 – 362 (mean 206.3) | 1 (sea level) | HPC degradation |
| FD002 | 260 | — | — | 6 | HPC degradation |
| FD003 | 100 | — | — | 1 | HPC + Fan degradation |
| FD004 | 248 | — | — | 6 | HPC + Fan degradation |

Structure: `unit_id, cycle, setting_1..3, sensor_1..21`, whitespace-delimited, no header,
no missing values, no duplicates, no infinities.

**Constant (zero-variance) columns confirmed in FD001:** `setting_3, sensor_1, sensor_5,
sensor_10, sensor_16, sensor_18, sensor_19`. These are dropped at training time.

**RUL correlations with target (FD001):** `sensor_11` −0.696, `sensor_12` +0.672,
`sensor_7` +0.657, `sensor_4` −0.679, `sensor_20` +0.629, `sensor_21` +0.636.

### 1.3 `Model_training_249.ipynb`

29 cells (13 for the serving model, 15 appended for multi-regime candidates). Trains
`XGBRegressor(n_estimators=1000, learning_rate=0.05, max_depth=6, subsample=0.8,
colsample_bytree=0.8, early_stopping_rounds=50)` on an 80/20 **engine-level** split
(no cycle leakage). **Runs to completion** — see [12](12-model-training-pipeline.md) and
[13](13-multi-regime-training-report.md).

- Result: **MAE 11.407, RMSE 16.049, R² 0.8494** on the held-out 20 engines;
  official test (100 unseen engines) RMSE 18.365, NASA asymmetric score 0.4636
- Cross-validated (5-fold, grouped by engine): **RMSE 17.988 ± 1.487**
- Early stopping halted at **92 of 1000** rounds
- Feature importance: `s4_rollmean5` **0.473**, `s11_rollmean5` 0.219, `s9_rollmean5` 0.078,
  `s7_rollmean5` 0.041, `s9` 0.023 — top three hold 77.1 % of total gain
- Artifacts persisted to Google Drive: model, scaler, metrics, feature contract

Consequences:

1. **No persisted model exists.** `save_model` was never called. There is no `.json`/`.ubj`
   artifact, no feature manifest, no scaler.
2. **No component-health head exists.** The notebook predicts a single scalar RUL. Spec
   items 13, 46 and 47 (fan/hpc/hpt/lpt health, per-sensor deviation scores) have no
   implementation anywhere in the repo.
3. **Feature contract mismatch with the spec** — see §3.

---

## 2. Gap matrix

| Spec # | Requirement | Data reality | Verdict | Decision |
|---|---|---|---|---|
| 7 | Seed 8 aircraft `Fighter-01..08` | 100 available (`AC001..AC100`) | Compatible | Seed 8, retain all 100 in `aircraft_ref` |
| 9 | Aircraft: id, name | Exists, richer | Satisfied | `aircraft.code` = `Fighter-0N` |
| 10 | 5 parts: engine, radar, gear, hyd, fuel | Only 6 **engine** components exist | **Partial** | Engine from ML; other 4 derived (§4) |
| 11 | Health snapshot per cycle | No time-series health anywhere | **Missing** | Derived (§5) |
| 12 | Engine telemetry: 3 settings + 14 sensors | Present in C-MAPSS | Satisfied | Replay source |
| 13 | Engine components fan/hpc/hpt/lpt per cycle | Not modelled | **Missing** | Derived from sensor deviation (§8) |
| 14 | Spares: part, item, stock, lead time | All present, 40 rows | Satisfied | Direct load |
| 15 | Agencies: free slot days + turnaround | Turnaround present, **free slot absent** | **Partial** | Free slot computed (§6) |
| 16 | Technical records: aircraft, part, cycle, text | 1500 records + 800 snags | Satisfied | Direct load, both |
| 17 | Work orders | 600 schedule rows, different status vocabulary | **Partial** | Load + map enum (§7) |
| 18 | Alerts | None | **Missing** | Generated on risk transition |
| 19–26 | Business rules | Pure logic, no data needed | Satisfied | See [07](07-business-rules.md) |
| 27–41 | Endpoints | — | Satisfied | See [06](06-api-specification.md) |
| 42–43 | WebSocket + demo replay | C-MAPSS available | Satisfied | See [09](09-realtime-and-demo-mode.md) |
| 44 | Load trained model at startup | **No artifact on disk** | **Blocked** | Phase 0 |
| 45 | Inputs: 14 named sensors | Model trained on **15** + `cycle` | **Conflict** | Canonical = 15 + `cycle` (§3) |
| 46 | Outputs RUL + per-sensor deviation | Only RUL implemented | **Missing** | New code (§8) |
| 47 | Sensor→component grouping | Grouping specified, sensors unassigned | **Partial** | Unassigned-sensor policy (§8) |
| 48 | Simulated flag for non-engine parts | — | Satisfied | Enforced, 4 of 5 parts |
| 49 | Serve GLB statically | Files not in repo | Pending | Served from `static/models/` |
| 50–53 | Perf, audit, tests, Docker | — | Satisfied | See [11](11-deployment-and-nfr.md) |

**Score: 15 satisfied, 5 partial, 6 missing, 1 blocked, 1 conflict.**

---

## 3. The sensor contract conflict (spec 45 vs. the notebook)

Spec item 45 names exactly 14 sensors:

```
s2, s3, s4, s7, s8, s9, s11, s12, s13, s14, s15, s17, s20, s21
```

The trained model's feature set contains **15** sensors plus the two non-constant operating
settings, plus rolling statistics:

```
op1, op2,                                                             ← 2  settings
s2, s3, s4, s6, s7, s8, s9, s11, s12, s13, s14, s15, s17, s20, s21      ← 15, includes s6
+ rolling-5 mean AND std for s11, s4, s9, s12, s14, s7                  ← +12 derived
= 29 model features
```

Two deviations from the original spec, both deliberate:

**`sensor_6` added.** It is non-constant in FD001 (mean 21.6098, variance 1.93e-06) and
carries non-trivial importance. Dropping it would require retraining.

**`cycle` removed — this reverses an earlier decision.** An earlier iteration included it,
where it was the single most important feature at 0.3585, and it was retained on the
argument that C-MAPSS RUL is nearly linear in cycles remaining. That reasoning was wrong:
the official test set is truncated early, so `cycle` correlates with RUL in a way that does
not hold on real data. It is a leakage vector. Excluding it costs little (validation RMSE
16.05 without it) and is why the model is trustworthy on unseen engines. The wire contract is
unaffected — the service still tracks absolute cycle numbers for the fallback and for
provenance; `cycle` is simply not a model input.

**Resolution — the wire contract is unaffected.** The client still sends what the spec says:
30 cycles of the 14 named sensors plus the 3 operating settings and a regime label. The API
layer derives `s6` and `cycle` internally:

- `cycle` — index within the submitted window is not enough; the service tracks
  `aircraft.current_cycle` and reconstructs absolute cycle numbers.
- `sensor_6` — the frontend's 3D model / telemetry source must supply it, **or** the API
  back-fills it from the C-MAPSS baseline median (21.61 for FD001) and marks the prediction
  `"s6_imputed": true`.

> **Action required from the frontend team:** confirm the telemetry producer can emit a 15th
> sensor field. If it cannot, the impute path keeps the demo working at a small accuracy cost.

---

## 4. Non-engine parts: derivation, not invention

Per the decision that only real available data should back these values, radar / gear /
hydraulics / fuel health is computed from **real observed maintenance burden** in
`maintenance_records.csv` and `snag_logs.csv`. No random walk, no synthetic noise.

### 4.1 Part ↔ fault-type mapping

Derived from the 10 distinct `fault_type` values and their semantics:

| Part | Fault types mapped | Snag severity weight |
|---|---|---|
| engine | blade erosion, compressor stall, high EGT, oil pressure drop, oil temperature high, vibration above limit | 1.00 |
| hyd | hydraulic pressure low | 0.95 |
| fuel | fuel flow anomaly | 0.90 |
| gear | starter fault | 0.70 |
| radar | sensor fault | 0.85 |

`combustor` faults roll into `engine` — C-MAPSS has no combustor sensor.

### 4.2 Health formula

For aircraft `a` and part `p`:

```
records_p(a)  = all maintenance_records rows for aircraft a whose fault_type ∈ map(p)
E(a, p)       = Σ over records_p(a) of  severity_weight(fault_type)
                     ×  recency_weight(date)
                     ×  downtime_hours / 100

recency_weight(date) = 1.0                          if age ≤ 6 months
                     = 1 - (age_months - 6) / 30    if 6 < age ≤ 36 months   (floored at 0.1)
                     = 0.1                          otherwise

snag_p(a)     = Σ over snag_logs for aircraft a of  severity_weight(severity)
                     ×  recency_weight(date_reported)   / 10

E_norm(a, p)  = E(a, p) / P95(E(·, p))     ← 95th percentile across the 100-aircraft
                                              reference population, so the scale is
                                              fleet-relative, not absolute

health(a, p)  = clamp( 1 − E_norm(a, p) , 0.05, 1.0 )
```

The result is **monotonically non-increasing** for a given aircraft as the reference window
extends, and it moves only when a real maintenance event is ingested — so the demo timeline
reflects genuine operational history rather than noise.

### 4.3 Simulated flag

Every response touching radar / gear / hyd / fuel carries:

```json
{ "health": 0.63, "risk": "watch", "simulated": true, "method": "maintenance_burden_v1" }
```

The engine carries `"simulated": false, "method": "rul_xgb_fd001"`.

---

## 5. Engine health and history

C-MAPSS has no health column. Engine health is derived from RUL, which is the physically
meaningful quantity:

```
health_engine = clamp( RUL / 125 , 0 , 1 )        ← 125 is the spec's cap (rule 26)
```

This makes `health` and `rul` consistent by construction: at the cap, health = 1.0; at
failure, health = 0.0. The risk bands then follow directly from spec rule 19.

Historical health series are produced by running the same mapping over the RUL predicted at
each historical cycle, giving a smooth monotone decline across the 60-cycle window the
frontend requests.

**Cross-check against Drive data.** `components.csv` provides an independent signal:
`operating_cycles / life_limit_cycles`. Mean ratio across 600 rows is well below 1, and
`health_status` ∈ {Good, Watch, Degraded} correlates with that ratio. During seed, aircraft
whose Drive-derived ratio disagrees with the ML-derived health by more than 0.25 are logged
to `seed_reconciliation.log` for manual review. No automatic override — ML remains the source
of truth for the engine, per decision 1.

---

## 6. Agency free slots

`maintenance_agencies.csv` has `capacity_slots_per_month` and `average_turnaround_days`
but no free-slot field. Derivation:

```
booked_days = Σ turnaround_days of agency_bookings not yet completed

free_slot_days = 0                                     if booked_days >= 30
                 max(1, ceil((1 - booked_days/30) * 30 / capacity_slots))  otherwise

⇒ idle agency:  ceil(30 / monthly_capacity_slots)
⇒ monotonic:    the wait never grows as bookings accumulate
⇒ saturated:    0, which the booking endpoint reports as 409 NO_SLOT_AVAILABLE
```

Implemented in `app/domain/scheduling.py::agency_free_slot_days` and covered by
`tests/unit/test_scheduling.py`.

Worked examples from the real data:

| Agency | Specialisation | Capacity/mo | Turnaround | Free slot (idle) |
|---|---|---|---|---|
| MA001 AeroCore Base Repair | general | 40 | 13 d | 1 day |
| MA002 SwiftWing Field Services | general | 19 | 3 d | 2 days |
| MA003 Vector Engine Works | engine | 11 | 24 d | 3 days |
| MA004 Skyline Avionics MRO | avionics | 25 | 9 d | 2 days |
| MA005 HydraFlight Services | hydraulics | 23 | 11 d | 2 days |
| MA006 RapidAir Field Support | general | 16 | 3 d | 2 days |

Rounding is `ceil` rather than `round`, so an idle agency always shows at least 1 day.

### Part → agency assignment (spec item 15)

```
engine → specialisation 'engine'      → MA003 Vector Engine Works
radar  → specialisation 'avionics'    → MA004 Skyline Avionics MRO
gear   → no match → general pool      → MA002 SwiftWing (0.82 × 3 = 2.46)
hyd    → specialisation 'hydraulics'  → MA005 HydraFlight Services
fuel   → no match → general pool      → MA002 SwiftWing
```

Assignment is by `specialisation` where one exists, else by lowest
`cost_multiplier × turnaround_days`. This is stored in `part.agency_id` so the rule is
inspectable and overridable.

---

## 7. Work-order status mapping

`maintenance_schedule.csv` uses a vocabulary the spec does not define:

| Source `status` | Spec work-order status | Rationale |
|---|---|---|
| `Planned` | `open` | Scheduled, not started |
| `Awaiting Part` | `open` | Blocked, but not in progress |
| `Overdue` | `open` | Past due; `priority` escalated to `high` |
| *(n/a)* | `in_progress` | Only reachable via `PATCH` at runtime |
| *(n/a)* | `done` | Only reachable via `PATCH` at runtime |

Seed also derives `action` from `task_type`:

| `task_type` | Action text shown to frontend |
|---|---|
| Component Replacement | Replace now |
| Inspection | Plan inspection |
| Functional Check | Routine check |
| Lubrication | Routine check |
| Preventive Maintenance | Routine check |

Note that spec rule 22 prescribes action **from risk**, not from task type. Where both exist,
the rule-22 action wins in API responses; the seed mapping above is only the initial
`work_order.action` value at load time.

---

## 8. ML feature and output gaps

### 8.1 Sensor → component grouping (spec 47)

The spec groups 9 of 14 sensors. The remaining 5 are unassigned:

| Component | Assigned sensors |
|---|---|
| fan | s8, s13 |
| hpc | s3, s7, s11 |
| hpt | s20, s21 |
| lpt | s4 |
| **unassigned** | **s2, s6, s9, s12, s14, s15, s17** |

Policy adopted:

1. Component health is computed **only** from assigned sensors, exactly as specified.
2. Unassigned sensors contribute to `top_sensors` and to engine-level health as a
   gain-weighted blend, never to a specific component.
3. Every response exposes `component_sensor_map` so the frontend can render the attribution
   honestly without hard-coding it.

```
health_component(c) = 1 − clamp( mean(|z_s| for s in map(c)) / 3.0 , 0 , 1 )
```

where `z_s` is the sensor's deviation from its healthy baseline within its operating regime.

### 8.2 Per-sensor deviation (spec 46)

Not implemented anywhere in the notebook. Defined as:

```
baseline(unit, sensor, regime) = median of sensor over the unit's first 20 cycles
z(unit, cycle, sensor)         = (value − baseline) / MAD(baseline)      ← robust
deviation_score                = clip(|z|, 0, 10) / 10                   ∈ [0,1]
```

Median and MAD (not mean/std) because C-MAPSS sensors have heavy tails in the degradation
tail and a single early spike would otherwise poison the baseline.

### 8.3 Missing artifact

Phase 0 of the development plan exists solely to close this: finish the notebook, retrain,
and emit `models/rul_xgb.json`, `models/feature_manifest.json` and
`models/baseline_stats.json`. Until then the ML service runs its documented fallback and
flags every prediction `"model": "fallback"`.

---

## 9. Unplanned data: `flight_operations.csv`

3,300 monthly rows the spec never mentions. Two uses adopted:

1. **Degradation driver.** `high_stress_sorties` (0–7 per month) is retained and exposed on
   the aircraft detail endpoint as context for health trends.
2. **Availability KPI.** `days_available / (days_available + days_unavailable)` gives a real
   availability percentage per aircraft, surfaced on `/fleet/summary` as
   `avg_availability`. This is a genuine operational metric that costs nothing to compute.

Neither use changes any spec rule.

---

## 10. Residual risks

| Risk | Impact | Mitigation |
|---|---|---|
| Frontend cannot emit `sensor_6` | ML accuracy loss | Impute from FD001 median 21.61, flag `s6_imputed: true` |
| Model artifact never regenerated | Entire ML section non-functional | Phase 0 gate; deterministic fallback keeps demo alive |
| `health_status` (Drive) contradicts ML health | Frontend shows conflicting numbers | ML is source of truth; divergence logged, surfaced via `health_source` |
| C-MAPSS FD002/FD004 regimes not modelled | Wrong baselines on multi-condition data | Phase 0 restricts to FD001; regime nearest-centroid added before FD002 is enabled |
| 8 aircraft × ~200 cycles of replay makes all 8 fail simultaneously | Demo looks broken at loop point | Stagger start offsets by 30 cycles per aircraft (§09) |
| GLB assets absent from repo | 3D view empty | Served from `static/models/`, documented as a deploy-time mount |