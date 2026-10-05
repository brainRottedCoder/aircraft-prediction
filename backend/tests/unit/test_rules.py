"""Unit tests for business rules 19-26 — spec item 52. No DB, no app, no fixtures.

Runs in well under 50 ms because app/domain imports no I/O module.
"""
from __future__ import annotations

import pytest

from app.domain.rules import (
    RuleError,
    back_in_service_days,
    cap_rul,
    do_by_cycle,
    engine_spare,
    evaluate_aircraft,
    mission_ready,
    recommended_action,
    risk_level,
    worst_part,
)


# ── rule 19 ────────────────────────────────────────────────────────────────────
def test_healthy_above_070():
    assert risk_level(0.71) == "healthy"
    assert risk_level(1.0) == "healthy"


def test_boundary_070_is_watch():
    assert risk_level(0.70) == "watch"


def test_boundary_040_is_critical():
    assert risk_level(0.40) == "critical"


def test_watch_band():
    assert risk_level(0.69) == "watch"
    assert risk_level(0.400001) == "watch"


def test_critical_band():
    assert risk_level(0.39) == "critical"
    assert risk_level(0.0) == "critical"


@pytest.mark.parametrize("bad", [1.4, -0.1, -1.0])
def test_risk_rejects_out_of_range(bad):
    with pytest.raises(RuleError):
        risk_level(bad)


# ── rule 20 ────────────────────────────────────────────────────────────────────
PARTS = [("engine", 0.94), ("radar", 0.81), ("gear", 0.72), ("hyd", 0.90), ("fuel", 0.88)]


def test_mission_ready_requires_rul_above_30():
    assert mission_ready(31, PARTS) is True
    assert mission_ready(29, PARTS) is False


def test_mission_ready_boundary_rul_30_is_false():
    assert mission_ready(30, PARTS) is False


def test_mission_ready_requires_every_part_above_040():
    assert mission_ready(118, PARTS) is True
    assert mission_ready(118, [("engine", 0.94), ("gear", 0.40)]) is False


def test_mission_ready_boundary_health_041_passes():
    assert mission_ready(118, [("engine", 0.41)]) is True


def test_mission_ready_empty_parts():
    assert mission_ready(118, []) is False


# ── rule 21 ────────────────────────────────────────────────────────────────────
def test_worst_part_min_health():
    assert worst_part([("engine", 0.94), ("radar", 0.31)]) == "radar"


def test_worst_part_tie_break_by_severity_order():
    assert worst_part([("engine", 0.50), ("radar", 0.50)]) == "engine"


def test_worst_part_tie_gear_before_hyd():
    assert worst_part([("hyd", 0.10), ("gear", 0.10)]) == "gear"


def test_worst_part_empty():
    assert worst_part([]) is None


# ── rule 22 ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "risk,expected",
    [("healthy", "Routine check"), ("watch", "Plan inspection"),
     ("critical", "Replace now")],
)
def test_action_per_band(risk, expected):
    assert recommended_action(risk) == expected


def test_action_rejects_unknown_band():
    with pytest.raises(RuleError):
        recommended_action("unknown")


# ── rule 23 ────────────────────────────────────────────────────────────────────
def test_doby_none_when_healthy():
    assert do_by_cycle(118, 87, "healthy") is None


def test_doby_is_rul_minus_10():
    assert do_by_cycle(118, 87, "watch") == 108
    assert do_by_cycle(118, 87, "critical") == 108


def test_doby_clamped_when_window_closed():
    assert do_by_cycle(8, 38, "critical") == 38
    assert do_by_cycle(24, 38, "critical") == 38


def test_doby_none_without_rul():
    assert do_by_cycle(None, 87, "watch") is None


# ── rule 24 ────────────────────────────────────────────────────────────────────
def test_bis_adds_lead_only_at_zero_stock():
    assert back_in_service_days(3, 13, 39, stock=0) == 55
    assert back_in_service_days(3, 13, 39, stock=4) == 16


def test_bis_real_agency_case():
    assert back_in_service_days(2, 24, 12, stock=0) == 38


def test_bis_zero_slot():
    assert back_in_service_days(0, 13, 39, stock=0) == 52


def test_bis_rejects_non_positive_turnaround():
    with pytest.raises(RuleError):
        back_in_service_days(1, 0, 10, stock=1)


# ── rule 25 ────────────────────────────────────────────────────────────────────
def test_engine_spare_weakest_component():
    weakest, health = engine_spare({"fan": 0.66, "hpc": 0.61, "hpt": 0.70, "lpt": 0.59})
    assert weakest == "lpt"
    assert health == pytest.approx(0.59)


def test_engine_spare_tie_prefers_upstream():
    assert engine_spare({"fan": 0.50, "hpc": 0.50, "hpt": 0.70, "lpt": 0.80})[0] == "fan"


def test_engine_spare_ignores_null_sensors():
    # fan is null, so the tie among hpc/hpt/lpt resolves to the first in COMPONENTS order
    assert engine_spare({"fan": None, "hpc": 0.70, "hpt": 0.70, "lpt": 0.70})[0] == "hpc"


def test_engine_spare_all_null():
    assert engine_spare({"fan": None, "hpc": None, "hpt": None, "lpt": None}) == (None, None)


# ── rule 26 ────────────────────────────────────────────────────────────────────
def test_rul_capped_at_125():
    assert cap_rul(145.6) == 125
    assert cap_rul(302.0) == 125


def test_rul_at_cap():
    assert cap_rul(125.0) == 125


def test_rul_below_cap():
    assert cap_rul(124.4) == 124


def test_rul_floored_at_zero():
    assert cap_rul(-8.0) == 0


def test_rul_rounds_half_up():
    assert cap_rul(117.4) == 117
    assert cap_rul(117.6) == 118


# ── composite ──────────────────────────────────────────────────────────────────
def test_evaluate_aircraft_matches_spec_example():
    parts = [
        {"part": "engine", "health": 0.94},
        {"part": "radar", "health": 0.31},
        {"part": "gear", "health": 0.72},
        {"part": "hyd", "health": 0.90},
        {"part": "fuel", "health": 0.88},
    ]
    out = evaluate_aircraft(parts, engine_rul=118, current_cycle=87)
    assert out["worst_part"] == "radar"
    assert out["risk"] == "critical"
    assert out["mission_ready"] is False     # radar at 0.31 fails rule 20


def test_evaluate_aircraft_healthy_fleet():
    parts = [{"part": p, "health": h} for p, h in PARTS]
    out = evaluate_aircraft(parts, engine_rul=118, current_cycle=87)
    assert out["mission_ready"] is True
    assert out["worst_part"] == "gear"
    assert out["risk"] == "healthy"