"""Telemetry use cases — HTTP ingest and internal predict (specs 40-41).

The router (`app/api/v1/telemetry.py`) is now transport-only: validate, delegate,
shape the response. All orchestration lives here and all persistence lives in
`app/repositories/telemetry_repo.py`.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.errors import NotFoundError
from ..domain.health import engine_health_from_rul
from ..domain.rules import do_by_cycle, risk_level
from ..ml import inference
from ..models.alert import Alert
from ..models.fleet import Aircraft
from ..realtime.bus import bus
from ..realtime.events import alert_raised, cycle_tick, health_updated
from ..repositories import fleet_repo as repo
from ..repositories import telemetry_repo as telemetry


def build_rows(raw_rows: list[Any]) -> list[dict[str, Any]]:
    """Normalise validated telemetry rows into the shape the ML layer consumes.

    spec 45 publishes 14 sensors; the feature contract also needs `s6`, which the
    ingest API does not carry, so it is filled from the FD001 median (docs/01 §3).
    """
    s6_median = get_settings().s6_median_fd001
    rows: list[dict[str, Any]] = []
    for r in raw_rows:
        sensors = dict(r.sensors)
        if sensors.get("s6") is None:
            sensors["s6"] = s6_median
        rows.append(
            {"cycle": r.cycle, "settings": r.settings.model_dump(), "sensors": sensors}
        )
    return rows


def ingest(db: Session, aircraft_code: str, raw_rows: list[Any]) -> dict[str, Any]:
    """One aircraft per batch (spec 40): infer, persist, publish."""
    aircraft = repo.get_aircraft(db, aircraft_code)
    if aircraft is None:
        raise NotFoundError(f"Aircraft {aircraft_code} not found")
    engine_part = telemetry.engine_part(db)
    now = datetime.now(UTC)
    last_cycle = raw_rows[-1].cycle

    rows = build_rows(raw_rows)
    prediction = inference.predict(rows, current_cycle=last_cycle)
    health = engine_health_from_rul(prediction["rul"])
    risk = risk_level(health)

    telemetry.write_engine_prediction(
        db,
        aircraft=aircraft,
        part=engine_part,
        prediction=prediction,
        health=health,
        risk=risk,
        cycle=last_cycle,
        # rule 22 — computed by the domain, written by the repository
        do_by=do_by_cycle(prediction["rul"], last_cycle, risk),
        source="api",
        cmapss_unit_id=aircraft.cmapss_unit_id,
        telemetry_rows=rows,
        recorded_at=now,
    )

    alerts_raised, alert_id = _sync_engine_alert(
        db, aircraft=aircraft, part_id=engine_part.id,
        risk=risk, health=health, rul=prediction["rul"], cycle=last_cycle, now=now,
    )
    db.commit()

    bus.publish_soon(cycle_tick(last_cycle))
    bus.publish_soon(
        health_updated(
            aircraft.code, aircraft.id, "engine", health, risk,
            prediction["rul"], last_cycle,
        )
    )
    if alerts_raised:
        bus.publish_soon(
            alert_raised(
                {
                    "id": alert_id,
                    "aircraft": aircraft.code,
                    "aircraft_id": aircraft.id,
                    "part": "engine",
                    "level": risk,
                    "message": f"Engine health {health:.2f} — RUL {prediction['rul']}.",
                    "health": health,
                    "rul": prediction["rul"],
                    "cycle": last_cycle,
                }
            )
        )

    return {
        "aircraft": aircraft.code,
        "rows_written": len(raw_rows),
        "current_cycle": last_cycle,
        "rul": prediction["rul"],
        "health": health,
        "risk": risk,
        "components": prediction["component_health"],
        "top_sensors": prediction["top_sensors"],
        "model": prediction["model"]["version"],
        "alerts_raised": alerts_raised,
        "latency_ms": prediction["latency_ms"],
    }


def predict(db: Session, payload: Any) -> dict[str, Any]:
    """Score an arbitrary window, optionally persisting the result (spec 41)."""
    rows = [
        {
            "cycle": r.cycle,
            "settings": r.settings.model_dump() if r.settings else {},
            "sensors": r.sensors,
        }
        for r in payload.window
    ]
    result = inference.predict(
        rows,
        regime=payload.regime,
        subset=payload.subset,
        current_cycle=rows[-1].get("cycle"),
    )
    result["aircraft"] = payload.aircraft

    if payload.persist and payload.aircraft:
        aircraft = repo.get_aircraft(db, payload.aircraft)
        if aircraft is not None:
            telemetry.upsert_prediction(
                db,
                aircraft_id=aircraft.id,
                cycle=result["cycle"],
                rul=result["rul"],
                component_health=result["component_health"],
                top_sensors=result["top_sensors"],
                deviation=result["deviation"],
                model_version=result["model"]["version"],
                latency_ms=result["latency_ms"],
                created_at=datetime.now(UTC),
            )
            db.commit()
    return result


def _sync_engine_alert(
    db: Session,
    *,
    aircraft: Aircraft,
    part_id: int,
    risk: str,
    health: float,
    rul: int,
    cycle: int,
    now: datetime,
) -> tuple[int, int | None]:
    """Keep at most one unacknowledged engine alert per aircraft (uq_alert_live).

    Returns `(alerts_raised, new_alert_id)` so the caller can publish an event only
    when something was actually created rather than merely refreshed.
    """
    if risk == "healthy":
        return 0, None

    existing = db.scalar(
        select(Alert).where(
            Alert.aircraft_id == aircraft.id,
            Alert.part_id == part_id,
            Alert.acknowledged.is_(False),
        )
    )
    message = f"Engine health {health:.2f} — RUL {rul}."
    if existing is not None:
        existing.level, existing.message = risk, message
        existing.health, existing.cycle = health, cycle
        return 0, None

    alert = Alert(
        aircraft_id=aircraft.id, part_id=part_id, level=risk, message=message,
        cycle=cycle, health=health, created_at=now,
    )
    db.add(alert)
    db.flush()
    return 1, alert.id