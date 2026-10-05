"""Telemetry persistence — the single writer for engine-cycle time-series tables.

Both the HTTP ingest path (`app/services/telemetry_service.py`) and the in-process
replay engine (`app/realtime/replay.py`) funnel through here, so the upsert
semantics for `engine_telemetry` / `component_health` / `ml_predictions` /
`health_snapshots` are defined exactly once (docs/02 §3: the repository layer owns
persistence; routers and background loops own none).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from ..models.fleet import Aircraft, AircraftPart, Part
from ..models.telemetry import ComponentHealth, EngineTelemetry, HealthSnapshot, MlPrediction


def engine_part(db: Session) -> Part:
    """The single `engine` part row. Six C-MAPSS components collapse onto it."""
    part = db.scalar(select(Part).where(Part.code == "engine"))
    if part is None:  # pragma: no cover - only reachable on an unseeded database
        raise LookupError("part 'engine' is missing; run `python -m app.seed.run`")
    return part


def upsert_engine_telemetry(
    db: Session,
    *,
    aircraft_id: int,
    cmapss_unit_id: int,
    cycle: int,
    settings: dict[str, Any],
    sensors: dict[str, Any],
    regime: int,
    source: str,
    recorded_at: datetime,
) -> None:
    db.execute(
        pg_insert(EngineTelemetry)
        .values(
            aircraft_id=aircraft_id,
            cycle=cycle,
            cmapss_unit_id=cmapss_unit_id,
            setting_1=settings.get("setting_1"),
            setting_2=settings.get("setting_2"),
            setting_3=settings.get("setting_3"),
            # the regime the prediction was actually made in, not a literal 0:
            # six-regime data would otherwise record every cycle as regime 0
            regime=regime,
            source=source,
            recorded_at=recorded_at,
            **sensors,
        )
        .on_conflict_do_update(
            index_elements=[EngineTelemetry.aircraft_id, EngineTelemetry.cycle],
            set_={"recorded_at": recorded_at},
        )
    )


def upsert_component_health(
    db: Session,
    *,
    aircraft_id: int,
    cycle: int,
    component_health: dict[str, float],
    recorded_at: datetime,
) -> None:
    db.execute(
        pg_insert(ComponentHealth)
        .values(
            aircraft_id=aircraft_id,
            cycle=cycle,
            recorded_at=recorded_at,
            **component_health,
        )
        .on_conflict_do_update(
            index_elements=[ComponentHealth.aircraft_id, ComponentHealth.cycle],
            set_=dict(component_health),
        )
    )


def upsert_prediction(
    db: Session,
    *,
    aircraft_id: int,
    cycle: int,
    rul: int,
    component_health: dict[str, float],
    top_sensors: list[dict[str, Any]],
    deviation: dict[str, float],
    model_version: str,
    latency_ms: float,
    created_at: datetime,
    refresh_latency: bool = False,
) -> None:
    # Every column the prediction derives is refreshed on conflict, not just `rul`.
    #
    # The replay walks each engine to end of life and then wraps to the start, so it
    # revisits cycle numbers that already have rows. A row first written while the API
    # was serving `rul = 125 - cycle` therefore kept `model_version='fallback'`, the
    # linear component health and an empty `deviation` for ever, even after the real
    # booster started answering for that same cycle — `model_version`, the one column
    # you audit to find out whether a number came from the model, was permanently
    # wrong, and the whole table read 'fallback' no matter what was serving traffic.
    set_: dict[str, Any] = {
        "rul": rul,
        "fan": component_health["fan"],
        "hpc": component_health["hpc"],
        "hpt": component_health["hpt"],
        "lpt": component_health["lpt"],
        "top_sensors": top_sensors,
        "deviation": deviation,
        "model_version": model_version,
    }
    if refresh_latency:
        set_["latency_ms"] = latency_ms

    db.execute(
        pg_insert(MlPrediction)
        .values(
            aircraft_id=aircraft_id,
            cycle=cycle,
            rul=rul,
            fan=component_health["fan"],
            hpc=component_health["hpc"],
            hpt=component_health["hpt"],
            lpt=component_health["lpt"],
            top_sensors=top_sensors,
            deviation=deviation,
            model_version=model_version,
            latency_ms=latency_ms,
            created_at=created_at,
        )
        .on_conflict_do_update(
            index_elements=[MlPrediction.aircraft_id, MlPrediction.cycle],
            set_=set_,
        )
    )


def upsert_health_snapshot(
    db: Session,
    *,
    aircraft_id: int,
    part_id: int,
    cycle: int,
    health: float,
    risk: str,
    recorded_at: datetime,
) -> None:
    db.execute(
        pg_insert(HealthSnapshot)
        .values(
            aircraft_id=aircraft_id,
            part_id=part_id,
            cycle=cycle,
            health=health,
            risk_level=risk,
            recorded_at=recorded_at,
        )
        .on_conflict_do_update(
            index_elements=[
                HealthSnapshot.aircraft_id,
                HealthSnapshot.part_id,
                HealthSnapshot.cycle,
            ],
            set_={"health": health, "risk_level": risk},
        )
    )


def write_engine_prediction(
    db: Session,
    *,
    aircraft: Aircraft,
    part: Part,
    prediction: dict[str, Any],
    health: float,
    risk: str,
    cycle: int,
    do_by: int,
    source: str,
    cmapss_unit_id: int,
    telemetry_rows: list[dict[str, Any]],
    recorded_at: datetime,
) -> None:
    """Persist one engine-cycle prediction across every derived table.

    Writes `engine_telemetry` (one row per supplied cycle), `component_health`,
    `ml_predictions`, `health_snapshots`, the `aircraft_parts` denormalised row and
    the `aircraft` counters. Does **not** commit: the caller owns the transaction
    boundary so a tick is either fully visible or not at all.
    """
    regime = int(prediction["model"].get("regime") or 0)
    component_health = prediction["component_health"]

    for row in telemetry_rows:
        upsert_engine_telemetry(
            db,
            aircraft_id=aircraft.id,
            cmapss_unit_id=cmapss_unit_id,
            cycle=row["cycle"],
            settings=row["settings"],
            sensors=row["sensors"],
            regime=regime,
            source=source,
            recorded_at=recorded_at,
        )
    upsert_component_health(
        db,
        aircraft_id=aircraft.id,
        cycle=cycle,
        component_health=component_health,
        recorded_at=recorded_at,
    )
    upsert_prediction(
        db,
        aircraft_id=aircraft.id,
        cycle=cycle,
        rul=prediction["rul"],
        component_health=component_health,
        top_sensors=prediction["top_sensors"],
        deviation=prediction["deviation"],
        model_version=prediction["model"]["version"],
        latency_ms=prediction["latency_ms"],
        created_at=recorded_at,
        refresh_latency=(source == "cmapss"),
    )
    upsert_health_snapshot(
        db,
        aircraft_id=aircraft.id,
        part_id=part.id,
        cycle=cycle,
        health=health,
        risk=risk,
        recorded_at=recorded_at,
    )

    db.execute(
        AircraftPart.__table__.update()
        .where(AircraftPart.aircraft_id == aircraft.id, AircraftPart.part_id == part.id)
        .values(
            health=health,
            risk_level=risk,
            rul=prediction["rul"],
            do_by_cycle=do_by,
            worst_component=prediction["weakest_component"],
            updated_at=recorded_at,
        )
    )
    aircraft.current_cycle = cycle
    aircraft.rul = prediction["rul"]
