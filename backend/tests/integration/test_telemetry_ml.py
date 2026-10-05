"""Telemetry ingest (spec 40) and internal ML predict (spec 41)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.models.telemetry import EngineTelemetry, HealthSnapshot, MlPrediction

SPEC_14 = {
    "s2": 642.4, "s3": 1590.1, "s4": 1401.2, "s7": 553.1, "s8": 2388.2,
    "s9": 9065.4, "s11": 521.3, "s12": 2387.9, "s13": 8131.5, "s14": 0.62,
    "s15": 522.2, "s17": 641.2, "s20": 542.7, "s21": 2388.0,
}


def _row(cycle: int, **overrides) -> dict:
    return {"cycle": cycle,
            "settings": {"setting_1": 0.0, "setting_2": 0.0, "setting_3": 100.0},
            "sensors": {**SPEC_14, **overrides}}


def _batch(aircraft="Fighter-01", start=1, size=30) -> dict:
    return {"aircraft": aircraft, "rows": [_row(start + i) for i in range(size)]}


# ── 40 POST /telemetry ─────────────────────────────────────────────────────────
def test_ingest_returns_202_with_the_new_rul(client, auth):
    response = client.post("/api/v1/telemetry", headers=auth("officer"),
                           json=_batch(start=1))
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["rows_written"] == 30
    assert body["current_cycle"] == 30
    assert 0 <= body["rul"] <= 125
    assert 0.0 <= body["health"] <= 1.0
    assert body["risk"] in ("healthy", "watch", "critical")


def test_ingest_persists_telemetry_and_a_prediction(client, auth, db):
    client.post("/api/v1/telemetry", headers=auth("officer"), json=_batch(start=200))
    telemetry = db.scalar(select(func.count()).select_from(EngineTelemetry))
    predictions = db.scalar(select(func.count()).select_from(MlPrediction))
    snapshots = db.scalar(select(func.count()).select_from(HealthSnapshot))
    assert telemetry > 0 and predictions > 0 and snapshots > 0


def test_ingest_records_the_api_source(client, auth, db):
    client.post("/api/v1/telemetry", headers=auth("officer"), json=_batch(start=300))
    rows = db.scalars(
        select(EngineTelemetry).where(EngineTelemetry.source == "api")
    ).all()
    assert rows
    assert all(r.s6 is not None for r in rows)   # s6 imputed, never null


def test_ingest_is_idempotent_for_the_same_cycle(client, auth, db):
    """Re-posting the same cycles must upsert, not append.

    Counted narrowly, on the cycles this payload actually writes. A count of the whole
    `engine_telemetry` table cannot work here: the replay engine inserts rows for all eight
    aircraft continuously, so the total moves between the two reads on its own — which is
    why this failed intermittently, not because idempotency broke.
    """
    payload = _batch(start=400, size=5)
    client.post("/api/v1/telemetry", headers=auth("officer"), json=payload)

    def written() -> int:
        db.rollback()          # fresh transaction, so the second POST is visible
        return db.scalar(
            select(func.count()).select_from(EngineTelemetry).where(
                EngineTelemetry.cycle.in_([400, 401, 402, 403, 404])
            )
        )

    first = written()
    assert first == 5, "the first ingest should have written every cycle"

    client.post("/api/v1/telemetry", headers=auth("officer"), json=payload)
    assert written() == first


def test_ingest_advances_the_aircraft(client, auth):
    client.post("/api/v1/telemetry", headers=auth("officer"), json=_batch(start=500))
    detail = client.get("/api/v1/aircraft/Fighter-01", headers=auth("viewer")).json()
    assert detail["current_cycle"] == 529      # cycles 500..529 inclusive


def test_ingest_health_is_consistent_with_rul(client, auth):
    body = client.post("/api/v1/telemetry", headers=auth("officer"),
                       json=_batch(start=600)).json()
    assert body["health"] == pytest.approx(min(1.0, body["rul"] / 125), abs=1e-3)


def test_ingest_rejects_an_unknown_aircraft(client, auth):
    response = client.post("/api/v1/telemetry", headers=auth("officer"),
                           json=_batch(aircraft="Fighter-99"))
    assert response.status_code == 404


def test_ingest_rejects_an_empty_batch(client, auth):
    assert client.post("/api/v1/telemetry", headers=auth("officer"),
                       json={"aircraft": "Fighter-01", "rows": []}).status_code == 422


def test_ingest_rejects_a_non_positive_cycle(client, auth):
    payload = _batch(size=1)
    payload["rows"][0]["cycle"] = 0
    assert client.post("/api/v1/telemetry", headers=auth("officer"),
                       json=payload).status_code == 422


def test_ingest_rejects_missing_settings(client, auth):
    payload = _batch(size=1)
    payload["rows"][0].pop("settings")
    assert client.post("/api/v1/telemetry", headers=auth("officer"),
                       json=payload).status_code == 422


def test_ingest_rejects_a_non_numeric_sensor(client, auth):
    payload = _batch(size=1)
    payload["rows"][0]["sensors"]["s4"] = "hot"
    assert client.post("/api/v1/telemetry", headers=auth("officer"),
                       json=payload).status_code == 422


def test_engine_detail_reflects_ingested_telemetry(client, auth):
    client.post("/api/v1/telemetry", headers=auth("officer"), json=_batch(start=700))
    body = client.get("/api/v1/aircraft/Fighter-01/engine", headers=auth("viewer")).json()
    assert body["history"], "history should exist after ingest"
    assert body["components"]["fan"] is not None
    assert body["weakest_component"] in ("fan", "hpc", "hpt", "lpt")


# ── 41 POST /internal/ml/predict ──────────────────────────────────────────────
def _predict_window(size: int = 30) -> list[dict]:
    return [{"cycle": i + 1,
             "settings": {"setting_1": 0.0, "setting_2": 0.0, "setting_3": 100.0},
             "sensors": dict(SPEC_14)} for i in range(size)]


def test_predict_returns_rul_and_components(client, auth):
    body = client.post("/api/v1/internal/ml/predict", headers=auth("officer"),
                       json={"window": _predict_window(), "persist": False}).json()
    assert 0 <= body["rul"] <= 125
    assert set(body["component_health"]) == {"fan", "hpc", "hpt", "lpt"}
    assert all(0.0 <= v <= 1.0 for v in body["component_health"].values())


def test_predict_deviation_is_bounded(client, auth):
    """Deviation needs baseline_stats.json. Without the artifact it is empty;
    with it, every one of the 15 sensors appears (unit-tested in test_inference)."""
    body = client.post("/api/v1/internal/ml/predict", headers=auth("officer"),
                       json={"window": _predict_window(), "persist": False}).json()
    assert all(0.0 <= v <= 1.0 for v in body["deviation"].values())
    if body["model"]["fallback"]:
        assert body["deviation"] == {}
    else:
        assert set(body["deviation"]) == {
            "s2", "s3", "s4", "s6", "s7", "s8", "s9", "s11",
            "s12", "s13", "s14", "s15", "s17", "s20", "s21",
        }


def test_predict_returns_the_component_sensor_map(client, auth):
    body = client.post("/api/v1/internal/ml/predict", headers=auth("officer"),
                       json={"window": _predict_window(), "persist": False}).json()
    # s9 sits in hpc: it was unassigned, which left the HPC readout ignoring the
    # sensor carrying 7.8% of the model's gain. Asserted against COMPONENT_SENSOR_MAP
    # itself so the expectation cannot drift from the rule again.
    from app.domain.rules import COMPONENT_SENSOR_MAP

    assert body["component_sensor_map"] == COMPONENT_SENSOR_MAP
    assert body["component_sensor_map"] == {
        "fan": ["s8", "s13"], "hpc": ["s3", "s7", "s9", "s11"],
        "hpt": ["s20", "s21"], "lpt": ["s4"],
    }


def test_predict_always_reports_which_model_answered(client, auth):
    """A real model may or may not be staged; either way the response must say which.

    A maintenance system that silently substitutes 125 - cycle is worse than one that
    is down, so `fallback` / `degraded` must be explicit either way — and each flag
    must agree with what the loader actually did.
    """
    from app.ml.model_store import handle

    body = client.post("/api/v1/internal/ml/predict", headers=auth("officer"),
                       json={"window": _predict_window(), "persist": False}).json()
    assert "version" in body["model"]

    # The assertions below describe the z-score variant, which ships no scaler. They
    # only hold when a real booster is staged; without one the documented
    # `rul = 125 - cycle` fallback is the correct answer, not a defect.
    if handle().ready:
        assert body["model"]["fallback"] is False
        assert body["model"]["degraded"] is False
        assert body["model"]["requires_scaler"] is False   # the z-score variant
    else:
        assert body["model"]["fallback"] is True
        assert body["model"]["degraded"] is True
    assert 0 < body["rul"] <= 125


def test_healthz_reports_degraded_when_the_model_is_missing(client, auth):
    from app.ml import model_store as ms

    previous = ms._handle
    ms._handle = ms.ModelHandle(ready=False, error="forced: no artifact")
    try:
        body = client.get("/healthz").json()
    finally:
        ms._handle = previous
    assert body["status"] == "degraded"
    assert body["model"]["degraded"] is True


def test_predict_rul_cap_flag(client, auth):
    body = client.post("/api/v1/internal/ml/predict", headers=auth("officer"),
                       json={"window": _predict_window(), "persist": False}).json()
    if body["rul"] == 125:
        assert body["rul_capped"] is True
    else:
        assert body["rul_capped"] is False


def test_predict_window_below_five_is_422(client, auth):
    assert client.post("/api/v1/internal/ml/predict", headers=auth("officer"),
                       json={"window": _predict_window(3)}).status_code == 422


def test_predict_window_above_thirty_is_422(client, auth):
    assert client.post("/api/v1/internal/ml/predict", headers=auth("officer"),
                       json={"window": _predict_window(40)}).status_code == 422


def _window_ending_at(cycle: int, size: int = 30) -> list[dict]:
    """A scoring window whose last row is `cycle`.

    Far-out cycle numbers matter for the `persist` tests: the replay engine writes this
    table continuously, so a test that scores a cycle the replay is about to reach races
    it. Cycles around 9100 are unreachable in a test run (the replay would need to walk an
    engine to end of life first), which is the same trick `test_repredicting_a_cycle…` uses.
    """
    first = cycle - size + 1
    return [{"cycle": c,
             "settings": {"setting_1": 0.0, "setting_2": 0.0, "setting_3": 100.0},
             "sensors": dict(SPEC_14)} for c in range(first, cycle + 1)]


def _prediction_at(db, aircraft_code: str, cycle: int):
    """The stored prediction for one aircraft/cycle, read in a fresh transaction.

    `rollback()` first, so the read starts a new transaction and can see rows another
    session committed after this one last looked.
    """
    from app.models.fleet import Aircraft

    aircraft_id = db.scalar(select(Aircraft.id).where(Aircraft.code == aircraft_code))
    assert aircraft_id is not None, f"{aircraft_code} is missing"
    db.rollback()
    return db.scalar(select(MlPrediction).where(
        MlPrediction.aircraft_id == aircraft_id,
        MlPrediction.cycle == cycle,
    ))


def test_predict_persist_false_writes_nothing(client, auth, db):
    """`persist: false` must not touch the table.

    Asserted on the one (aircraft, cycle) this request would have written, not on a
    before/after count of the whole table: the replay engine inserts into `ml_predictions`
    continuously in the background, so the global count moves on its own and the delta was
    never attributable to this request. That made the test fail whenever the replay ticked
    inside the two reads.
    """
    cycle = 9102
    assert _prediction_at(db, "Fighter-02", cycle) is None, "precondition"

    client.post("/api/v1/internal/ml/predict", headers=auth("officer"),
                json={"aircraft": "Fighter-02", "window": _window_ending_at(cycle),
                      "persist": False})

    assert _prediction_at(db, "Fighter-02", cycle) is None


def test_predict_persist_true_writes_one_row(client, auth, db):
    """`persist: true` must leave exactly the row it scored."""
    cycle = 9101
    assert _prediction_at(db, "Fighter-02", cycle) is None, "precondition"

    client.post("/api/v1/internal/ml/predict", headers=auth("officer"),
                json={"aircraft": "Fighter-02", "window": _window_ending_at(cycle),
                      "persist": True})

    row = _prediction_at(db, "Fighter-02", cycle)
    assert row is not None, "persist: true did not write the scored row"
    assert row.rul is not None


def test_repredicting_a_cycle_refreshes_its_provenance(client, auth, db):
    """A row first written by the fallback must not keep saying 'fallback' for ever.

    The replay wraps and revisits cycle numbers that already have rows, and the
    upsert used to refresh only `rul`. Every other prediction-derived column kept
    the values from whichever run created the row — so `model_version`, the column
    you audit to learn whether a number came from the model, was permanently wrong
    for any cycle first seen while the artifact was missing.
    """
    from app.repositories import telemetry_repo as telemetry

    aircraft_id = db.scalar(
        select(MlPrediction.aircraft_id).limit(1)
    ) or _first_aircraft_id(db)

    def write(model_version: str, rul: int) -> None:
        telemetry.upsert_prediction(
            db, aircraft_id=aircraft_id, cycle=9001, rul=rul,
            component_health={"fan": 1.0, "hpc": 0.9, "hpt": 0.8, "lpt": 0.7},
            top_sensors=[{"sensor": "s11", "contribution": 0.19}],
            deviation={"s11": 1.4},
            model_version=model_version, latency_ms=2.0,
            created_at=datetime(2026, 1, 1, tzinfo=UTC), refresh_latency=True,
        )
        db.commit()

    write("fallback", 100)
    write("ALL", 42)

    row = db.scalar(select(MlPrediction).where(
        MlPrediction.aircraft_id == aircraft_id, MlPrediction.cycle == 9001))
    assert row.model_version == "ALL"
    assert row.rul == 42
    assert float(row.hpc) == 0.9
    assert row.deviation == {"s11": 1.4}


def _first_aircraft_id(db) -> int:
    from app.models.fleet import Aircraft

    return db.scalar(select(Aircraft.id).order_by(Aircraft.id).limit(1))


def test_predict_is_deterministic(client, auth):
    results = [
        client.post("/api/v1/internal/ml/predict", headers=auth("officer"),
                    json={"window": _predict_window(), "persist": False}).json()
        for _ in range(3)
    ]
    for result in results:      # latency_ms is a measurement, not a result
        result.pop("latency_ms")
    assert results[1] == results[0]
    assert results[2] == results[0]


def test_predict_meets_the_hundred_millisecond_budget(client, auth):
    for _ in range(5):
        body = client.post("/api/v1/internal/ml/predict", headers=auth("officer"),
                           json={"window": _predict_window(),
                                 "persist": False}).json()
    assert body["latency_ms"] < 100
