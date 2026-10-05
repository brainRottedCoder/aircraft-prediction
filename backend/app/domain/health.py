"""Health derivation and smoothing. Pure — no I/O.

Two paths, per docs/01-requirements-evaluation.md:
  engine            -> rul / RUL_CAP, from the ML service (not simulated)
  radar/gear/hyd/fuel -> maintenance_burden_v1 from real maintenance history
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .rules import RUL_CAP

EMA_ALPHA = 0.3
HEALTH_FLOOR = 0.05

# fault_type -> (part, severity weight)
FAULT_MAP: dict[str, tuple[str, float]] = {
    "blade erosion": ("engine", 1.00),
    "compressor stall": ("engine", 1.00),
    "high EGT": ("engine", 1.00),
    "oil pressure drop": ("engine", 1.00),
    "oil temperature high": ("engine", 1.00),
    "vibration above limit": ("engine", 1.00),
    "hydraulic pressure low": ("hyd", 0.95),
    "fuel flow anomaly": ("fuel", 0.90),
    "sensor fault": ("radar", 0.85),
    "starter fault": ("gear", 0.70),
}
SEVERITY_WEIGHT = {"Critical": 1.0, "Major": 0.6, "Minor": 0.3}
SNAG_PART_WEIGHT = {
    "Critical": ("engine", 1.00),
    "Major": ("engine", 0.85),
    "Minor": ("hyd", 0.55),
}


# ── engine ──────────────────────────────────────────────────────────────────────
def engine_health_from_rul(rul: int | float) -> float:
    """health = rul / 125. 1.0 at the cap, 0.0 at failure."""
    return round(max(0.0, min(1.0, float(rul) / RUL_CAP)), 3)


# ── non-engine ──────────────────────────────────────────────────────────────────
def recency_weight(age_months: float) -> float:
    """Full weight under 6 months, decaying to a 0.1 floor over 30 months."""
    if age_months <= 6:
        return 1.0
    return max(0.1, 1.0 - (age_months - 6) / 30.0)


def maintenance_burden(
    records: list[dict[str, Any]], snags: list[dict[str, Any]], part: str, p95: float
) -> float:
    """Raw maintenance-burden score for one aircraft+part.

    records: {fault_type, date, downtime_hours}
    snags:   {severity, date_reported}
    p95:     95th-percentile burden across the fleet, for normalisation.
    """
    score = 0.0
    for r in records:
        mapped = FAULT_MAP.get(r["fault_type"] or "")
        if not mapped or mapped[0] != part:
            continue
        score += (
            mapped[1] * recency_weight(r["age_months"]) * float(r["downtime_hours"]) / 100
        )
    for s in snags:
        sev = s["severity"]
        if sev not in SEVERITY_WEIGHT or SNAG_PART_WEIGHT[sev][0] != part:
            continue
        score += SEVERITY_WEIGHT[sev] * recency_weight(s["age_months"]) / 10
    if p95 <= 0:
        return 0.0
    return score / p95


def burden_health(score: float) -> float:
    """Map a normalised burden in [0, ~1+] onto health in [0, 1]."""
    return round(max(HEALTH_FLOOR, min(1.0, 1.0 - score)), 3)


# ── smoothing ───────────────────────────────────────────────────────────────────
@dataclass
class EmaState:
    """Per (aircraft, component) exponential moving average."""

    values: dict[str, float]

    @classmethod
    def empty(cls) -> EmaState:
        return cls(values={})

    def update(self, key: str, raw: float) -> float:
        prev = self.values.get(key)
        value = raw if prev is None else EMA_ALPHA * raw + (1 - EMA_ALPHA) * prev
        self.values[key] = value
        return value

    def reset(self) -> None:
        self.values.clear()


