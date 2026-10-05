"""Series, matrices and rollups — pure aggregation helpers."""
from __future__ import annotations

import pytest

from app.domain.aggregation import (
    fleet_series,
    heatmap_matrix,
    rank_worst_parts,
    rollup,
)

# shape produced by fleet_service._part_rows
PARTS = [
    {"part": "engine", "health": 0.94, "risk": "healthy", "is_simulated": False},
    {"part": "radar", "health": 0.31, "risk": "critical", "is_simulated": True},
    {"part": "gear", "health": 0.72, "risk": "healthy", "is_simulated": True},
    {"part": "hyd", "health": 0.90, "risk": "healthy", "is_simulated": True},
    {"part": "fuel", "health": 0.88, "risk": "healthy", "is_simulated": True},
]


def test_heatmap_always_has_five_cells_in_order():
    matrix = heatmap_matrix(PARTS)
    assert [c["part"] for c in matrix["cells"]] == ["engine", "radar", "gear", "hyd", "fuel"]
    assert matrix["parts"] == ["engine", "radar", "gear", "hyd", "fuel"]


def test_heatmap_fills_missing_parts_with_null():
    matrix = heatmap_matrix([{"part": "engine", "health": 0.9, "risk": "healthy",
                              "is_simulated": False}])
    assert len(matrix["cells"]) == 5
    assert matrix["cells"][1]["health"] is None


def test_rank_worst_parts_is_ascending_and_limited():
    rows = [
        {"part": "engine", "health": 0.9}, {"part": "radar", "health": 0.2},
        {"part": "gear", "health": 0.5}, {"part": "hyd", "health": 0.1},
        {"part": "fuel", "health": 0.7},
    ]  # rank_worst_parts only needs health + part
    ranked = rank_worst_parts(rows, limit=3)
    assert [r["part"] for r in ranked] == ["hyd", "radar", "gear"]
    assert [r["rank"] for r in ranked] == [1, 2, 3]


def test_rank_worst_parts_tie_breaks_by_severity_order():
    rows = [{"part": "fuel", "health": 0.5}, {"part": "engine", "health": 0.5}]
    assert rank_worst_parts(rows, 2)[0]["part"] == "engine"


AIRCRAFT = [
    {"id": 1, "code": "Fighter-01", "name": "Fighter-01", "rul": 118, "mission_ready": True},
    {"id": 2, "code": "Fighter-02", "name": "Fighter-02", "rul": 24, "mission_ready": False},
]


def test_rollup_counts():
    parts = [
        {"part": "engine", "health": 0.9, "risk": "healthy"},
        {"part": "radar", "health": 0.5, "risk": "watch"},
        {"part": "gear", "health": 0.2, "risk": "critical"},
    ]
    out = rollup(AIRCRAFT, parts)
    assert out["total_aircraft"] == 2
    assert out["mission_ready_count"] == 1
    assert out["critical_parts"] == 1
    assert out["average_rul"] == 71.0
    assert out["lowest_rul_aircraft"]["code"] == "Fighter-02"
    assert out["risk_breakdown"] == {"healthy": 1, "watch": 1, "critical": 1}


def test_rollup_of_empty_fleet():
    out = rollup([], [])
    assert out["total_aircraft"] == 0
    assert out["lowest_rul_aircraft"] is None
    assert out["average_rul"] == 0.0


def test_fleet_series_shares_cycles_between_both_lines():
    snapshots = {
        1: [{"cycle": c, "health": 0.9 - c / 100} for c in range(1, 6)],
        2: [{"cycle": c, "health": 0.5 - c / 100} for c in range(1, 6)],
    }
    out = fleet_series(snapshots, window=5, weakest_aircraft=2)
    avg = [p["cycle"] for p in out["fleet_avg_health"]]
    weak = [p["cycle"] for p in out["weakest_aircraft_health"]]
    assert avg == weak
    assert out["fleet_avg_health"][0]["value"] == pytest.approx(
        (0.89 + 0.49) / 2, abs=1e-3
    )


def test_fleet_series_of_empty_input():
    out = fleet_series({}, 60, None)
    assert out["fleet_avg_health"] == []
    assert out["weakest_aircraft_health"] == []
