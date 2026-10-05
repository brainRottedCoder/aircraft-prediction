"""Maintenance use cases — work orders, spares, agencies, alerts (spec 35-39).

Every mutation runs inside a UnitOfWork and writes an audit_log row in the same
transaction (spec 51).
"""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..core.errors import (
    BusinessRuleError,
    ConflictError,
    DuplicateWorkOrderError,
    NoSlotAvailableError,
    NotFoundError,
    OutOfStockError,
)
from ..db.unit_of_work import UnitOfWork
from ..domain.rules import recommended_action
from ..domain.scheduling import back_in_service_breakdown, eta_date
from ..models.auth import User
from ..models.maintenance import (
    Agency,
    AgencyBooking,
    Spare,
    StockMovement,
    WorkOrder,
)
from ..realtime.bus import bus
from ..realtime.events import (
    alert_acked,
    booking_created,
    spare_reserved,
    work_order_created,
    work_order_updated,
)
from ..repositories import fleet_repo as repo

VALID_TRANSITIONS = {
    ("open", "in_progress"),
    ("open", "done"),
    ("in_progress", "done"),
}


def _next_reference(db: Session) -> str:
    count = db.query(WorkOrder).count()
    return f"WO-{count + 1:04d}"


# ── 35 ─────────────────────────────────────────────────────────────────────────
def create_work_order(db: Session, payload: dict, user: User) -> dict:
    aircraft = repo.get_aircraft(db, payload["aircraft"])
    if aircraft is None:
        raise NotFoundError(f"Aircraft {payload['aircraft']!r} not found")
    part = repo.get_part(db, payload["part"])
    if part is None:
        raise BusinessRuleError(f"Unknown part {payload['part']!r}")

    with UnitOfWork(db) as uow:
        if repo.open_work_order(db, aircraft.id, part.id):
            raise DuplicateWorkOrderError(
                f"An open work order already exists for {aircraft.code} / {part.code}."
            )

        state = next(
            (r for r in repo.parts_for_aircraft(db, aircraft.id)
             if r.part.code == part.code),
            None,
        )
        # action is derived from live risk — the client cannot set it
        action = recommended_action(state.risk_level if state else "critical")

        work_order = WorkOrder(
            reference=_next_reference(db),
            aircraft_id=aircraft.id,
            part_id=part.id,
            action=action,
            due_date=payload["due_date"],
            due_cycle=state.do_by_cycle if state else None,
            status="open",
            priority=payload.get("priority", "medium"),
            notes=payload.get("notes"),
            created_by=user.id,
            created_at=uow.as_of(),
            updated_at=uow.as_of(),
        )
        db.add(work_order)
        uow.flush()
        after = _wo_out(work_order, aircraft.code, part.code, user)
        uow.audit(entity="work_order", entity_id=work_order.id, action="create",
                  actor_id=user.id, actor_name=user.username, after=after)

    bus_payload = dict(after)
    return after | {"_event": work_order_created(bus_payload)}


def update_work_order(db: Session, work_order_id: int, payload: dict, user: User) -> dict:
    work_order = db.get(WorkOrder, work_order_id)
    if work_order is None:
        raise NotFoundError(f"Work order {work_order_id} not found")

    before = {"status": work_order.status, "priority": work_order.priority,
              "due_date": str(work_order.due_date)}

    if work_order.status == "done":
        raise ConflictError("A completed work order is immutable.")

    new_status = payload.get("status")
    if new_status:
        if (work_order.status, new_status) not in VALID_TRANSITIONS:
            raise BusinessRuleError(
                f"Cannot move a work order from {work_order.status} to {new_status}."
            )
        now = datetime.now(UTC)
        work_order.status = new_status
        if new_status == "in_progress":
            work_order.started_at = work_order.started_at or now
        if new_status == "done":
            work_order.completed_at = now
            for booking in work_order.bookings:
                booking.completed_at = now

    for field in ("priority", "due_date", "notes", "action"):
        if payload.get(field) is not None:
            setattr(work_order, field, payload[field])
    work_order.updated_at = datetime.now(UTC)

    aircraft = repo.get_aircraft(db, work_order.aircraft_id)
    part = repo.get_part_by_id(db, work_order.part_id)
    after = _wo_out(work_order, aircraft.code, part.code, user)

    with UnitOfWork(db) as uow:
        uow.audit(entity="work_order", entity_id=work_order.id, action="update",
                  actor_id=user.id, actor_name=user.username,
                  before=before, after={"status": after["status"],
                                        "priority": after["priority"]})

    return after | {"_event": work_order_updated(after)}


def _wo_out(wo: WorkOrder, aircraft_code: str, part_code: str, user: User | None) -> dict:
    creator = None
    if wo.created_by:
        creator = {"id": user.id, "username": user.username, "full_name": user.full_name,
                   "role": user.role.value if hasattr(user.role, "value") else user.role} \
            if user else None
    return {
        "id": wo.id, "reference": wo.reference, "aircraft": aircraft_code,
        "part": part_code, "action": wo.action, "due_date": wo.due_date,
        "due_cycle": wo.due_cycle, "status": wo.status, "priority": wo.priority,
        "notes": wo.notes, "created_by": creator, "created_at": wo.created_at,
        "updated_at": wo.updated_at, "started_at": wo.started_at,
        "completed_at": wo.completed_at,
    }


def list_work_orders(db: Session, status: str | None, aircraft_id: int | None,
                     part_code: str | None, limit: int, offset: int) -> dict:
    work_orders = db.query(WorkOrder).offset(offset).limit(limit).all()
    items = []
    for wo in work_orders:
        if status and wo.status != status:
            continue
        aircraft = repo.get_aircraft(db, wo.aircraft_id)
        if aircraft_id and wo.aircraft_id != aircraft_id:
            continue
        part = repo.get_part_by_id(db, wo.part_id)
        if part_code and part.code != part_code:
            continue
        items.append(_wo_out(wo, aircraft.code, part.code, None))
    return {"items": items, "total": db.query(WorkOrder).count()}


# ── 36 / 37 ────────────────────────────────────────────────────────────────────
def update_spare(db: Session, part_ref_id: str, payload: dict, user: User) -> dict:
    with UnitOfWork(db) as uow:
        spare = (
            db.query(Spare)
            .filter(Spare.part_ref_id == part_ref_id)
            .with_for_update()
            .one_or_none()
        )
        if spare is None:
            raise NotFoundError(f"Spare {part_ref_id} not found")
        before = {"stock": spare.stock}
        new_stock = payload["stock"]
        delta = new_stock - spare.stock
        if delta:
            spare.stock = new_stock
            db.add(StockMovement(spare_id=spare.id, delta=delta,
                                 reason=payload.get("reason", "restock"),
                                 user_id=user.id, note=payload.get("note"),
                                 created_at=uow.as_of()))
        uow.audit(entity="spare", entity_id=spare.part_ref_id, action=payload.get("reason", "restock"),
                  actor_id=user.id, actor_name=user.username,
                  before=before, after={"stock": new_stock})
    return _spare_out(spare, None)


def reserve_spare(db: Session, part_ref_id: str, work_order_id: int | None, user: User) -> dict:
    """SELECT ... FOR UPDATE — prevents oversell under concurrent reservations."""
    with UnitOfWork(db) as uow:
        spare = (
            db.query(Spare)
            .filter(Spare.part_ref_id == part_ref_id)
            .with_for_update()
            .one_or_none()
        )
        if spare is None:
            raise NotFoundError(f"Spare {part_ref_id} not found")
        if spare.stock <= 0:
            raise OutOfStockError(
                f"{spare.part_ref_id} has no stock. Lead time is {spare.lead_time_days} days.",
                detail={"stock": spare.stock, "lead_time_days": spare.lead_time_days},
            )

        before = spare.stock
        spare.stock -= 1
        db.add(StockMovement(spare_id=spare.id, delta=-1, reason="reserve",
                             work_order_id=work_order_id, user_id=user.id,
                             created_at=uow.as_of()))

        wo_status = None
        if work_order_id:
            work_order = db.get(WorkOrder, work_order_id)
            if work_order is not None and work_order.status == "open":
                work_order.status = "in_progress"
                work_order.started_at = work_order.started_at or uow.as_of()
                work_order.updated_at = uow.as_of()
                wo_status = work_order.status

        uow.audit(entity="spare", entity_id=spare.part_ref_id, action="reserve",
                  actor_id=user.id, actor_name=user.username,
                  before={"stock": before}, after={"stock": spare.stock})

    result = {
        "part_ref_id": spare.part_ref_id, "stock_before": before,
        "stock_remaining": spare.stock, "reserved_at": datetime.now(UTC),
        "work_order_id": work_order_id, "work_order_status": wo_status,
    }
    return result | {"_event": spare_reserved({"part_ref_id": spare.part_ref_id,
                                               "stock_remaining": spare.stock})}


def _spare_out(spare: Spare, last_movement) -> dict:
    return {
        "part_ref_id": spare.part_ref_id, "item_name": spare.item_name,
        "component_name": spare.component_name, "aircraft_model": spare.aircraft_model,
        "stock": spare.stock, "minimum_stock": spare.minimum_stock,
        "reorder_quantity": spare.reorder_quantity, "supplier": spare.supplier,
        "lead_time_days": spare.lead_time_days, "unit_cost_inr": spare.unit_cost_inr,
        "storage_location": spare.storage_location, "criticality": spare.criticality,
        "in_stock": spare.stock > 0, "low_stock": spare.stock <= spare.minimum_stock,
        "last_movement": (
            {"delta": last_movement.delta, "reason": last_movement.reason,
             "at": last_movement.created_at}
            if last_movement else None
        ),
    }


def list_spares(db: Session) -> dict:
    movements = repo.last_stock_movements(db)      # one query, not forty
    items = [_spare_out(spare, movements.get(spare.id))
             for spare in repo.list_spares(db)]
    return {"items": items, "total": len(items)}


# ── 38 ─────────────────────────────────────────────────────────────────────────
def list_agencies(db: Session) -> dict:
    parts = repo.list_parts(db)
    items = []
    for agency in repo.list_agencies(db):
        bookings = repo.open_bookings(db, agency.id)
        items.append({
            "id": agency.id, "agency_ref_id": agency.agency_ref_id, "name": agency.name,
            "type": agency.type, "location": agency.location,
            "specialisation": agency.specialisation,
            "turnaround_days": agency.turnaround_days,
            "monthly_capacity_slots": agency.monthly_capacity_slots,
            "cost_multiplier": float(agency.cost_multiplier),
            "free_slot_days": agency.free_slot_days,
            "open_bookings": len(bookings),
            "handles_parts": [p.code for p in parts if p.agency_id == agency.id],
        })
    return {"items": items}


def create_booking(db: Session, agency_id: int, payload: dict, user: User) -> dict:
    aircraft = repo.get_aircraft(db, payload["aircraft"])
    if aircraft is None:
        raise NotFoundError(f"Aircraft {payload['aircraft']!r} not found")
    part = repo.get_part(db, payload["part"])
    if part is None:
        raise BusinessRuleError(f"Unknown part {payload['part']!r}")

    with UnitOfWork(db) as uow:
        agency = (
            db.query(Agency).filter(Agency.id == agency_id).with_for_update().one_or_none()
        )
        if agency is None:
            raise NotFoundError(f"Agency {agency_id} not found")

        bookings = repo.open_bookings(db, agency.id)
        booked_days = sum(b.turnaround_days for b in bookings)
        from ..domain.scheduling import agency_free_slot_days

        free_slot = agency_free_slot_days(
            agency.monthly_capacity_slots, booked_days, len(bookings)
        )
        if free_slot <= 0:
            raise NoSlotAvailableError(
                f"{agency.name} has no free slot this month.",
                detail={"turnaround_days": agency.turnaround_days},
            )
        agency.free_slot_days = free_slot

        state = next(
            (r for r in repo.parts_for_aircraft(db, aircraft.id)
             if r.part.code == part.code), None
        )
        spare = state.spare if state else None
        stock = spare.stock if spare else 0
        lead = spare.lead_time_days if spare else 0

        breakdown = back_in_service_breakdown(
            free_slot, agency.turnaround_days, lead, stock
        )
        booking = AgencyBooking(
            agency_id=agency.id, aircraft_id=aircraft.id, part_id=part.id,
            work_order_id=payload.get("work_order_id"),
            booked_on=uow.as_of().date(),
            slot_days=breakdown["slot_days"],
            turnaround_days=breakdown["turnaround_days"],
            lead_time_days=breakdown["lead_time_days"],
            eta_date=eta_date(breakdown["slot_days"], breakdown["turnaround_days"],
                              breakdown["lead_time_days"], stock),
            created_by=user.id, created_at=uow.as_of(),
        )
        db.add(booking)
        uow.flush()
        uow.audit(entity="agency", entity_id=agency.agency_ref_id, action="book",
                  actor_id=user.id, actor_name=user.username,
                  after={"eta_date": str(booking.eta_date),
                         "back_in_service_days": breakdown["total"]})

    result = {
        "id": booking.id, "agency": agency.name, "aircraft": aircraft.code,
        "part": part.code, "booked_on": booking.booked_on,
        "slot_days": booking.slot_days, "turnaround_days": booking.turnaround_days,
        "lead_time_days": booking.lead_time_days, "eta_date": booking.eta_date,
        "back_in_service_days": breakdown["total"],
        "breakdown": {"slot": breakdown["slot_days"],
                      "turnaround": breakdown["turnaround_days"],
                      "lead_time": breakdown["lead_time_days"]},
    }
    return result | {"_event": booking_created(result)}


# ── 39 ─────────────────────────────────────────────────────────────────────────
def list_alerts(db: Session, acknowledged: bool | None, level: str | None,
                aircraft_code: str | None, limit: int) -> dict:
    rows = repo.list_alerts(db, acknowledged, limit * 2)
    items = []
    for alert in rows:
        aircraft = repo.get_aircraft(db, alert.aircraft_id)
        part = repo.get_part_by_id(db, alert.part_id)
        if level and alert.level != level:
            continue
        if aircraft_code and aircraft.code != aircraft_code:
            continue
        items.append({
            "id": alert.id, "aircraft": aircraft.code, "aircraft_id": aircraft.id,
            "part": part.code, "level": alert.level, "message": alert.message,
            "health": float(alert.health) if alert.health is not None else None,
            "cycle": alert.cycle, "acknowledged": alert.acknowledged,
            "acknowledged_by": None, "acknowledged_at": alert.acked_at,
            "created_at": alert.created_at,
        })
        if len(items) >= limit:
            break
    return {"items": items, "unacknowledged_count": repo.unacked_count(db)}


def ack_alert(db: Session, alert_id: int, note: str | None, user: User) -> dict:
    with UnitOfWork(db) as uow:
        alert = repo.get_alert(db, alert_id)
        if alert is None:
            raise NotFoundError(f"Alert {alert_id} not found")
        before = {"acknowledged": alert.acknowledged}
        now = uow.as_of()
        alert.acknowledged = True
        alert.acked_by = user.id
        alert.acked_at = now
        uow.audit(entity="alert", entity_id=alert.id, action="ack",
                  actor_id=user.id, actor_name=user.username,
                  before=before, after={"acknowledged": True, "note": note})

    return {
        "id": alert.id, "acknowledged": True,
        "acknowledged_by": {"id": user.id, "username": user.username,
                            "full_name": user.full_name,
                            "role": user.role.value if hasattr(user.role, "value") else user.role},
        "acknowledged_at": alert.acked_at, "note": note,
        "_event": alert_acked({"id": alert.id, "acknowledged_by": user.username,
                               "acknowledged_at": str(alert.acked_at)}),
    }


def publish_events(result: dict) -> dict:
    """Strip the internal _event key and publish it on the bus."""
    event = result.pop("_event", None)
    if event is not None:
        bus.publish_soon(event)
    return result