"""Write-path schemas — auth, work orders, spares, agencies, alerts, telemetry, ML."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


# ── auth ───────────────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    full_name: str
    role: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class MeResponse(UserOut):
    permissions: dict[str, bool]


# ── work orders ────────────────────────────────────────────────────────────────
PartCode = Literal["engine", "radar", "gear", "hyd", "fuel"]


class WorkOrderCreate(BaseModel):
    aircraft: str
    part: PartCode
    due_date: date
    priority: Literal["low", "medium", "high"] = "medium"
    notes: str | None = None


class WorkOrderUpdate(BaseModel):
    status: Literal["open", "in_progress", "done"] | None = None
    priority: Literal["low", "medium", "high"] | None = None
    due_date: date | None = None
    notes: str | None = None
    action: str | None = None


class WorkOrderOut(BaseModel):
    id: int
    reference: str
    aircraft: str
    part: str
    action: str
    due_date: date
    due_cycle: int | None = None
    status: str
    priority: str
    notes: str | None = None
    created_by: UserOut | None = None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class WorkOrderList(BaseModel):
    items: list[WorkOrderOut]
    total: int


# ── spares ─────────────────────────────────────────────────────────────────────
class SpareOut(BaseModel):
    part_ref_id: str
    item_name: str
    component_name: str | None = None
    aircraft_model: str | None = None
    stock: int
    minimum_stock: int
    reorder_quantity: int
    supplier: str | None = None
    lead_time_days: int
    unit_cost_inr: int | None = None
    storage_location: str | None = None
    criticality: str | None = None
    in_stock: bool
    low_stock: bool
    last_movement: dict[str, Any] | None = None


class SpareList(BaseModel):
    items: list[SpareOut]
    total: int


class SpareUpdate(BaseModel):
    stock: int = Field(ge=0)
    reason: Literal["restock", "adjust", "return"] = "restock"
    note: str | None = None


class ReserveRequest(BaseModel):
    work_order_id: int | None = None


class ReserveResponse(BaseModel):
    part_ref_id: str
    stock_before: int
    stock_remaining: int
    reserved_at: datetime
    work_order_id: int | None = None
    work_order_status: str | None = None


# ── agencies ───────────────────────────────────────────────────────────────────
class AgencyListItem(BaseModel):
    id: int
    agency_ref_id: str
    name: str
    type: str | None = None
    location: str | None = None
    specialisation: str
    turnaround_days: int
    monthly_capacity_slots: int
    cost_multiplier: float
    free_slot_days: int
    open_bookings: int
    handles_parts: list[str]


class AgencyList(BaseModel):
    items: list[AgencyListItem]


class BookingRequest(BaseModel):
    aircraft: str
    part: PartCode
    work_order_id: int | None = None


class BookingResponse(BaseModel):
    id: int
    agency: str
    aircraft: str
    part: str
    booked_on: date
    slot_days: int
    turnaround_days: int
    lead_time_days: int
    eta_date: date
    back_in_service_days: int
    breakdown: dict[str, int]


# ── alerts ─────────────────────────────────────────────────────────────────────
class AlertOut(BaseModel):
    id: int
    aircraft: str
    aircraft_id: int
    part: str
    level: str
    message: str
    health: float | None = None
    rul: int | None = None
    cycle: int | None = None
    acknowledged: bool
    acknowledged_by: UserOut | None = None
    acknowledged_at: datetime | None = None
    created_at: datetime


class AckRequest(BaseModel):
    note: str | None = None


class AckResponse(BaseModel):
    id: int
    acknowledged: bool
    acknowledged_by: UserOut
    acknowledged_at: datetime
    note: str | None = None


# ── telemetry ──────────────────────────────────────────────────────────────────
class SettingsIn(BaseModel):
    setting_1: float
    setting_2: float
    setting_3: float


class TelemetryRow(BaseModel):
    cycle: int = Field(ge=1)
    settings: SettingsIn
    sensors: dict[str, float]   # the 14 spec sensors; s6 optional


class TelemetryBatch(BaseModel):
    aircraft: str
    rows: list[TelemetryRow] = Field(min_length=1, max_length=60)


class TelemetryAccepted(BaseModel):
    aircraft: str
    rows_written: int
    current_cycle: int
    rul: int
    health: float
    risk: str
    components: dict[str, float]
    top_sensors: list[dict[str, Any]]
    model: str
    alerts_raised: int
    latency_ms: float


# ── ML ─────────────────────────────────────────────────────────────────────────
class PredictWindowRow(BaseModel):
    cycle: int | None = None
    settings: SettingsIn | None = None
    sensors: dict[str, float]


class PredictRequest(BaseModel):
    aircraft: str | None = None
    # Both optional: omit them and the regime is classified from the window's own
    # operating settings against the artifact's centroids, and the subset defaults to
    # the replayed one. Sending 0 explicitly pins the regime, which is what you want
    # when replaying a stored history rather than classifying it.
    regime: int | None = None
    subset: str | None = None
    window: list[PredictWindowRow] = Field(min_length=5, max_length=30)
    persist: bool = True


class PredictResponse(BaseModel):
    aircraft: str | None = None
    cycle: int
    rul: int
    rul_raw: float
    rul_capped: bool
    component_health: dict[str, float]
    weakest_component: str | None = None
    deviation: dict[str, float]
    top_sensors: list[dict[str, Any]]
    component_sensor_map: dict[str, list[str]]
    model: dict[str, Any]
    latency_ms: float


# ── operational ────────────────────────────────────────────────────────────────
class Healthz(BaseModel):
    status: str
    db: bool
    model: dict[str, Any]
    replay: dict[str, Any]
    # Pruner state: enabled/running/passes plus the horizon in force. Defaulted so a
    # caller that predates retention still validates.
    retention: dict[str, Any] = Field(default_factory=dict)


class Readyz(BaseModel):
    status: str
    aircraft_seeded: int
    db: bool
    model_loaded: bool
    model_degraded: bool = False
    model_error: str | None = None


class DemoStatus(BaseModel):
    running: bool
    paused: bool
    tick: int
    interval_seconds: float
    cmapss_loaded: bool
    units: int
    subscribers: int
    dropped_events: int


class SeedResult(BaseModel):
    status: str
    counts: dict[str, int] = Field(default_factory=dict)