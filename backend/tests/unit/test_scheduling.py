"""Scheduling arithmetic — free slots, back-in-service breakdown, ETA."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.domain.scheduling import (
    agency_free_slot_days,
    back_in_service_breakdown,
    cycles_to_date,
    eta_date,
    part_agency_assignment,
)


@pytest.mark.parametrize(
    "capacity,expected", [(40, 1), (19, 2), (25, 2), (23, 2), (16, 2), (11, 3)]
)
def test_idle_agency_slot_matches_real_source_data(capacity, expected):
    """Real maintenance_agencies.csv capacities: 40, 19, 25, 23, 16, 11."""
    assert agency_free_slot_days(capacity) == expected


def test_idle_agency_always_has_at_least_one_day():
    assert agency_free_slot_days(1000) >= 1


def test_bookings_consume_the_horizon():
    """The wait shrinks as bookings fill the 30-day horizon."""
    idle = agency_free_slot_days(20)
    assert agency_free_slot_days(20, booked_days=15, open_bookings=1) < idle
    assert agency_free_slot_days(20, booked_days=29, open_bookings=2) == 1


def test_free_slot_reaches_zero_when_the_horizon_is_full():
    assert agency_free_slot_days(20, booked_days=30, open_bookings=2) == 0
    assert agency_free_slot_days(20, booked_days=45, open_bookings=3) == 0


def test_more_booked_days_never_lengthen_the_wait():
    waits = [agency_free_slot_days(20, booked_days=d, open_bookings=2)
             for d in range(0, 31, 3)]
    assert all(a >= b for a, b in zip(waits, waits[1:])), waits


def test_zero_capacity_is_safe():
    assert agency_free_slot_days(0) == 0


def test_breakdown_exposes_the_arithmetic():
    out = back_in_service_breakdown(2, 24, 12, stock=0)
    assert out == {"slot_days": 2, "turnaround_days": 24, "lead_time_days": 12,
                   "lead_time_applied": True, "total": 38}


def test_breakdown_drops_lead_time_when_in_stock():
    out = back_in_service_breakdown(2, 24, 12, stock=1)
    assert out["lead_time_days"] == 0
    assert out["lead_time_applied"] is False
    assert out["total"] == 26


def test_eta_date_is_today_plus_total():
    start = date(2026, 10, 3)
    assert eta_date(2, 24, 12, 0, start=start) == start + timedelta(days=38)


def test_eta_date_respects_stock():
    start = date(2026, 10, 3)
    assert eta_date(2, 24, 12, 5, start=start) == start + timedelta(days=26)


def test_cycles_to_date_scales_by_cycles_per_month():
    start = date(2026, 10, 3)
    assert cycles_to_date(40, 0, 40.0, start=start) == start + timedelta(days=30)


def test_cycles_to_date_never_precedes_today():
    start = date(2026, 10, 3)
    assert cycles_to_date(10, 500, 40.0, start=start) == start


def test_cycles_to_date_without_history_is_none():
    assert cycles_to_date(100, 1, 0.0) is None


def test_part_agency_assignment_by_specialisation():
    mapping = {"engine": "engine", "hyd": "hydraulics", "radar": "avionics"}
    assert part_agency_assignment(mapping, "engine") == "engine"
    assert part_agency_assignment(mapping, "hyd") == "hydraulics"
    assert part_agency_assignment(mapping, "fuel") is None
