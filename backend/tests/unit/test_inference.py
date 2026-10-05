"""Inference, attribution and fallback — the ML layer with and without an artifact."""
from __future__ import annotations

import pytest

from app.domain.rules import COMPONENT_SENSOR_MAP
from app.ml import fallback
from app.ml.attribution import deviation_scores, top_sensors, z_scores
from app.ml.features import FEATURE_ORDER, SENSORS
from app.ml.inference import component_health, predict

SPEC_14 = ["s2", "s3", "s4", "s7", "s8", "s9", "s11", "s12",
           "s13", "s14", "s15", "s17", "s20", "s21"]


def row(cycle: int, **overrides) -> dict:
    sensors = {s: 100.0 + i for i, s in enumerate(SENSORS) if s != "s6"}
    sensors.update(overrides)
    return {"cycle": cycle, "settings": {"setting_1": 0.0, "setting_2": 0.0,
                                         "setting_3": 100.0}, "sensors": sensors}


def window(size: int = 30, **overrides) -> list[dict]:
    return [row(c, **overrides) for c in range(1, size + 1)]


# ── fallback (spec 46/47 with no artifact) ─────────────────────────────────────
@pytest.mark.parametrize(
    "cycle,expected", [(1, 124), (10, 115), (124, 1), (125, 0), (200, 0)]
)
def test_fallback_rul_is_the_degradation_curve(cycle, expected):
    assert fallback.fallback_rul(cycle) == expected


def test_fallback_components_decline_with_rul():
    healthy = fallback.fallback_components(125)
    failing = fallback.fallback_components(0)
    assert all(healthy[c] > failing[c] for c in ("fan", "hpc", "hpt", "lpt"))
    assert all(0.0 <= v <= 1.0 for v in healthy.values())


def test_predict_without_an_artifact_falls_back():
    """Force the missing-artifact path.

    A real model is now staged in data/ml/full/, so this cannot rely on the ambient
    handle being empty any more -- it has to create the condition it is testing.
    """
    from app.ml import model_store as ms

    previous = ms._handle
    ms._handle = ms.ModelHandle(ready=False, error="forced: no artifact")
    try:
        result = predict(window(), current_cycle=40)
    finally:
        ms._handle = previous
    assert result["model"]["fallback"] is True
    assert result["model"]["version"] == "fallback"
    assert result["model"]["degraded"] is True
    assert result["rul"] == fallback.fallback_rul(40)


def test_fallback_response_is_flagged_and_bounded():
    result = predict(window(), current_cycle=10)
    assert 0 <= result["rul"] <= 125
    assert all(0.0 <= v <= 1.0 for v in result["component_health"].values())
    assert result["component_sensor_map"] == COMPONENT_SENSOR_MAP
    assert result["latency_ms"] >= 0


def test_predict_rejects_a_short_window_without_crashing():
    result = predict([row(1)], current_cycle=1)
    assert result["model"]["fallback"] is True
    assert result["model"]["reason"].startswith("window_rejected:")


def test_predict_reports_a_missing_baseline_distinctly_from_a_short_window():
    """Both are fallback, but they are different bugs and must not share a reason.

    A pooled contract asked for a subset its baselines do not cover used to report
    "short_window", which points at the window rather than at the artifact lookup.
    """
    from app.ml import model_store as ms

    previous = ms._handle
    ms._handle = ms.ModelHandle(
        contract={"subsets": ["FD001", "FD004"], "requires_scaler": False,
                  "feature_order": list(FEATURE_ORDER)},
        stats={"baselines": {"FD001": {"0": {s: {"median": 1.0, "mad": 1.0}
                                                for s in SENSORS}}}},
        ready=True,
    )
    try:
        result = predict(window(), subset="FD003", current_cycle=30)
    finally:
        ms._handle = previous
    assert result["model"]["fallback"] is True
    assert "no baseline block for subset='FD003'" in result["model"]["reason"]


def test_predict_handles_an_empty_window():
    result = predict([], current_cycle=5)
    assert result["rul"] == fallback.fallback_rul(5)


def test_predict_is_deterministic():
    """Same input -> same output. latency_ms is excluded: it is a measurement."""
    first = predict(window(), current_cycle=30)
    second = predict(window(), current_cycle=30)
    first.pop("latency_ms"), second.pop("latency_ms")
    assert first == second


def test_predict_reports_weakest_component():
    result = predict(window(), current_cycle=30)
    weakest = result["weakest_component"]
    components = result["component_health"]
    assert components[weakest] == min(components.values())


# ── attribution (spec 46) ──────────────────────────────────────────────────────
STATS = {"regime_0": {s: {"median": 100.0 + i, "mad": 1.0}
                      for i, s in enumerate(SENSORS)}}


def test_z_scores_are_robust_to_the_baseline():
    median = STATS["regime_0"]["s11"]["median"]
    window_rows = window(5)
    window_rows[-1]["sensors"]["s11"] = median + 3.0     # median + 3 MAD
    z = z_scores(window_rows, STATS)
    assert z["s11"] == pytest.approx(3.0)


def test_z_scores_of_an_undisturbed_window_are_zero():
    z = z_scores(window(5), STATS)
    assert z["s11"] == pytest.approx(0.0)


def test_z_scores_without_stats_are_empty():
    assert z_scores(window(5), {}) == {}


def test_deviation_scores_are_bounded_to_unit_interval():
    scores = deviation_scores({"s11": 0.5, "s20": -12.0, "s21": 30.0})
    assert scores["s20"] == 1.0
    assert scores["s21"] == 1.0
    assert 0.0 <= scores["s11"] <= 1.0


def test_top_sensors_are_ranked_and_capped():
    z = {s: (i + 1) / 10 for i, s in enumerate(SENSORS)}
    top = top_sensors(z, limit=5)
    assert len(top) == 5
    impacts = [t["health_impact"] for t in top]
    assert impacts == sorted(impacts, reverse=True)


def test_top_sensors_carry_their_component():
    top = top_sensors({"s8": 2.0, "s4": 1.0})
    by_sensor = {t["sensor"]: t["component"] for t in top}
    assert by_sensor["s8"] == "fan"
    assert by_sensor["s4"] == "lpt"


def test_top_sensors_of_an_empty_window_is_empty():
    assert top_sensors({}) == []


# ── component health (spec 47) ────────────────────────────────────────────────
def test_component_health_groups_cover_every_assigned_sensor():
    """s9 was added to hpc: highest end-of-life deviation (mean |z| 8.0) and 7.8% of
    the model's gain, previously unassigned."""
    assert COMPONENT_SENSOR_MAP == {
        "fan": ["s8", "s13"], "hpc": ["s3", "s7", "s9", "s11"],
        "hpt": ["s20", "s21"], "lpt": ["s4"],
    }
    from app.domain.rules import UNASSIGNED_SENSORS
    # every sensor is either assigned to exactly one component or explicitly unassigned
    assigned = [s for v in COMPONENT_SENSOR_MAP.values() for s in v]
    assert len(assigned) == len(set(assigned)), "a sensor is in two components"
    assert set(assigned) | set(UNASSIGNED_SENSORS) == set(SENSORS)
    assert "s9" not in UNASSIGNED_SENSORS


def test_healthy_sensors_give_full_component_health():
    assert component_health(dict.fromkeys(SENSORS, 0.0)) == pytest.approx(
        {"fan": 1.0, "hpc": 1.0, "hpt": 1.0, "lpt": 1.0}
    )


def test_large_deviation_drives_component_health_to_zero():
    z = dict.fromkeys(SENSORS, 9.0)
    assert component_health(z)["hpc"] == 0.0


def test_component_health_only_uses_assigned_sensors():
    """s12 is unassigned: a huge deviation there must not move any component."""
    baseline = component_health(dict.fromkeys(SENSORS, 1.0))
    spiked = component_health({**dict.fromkeys(SENSORS, 1.0), "s12": 99.0})
    assert spiked == baseline


def test_component_health_reads_full_health_at_the_measured_healthy_anchor():
    """The old divisor of 3.0 scored a healthy engine at 0.62."""
    from app.ml.inference import Z_HEALTHY_ANCHOR
    out = component_health(dict.fromkeys(SENSORS, Z_HEALTHY_ANCHOR))
    assert min(out.values()) == pytest.approx(1.0, abs=1e-3)


def test_component_health_reaches_zero_at_the_measured_eol_anchor():
    from app.ml.inference import Z_EOL_ANCHOR
    out = component_health(dict.fromkeys(SENSORS, Z_EOL_ANCHOR))
    assert max(out.values()) == pytest.approx(0.0, abs=1e-3)


def test_component_health_is_monotonic_in_deviation():
    """More deviation must never mean a healthier component."""
    prev = 1.1
    for z in (1.5, 2.0, 3.0, 4.0, 5.0, 6.0):
        h = component_health(dict.fromkeys(SENSORS, z))["hpc"]
        assert h <= prev + 1e-9
        prev = h


def test_component_health_with_no_z_is_healthy():
    assert component_health({}) == pytest.approx(
        {"fan": 1.0, "hpc": 1.0, "hpt": 1.0, "lpt": 1.0}
    )
