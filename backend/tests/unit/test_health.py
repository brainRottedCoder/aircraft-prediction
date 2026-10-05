"""Health derivation — engine from RUL, non-engine from maintenance burden."""
from __future__ import annotations

import pytest

from app.domain.health import (
    EmaState,
    burden_health,
    engine_health_from_rul,
    maintenance_burden,
    recency_weight,
)


@pytest.mark.parametrize(
    "rul,expected",
    [(125, 1.0), (63, 0.504), (0, 0.0), (200, 1.0), (-5, 0.0)],
)
def test_engine_health_from_rul(rul, expected):
    assert engine_health_from_rul(rul) == expected


def test_engine_health_is_one_at_the_cap():
    assert engine_health_from_rul(125) == 1.0


@pytest.mark.parametrize(
    "age,expected",
    [(0, 1.0), (6, 1.0), (12, 0.8), (36, 0.1), (100, 0.1)],
)
def test_recency_weight_decays_to_a_floor(age, expected):
    assert recency_weight(age) == pytest.approx(expected, abs=1e-6)


def test_recency_weight_is_monotonically_decreasing():
    weights = [recency_weight(m) for m in range(0, 40)]
    assert all(a >= b for a, b in zip(weights, weights[1:]))


RECORDS = [
    {"fault_type": "high EGT", "age_months": 2.0, "downtime_hours": 50.0},
    {"fault_type": "hydraulic pressure low", "age_months": 3.0, "downtime_hours": 20.0},
    {"fault_type": "blade erosion", "age_months": 1.0, "downtime_hours": 10.0},
]


def test_burden_only_counts_mapped_faults():
    engine = maintenance_burden(RECORDS, [], "engine", p95=1.0)
    hyd = maintenance_burden(RECORDS, [], "hyd", p95=1.0)
    assert engine > hyd > 0
    assert maintenance_burden(RECORDS, [], "radar", p95=1.0) == 0.0


def test_burden_normalises_against_p95():
    raw = maintenance_burden(RECORDS, [], "engine", p95=1.0)
    assert maintenance_burden(RECORDS, [], "engine", p95=2.0) == pytest.approx(raw / 2)


def test_burden_of_no_history_is_zero():
    assert maintenance_burden([], [], "engine", p95=1.0) == 0.0


def test_burden_handles_zero_p95():
    assert maintenance_burden(RECORDS, [], "engine", p95=0.0) == 0.0


def test_snags_contribute_weighted_by_severity():
    critical = [{"severity": "Critical", "age_months": 1.0}]
    minor = [{"severity": "Minor", "age_months": 1.0}]
    assert (maintenance_burden([], critical, "engine", 1.0)
            > maintenance_burden([], minor, "engine", 1.0))


@pytest.mark.parametrize(
    "score,expected", [(0.0, 1.0), (0.5, 0.5), (1.0, 0.05), (2.0, 0.05)]
)
def test_burden_health_inverse_and_floored(score, expected):
    assert burden_health(score) == pytest.approx(expected)


def test_ema_state_seeds_on_first_value():
    assert EmaState.empty().update("engine", 0.8) == 0.8


def test_ema_state_converges_upward_but_lags():
    state = EmaState.empty()
    first = state.update("engine", 0.5)
    for _ in range(3):
        last = state.update("engine", 0.8)
    assert last > first
    assert last < 0.8


def test_ema_state_update_and_reset():
    state = EmaState.empty()
    assert state.update("engine", 0.9) == 0.9
    second = state.update("engine", 0.3)
    assert 0.3 < second < 0.9
    state.reset()
    assert state.update("engine", 0.55) == 0.55
