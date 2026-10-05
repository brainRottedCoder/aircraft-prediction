"""Maintenance write endpoints — spec items 35-39."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Query, status

from ...repositories import fleet_repo as repo
from ...schemas.ops import (
    AckRequest,
    AckResponse,
    AgencyList,
    BookingRequest,
    BookingResponse,
    ReserveRequest,
    ReserveResponse,
    SpareList,
    SpareOut,
    SpareUpdate,
    WorkOrderCreate,
    WorkOrderList,
    WorkOrderOut,
    WorkOrderUpdate,
)
from ...services import maintenance_service as svc
from ..deps import CanMutate, CurrentUser, DbSession

router = APIRouter(tags=["maintenance"], prefix="/api/v1")


# ── work orders ────────────────────────────────────────────────────────────────
@router.get("/work-orders", response_model=WorkOrderList)
def list_work_orders(
    db: DbSession,
    user: CurrentUser,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    aircraft: str | None = None,
    part: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    aircraft_id = None
    if aircraft:
        model = repo.get_aircraft(db, aircraft)
        aircraft_id = model.id if model else -1
    return svc.list_work_orders(db, status_filter, aircraft_id, part, limit, offset)


@router.post("/work-orders", response_model=WorkOrderOut, status_code=status.HTTP_201_CREATED)
def create_work_order(db: DbSession, user: CanMutate, payload: WorkOrderCreate):
    return svc.publish_events(svc.create_work_order(db, payload.model_dump(), user))


@router.patch("/work-orders/{work_order_id}", response_model=WorkOrderOut)
def update_work_order(db: DbSession, user: CanMutate, work_order_id: int, payload: WorkOrderUpdate):
    return svc.publish_events(
        svc.update_work_order(db, work_order_id, payload.model_dump(exclude_none=True), user)
    )


# ── spares ─────────────────────────────────────────────────────────────────────
@router.get("/spares", response_model=SpareList)
def list_spares(db: DbSession, user: CurrentUser):
    return svc.list_spares(db)


@router.patch("/spares/{part_ref_id}", response_model=SpareOut)
def update_spare(db: DbSession, user: CanMutate, part_ref_id: str, payload: SpareUpdate):
    return svc.publish_events(svc.update_spare(db, part_ref_id, payload.model_dump(), user))


@router.post("/spares/{part_ref_id}/reserve", response_model=ReserveResponse)
def reserve_spare(db: DbSession, user: CanMutate, part_ref_id: str, payload: ReserveRequest):
    return svc.publish_events(svc.reserve_spare(db, part_ref_id, payload.work_order_id, user))


# ── agencies ───────────────────────────────────────────────────────────────────
@router.get("/agencies", response_model=AgencyList)
def list_agencies(db: DbSession, user: CurrentUser):
    return svc.list_agencies(db)


@router.post("/agencies/{agency_id}/bookings", response_model=BookingResponse,
             status_code=status.HTTP_201_CREATED)
def create_booking(db: DbSession, user: CanMutate, agency_id: int, payload: BookingRequest):
    return svc.publish_events(svc.create_booking(db, agency_id, payload.model_dump(), user))


# ── alerts ─────────────────────────────────────────────────────────────────────
@router.get("/alerts", response_model=dict)
def list_alerts(
    db: DbSession,
    user: CurrentUser,
    acknowledged: bool | None = None,
    level: str | None = None,
    aircraft: str | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    return svc.list_alerts(db, acknowledged, level, aircraft, limit)


@router.post("/alerts/{alert_id}/ack", response_model=AckResponse)
def ack_alert(db: DbSession, user: CanMutate, alert_id: int, payload: AckRequest = Body(default=AckRequest())):
    return svc.publish_events(svc.ack_alert(db, alert_id, payload.note, user))