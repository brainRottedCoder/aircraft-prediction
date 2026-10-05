"""Fleet read endpoints — spec items 27-34. Thin handlers: validate, delegate, return."""
from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query

from ...schemas.fleet import (
    ActionsResponse,
    AircraftDetail,
    AircraftList,
    EngineDetail,
    FleetSummary,
    Heatmap,
    PartDetail,
    ScheduleResponse,
)
from ...services import fleet_service as svc
from ..deps import CurrentUser, DbSession

router = APIRouter(tags=["fleet"], prefix="/api/v1")

PartPath = Annotated[str, Path(pattern="^(engine|radar|gear|hyd|fuel)$")]


@router.get("/fleet/summary", response_model=FleetSummary, summary="Mission Control cards and 60-cycle series")
def fleet_summary(db: DbSession, user: CurrentUser, window: int = Query(60, ge=1, le=500)):
    return svc.fleet_summary(db, window)


@router.get("/aircraft", response_model=AircraftList, summary="Fleet list with readiness and RUL")
def list_aircraft(
    db: DbSession,
    user: CurrentUser,
    risk: Annotated[Literal["healthy", "watch", "critical"] | None, Query()] = None,
    ready: Annotated[bool | None, Query()] = None,
):
    return svc.list_aircraft(db, risk, ready)


@router.get("/aircraft/{code_or_id}", response_model=AircraftDetail, summary="Health of all five parts")
def aircraft_detail(db: DbSession, user: CurrentUser, code_or_id: str):
    return svc.aircraft_detail(db, code_or_id)


@router.get("/aircraft/{code_or_id}/engine", response_model=EngineDetail, summary="Engine history, RUL, components, top sensors")
def engine_detail(
    db: DbSession,
    user: CurrentUser,
    code_or_id: str,
    window: Annotated[int, Query(ge=1, le=500)] = 60,
):
    return svc.engine_detail(db, code_or_id, window)


@router.get("/aircraft/{code_or_id}/parts/{part}", response_model=PartDetail, summary="Part detail with spare, agency and back-in-service")
def part_detail(db: DbSession, user: CurrentUser, code_or_id: str, part: PartPath):
    return svc.part_detail(db, code_or_id, part)


@router.get("/fleet/heatmap", response_model=Heatmap, summary="Aircraft x part health matrix")
def heatmap(db: DbSession, user: CurrentUser):
    return svc.fleet_heatmap(db)


@router.get("/fleet/actions", response_model=ActionsResponse, summary="Worst parts with actions and logistics")
def actions(
    db: DbSession,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=50)] = 5,
):
    return svc.fleet_actions(db, limit)


@router.get("/maintenance/schedule", response_model=ScheduleResponse, summary="One row per aircraft")
def schedule(db: DbSession, user: CurrentUser):
    return svc.maintenance_schedule(db)