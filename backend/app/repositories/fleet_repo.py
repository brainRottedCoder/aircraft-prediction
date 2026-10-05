"""Data access. No business logic, no rule evaluation — load and persist only.

The fixed query count per endpoint is deliberate: the N+1 guard asserted by
tests/performance/test_budgets.py (docs/11 §5.3).
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models.alert import Alert
from ..models.auth import User
from ..models.fleet import Aircraft, AircraftPart, Part
from ..models.maintenance import (
    Agency,
    AgencyBooking,
    Spare,
    StockMovement,
    WorkOrder,
)
from ..models.reference import FlightOpsMonthly, TechnicalRecord
from ..models.telemetry import ComponentHealth, HealthSnapshot, MlPrediction


# ── users ──────────────────────────────────────────────────────────────────────
def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def get_user(db: Session, user_id: int) -> User | None:
    return db.get(User, user_id)


def count_users(db: Session) -> int:
    """How many accounts exist.

    Separate from `count_aircraft` on purpose: a database can hold a full fleet and zero
    users, and that combination makes every login fail with an indistinguishable 401. The
    boot seed uses this to tell "already seeded, skip the expensive work" apart from
    "needs accounts".
    """
    return db.scalar(select(func.count(User.id))) or 0


# ── fleet ──────────────────────────────────────────────────────────────────────
def list_aircraft(db: Session) -> list[Aircraft]:
    return list(db.scalars(select(Aircraft).order_by(Aircraft.code)))


def get_aircraft(db: Session, code_or_id: str | int) -> Aircraft | None:
    if isinstance(code_or_id, int) or str(code_or_id).isdigit():
        return db.get(Aircraft, int(code_or_id))
    return db.scalar(select(Aircraft).where(Aircraft.code == code_or_id))


def get_part(db: Session, code: str) -> Part | None:
    """By part CODE ('engine', 'hyd', …) — not the surrogate id."""
    return db.scalar(select(Part).where(Part.code == code))


def get_part_by_id(db: Session, part_id: int) -> Part | None:
    """By surrogate id, for rows that carry only the foreign key."""
    return db.get(Part, part_id)


def list_parts(db: Session) -> list[Part]:
    return list(db.scalars(select(Part).order_by(Part.sort_order)))


def parts_for_aircraft(db: Session, aircraft_id: int) -> list[AircraftPart]:
    return list(
        db.scalars(
            select(AircraftPart)
            .join(Part, Part.id == AircraftPart.part_id)
            .where(AircraftPart.aircraft_id == aircraft_id)
            .order_by(Part.sort_order)
        )
    )


def all_parts_by_aircraft(db: Session) -> dict[int, list[AircraftPart]]:
    """Every aircraft's parts in ONE query — the N+1 guard for heatmap/schedule."""
    grouped: dict[int, list[AircraftPart]] = {}
    rows = db.execute(
        select(AircraftPart)
        .join(Part, Part.id == AircraftPart.part_id)
        .order_by(AircraftPart.aircraft_id, Part.sort_order)
    ).scalars()
    for row in rows:
        grouped.setdefault(row.aircraft_id, []).append(row)
    return grouped


def open_work_orders(db: Session) -> dict[tuple[int, int], WorkOrder]:
    """(aircraft_id, part_id) -> open work order, in ONE query."""
    return {
        (w.aircraft_id, w.part_id): w
        for w in db.scalars(select(WorkOrder).where(WorkOrder.status != "done"))
    }


def agencies_by_id(db: Session) -> dict[int, Agency]:
    return {agency.id: agency for agency in db.scalars(select(Agency))}


def all_parts_with_aircraft(db: Session) -> list[tuple[Aircraft, AircraftPart, Part]]:
    return list(
        db.execute(
            select(Aircraft, AircraftPart, Part)
            .join(AircraftPart, AircraftPart.aircraft_id == Aircraft.id)
            .join(Part, Part.id == AircraftPart.part_id)
            .order_by(Aircraft.code, Part.sort_order)
        ).tuples()
    )


def open_work_order(db: Session, aircraft_id: int, part_id: int) -> WorkOrder | None:
    return db.scalar(
        select(WorkOrder).where(
            WorkOrder.aircraft_id == aircraft_id,
            WorkOrder.part_id == part_id,
            WorkOrder.status != "done",
        )
    )


# ── time-series ────────────────────────────────────────────────────────────────
def engine_history(
    db: Session, aircraft_id: int, window: int
) -> list[tuple[HealthSnapshot, MlPrediction | None, ComponentHealth | None]]:
    cycles = db.scalars(
        select(HealthSnapshot.cycle)
        .join(Part, Part.id == HealthSnapshot.part_id)
        .where(
            HealthSnapshot.aircraft_id == aircraft_id,
            Part.code == "engine",
        )
        .order_by(HealthSnapshot.cycle.desc())
        .limit(window)
    ).all()
    if not cycles:
        return []
    lo, hi = min(cycles), max(cycles)

    snapshots = {
        s.cycle: s
        for s in db.scalars(
            select(HealthSnapshot).where(
                HealthSnapshot.aircraft_id == aircraft_id,
                HealthSnapshot.cycle.between(lo, hi),
            )
        )
    }
    preds = {
        p.cycle: p
        for p in db.scalars(
            select(MlPrediction).where(
                MlPrediction.aircraft_id == aircraft_id,
                MlPrediction.cycle.between(lo, hi),
            )
        )
    }
    comps = {
        c.cycle: c
        for c in db.scalars(
            select(ComponentHealth).where(
                ComponentHealth.aircraft_id == aircraft_id,
                ComponentHealth.cycle.between(lo, hi),
            )
        )
    }
    return [(snapshots[c], preds.get(c), comps.get(c)) for c in sorted(snapshots)]


def fleet_snapshots(db: Session, window: int) -> dict[int, list[dict]]:
    """Last `window` engine snapshots per aircraft, in one query."""
    rows = db.execute(
        select(HealthSnapshot.aircraft_id, HealthSnapshot.cycle, HealthSnapshot.health)
        .join(Part, Part.id == HealthSnapshot.part_id)
        .where(Part.code == "engine")
        .order_by(HealthSnapshot.aircraft_id, HealthSnapshot.cycle)
    ).all()
    grouped: dict[int, list[dict]] = {}
    for aircraft_id, cycle, health in rows:
        grouped.setdefault(aircraft_id, []).append(
            {"cycle": cycle, "health": float(health)}
        )
    return {k: v[-window:] for k, v in grouped.items()}


# ── maintenance ────────────────────────────────────────────────────────────────
def list_spares(db: Session) -> list[Spare]:
    return list(db.scalars(select(Spare).order_by(Spare.part_ref_id)))


def last_stock_movements(db: Session) -> dict[int, StockMovement]:
    """Latest movement per spare in ONE query — the N+1 guard for GET /spares."""
    latest: dict[int, StockMovement] = {}
    rows = db.scalars(
        select(StockMovement).order_by(StockMovement.created_at.desc())
    )
    for row in rows:
        latest.setdefault(row.spare_id, row)
    return latest


def list_agencies(db: Session) -> list[Agency]:
    return list(db.scalars(select(Agency).order_by(Agency.agency_ref_id)))


def open_bookings(db: Session, agency_id: int) -> list[AgencyBooking]:
    return list(
        db.scalars(
            select(AgencyBooking).where(
                AgencyBooking.agency_id == agency_id,
                AgencyBooking.completed_at.is_(None),
            )
        )
    )


def spare_for_component(db: Session, component_name: str | None) -> Spare | None:
    """Rule 25 — resolve the spare for a component family (docs/05 §7)."""
    if not component_name:
        return None
    return db.scalar(
        select(Spare)
        .where(Spare.component_name == component_name)
        .order_by(
            Spare.criticality.desc().nullslast(),
            Spare.stock.asc(),
        )
        .limit(1)
    )


def spares_by_component(db: Session) -> dict[str, Spare]:
    """component_name -> best spare, in ONE query (rule 25).

    Ordering matches spare_for_component: highest criticality, then lowest stock,
    so the part most likely to be missing is the one surfaced.
    """
    ordered = db.scalars(
        select(Spare)
        .where(Spare.component_name.is_not(None))
        .order_by(Spare.criticality.desc().nullslast(), Spare.stock.asc())
    )
    out: dict[str, Spare] = {}
    for spare in ordered:
        out.setdefault(str(spare.component_name), spare)
    return out


# ── alerts ─────────────────────────────────────────────────────────────────────
def list_alerts(db: Session, acknowledged: bool | None, limit: int) -> list[Alert]:
    stmt = select(Alert).order_by(Alert.created_at.desc()).limit(limit)
    if acknowledged is not None:
        stmt = stmt.where(Alert.acknowledged.is_(acknowledged))
    return list(db.scalars(stmt))


def get_alert(db: Session, alert_id: int) -> Alert | None:
    return db.get(Alert, alert_id)


def unacked_count(db: Session) -> int:
    return db.scalar(
        select(func.count(Alert.id)).where(Alert.acknowledged.is_(False))
    ) or 0


# ── technical records ──────────────────────────────────────────────────────────
def recent_records(
    db: Session, aircraft_ref_id: int, part: str, limit: int = 2
) -> list[tuple[TechnicalRecord, str | None]]:
    """Last `limit` records for the parts mapped to `part` (docs/06 §2)."""
    from ..domain.health import FAULT_MAP

    if part == "engine":
        rows = db.scalars(
            select(TechnicalRecord)
            .where(TechnicalRecord.aircraft_ref_id == aircraft_ref_id)
            .order_by(TechnicalRecord.event_date.desc())
            .limit(limit)
        ).all()
        return [(r, r.agency_ref_id) for r in rows]

    faults = [f for f, (p, _) in FAULT_MAP.items() if p == part]
    if not faults:
        return []
    rows = db.scalars(
        select(TechnicalRecord)
        .where(
            TechnicalRecord.aircraft_ref_id == aircraft_ref_id,
            TechnicalRecord.fault_type.in_(faults),
        )
        .order_by(TechnicalRecord.event_date.desc())
        .limit(limit)
    ).all()
    return [(r, r.agency_ref_id) for r in rows]


def availability(db: Session, aircraft_ref_id: int) -> float | None:
    """days_available / (available + unavailable) over flight_operations (docs/01 §9)."""
    row = db.execute(
        select(
            func.avg(
                FlightOpsMonthly.days_available
                / func.nullif(
                    FlightOpsMonthly.days_available + FlightOpsMonthly.days_unavailable, 0
                )
            )
        ).where(FlightOpsMonthly.aircraft_ref_id == aircraft_ref_id)
    ).scalar()
    return round(float(row), 4) if row is not None else None


def high_stress_sorties(db: Session, aircraft_ref_id: int, months: int = 12) -> int | None:
    rows = db.scalars(
        select(FlightOpsMonthly.high_stress_sorties)
        .where(FlightOpsMonthly.aircraft_ref_id == aircraft_ref_id)
        .order_by(FlightOpsMonthly.month.desc())
        .limit(months)
    ).all()
    return sum(v for v in rows if v is not None) if rows else None


# ── reference ──────────────────────────────────────────────────────────────────
def count_aircraft(db: Session) -> int:
    return db.scalar(select(func.count(Aircraft.id))) or 0