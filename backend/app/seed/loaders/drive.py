"""Loaders for the eight Drive CSVs.

Each maps the source column names onto model column names and upserts on the
model's natural key, so the seed is idempotent (docs/10 Phase 2).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models.maintenance import Agency, Spare
from ...models.reference import (
    AircraftRef,
    ComponentRef,
    FlightOpsMonthly,
    Snag,
    TechnicalRecord,
)
from .base import _date, _decimal, _int, _text, read_csv, rename, upsert_all


def _aircraft_ref_index(db: Session) -> dict[str, int]:
    return {a.aircraft_id: a.id for a in db.scalars(select(AircraftRef))}


def _component_ref_index(db: Session) -> dict[str, int]:
    return {c.component_id: c.id for c in db.scalars(select(ComponentRef))}


def load_aircraft(db: Session) -> int:
    return upsert_all(db, AircraftRef, read_csv("aircraft.csv"), ("aircraft_id",), {
        "aircraft_id": _text, "tail_number": _text, "aircraft_model": _text,
        "engine_id": _text, "engine_model": _text,
        "manufacture_date": _date, "induction_date": _date, "home_base": _text,
        "total_flight_hours": _int, "total_cycles": _int, "current_status": _text,
    })


def load_components(db: Session) -> int:
    refs = _aircraft_ref_index(db)
    rows = [
        {**r, "aircraft_ref_id": refs[r["aircraft_id"]]}
        for r in read_csv("components.csv")
        if r["aircraft_id"] in refs
    ]
    return upsert_all(db, ComponentRef, rows, ("component_id",), {
        "component_id": _text, "aircraft_ref_id": _int, "engine_id": _text,
        "component_name": _text, "component_system": _text,
        "installation_date": _date, "operating_hours": _int,
        "operating_cycles": _int, "life_limit_hours": _int,
        "life_limit_cycles": _int, "health_status": _text, "times_replaced": _int,
    })


def load_flight_ops(db: Session) -> int:
    refs = _aircraft_ref_index(db)
    rows = [
        {**r, "aircraft_ref_id": refs[r["aircraft_id"]]}
        for r in read_csv("flight_operations.csv")
        if r["aircraft_id"] in refs
    ]
    return upsert_all(
        db, FlightOpsMonthly, rows, ("aircraft_ref_id", "month"), {
            "aircraft_ref_id": _int, "month": _text, "flight_hours": _decimal,
            "sorties": _int, "flight_cycles": _int,
            "avg_sortie_duration_hours": _decimal, "high_stress_sorties": _int,
            "days_available": _int, "days_unavailable": _int,
        }
    )


def load_agencies(db: Session) -> int:
    """No free_slot_days in the source — derived as ceil(30 / capacity_slots)."""
    import math

    rows = rename(read_csv("maintenance_agencies.csv"), {
        "agency_id": "agency_ref_id",
        "agency_name": "name",
        "average_turnaround_days": "turnaround_days",
        "capacity_slots_per_month": "monthly_capacity_slots",
    })
    count = upsert_all(db, Agency, rows, ("agency_ref_id",), {
        "agency_ref_id": _text, "name": _text, "type": _text, "location": _text,
        "specialisation": _text, "turnaround_days": _int,
        "monthly_capacity_slots": _int, "cost_multiplier": _decimal,
        "free_slot_days": _int,
    })
    for agency in db.scalars(select(Agency)):
        agency.free_slot_days = max(
            1, math.ceil(30 / max(1, agency.monthly_capacity_slots))
        )
    db.flush()
    return count


def load_spares(db: Session) -> int:
    components = _component_ref_index(db)
    rows = [
        {**r, "component_ref_id": components.get(r.get("component_id"))}
        for r in rename(read_csv("spare_parts.csv"), {
            "part_id": "part_ref_id",
            "part_name": "item_name",
            "quantity_available": "stock",
        })
    ]
    return upsert_all(db, Spare, rows, ("part_ref_id",), {
        "part_ref_id": _text, "item_name": _text, "component_ref_id": _int,
        "component_name": _text, "aircraft_model": _text, "stock": _int,
        "minimum_stock": _int, "reorder_quantity": _int, "supplier": _text,
        "lead_time_days": _int, "unit_cost_inr": _int, "storage_location": _text,
        "last_restock_date": _date, "criticality": _text,
    })


def load_tech_records(db: Session) -> int:
    refs = _aircraft_ref_index(db)
    components = _component_ref_index(db)
    source = rename(read_csv("maintenance_records.csv"), {
        "date": "event_date",
        "maintenance_action": "action",
        "agency_id": "agency_ref_id",
        "part_id": "part_ref_id",
    })
    rows = [
        {**r,
         "aircraft_ref_id": refs.get(r["aircraft_id"]),
         "component_ref_id": components.get(r.get("component_id")),
         "record_id": r["maintenance_id"]}
        for r in source
        if r["aircraft_id"] in refs
    ]
    return upsert_all(db, TechnicalRecord, rows, ("record_id",), {
        "record_id": _text, "aircraft_ref_id": _int, "component_ref_id": _int,
        "agency_ref_id": _text, "part_ref_id": _text, "event_date": _date,
        "maintenance_type": _text, "fault_type": _text, "action": _text,
        "downtime_hours": _decimal, "cost_inr": _int,
        "flight_hours_at_event": _decimal, "cycles_at_event": _int,
        "outcome": _text,
    })


def load_snags(db: Session) -> int:
    refs = _aircraft_ref_index(db)
    records = {r.record_id: r.id for r in db.scalars(select(TechnicalRecord))}
    rows = [
        {**r,
         "aircraft_ref_id": refs.get(r["aircraft_id"]),
         "technical_record_id": records.get(r.get("maintenance_id"))}
        for r in read_csv("snag_logs.csv")
        if r["aircraft_id"] in refs
    ]
    return upsert_all(db, Snag, rows, ("snag_id",), {
        "snag_id": _text, "technical_record_id": _int, "aircraft_ref_id": _int,
        "date_reported": _date, "reported_by_role": _text, "snag_text": _text,
        "severity": _text, "resolution_text": _text,
    })