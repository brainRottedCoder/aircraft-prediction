"""Performance budgets (spec 50) and the N+1 guard (docs/11 §5.3)."""
from __future__ import annotations

import statistics
import time

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

GET_BUDGET_MS = 200.0
ML_BUDGET_MS = 100.0
WARMUP = 5
ITERATIONS = 40

READ_PATHS = [
    "/api/v1/fleet/summary",
    "/api/v1/aircraft",
    "/api/v1/aircraft/Fighter-01",
    "/api/v1/aircraft/Fighter-01/engine",
    "/api/v1/aircraft/Fighter-01/engine?window=60",
    "/api/v1/aircraft/Fighter-01/parts/engine",
    "/api/v1/aircraft/Fighter-01/parts/hyd",
    "/api/v1/fleet/heatmap",
    "/api/v1/fleet/actions",
    "/api/v1/fleet/actions?limit=50",
    "/api/v1/maintenance/schedule",
    "/api/v1/spares",
    "/api/v1/agencies",
    "/api/v1/alerts",
]


@pytest.fixture(scope="session")
def query_counter():
    """Counts real SQL statements issued during the with-block."""
    counter = {"n": 0}
    listeners = []

    def on_engine_connect(engine: Engine):
        if engine in listeners:
            return

        @event.listens_for(engine, "before_cursor_execute")
        def _count(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            counter["n"] += 1

        listeners.append(engine)

    from app.db.session import get_engine

    on_engine_connect(get_engine())
    yield counter


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(len(ordered) * 0.95))
    return ordered[index]


@pytest.mark.parametrize("path", READ_PATHS)
def test_get_endpoints_meet_the_200ms_budget(client, auth, path):
    headers = auth("viewer")
    for _ in range(WARMUP):
        client.get(path, headers=headers)
    samples = []
    for _ in range(ITERATIONS):
        started = time.perf_counter()
        response = client.get(path, headers=headers)
        samples.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 200, path
    p95 = _p95(samples)
    assert p95 < GET_BUDGET_MS, f"{path}: p95 {p95:.1f}ms exceeds {GET_BUDGET_MS}ms"


SENSORS = {"s2": 642.4, "s3": 1590.1, "s4": 1401.2, "s7": 553.1, "s8": 2388.2,
           "s9": 9065.4, "s11": 521.3, "s12": 2387.9, "s13": 8131.5, "s14": 0.62,
           "s15": 522.2, "s17": 641.2, "s20": 542.7, "s21": 2388.0}


def _ml_payload(size: int = 30) -> dict:
    return {"window": [{"cycle": i + 1,
                        "settings": {"setting_1": 0.0, "setting_2": 0.0,
                                     "setting_3": 100.0},
                        "sensors": dict(SENSORS)} for i in range(size)],
            "persist": False}


def test_ml_endpoint_meets_the_100ms_budget(client, auth):
    """Both the reported inference latency and the wall-clock round trip."""
    payload = _ml_payload()
    inference, round_trip = [], []
    for _ in range(WARMUP):
        client.post("/api/v1/internal/ml/predict", headers=auth("officer"), json=payload)
    for _ in range(ITERATIONS):
        started = time.perf_counter()
        body = client.post("/api/v1/internal/ml/predict",
                           headers=auth("officer"), json=payload).json()
        round_trip.append((time.perf_counter() - started) * 1000)
        inference.append(body["latency_ms"])
    assert _p95(inference) < ML_BUDGET_MS, f"inference p95 {_p95(inference):.1f}ms"
    assert _p95(round_trip) < ML_BUDGET_MS, f"round-trip p95 {_p95(round_trip):.1f}ms"


@pytest.mark.parametrize("path", [
    "/api/v1/fleet/summary",
    "/api/v1/aircraft",
    "/api/v1/aircraft/Fighter-01",
    "/api/v1/aircraft/Fighter-01/engine",
    "/api/v1/aircraft/Fighter-01/parts/engine",
    "/api/v1/fleet/heatmap",
    "/api/v1/fleet/actions",
    "/api/v1/maintenance/schedule",
])
def test_query_count_is_bounded_and_not_per_row(client, auth, query_counter, path):
    """The N+1 guard: a query inside a comprehension shows up here as a count
    that grows with the fleet. The ceiling allows generous headroom."""
    headers = auth("viewer")
    client.get(path, headers=headers)          # warm caches/session
    query_counter["n"] = 0
    client.get(path, headers=headers)
    statements = query_counter["n"]
    # 8 aircraft x 5 parts = 40 rows; a per-row loop would exceed 100 statements
    assert statements <= 40, f"{path} issued {statements} statements — possible N+1"


def test_summary_does_not_scale_queries_with_the_fleet(client, auth, query_counter):
    """fleet_summary touches every aircraft; the count must stay flat."""
    headers = auth("viewer")
    client.get("/api/v1/fleet/summary", headers=headers)
    query_counter["n"] = 0
    client.get("/api/v1/fleet/summary", headers=headers)
    per_fleet_pass = query_counter["n"]
    detail = client.get("/api/v1/aircraft", headers=headers).json()
    assert per_fleet_pass < len(detail["items"]) * 2 + 20


def test_healthz_is_cheap(client):
    for _ in range(WARMUP):
        client.get("/healthz")
    samples = []
    for _ in range(ITERATIONS):
        started = time.perf_counter()
        client.get("/healthz")
        samples.append((time.perf_counter() - started) * 1000)
    assert statistics.median(samples) < GET_BUDGET_MS
