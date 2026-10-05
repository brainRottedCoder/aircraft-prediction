"""Business rules 19-26. Pure functions — stdlib + pydantic only, no I/O."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

RiskLevel = Literal["healthy", "watch", "critical"]
PartCode = Literal["engine", "radar", "gear", "hyd", "fuel"]

RUL_CAP = 125
MISSION_READY_MIN_RUL = 30
HEALTHY_ABOVE = 0.7
WATCH_ABOVE = 0.4
DOBY_MARGIN = 10

PART_ORDER: tuple[str, ...] = ("engine", "radar", "gear", "hyd", "fuel")
COMPONENTS: tuple[str, ...] = ("fan", "hpc", "hpt", "lpt")
ACTIONS: dict[str, str] = {
    "healthy": "Routine check",
    "watch": "Plan inspection",
    "critical": "Replace now",
}
COMPONENT_SENSOR_MAP: dict[str, list[str]] = {
    "fan": ["s8", "s13"],
    # s9 (HPC speed) added: it carries 7.8% of the model's gain and has the largest
    # end-of-life deviation of any sensor (mean |z| 8.0) — it was unassigned, so the
    # HPC readout ignored its strongest signal.
    "hpc": ["s3", "s7", "s9", "s11"],
    "hpt": ["s20", "s21"],
    "lpt": ["s4"],
}
# Sensors that drive RUL but belong to no component. Derived, not hand-maintained, so
# adding a sensor to a component above cannot silently leave the list stale.
UNASSIGNED_SENSORS: tuple[str, ...] = (
    "s2", "s6", "s12", "s14", "s15", "s17",
)

ENGINE_PARTS = {"engine"}


class RuleError(ValueError):
    """Raised when a rule input is outside its legal domain."""


def _check_health(health: float) -> float:
    if not 0.0 <= health <= 1.0:
        raise RuleError(f"health must be within [0, 1], got {health}")
    return float(health)


# ── 19 ──────────────────────────────────────────────────────────────────────────
def risk_level(health: float) -> RiskLevel:
    """> 0.7 healthy, > 0.4 watch, else critical. Strict >, so 0.7 is watch."""
    h = _check_health(health)
    if h > HEALTHY_ABOVE:
        return "healthy"
    if h > WATCH_ABOVE:
        return "watch"
    return "critical"


# ── 20 ──────────────────────────────────────────────────────────────────────────
def mission_ready(rul: int, parts: list[tuple[str, float]]) -> bool:
    """RUL must be strictly > 30 and every part health strictly > 0.4."""
    if not parts:
        return False
    if rul <= MISSION_READY_MIN_RUL:
        return False
    return all(_check_health(h) > WATCH_ABOVE for _, h in parts)


# ── 21 ──────────────────────────────────────────────────────────────────────────
def worst_part(parts: list[tuple[str, float]]) -> str | None:
    """Lowest health; ties broken by PART_ORDER (engine first)."""
    if not parts:
        return None
    return min(
        parts,
        key=lambda p: (_check_health(p[1]), PART_ORDER.index(p[0])),
    )[0]


# ── 22 ──────────────────────────────────────────────────────────────────────────
def recommended_action(risk: str) -> str:
    if risk not in ACTIONS:
        raise RuleError(f"unknown risk level {risk!r}")
    return ACTIONS[risk]


# ── 23 ──────────────────────────────────────────────────────────────────────────
def do_by_cycle(rul: int | None, current_cycle: int, risk: str) -> int | None:
    """rul - 10 for non-healthy parts, clamped to current_cycle. None if healthy."""
    if risk == "healthy" or rul is None:
        return None
    target = rul - DOBY_MARGIN
    return target if target > current_cycle else current_cycle


# ── 24 ──────────────────────────────────────────────────────────────────────────
def back_in_service_days(
    slot_days: int, turnaround_days: int, lead_time_days: int, stock: int
) -> int:
    """slot + turnaround + lead_time, lead_time only when stock == 0."""
    if turnaround_days <= 0:
        raise RuleError("turnaround_days must be positive")
    if slot_days < 0 or lead_time_days < 0:
        raise RuleError("slot_days and lead_time_days must be non-negative")
    lead = lead_time_days if stock == 0 else 0
    return int(slot_days + turnaround_days + lead)


# ── 25 ──────────────────────────────────────────────────────────────────────────
def engine_spare(
    components: Mapping[str, float | None],
) -> tuple[str | None, float | None]:
    """Weakest of fan/hpc/hpt/lpt; ties prefer upstream. Null values ignored."""
    present = {
        k: float(v)
        for k, v in components.items()
        if k in COMPONENTS and v is not None
    }
    if not present:
        return None, None
    weakest = min(present.items(), key=lambda kv: (kv[1], COMPONENTS.index(kv[0])))[0]
    return weakest, present[weakest]


# ── 26 ──────────────────────────────────────────────────────────────────────────
def cap_rul(raw_rul: float) -> int:
    """Round then clamp to [0, 125]."""
    return int(max(0, min(RUL_CAP, round(raw_rul))))


def evaluate_aircraft(
    parts: list[dict[str, Any]],
    engine_rul: int,
    current_cycle: int,
) -> dict[str, Any]:
    """Aircraft-level rollup: rules 20, 21 and the aircraft risk band."""
    pairs = [(p["part"], p["health"]) for p in parts]
    worst = worst_part(pairs)
    worst_health = min((h for _, h in pairs), default=0.0)
    return {
        "mission_ready": mission_ready(engine_rul, pairs),
        "worst_part": worst,
        "risk": risk_level(worst_health) if pairs else "critical",
        "rul": cap_rul(engine_rul),
        "current_cycle": current_cycle,
    }