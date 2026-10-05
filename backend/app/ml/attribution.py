"""Per-sensor deviation and top-sensor attribution (docs/08 §6.3, §6.6)."""
from __future__ import annotations

from typing import Any

from ..domain.rules import COMPONENT_SENSOR_MAP
from . import model_store
from .features import MAD_REL_FLOOR, SENSORS, regime_block

EPS = 1e-6
GAIN_WEIGHT = 0.6
Z_WEIGHT = 0.4

SENSOR_COMPONENT = {
    sensor: component
    for component, sensors in COMPONENT_SENSOR_MAP.items()
    for sensor in sensors
}


def z_scores(
    window: list[dict[str, Any]], stats: dict[str, Any], regime: int = 0,
    subset: str | None = None, *, s6_median: float | None = None,
) -> dict[str, float]:
    """Robust z against the healthy baseline (median/MAD over the first 20 cycles).

    This MUST mirror `features.regime_z_scorer`, because both describe the same
    quantity for the same window: the model is fed z-scores, and the deviation
    reported back is supposed to explain that prediction. When the two disagreed:

      * s6 was imputed for the feature matrix (build_window -> impute_s6) but read
        raw here, so a request that omits s6 -- which the API spec's 14-sensor payload
        does -- silently lost s6 from `deviation` even though s6 is one of the 29
        columns the model actually consumes;
      * the MAD floor was missing here but present there, so a sensor whose baseline
        MAD rounds to 0.0 (s6 stores mad=0.0) divided by 1e-6 and reported |z| in the
        thousands, pinning its deviation at the 1.0 clip.

    Both made the explanation disagree with the prediction, which is worse than not
    explaining it at all.
    """
    # regime_block knows both artifact layouts (regime_<n> and baselines[<n>]).
    # Reading the keys directly here once made z empty for every Phase 2 artifact,
    # which surfaced as component health of 1.0 everywhere — healthy, and wrong.
    baseline = regime_block(stats, regime, subset)
    if not baseline:
        return {}
    latest = window[-1]
    sensors = dict(latest.get("sensors") or {})
    if sensors.get("s6") is None and s6_median is not None:
        sensors["s6"] = s6_median      # same imputation build_window applies
    out: dict[str, float] = {}
    for sensor in SENSORS:
        stat = baseline.get(sensor)
        value = sensors.get(sensor)
        if not stat or value is None:
            continue          # a missing sensor is skipped, not a KeyError
        median = float(stat["median"])
        mad = float(stat.get("mad", EPS))
        if mad <= max(abs(median) * MAD_REL_FLOOR, EPS):
            out[sensor] = 0.0
            continue
        out[sensor] = (float(value) - median) / mad
    return out


def deviation_scores(z: dict[str, float]) -> dict[str, float]:
    """|z| clipped to [0, 10] and rescaled to [0, 1] — spec item 46."""
    return {s: round(min(10.0, abs(v)) / 10.0, 3) for s, v in z.items()}


def top_sensors(
    z: dict[str, float],
    *,
    limit: int = 5,
    h: model_store.ModelHandle | None = None,
) -> list[dict[str, Any]]:
    """Blend global gain with current deviation so quiet sensors do not dominate."""
    gain = model_store.gain_scores(h)
    total = sum(gain.values()) or 1.0
    ranked: list[dict[str, Any]] = []
    for sensor, z_val in z.items():
        g = gain.get(sensor, 0.0) / total
        dev = min(10.0, abs(z_val)) / 10.0
        ranked.append(
            {
                "sensor": sensor,
                "label": f"Sensor {sensor[1:]}",
                "contribution": round(g, 4),
                "z": round(z_val, 2),
                "health_impact": round(GAIN_WEIGHT * g + Z_WEIGHT * dev, 4),
                "component": SENSOR_COMPONENT.get(sensor),
            }
        )
    ranked.sort(key=lambda r: float(r["health_impact"]), reverse=True)
    return ranked[:limit]