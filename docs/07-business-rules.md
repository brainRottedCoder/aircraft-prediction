# 07 — Business Rules

Spec items 19–26, specified exactly enough that two independent implementations agree. The
frontend depends on these values matching exactly, so every threshold, boundary and
tie-break is pinned down here.

All rules live in `app/domain/rules.py`, which imports only `stdlib` and `pydantic`. No
database, no HTTP, no clock — which is what makes them testable in microseconds
([02 §7](02-backend-architecture.md)).

## Rule index

| # | Rule | Function | Pure inputs → output |
|---|---|---|---|
| 19 | Risk level | `risk_level()` | `health` → `healthy\|watch\|critical` |
| 20 | Mission ready | `mission_ready()` | `rul`, `parts` → `bool` |
| 21 | Worst part | `worst_part()` | `parts` → `part code` |
| 22 | Recommended action | `recommended_action()` | `risk` → `str` |
| 23 | Do-by cycle | `do_by_cycle()` | `rul`, `current_cycle`, `risk` → `int\|None` |
| 24 | Back in service | `back_in_service_days()` | 4 ints → `int` |
| 25 | Engine spare | `engine_spare()` | 4 component healths → `component` |
| 26 | RUL cap | `cap_rul()` | `raw_rul` → `int` |

---

## Rule 19 — Risk level

> *Health above 0.7 is healthy, above 0.4 is watch, otherwise critical.*

```python
def risk_level(health: float) -> Literal["healthy", "watch", "critical"]:
    if health > 0.7:
        return "healthy"
    if health > 0.4:
        return "watch"
    return "critical"
```

**Boundary behaviour — the single most important detail in this document.** The
comparisons are **strict `>`**, so a health of *exactly* 0.7 is **watch** and *exactly* 0.4
is **critical**.

| health | risk |
|---|---|
| 1.00, 0.71, 0.700001 | `healthy` |
| **0.70** | **`watch`** |
| 0.69, 0.41, 0.400001 | `watch` |
| **0.40** | **`critical`** |
| 0.39, 0.10, 0.00 | `critical` |

Validation: `health` outside `[0, 1]` raises `ValueError` rather than clamping. Silently
clamping a 1.4 would hide a bug upstream; a `CHECK` constraint on the column catches the
same case at the database level as a second line of defence.

> **Confirm with the frontend team.** If their UI expects `0.7` to render as green, this
> implementation is one boundary off. The spec wording ("above 0.7") supports strict `>`,
> and that is what is implemented and tested.

---

## Rule 20 — Mission ready

> *Engine RUL above 30 cycles and every part health above 0.4.*

```python
def mission_ready(rul: int, parts: list[tuple[str, float]]) -> bool:
    if rul <= 30:
        return False
    return all(health > 0.4 for _, health in parts)
```

Both conditions must hold. Note the two thresholds are compared differently and
deliberately so:

- `rul <= 30` fails — i.e. RUL **must be strictly greater than 30**. RUL exactly 30 is
  **not** mission ready.
- health `> 0.4` — a part at exactly 0.4 makes the aircraft **not** ready. This is
  consistent with rule 19 classifying 0.4 as critical.

Worked examples:

| rul | part healths | mission_ready | Why |
|---:|---|:---:|---|
| 118 | 0.94, 0.81, 0.72, 0.90, 0.88 | ✅ true | Both conditions pass |
| 31 | 0.94, 0.81, 0.72, 0.90, 0.88 | ✅ true | 31 > 30, all > 0.4 |
| **30** | 0.94, 0.81, 0.72, 0.90, 0.88 | ❌ false | 30 is not *above* 30 |
| 29 | 0.94, 0.81, 0.72, 0.90, 0.88 | ❌ false | RUL below threshold |
| 118 | 0.94, 0.81, **0.40**, 0.90, 0.88 | ❌ false | One part is critical |
| 118 | 0.94, 0.81, 0.41, 0.90, 0.88 | ✅ true | 0.41 is above 0.4 |

**Empty `parts` → `False`.** An aircraft with no parts has unknown condition, and unknown
is not ready.

---

## Rule 21 — Worst part

> *The part with the lowest health.*

```python
PART_ORDER = ("engine", "radar", "gear", "hyd", "fuel")   # = part.sort_order

def worst_part(parts: list[tuple[str, float]]) -> str | None:
    if not parts:
        return None
    return min(parts, key=lambda p: (p[1], PART_ORDER.index(p[0])))[0]
```

**Tie-break.** Two parts at identical health is common once values are rounded to 3 dp.
The tie is broken by the fixed severity order above — engine first, fuel last — because the
engine is the part with the highest consequence and the longest lead time.

| parts (part, health) | worst_part |
|---|---|
| (engine, 0.94), (radar, 0.31) | `radar` |
| **engine 0.50, radar 0.50** | **`engine`** — tie broken by severity order |
| (hyd, 0.10), (gear, 0.10) | `gear` — `gear` precedes `hyd` |
| (fuel, 0.62), (engine, 0.90) | `fuel` |

`worst_part` returns a **part code**, and `aircraft.worst_part` stores it. `risk` on the
aircraft row is then `risk_level(that part's health)` — the fleet-level risk is the worst
part's risk, never a separate computation.

---

## Rule 22 — Recommended action

> *Healthy means routine check, watch means plan inspection, critical means replace now.*

```python
ACTIONS = {"healthy": "Routine check",
           "watch":   "Plan inspection",
           "critical":"Replace now"}

def recommended_action(risk: str) -> str:
    return ACTIONS[risk]
```

Input is the **risk band**, not raw health — so rule 22 and rule 19 can never disagree.

| risk | action |
|---|---|
| `healthy` | `Routine check` |
| `watch` | `Plan inspection` |
| `critical` | `Replace now` |

**The client cannot set this.** `POST /work-orders` takes no `action` field; the server
derives it from the part's current risk at creation time and stores the result. If the
health changes later, the stored action is not silently rewritten — a work order is a
historical record of what was decided, and changing it requires a `PATCH`.

Seeded work orders get an initial action derived from `maintenance_schedule.task_type`
([01 §7](01-requirements-evaluation.md)), but every API response recomputes from live risk.

---

## Rule 23 — Do-by cycle

> *Within (RUL − 10) cycles for any non-healthy part.*

```python
def do_by_cycle(rul: int, current_cycle: int, risk: str) -> int | None:
    if risk == "healthy":
        return None
    target = rul - 10
    if target <= current_cycle:
        return current_cycle          # already overdue — act now
    return target
```

Three decisions that the one-line spec leaves open:

1. **`None` for healthy parts.** The spec says "for any non-healthy part", so a healthy part
   has no do-by. The frontend must render `—`, not `rul - 10`.
2. **Clamped to `current_cycle`.** When `rul - 10 <= current_cycle` the window has already
   closed. Returning a past cycle would render as a negative countdown; returning
   `current_cycle` renders as "due now", which is the correct operational meaning.
3. **Absolute cycle, not a count.** The field is `do_by_cycle`; a companion
   `do_by_in_cycles = do_by_cycle − current_cycle` is what the UI shows as a countdown.

| rul | current_cycle | risk | do_by_cycle | do_by_in_cycles |
|---:|---:|---|---:|---:|
| 118 | 87 | healthy | `null` | `null` |
| 118 | 87 | watch | 108 | 21 |
| 118 | 87 | critical | 108 | 21 |
| 24 | 38 | critical | **38** | **0** — "due now" |
| 8 | 38 | critical | **38** | **0** — clamped, window closed |
| 31 | 38 | watch | 21 → clamped to **38** | 0 |

**Non-engine parts have no RUL**, so they have no do-by either — `rul` is `NULL` in
`aircraft_part` for radar/gear/hyd/fuel and `do_by_cycle` is `null`. Their action and risk
still apply.

---

## Rule 24 — Back in service days

> *Agency slot days + turnaround days + spare lead time, only if stock is 0.*

```python
def back_in_service_days(slot_days: int, turnaround_days: int,
                         lead_time_days: int, stock: int) -> int:
    lead = lead_time_days if stock == 0 else 0
    return slot_days + turnaround_days + lead
```

The `stock == 0` test is exact. Stock of 1 still means the part is on the shelf and
procurement lead time is irrelevant.

| slot | turnaround | lead | stock | back_in_service | Explanation |
|---:|---:|---:|---:|---:|---|
| 2 | 13 | 39 | 0 | **54** | Nothing on the shelf — procure first |
| 2 | 13 | 39 | 1 | **15** | In stock — no procurement delay |
| 2 | 13 | 39 | 40 | **15** | Same |
| 1 | 24 | 12 | 0 | **37** | Real MA005 + 12-day-lead LPT case |
| 0 | 13 | 39 | 0 | **52** | No free slot, long lead time |
| 3 | 3 | 11 | 0 | **17** | Field unit, fast turnaround |

The API always returns `back_in_service_breakdown` alongside the total, so the frontend can
render the arithmetic rather than a bare number.

**Where the inputs come from:**

- `slot_days` ← `agency.free_slot_days`, derived as `CEIL((30 − booked_days) / open_bookings)`
  ([05 §7.1](05-database-design.md)). No such column exists in the source data.
- `turnaround_days` ← `agency.average_turnaround_days` (real values 3, 9, 11, 13, 13, 24).
- `lead_time_days` ← `spare.lead_time_days` (real values 3–118 days).
- `stock` ← `spare.quantity_available`.

A negative or zero turnaround is rejected at the database level
(`ck_agency_turn CHECK (turnaround_days > 0)`), so rule 24 can never produce a nonsensical
total.

---

## Rule 25 — Engine spare

> *Engine spare = the module of the weakest engine component (fan, hpc, hpt or lpt).*

```python
COMPONENTS = ("fan", "hpc", "hpt", "lpt")

def engine_spare(components: dict[str, float]) -> tuple[str | None, float | None]:
    present = {k: v for k, v in components.items() if k in COMPONENTS and v is not None}
    if not present:
        return None, None
    weakest = min(present.items(), key=lambda kv: (kv[1], COMPONENTS.index(kv[0])))[0]
    return weakest, present[weakest]
```

Tie-break follows the same fixed order as rule 21: `fan → hpc → hpt → lpt`, i.e. upstream
modules are preferred, matching how engine teardowns proceed.

| fan | hpc | hpt | lpt | weakest | recommended spare family |
|---:|---:|---:|---:|---|---|
| 0.66 | 0.61 | 0.70 | **0.59** | `lpt` | Low Pressure Turbine parts |
| **0.50** | **0.50** | 0.70 | 0.80 | `fan` | Fan parts (tie, upstream wins) |
| 0.95 | 0.93 | **0.31** | 0.88 | `hpt` | High Pressure Turbine parts |
| 0.20 | 0.80 | 0.80 | 0.80 | `fan` | Fan parts |
| null | 0.70 | 0.70 | 0.70 | `hpc` | `null` sensors ignored, tie among present |

The weakest component name is stored on `aircraft_part.worst_component` and the matching
spare row is resolved through `v_spare_for_component`
([05 §7](05-database-design.md)). When several spares cover the same component family, the
resolver prefers higher `criticality`, then lower `stock` — the part most likely to be
missing is the one worth surfacing.

**Only engine parts have components.** For radar/gear/hyd/fuel, `worst_component` is
`null` and the spare is resolved from the part's own assignment table.

---

## Rule 26 — RUL cap

> *Engine RUL is capped at 125 cycles.*

```python
RUL_CAP = 125

def cap_rul(raw_rul: float) -> int:
    return int(max(0, min(RUL_CAP, round(raw_rul))))
```

Applied at ingest, in this order: round → clamp to `[0, 125]` → cast to int. Applied in
**every** place RUL is produced or read — `ml_prediction`, `aircraft_part`, `aircraft`, and
the API response — so the frontend can never observe an out-of-range value.

| raw | capped | note |
|---:|---:|---|
| 145.6 | 125 | above cap |
| 125.0 | 125 | exactly at cap |
| 124.4 | 124 | below cap |
| 0.3 | 0 | rounding |
| −8.0 | 0 | negative predictions are floored at 0 |
| 302 | 125 | XGBoost's first validation prediction was 239.7 — a real case |

**Why 125 when C-MAPSS FD001 engines live 128–362 cycles?** It is a product decision, not a
data limit: it bounds the mission-ready window, makes the demo reach a critical state within
a few minutes, and keeps `do_by_cycle = rul − 10` inside a two-digit display. The response
sets `rul_capped: true` whenever the raw prediction exceeded 125, so the UI can mark the
value as a ceiling rather than a measurement.

The `CHECK (rul BETWEEN 0 AND 125)` constraint on three tables is the final backstop.

---

## Composite derivations

### Health → risk → action → do-by → schedule

The four rules compose into a single chain, and the ordering matters:

```
health
  ↓ rule 19  risk_level(health)
risk
  ↓ rule 22  recommended_action(risk)
action
  ↓ rule 23  do_by_cycle(rul, current_cycle, risk)   → null when risk == healthy
do_by_cycle
  ↓ rule 21  worst_part(parts) → aircraft-level risk
aircraft_risk
  ↓ rule 20  mission_ready(rul, parts)
mission_ready
  ↓ rule 25  engine_spare(components) → spare
  ↓ rule 24  back_in_service_days(slot, turnaround, lead, stock)
back_in_service_days
```

### The full evaluation for one part

```python
def evaluate_part(part_code, health, rul, current_cycle, components,
                  agency, spare) -> PartEvaluation:
    risk = risk_level(health)                                     # 19
    return PartEvaluation(
        health=health,
        risk=risk,
        action=recommended_action(risk),                          # 22
        do_by_cycle=do_by_cycle(rul, current_cycle, risk),        # 23
        weakest_component=engine_spare(components)[0],            # 25
        back_in_service_days=back_in_service_days(               # 24
            agency.free_slot_days, agency.turnaround_days,
            spare.lead_time_days, spare.stock),
    )
```

Called once per part per tick (40 calls) and once per part per detail request. Pure,
allocation-light, and total: **under 10 ms for the whole fleet**, which is why
`mission_ready` and `worst_part` can safely be recomputed on every write rather than
trusted from a stale cache.

---

## Test matrix (spec 52)

`tests/unit/test_rules.py` covers, per rule: the nominal case, both boundaries, the
tie-break, the empty/None input, and one real value drawn from the drive data.

| Test | Assertion |
|---|---|
| `test_healthy_above_070` | `risk_level(0.71) == "healthy"` |
| `test_boundary_070_is_watch` | `risk_level(0.70) == "watch"` |
| `test_boundary_040_is_critical` | `risk_level(0.40) == "critical"` |
| `test_risk_rejects_out_of_range` | `pytest.raises(ValueError)` for `1.4` and `-0.1` |
| `test_mission_ready_requires_rul_above_30` | 31 → True, **30 → False** |
| `test_mission_ready_requires_all_parts_above_040` | one part at 0.40 → False |
| `test_mission_ready_empty_parts` | False |
| `test_worst_part_min_health` | picks the minimum |
| `test_worst_part_tie_break_by_severity` | engine 0.50 vs radar 0.50 → engine |
| `test_action_per_band` | the three mappings |
| `test_doby_none_when_healthy` | `do_by_cycle(118, 87, "healthy") is None` |
| `test_doby_is_rul_minus_10` | `(118, 87, "watch") == 108` |
| `test_doby_clamped_when_window_closed` | `(8, 38, "critical") == 38` |
| `test_bis_adds_lead_only_at_zero_stock` | stock 0 → 55; stock 4 → 16 |
| `test_bis_real_agency_case` | slot 2, turn 24, lead 12, stock 0 → 38 |
| `test_engine_spare_weakest_component` | lpt 0.59 → `"lpt"` |
| `test_engine_spare_tie_prefers_upstream` | fan 0.50 = hpc 0.50 → `"fan"` |
| `test_engine_spare_ignores_null` | fan None → picks among the rest |
| `test_rul_capped_at_125` | `cap_rul(145.6) == 125` |
| `test_rul_floored_at_zero` | `cap_rul(-8.0) == 0` |
| `test_rul_rounds_half_up` | `cap_rul(117.4) == 117`, `cap_rul(117.6) == 118` |
| `test_fleet_evaluation_matches_spec_example` | full 8-aircraft golden fixture |

The last one is a golden-file test: a frozen JSON snapshot of the seeded fleet's evaluation,
regenerated only when a rule intentionally changes. Any accidental drift in any of the
eight rules fails it.

---

## Invariants the database enforces independently

Rules are implemented in the domain layer, but the schema refuses to store an impossible
state, so a domain bug cannot corrupt the database:

| Invariant | Enforcement |
|---|---|
| `health ∈ [0, 1]` | `CHECK` on `aircraft_part`, `health_snapshot`, `component_health` |
| `rul ∈ [0, 125]` | `CHECK` on `aircraft`, `aircraft_part`, `ml_prediction` |
| `risk ∈ {healthy, watch, critical}` | `ENUM` type |
| `worst_part` is a valid part | `CHECK` against the part vocabulary |
| `back_in_service_days ≥ 0` | `CHECK` |
| `stock ≥ 0` | `CHECK` on `spare` |
| `turnaround_days > 0` | `CHECK` on `agency` |
| One live alert per aircraft+part | partial unique index |
| One open work order per aircraft+part | partial unique index |
| Every mutation is audited | same-transaction write in `unit_of_work` |