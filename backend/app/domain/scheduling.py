"""Scheduling arithmetic — back-in-service, do-by dates, agency slots. Pure."""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from .rules import back_in_service_days

DEFAULT_BOOKING_HORIZON_DAYS = 30


def agency_free_slot_days(
    monthly_capacity_slots: int, booked_days: int = 0, open_bookings: int = 0
) -> int:
    """Wait until the next slot frees up. No such column exists in the source data.

    Idle agency -> ceil(30 / capacity_slots_per_month), matching the seeded
    values in docs/01 §6. As bookings fill the 30-day horizon the wait shrinks
    monotonically, and reaches 0 (=> 409 NO_SLOT_AVAILABLE) once full.
    """
    if monthly_capacity_slots <= 0:
        return 0
    horizon = DEFAULT_BOOKING_HORIZON_DAYS
    occupied = min(1.0, max(0, booked_days) / horizon)
    free = max(1, math.ceil((1.0 - occupied) * horizon / monthly_capacity_slots))
    return free if occupied < 1.0 else 0


def back_in_service_breakdown(
    slot_days: int, turnaround_days: int, lead_time_days: int, stock: int
) -> dict[str, Any]:
    """Rule 24 with its arithmetic exposed for the UI."""
    lead_applied = lead_time_days if stock == 0 else 0
    return {
        "slot_days": slot_days,
        "turnaround_days": turnaround_days,
        "lead_time_days": lead_applied,
        "lead_time_applied": lead_applied > 0,
        "total": back_in_service_days(slot_days, turnaround_days, lead_time_days, stock),
    }


def eta_date(
    slot_days: int, turnaround_days: int, lead_time_days: int, stock: int,
    start: date | None = None,
) -> date:
    breakdown = back_in_service_breakdown(slot_days, turnaround_days, lead_time_days, stock)
    return (start or date.today()) + timedelta(days=breakdown["total"])


def cycles_to_date(
    target_cycle: int, current_cycle: int, cycles_per_month: float, start: date | None = None
) -> date | None:
    """Convert a do-by cycle into a calendar date via flight_operations averages."""
    if cycles_per_month <= 0:
        return None
    months = max(0, (target_cycle - current_cycle)) / cycles_per_month
    return (start or date.today()) + timedelta(days=int(round(months * 30)))


def part_agency_assignment(specialisation_map: dict[str, str], code: str) -> str | None:
    """Resolve a part to its agency specialisation key."""
    return specialisation_map.get(code)


