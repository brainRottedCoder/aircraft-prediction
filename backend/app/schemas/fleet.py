"""Fleet read schemas — the exact response contracts of docs/06 §2."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["healthy", "watch", "critical"]
PartCode = Literal["engine", "radar", "gear", "hyd", "fuel"]


class LowestRul(BaseModel):
    id: int
    code: str
    name: str
    rul: int


class FleetSummary(BaseModel):
    mission_ready_count: int
    total_aircraft: int
    critical_parts: int
    average_rul: float
    lowest_rul_aircraft: LowestRul | None = None
    avg_availability: float | None = None
    risk_breakdown: dict[str, int] = Field(default_factory=dict)
    series: dict[str, Any] = Field(default_factory=dict)
    generated_at: datetime
    demo_mode: bool


class AircraftListItem(BaseModel):
    id: int
    code: str
    name: str
    tail_number: str | None = None
    model: str | None = None
    home_base: str | None = None
    mission_ready: bool
    rul: int
    current_cycle: int
    worst_part: str | None = None
    risk: RiskLevel
    engine_health: float | None = None
    parts: dict[str, RiskLevel] = Field(default_factory=dict)


class AircraftList(BaseModel):
    items: list[AircraftListItem]
    total: int


class PartState(BaseModel):
    part: PartCode
    label: str
    health: float
    risk: RiskLevel
    rul: int | None = None
    do_by_cycle: int | None = None
    worst_component: str | None = None
    simulated: bool
    method: str
    updated_at: datetime


class AircraftDetail(BaseModel):
    id: int
    code: str
    name: str
    tail_number: str | None = None
    model: str | None = None
    home_base: str | None = None
    current_cycle: int
    rul: int
    mission_ready: bool
    risk: RiskLevel
    worst_part: str | None = None
    availability: float | None = None
    high_stress_sorties_last_12m: int | None = None
    parts: list[PartState]
    parts_note: str


class SensorContribution(BaseModel):
    sensor: str
    label: str
    contribution: float
    z: float
    health_impact: float
    component: str | None = None


class ComponentHealthOut(BaseModel):
    """null before any telemetry has been ingested — not a fabricated 1.0."""

    fan: float | None = None
    hpc: float | None = None
    hpt: float | None = None
    lpt: float | None = None


class SpareRef(BaseModel):
    part_ref_id: str
    item_name: str
    stock: int
    lead_time_days: int
    criticality: str | None = None


class ModelInfo(BaseModel):
    version: str
    dataset: str | None = None
    mae: float | None = None
    s6_imputed: bool = False
    fallback: bool = False


class EngineHistoryPoint(BaseModel):
    cycle: int
    health: float
    rul: int
    fan: float
    hpc: float
    hpt: float
    lpt: float


class EngineDetail(BaseModel):
    aircraft: str
    current_cycle: int
    rul: int
    rul_capped: bool
    health: float
    risk: RiskLevel
    mission_ready: bool
    components: ComponentHealthOut
    weakest_component: str | None = None
    weakest_component_sensor: str | None = None
    engine_spare: SpareRef | None = None
    top_sensors: list[SensorContribution]
    component_sensor_map: dict[str, list[str]]
    history: list[EngineHistoryPoint]
    model: ModelInfo


class RecordOut(BaseModel):
    id: str
    date: datetime
    type: str | None = None
    fault: str | None = None
    action: str | None = None
    agency: str | None = None
    downtime_hours: float | None = None
    cycles_at_event: int | None = None
    outcome: str | None = None


class SpareDetail(BaseModel):
    part_ref_id: str
    item_name: str
    stock: int
    lead_time_days: int
    minimum_stock: int
    criticality: str | None = None
    supplier: str | None = None
    in_stock: bool
    low_stock: bool


class AgencyOut(BaseModel):
    id: int
    agency_ref_id: str
    name: str
    specialisation: str
    free_slot_days: int
    turnaround_days: int
    location: str | None = None


class BisBreakdown(BaseModel):
    slot_days: int
    turnaround_days: int
    lead_time_days: int
    lead_time_applied: bool


class PartDetail(BaseModel):
    aircraft: str
    part: PartCode
    label: str
    health: float
    risk: RiskLevel
    simulated: bool
    method: str
    rul: int | None = None
    do_by_cycle: int | None = None
    do_by_in_cycles: int | None = None
    action: str
    components: ComponentHealthOut | None = None
    weakest_component: str | None = None
    records: list[RecordOut]
    spare: SpareDetail | None = None
    agency: AgencyOut | None = None
    back_in_service_days: int
    back_in_service_breakdown: BisBreakdown


class HeatmapCell(BaseModel):
    part: PartCode
    health: float | None = None
    risk: RiskLevel | None = None
    simulated: bool | None = None


class HeatmapRow(BaseModel):
    code: str
    cells: list[HeatmapCell]


class Heatmap(BaseModel):
    parts: list[str]
    aircraft: list[HeatmapRow]
    legend: dict[str, str]


class ActionItem(BaseModel):
    rank: int
    aircraft: str
    aircraft_id: int
    part: PartCode
    health: float
    risk: RiskLevel
    action: str
    do_by_cycle: int | None = None
    do_by_in_cycles: int | None = None
    spare: SpareDetail | None = None
    agency: AgencyOut | None = None
    back_in_service_days: int | None = None
    simulated: bool


class ActionsResponse(BaseModel):
    items: list[ActionItem]
    limit: int


class ScheduleRow(BaseModel):
    aircraft: str
    aircraft_id: int
    worst_part: PartCode
    worst_health: float
    risk: RiskLevel
    action: str
    do_by_cycle: int | None = None
    do_by_date: str | None = None
    do_by_in_cycles: int | None = None
    spare_status: Literal["in_stock", "low", "out_of_stock", "none"]
    spare_item: str | None = None
    lead_time_days: int | None = None
    agency: str | None = None
    back_in_service_days: int | None = None
    open_work_order: str | None = None
    mission_ready: bool


class ScheduleResponse(BaseModel):
    items: list[ScheduleRow]
    generated_at: datetime