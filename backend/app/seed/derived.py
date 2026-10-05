"""Derived state — part catalog, health, spare/agency wiring, work orders, alerts."""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..domain.health import burden_health, maintenance_burden
from ..domain.rules import PART_ORDER, risk_level
from ..domain.scheduling import back_in_service_breakdown
from ..models.alert import Alert
from ..models.fleet import Aircraft, AircraftPart, Part
from ..models.maintenance import Agency, Spare, WorkOrder
from ..models.reference import AircraftRef, ComponentRef, Snag, TechnicalRecord

log = logging.getLogger(__name__)

PART_CATALOG = [
    ("engine", "Engine", 1, False, "rul_xgb_fd001", "engine"),
    ("radar", "Radar", 2, True, "maintenance_burden_v1", "avionics"),
    ("gear", "Landing Gear", 3, True, "maintenance_burden_v1", "general"),
    ("hyd", "Hydraulics", 4, True, "maintenance_burden_v1", "hydraulics"),
    ("fuel", "Fuel System", 5, True, "maintenance_burden_v1", "general"),
]

# 8 aircraft bound 1:1 to C-MAPSS FD001 engine units, chosen for spread of
# remaining life; offsets stagger the demo so the fleet does not fail in lockstep.
FLEET_BINDING = [
    ("Fighter-01", "AC001", 1, 0),
    ("Fighter-02", "AC002", 2, 30),
    ("Fighter-03", "AC003", 3, 60),
    ("Fighter-04", "AC004", 4, 90),
    ("Fighter-05", "AC005", 7, 120),
    ("Fighter-06", "AC006", 12, 150),
    ("Fighter-07", "AC007", 18, 30),
    ("Fighter-08", "AC008", 24, 60),
]

TASK_TYPE_TO_ACTION = {
    "Component Replacement": "Replace now",
    "Inspection": "Plan inspection",
    "Functional Check": "Routine check",
    "Lubrication": "Routine check",
    "Preventive Maintenance": "Routine check",
}


def seed_part_catalog(db: Session) -> int:
    """Part → agency by specialisation, else by lowest cost × turnaround (docs/01 §6)."""
    all_agencies = list(db.scalars(select(Agency)))
    by_spec: dict[str, list] = {}
    for agency in all_agencies:
        by_spec.setdefault(agency.specialisation, []).append(agency)

    def cheapest(candidates: list) -> object | None:
        # the general pool has no specialisation match, so rank it by cost
        return min(candidates, key=lambda a: float(a.cost_multiplier) * a.turnaround_days,
                   default=None)

    for code, label, order, simulated, method, spec in PART_CATALOG:
        agency = cheapest(by_spec.get(spec) or by_spec.get("general") or [])
        part = db.scalar(select(Part).where(Part.code == code))
        if part is None:
            part = Part(code=code, label=label, sort_order=order,
                        is_simulated=simulated, method=method,
                        agency_id=agency.id if agency else None)
            db.add(part)
        else:
            part.label = label
            part.is_simulated = simulated
            part.method = method
            part.agency_id = getattr(agency, "id", None)
    db.flush()
    return len(PART_CATALOG)


def seed_aircraft(db: Session) -> int:
    refs = {a.aircraft_id: a for a in db.scalars(select(AircraftRef))}
    count = 0
    for code, ref_id, unit, offset in FLEET_BINDING:
        ref = refs.get(ref_id)
        if ref is None:
            continue
        aircraft = db.scalar(select(Aircraft).where(Aircraft.code == code))
        if aircraft is None:
            aircraft = Aircraft(code=code, name=code, aircraft_ref_id=ref.id,
                                cmapss_unit_id=unit, tail_number=ref.tail_number,
                                aircraft_model=ref.aircraft_model,
                                home_base=ref.home_base, demo_offset=offset,
                                current_cycle=offset + 1, rul=125)
            db.add(aircraft)
            db.flush()
        count += 1
    db.flush()
    return count


def _months_between(earlier: date, later: date) -> float:
    return max(0.0, (later - earlier).days / 30.44)


def seed_aircraft_parts(db: Session) -> int:
    """Engine health is filled by the ML service; the other four come from
    maintenance_burden_v1 (docs/01 §4)."""
    parts = {p.code: p for p in db.scalars(select(Part))}
    snags_by = {}
    today = date.today()
    for snag in db.scalars(select(Snag)):
        snags_by.setdefault(snag.aircraft_ref_id, []).append({
            "severity": snag.severity,
            "age_months": _months_between(snag.date_reported, today) if snag.date_reported else 0.0,
        })
    records_by = {}
    for record in db.scalars(select(TechnicalRecord)):
        records_by.setdefault(record.aircraft_ref_id, []).append({
            "fault_type": record.fault_type,
            "age_months": _months_between(record.event_date, today) if record.event_date else 0.0,
            "downtime_hours": float(record.downtime_hours or 0),
        })

    # p95 burden per part across the whole fleet, so health is fleet-relative
    p95: dict[str, float] = {}
    for code in PART_ORDER:
        if code == "engine":
            continue
        scores = []
        for ref in db.scalars(select(AircraftRef)):
            score = maintenance_burden(
                records_by.get(ref.id, []), snags_by.get(ref.id, []), code, 1.0
            )
            scores.append(score)
        p95[code] = _percentile(scores, 95) or 1.0

    count = 0
    for aircraft in db.scalars(select(Aircraft)):
        for code in PART_ORDER:
            part = parts[code]
            row = db.scalar(
                select(AircraftPart).where(
                    AircraftPart.aircraft_id == aircraft.id,
                    AircraftPart.part_id == part.id,
                )
            )
            if row is None:
                row = AircraftPart(aircraft_id=aircraft.id, part_id=part.id,
                                   health=1.0, risk_level="healthy",
                                   is_simulated=part.is_simulated)
                db.add(row)

            if code == "engine":
                continue          # ML owns the engine; filled on first tick

            score = maintenance_burden(
                records_by.get(aircraft.aircraft_ref_id, []),
                snags_by.get(aircraft.aircraft_ref_id, []),
                code, p95[code],
            )
            health = burden_health(score)
            row.health = health
            row.risk_level = risk_level(health)
            row.is_simulated = True
            row.agency_id = part.agency_id

            spare = _spare_for_part(db, code, part)
            agency = db.get(Agency, part.agency_id) if part.agency_id else None
            if spare is not None:
                row.spare_id = spare.id
                # rule 24 with the part's assigned agency's real slot and turnaround
                row.back_in_service_days = back_in_service_breakdown(
                    agency.free_slot_days if agency else 1,
                    agency.turnaround_days if agency else 1,
                    spare.lead_time_days, spare.stock,
                )["total"]
            count += 1
    db.flush()
    return count


def _spare_for_part(db: Session, code: str, part: Part) -> Spare | None:
    if code == "engine":
        return None
    component = {
        "radar": None, "gear": "Fan", "hyd": None, "fuel": None
    }.get(code)
    stmt = select(Spare)
    if component:
        stmt = stmt.where(Spare.component_name == component)
    return db.scalars(stmt.order_by(Spare.stock.asc())).first()


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


def seed_work_orders(db: Session) -> int:
    """maintenance_schedule.csv with its status vocabulary mapped (docs/01 §7)."""
    from ..models.auth import User
    from ..seed.loaders.base import read_csv

    seeder = db.scalar(select(User).where(User.username == "commander"))
    rows = read_csv("maintenance_schedule.csv")
    refs = {a.aircraft_id: a for a in db.scalars(select(AircraftRef))}
    components = {c.component_id: c for c in db.scalars(select(ComponentRef))}
    parts = {p.code: p for p in db.scalars(select(Part))}
    aircraft_by_ref = {
        a.aircraft_ref_id: a for a in db.scalars(select(Aircraft))
    }

    count = 0
    for row in rows:
        ref = refs.get(row["aircraft_id"])
        component = components.get(row.get("component_id"))
        aircraft = aircraft_by_ref.get(ref.id) if ref else None
        if aircraft is None or component is None:
            continue

        # All 6 engine components collapse onto the single `engine` part (docs/01 §4);
        # radar/gear/hyd/fuel have no per-aircraft work orders in the source schedule.
        part = parts["engine"]
        if db.scalar(
            select(WorkOrder).where(
                WorkOrder.aircraft_id == aircraft.id,
                WorkOrder.part_id == part.id,
                WorkOrder.status != "done",
            )
        ):
            continue

        priority = (row.get("priority") or "Medium").lower()
        db.add(WorkOrder(
            reference=f"WO-{len(rows) + count:04d}",
            aircraft_id=aircraft.id, part_id=part.id,
            action=TASK_TYPE_TO_ACTION.get(row.get("task_type"), "Routine check"),
            due_date=_parse_date(row.get("due_date")),
            due_cycle=_parse_int(row.get("due_cycles")),
            status="open",
            priority=priority if priority in ("low", "medium", "high") else "medium",
            source_ref=row.get("schedule_id"),
            created_by=seeder.id if seeder else None,
        ))
        db.flush()   # autoflush is off, so the uniqueness check needs this
        count += 1
    db.flush()
    return count


def _parse_date(value: str | None) -> date:
    from datetime import datetime

    if not value:
        return date.today()
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return date.today()


def _parse_int(value: str | None) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def seed_alerts(db: Session) -> int:
    """One alert per non-healthy part at seed time (docs/03 §9)."""
    count = 0
    for aircraft, row in db.execute(
        select(Aircraft, AircraftPart).join(
            AircraftPart, AircraftPart.aircraft_id == Aircraft.id
        )
    ).tuples():
        if row.risk_level == "healthy":
            continue
        if db.scalar(
            select(Alert).where(
                Alert.aircraft_id == aircraft.id,
                Alert.part_id == row.part_id,
                Alert.acknowledged.is_(False),
            )
        ):
            continue
        db.add(Alert(
            aircraft_id=aircraft.id, part_id=row.part_id, level=row.risk_level,
            message=f"{row.part.label} health {float(row.health):.2f} — "
                    f"{'Replace now' if row.risk_level == 'critical' else 'Plan inspection'}.",
            cycle=aircraft.current_cycle, health=row.health,
        ))
        count += 1
    db.flush()
    return count


def reconcile_health(db: Session) -> list[str]:
    """Log any aircraft where ML health and component_ref life-fraction disagree
    by more than 0.25 (docs/01 §5). ML stays the source of truth."""
    notes: list[str] = []
    for aircraft in db.scalars(select(Aircraft)):
        rows = db.scalars(
            select(ComponentRef).where(ComponentRef.aircraft_ref_id == aircraft.aircraft_ref_id)
        ).all()
        fractions = [c.implied_health for c in rows if c.implied_health is not None]
        if not fractions:
            continue
        mean = sum(fractions) / len(fractions)
        implied = max(0.0, min(1.0, mean))
        actual = aircraft.rul / 125
        if abs(implied - actual) > 0.25:
            notes.append(
                f"{aircraft.code}: drive-implied health {implied:.2f} vs "
                f"ML {actual:.2f} (divergence {abs(implied - actual):.2f})"
            )
    return notes


__all__ = [
    "seed_part_catalog", "seed_aircraft", "seed_aircraft_parts",
    "seed_work_orders", "seed_alerts", "reconcile_health",
    "PART_CATALOG", "FLEET_BINDING",
]