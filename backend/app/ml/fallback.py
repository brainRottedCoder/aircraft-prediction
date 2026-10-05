"""Deterministic degradation curve — used when the artifact is unavailable (docs/08 §9)."""
from __future__ import annotations

from ..domain.rules import RUL_CAP, cap_rul

VERSION = "fallback"


def fallback_rul(current_cycle: int, cap: int = RUL_CAP) -> int:
    return cap_rul(cap - current_cycle)


def fallback_components(rul: int, cap: int = RUL_CAP) -> dict[str, float]:
    """Linear decline, worst component offset so rule 25 stays deterministic."""
    base = max(0.0, min(1.0, rul / cap))
    return {
        "fan": round(min(1.0, base + 0.02), 3),
        "hpc": round(base, 3),
        "hpt": round(max(0.0, base - 0.01), 3),
        "lpt": round(max(0.0, base - 0.03), 3),
    }


def fallback_deviation() -> dict[str, float]:
    return {}


# FD001 feature importances, so the UI shows a stable shape instead of nothing
_FALLBACK_GAIN = [("s11", 0.1915, "hpc"), ("s4", 0.0735, "lpt"),
                  ("s9", 0.0462, None), ("s12", 0.0423, None),
                  ("s14", 0.0338, None)]


def fallback_top_sensors() -> list[dict[str, object]]:
    """Same shape as the real attribution: label and health_impact included."""
    return [
        {"sensor": sensor, "label": f"Sensor {sensor[1:]}",
         "contribution": contribution, "z": 0.0, "health_impact": 0.0,
         "component": component}
        for sensor, contribution, component in _FALLBACK_GAIN
    ]


