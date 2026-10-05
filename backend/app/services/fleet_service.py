"""Fleet use cases — spec items 27-34. One function per endpoint intent."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from ..core.errors import NotFoundError
from ..domain import aggregation
from ..domain.rules import PART_ORDER, engine_spare, recommended_action, worst_part
from ..domain.scheduling import back_in_service_breakdown, cycles_to_date
from ..ml import model_store
from ..repositories import fleet_repo as repo

PARTS_NOTE = (
    "radar, gear, hyd and fuel have no sensor source and are derived from "
    "maintenance history (simulated: true)."
)


def _resolve(db: Session, code_or_id: str | int):
    aircraft = repo.get_aircraft(db, code_or_id)
    if aircraft is None:
        raise NotFoundError(f"Aircraft {code_or_id!r} not found")
    return aircraft


def _part_rows(db: Session, aircraft_id: int) -> list[dict]:
    return _part_rows_from(repo.parts_for_aircraft(db, aircraft_id))


def _part_rows_from(rows) -> list[dict]:
    return [
        {
            "part": p.part.code,
            "label": p.part.label,
            "health": float(p.health),
            "risk": p.risk_level,
            "rul": p.rul,
            "do_by_cycle": p.do_by_cycle,
            "worst_component": p.worst_component,
            "simulated": p.is_simulated,
            "method": p.part.method,
            "updated_at": p.updated_at,
        }
        for p in rows
    ]


# ── 27 ─────────────────────────────────────────────────────────────────────────
def fleet_summary(db: Session, window: int = 60) -> dict:
    from ..realtime.bus import bus

    aircraft_rows = repo.list_aircraft(db)
    snapshots = repo.fleet_snapshots(db, window)

    parts_flat: list[dict] = []
    for aircraft, ap, part in repo.all_parts_with_aircraft(db):
        parts_flat.append({
            "part": part.code, "health": float(ap.health), "risk": ap.risk_level,
            "is_simulated": ap.is_simulated, "aircraft": aircraft.code,
            "aircraft_id": aircraft.id, "action": recommended_action(ap.risk_level),
            "do_by_cycle": ap.do_by_cycle,
            "back_in_service_days": ap.back_in_service_days,
        })

    aircraft_dicts = [
        {"id": a.id, "code": a.code, "name": a.name, "rul": a.rul,
         "mission_ready": a.mission_ready}
        for a in aircraft_rows
    ]
    summary = aggregation.rollup(aircraft_dicts, parts_flat)

    lowest_id = (
        summary["lowest_rul_aircraft"]["id"] if summary["lowest_rul_aircraft"] else None
    )
    summary["series"] = aggregation.fleet_series(snapshots, window, lowest_id)

    availabilities = [
        repo.availability(db, a.aircraft_ref_id) for a in aircraft_rows
    ]
    availabilities = [v for v in availabilities if v is not None]
    summary["avg_availability"] = (
        round(sum(availabilities) / len(availabilities), 4) if availabilities else None
    )
    summary["generated_at"] = datetime.now(UTC)
    summary["demo_mode"] = True
    summary["subscribers"] = bus.subscriber_count
    return summary


# ── 28 ─────────────────────────────────────────────────────────────────────────
def list_aircraft(db: Session, risk: str | None = None, ready: bool | None = None) -> dict:
    items = []
    for a in repo.list_aircraft(db):
        rows = repo.parts_for_aircraft(db, a.id)
        by_part = {r.part.code: r for r in rows}
        engine = by_part.get("engine")
        item = {
            "id": a.id, "code": a.code, "name": a.name,
            "tail_number": a.tail_number, "model": a.aircraft_model,
            "home_base": a.home_base, "mission_ready": a.mission_ready,
            "rul": a.rul, "current_cycle": a.current_cycle,
            "worst_part": a.worst_part, "risk": a.risk_level,
            "engine_health": float(engine.health) if engine else None,
            "parts": {r.part.code: r.risk_level for r in rows},
        }
        if risk and item["risk"] != risk:
            continue
        if ready is not None and item["mission_ready"] != ready:
            continue
        items.append(item)
    return {"items": items, "total": len(items)}


# ── 29 ─────────────────────────────────────────────────────────────────────────
def aircraft_detail(db: Session, code_or_id: str | int) -> dict:
    a = _resolve(db, code_or_id)
    parts = _part_rows(db, a.id)
    return {
        "id": a.id, "code": a.code, "name": a.name,
        "tail_number": a.tail_number, "model": a.aircraft_model,
        "home_base": a.home_base, "current_cycle": a.current_cycle,
        "rul": a.rul, "mission_ready": a.mission_ready,
        "risk": a.risk_level, "worst_part": a.worst_part,
        "availability": repo.availability(db, a.aircraft_ref_id),
        "high_stress_sorties_last_12m": repo.high_stress_sorties(db, a.aircraft_ref_id),
        "parts": parts, "parts_note": PARTS_NOTE,
    }


# ── 30 ─────────────────────────────────────────────────────────────────────────
def engine_detail(db: Session, code_or_id: str | int, window: int = 60) -> dict:
    from ..domain.scheduling import back_in_service_breakdown  # noqa: F401

    a = _resolve(db, code_or_id)
    rows = repo.parts_for_aircraft(db, a.id)
    engine_part = next((r for r in rows if r.part.code == "engine"), None)
    if engine_part is None:
        raise NotFoundError(f"{a.code} has no engine part")

    history_rows = repo.engine_history(db, a.id, window)
    history = [
        {
            "cycle": s.cycle, "health": float(s.health),
            "rul": (p.rul if p else a.rul),
            "fan": float(c.fan) if c else None,
            "hpc": float(c.hpc) if c else None,
            "hpt": float(c.hpt) if c else None,
            "lpt": float(c.lpt) if c else None,
        }
        for s, p, c in history_rows
    ]
    latest = history[-1] if history else None
    components = {
        "fan": latest["fan"], "hpc": latest["hpc"],
        "hpt": latest["hpt"], "lpt": latest["lpt"],
    } if latest else {"fan": None, "hpc": None, "hpt": None, "lpt": None}
    weakest, _ = engine_spare(components)

    prediction = None
    for _snapshot, p, _components in reversed(history_rows):
        if p is not None:
            prediction = p
            break

    top_sensors = (prediction.top_sensors if prediction else []) or []
    spare = Logistics(db).by_component.get(
        _component_display(weakest) or ""
    ) if weakest else None

    from ..domain.rules import COMPONENT_SENSOR_MAP

    return {
        "aircraft": a.code, "current_cycle": a.current_cycle, "rul": a.rul,
        "rul_capped": a.rul >= 125,
        "health": float(engine_part.health), "risk": engine_part.risk_level,
        "mission_ready": a.mission_ready,
        "components": components,
        "weakest_component": weakest,
        "weakest_component_sensor": (COMPONENT_SENSOR_MAP.get(weakest) or [None])[0],
        "engine_spare": (
            {"part_ref_id": spare.part_ref_id, "item_name": spare.item_name,
             "stock": spare.stock, "lead_time_days": spare.lead_time_days,
             "criticality": spare.criticality}
            if spare else None
        ),
        "top_sensors": top_sensors,
        "component_sensor_map": COMPONENT_SENSOR_MAP,
        "history": history,
        "model": model_store.handle().describe(),
    }


COMPONENT_DISPLAY = {
    "fan": "Fan",
    "hpc": "High Pressure Compressor",
    "hpt": "High Pressure Turbine",
    "lpt": "Low Pressure Turbine",
}


def _component_display(component: str | None) -> str | None:
    return COMPONENT_DISPLAY.get(component or "")


class Logistics:
    """Pre-loaded spare and agency lookups — one query each, reused per request.

    `aircraft_part.spare_id` is only populated at seed time for the non-engine
    parts; the engine's spare depends on the weakest component (rule 25), which
    changes every cycle. Resolving through one object keeps /parts/{part},
    /fleet/actions and /maintenance/schedule from disagreeing, and keeps the
    query count flat instead of growing with the fleet.
    """

    def __init__(self, db: Session) -> None:
        self.by_component = repo.spares_by_component(db)
        self.agencies = repo.agencies_by_id(db)
        self.parts = {p.id: p for p in repo.list_parts(db)}

    def for_part(self, row, part_code: str):
        spare = row.spare or self.by_component.get(
            _component_display(row.worst_component) or ""
        )
        part = self.parts.get(row.part_id)
        agency = row.agency or (
            self.agencies.get(part.agency_id) if part and part.agency_id else None
        )
        return spare, agency


# ── 31 ─────────────────────────────────────────────────────────────────────────
def part_detail(db: Session, code_or_id: str | int, part_code: str) -> dict:
    a = _resolve(db, code_or_id)
    if part_code not in PART_ORDER:
        raise NotFoundError(f"Unknown part {part_code!r}")

    part = repo.get_part(db, part_code)
    row = next((r for r in repo.parts_for_aircraft(db, a.id)
                if r.part.code == part_code), None)
    if part is None or row is None:
        raise NotFoundError(f"{a.code} has no {part_code} part")

    records = [
        {"id": r.record_id, "date": r.event_date, "type": r.maintenance_type,
         "fault": r.fault_type, "action": r.action, "agency": agency,
         "downtime_hours": float(r.downtime_hours) if r.downtime_hours else None,
         "cycles_at_event": r.cycles_at_event, "outcome": r.outcome}
        for r, agency in repo.recent_records(db, a.aircraft_ref_id, part_code, 2)
    ]

    components = None
    if part_code == "engine":
        detail = engine_detail(db, a.id)
        components = detail["components"]

    logistics = Logistics(db)
    spare, agency = logistics.for_part(row, part_code)

    slot = agency.free_slot_days if agency else 0
    turnaround = agency.turnaround_days if agency else 0
    lead = spare.lead_time_days if spare else 0
    stock = spare.stock if spare else 0
    breakdown = back_in_service_breakdown(slot, turnaround, lead, stock)

    do_by = row.do_by_cycle
    return {
        "aircraft": a.code, "part": part_code, "label": part.label,
        "health": float(row.health), "risk": row.risk_level,
        "simulated": row.is_simulated, "method": part.method,
        "rul": row.rul, "do_by_cycle": do_by,
        "do_by_in_cycles": (do_by - a.current_cycle) if do_by else None,
        "action": recommended_action(row.risk_level),
        "components": components, "weakest_component": row.worst_component,
        "records": records,
        "spare": (
            {"part_ref_id": spare.part_ref_id, "item_name": spare.item_name,
             "stock": spare.stock, "lead_time_days": spare.lead_time_days,
             "minimum_stock": spare.minimum_stock, "criticality": spare.criticality,
             "supplier": spare.supplier, "in_stock": spare.stock > 0,
             "low_stock": spare.stock <= spare.minimum_stock}
            if spare else None
        ),
        "agency": (
            {"id": agency.id, "agency_ref_id": agency.agency_ref_id, "name": agency.name,
             "specialisation": agency.specialisation,
             "free_slot_days": agency.free_slot_days,
             "turnaround_days": agency.turnaround_days, "location": agency.location}
            if agency else None
        ),
        "back_in_service_days": breakdown["total"],
        "back_in_service_breakdown": {
            "slot_days": breakdown["slot_days"],
            "turnaround_days": breakdown["turnaround_days"],
            "lead_time_days": breakdown["lead_time_days"],
            "lead_time_applied": breakdown["lead_time_applied"],
        },
    }


# ── 32 ─────────────────────────────────────────────────────────────────────────
def fleet_heatmap(db: Session) -> dict:
    parts_by_aircraft = repo.all_parts_by_aircraft(db)      # one query, not eight
    grouped: dict[str, list[dict]] = {}
    for a in repo.list_aircraft(db):
        grouped[a.code] = aggregation.heatmap_matrix(
            _part_rows_from(parts_by_aircraft.get(a.id, []))
        )["cells"]
    return {
        "parts": list(PART_ORDER),
        "aircraft": [{"code": code, "cells": cells} for code, cells in grouped.items()],
        "legend": {"healthy": "> 0.70", "watch": "0.40 - 0.70", "critical": "<= 0.40"},
    }


# ── 33 ─────────────────────────────────────────────────────────────────────────
def fleet_actions(db: Session, limit: int = 5) -> dict:
    logistics = Logistics(db)
    rows = []
    for aircraft, ap, part in repo.all_parts_with_aircraft(db):
        spare, agency = logistics.for_part(ap, part.code)
        rows.append({
            "aircraft": aircraft.code, "aircraft_id": aircraft.id, "part": part.code,
            "health": float(ap.health), "risk": ap.risk_level,
            "action": recommended_action(ap.risk_level),
            "do_by_cycle": ap.do_by_cycle,
            "do_by_in_cycles": (
                ap.do_by_cycle - aircraft.current_cycle if ap.do_by_cycle else None
            ),
            "spare": (
                {"part_ref_id": spare.part_ref_id, "item_name": spare.item_name,
                 "stock": spare.stock, "lead_time_days": spare.lead_time_days,
                 "minimum_stock": spare.minimum_stock, "criticality": spare.criticality,
                 "supplier": spare.supplier, "in_stock": spare.stock > 0,
                 "low_stock": spare.stock <= spare.minimum_stock}
                if spare else None
            ),
            "agency": (
                {"id": agency.id, "agency_ref_id": agency.agency_ref_id,
                 "name": agency.name, "specialisation": agency.specialisation,
                 "free_slot_days": agency.free_slot_days,
                 "turnaround_days": agency.turnaround_days, "location": agency.location}
                if agency else None
            ),
            "back_in_service_days": back_in_service_breakdown(
                agency.free_slot_days if agency else 1,
                agency.turnaround_days if agency else 1,
                spare.lead_time_days if spare else 0,
                spare.stock if spare else 0,
            )["total"],
            "simulated": ap.is_simulated,
        })
    ranked = aggregation.rank_worst_parts(rows, limit)
    return {"items": ranked, "limit": limit}


# ── 34 ─────────────────────────────────────────────────────────────────────────
def maintenance_schedule(db: Session) -> dict:
    parts_by_aircraft = repo.all_parts_by_aircraft(db)
    open_orders = repo.open_work_orders(db)
    logistics = Logistics(db)
    items = []
    for a in repo.list_aircraft(db):
        rows = parts_by_aircraft.get(a.id, [])
        pairs = [(r.part.code, float(r.health)) for r in rows]
        worst_code = worst_part(pairs)
        row = next((r for r in rows if r.part.code == worst_code), None)
        if row is None:
            continue
        spare, agency = logistics.for_part(row, row.part.code)
        stock = spare.stock if spare else None
        if stock is None:
            status = "none"
        elif stock <= 0:
            status = "out_of_stock"
        elif spare and stock <= spare.minimum_stock:
            status = "low"
        else:
            status = "in_stock"

        work_order = open_orders.get((a.id, row.part_id))
        cycles_per_month = 40.0
        bis = back_in_service_breakdown(
            agency.free_slot_days if agency else 1,
            agency.turnaround_days if agency else 1,
            spare.lead_time_days if spare else 0,
            stock if stock is not None else 0,
        )["total"]
        items.append({
            "aircraft": a.code, "aircraft_id": a.id, "worst_part": worst_code,
            "worst_health": float(row.health), "risk": row.risk_level,
            "action": recommended_action(row.risk_level),
            "do_by_cycle": row.do_by_cycle,
            "do_by_date": (
                cycles_to_date(row.do_by_cycle, a.current_cycle, cycles_per_month).isoformat()
                if row.do_by_cycle else None
            ),
            "do_by_in_cycles": (
                row.do_by_cycle - a.current_cycle if row.do_by_cycle else None
            ),
            "spare_status": status,
            "spare_item": spare.item_name if spare else None,
            "lead_time_days": spare.lead_time_days if spare else None,
            "agency": agency.name if agency else None,
            "back_in_service_days": bis,
            "open_work_order": work_order.reference if work_order else None,
            "mission_ready": a.mission_ready,
        })
    return {"items": items, "generated_at": datetime.now(UTC)}