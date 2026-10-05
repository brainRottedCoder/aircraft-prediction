"""RUL and component-health inference (docs/08 §6)."""
from __future__ import annotations

import time
from typing import Any

from ..core.config import Settings, get_settings
from ..core.errors import BusinessRuleError
from ..domain.rules import COMPONENT_SENSOR_MAP, RUL_CAP, cap_rul, engine_spare
from . import fallback, model_store
from .attribution import deviation_scores, top_sensors, z_scores
from .features import FEATURE_ORDER, build_window

# Component health is anchored at BOTH ends, measured on real FD001 telemetry rather
# than assumed. The previous single divisor (3.0) was wrong: healthy engines already
# sit at mean |z| 1.14, so health = 1 - |z|/3 read 0.62 for a perfectly healthy engine.
#
#   healthy mean |z|  1.14   (mean over 100 engines x first 20 cycles, all components)
#   end-of-life      5.28   (last 5 cycles, all components)
#
# No single divisor can satisfy both ends (healthy >= 0.95 needs D >= 22.9; reaching 0
# at end of life needs D <= 5.3), so health is interpolated between the two anchors.
Z_HEALTHY_ANCHOR = 1.15
Z_EOL_ANCHOR = 5.30


def _classify(window: list[dict[str, Any]], h: model_store.ModelHandle,
              subset: str | None) -> int:
    """Nearest-centroid regime for the latest cycle. 0 when no centroids are shipped."""
    from .features import classify_regime

    latest = window[-1] if window else {}
    settings = latest.get("settings") or {}
    return classify_regime(settings, h.centroids_for(subset))


def predict(
    window: list[dict[str, Any]],
    *,
    regime: int | None = None,
    subset: str | None = None,
    current_cycle: int | None = None,
    settings: Settings | None = None,
    h: model_store.ModelHandle | None = None,
) -> dict[str, Any]:
    """window: last <=30 cycles of {cycle, settings, sensors}. Never raises.

    `subset` selects which block of the baselines artifact to z-score against. It
    defaults to the replayed subset, which is right for the fleet: every aircraft
    binds 1:1 to an FD001 engine whatever the model was trained on.
    """
    s = settings or get_settings()
    h = h or model_store.handle()
    started = time.perf_counter()
    cycle = current_cycle or int(window[-1].get("cycle", len(window)))
    subset = subset or h.subset or s.replay_subset

    # The contract may or may not carry regime_global (29 vs 30 features), and the
    # regime is classified from the latest cycle's settings: within one engine a
    # flight may cross operating conditions, and the booster expects the id that
    # matches the cycle being predicted.
    local_regime = _classify(window, h, subset) if regime is None else regime
    regime_global = local_regime + h.regime_offset(subset)

    # Request validation happens before the artifact check on purpose: a window that
    # is too short is a 422 regardless of whether a model is loaded, and the caller
    # needs to hear about its own malformed request rather than about our state.
    try:
        matrix, imputed = build_window(
            window,
            s6_median=s.s6_median_fd001,
            feature_order=h.feature_order or FEATURE_ORDER,
            sensors_to_z=h.z_scorer(local_regime, subset),
            regime_global=regime_global,
            regime_column=h.regime_column,
            # s10/s16 are constant in FD001 so FD001 telemetry never carries them; the
            # pooled booster still expects both columns, so they are pinned to the
            # healthy median instead of left out (which misaligns the matrix).
            missing_sensor_values=h.missing_sensor_defaults(subset),
        )
    except BusinessRuleError as exc:
        # A rejected window (too short, or no baseline block for the subset) is the
        # caller's problem and is reported as such. Anything else is a broken baseline
        # lookup — reporting that as "short_window" sent debugging in the wrong
        # direction entirely.
        return _fallback_result(cycle, started, fallback_on=f"window_rejected:{exc}")
    except Exception as exc:  # noqa: BLE001 — short window falls back
        model_store.log.warning("feature build failed (%s); using fallback", exc)
        return _fallback_result(cycle, started, fallback_on="short_window")

    if not h.ready or h.booster is None:
        return _fallback_result(cycle, started, fallback_on="artifact_missing")

    try:
        prediction = h.predict(matrix)[-1]
        rul_raw = float(prediction)
        rul = cap_rul(rul_raw)
        # Same baseline the model was fed: the local regime id, under `subset`. Using
        # h.subset here would silently score against FD001's block for every subset.
        z = z_scores(window, h.stats, local_regime, subset, s6_median=s.s6_median_fd001)
        components = component_health(z)
        weakest, weakest_health = engine_spare(components)
        top = top_sensors(z, h=h)
        deviation = deviation_scores(z)
    except Exception as exc:  # noqa: BLE001
        model_store.log.warning("inference failed (%s); using fallback", exc)
        return _fallback_result(cycle, started, fallback_on=f"inference_error:{exc}")

    latency_ms = round((time.perf_counter() - started) * 1000, 2)
    return {
        "cycle": cycle,
        "rul": rul,
        "rul_raw": round(rul_raw, 2),
        "rul_capped": rul_raw > s.rul_cap,
        "component_health": components,
        "weakest_component": weakest,
        "weakest_component_health": weakest_health,
        "deviation": deviation,
        "top_sensors": top,
        "component_sensor_map": COMPONENT_SENSOR_MAP,
        "model": {
            "version": h.version,
            "dataset": h.dataset,
            "requires_scaler": h.requires_scaler,
            "mae": h.mae,
            # the refit has no holdout, so the headline number is a CV RMSE
            "mae_is_cv": h.mae_is_cv,
            "s6_imputed": imputed,
            # which baselines block answered, and which regime it was. Reported because
            # a pooled contract silently resolving to the wrong block is invisible
            # otherwise — the prediction still looks entirely reasonable.
            "subset": subset,
            "regime": local_regime,
            "regime_global": regime_global,
            "fallback": False,
            "degraded": False,
        },
        "latency_ms": latency_ms,
        "health_anchors": {"z_healthy": Z_HEALTHY_ANCHOR, "z_end_of_life": Z_EOL_ANCHOR},
    }


def component_health(
    z: dict[str, float],
    *,
    healthy_anchor: float = Z_HEALTHY_ANCHOR,
    eol_anchor: float = Z_EOL_ANCHOR,
) -> dict[str, float]:
    """Map robust z-scores to [0, 1] using two measured anchors.

    Unassigned sensors never contribute. A component with no data reads 1.0 (healthy)
    rather than 0.0, because absent telemetry is not evidence of failure.
    """
    span = max(eol_anchor - healthy_anchor, 1e-6)
    out: dict[str, float] = {}
    for component, sensors in COMPONENT_SENSOR_MAP.items():
        vals = [abs(z[s]) for s in sensors if s in z]
        if not vals:
            out[component] = 1.0
            continue
        mean_z = sum(vals) / len(vals)
        health = (eol_anchor - mean_z) / span
        out[component] = round(max(0.0, min(1.0, health)), 3)
    return out


def _fallback_result(cycle: int, started: float, *, fallback_on: str) -> dict[str, Any]:
    rul = fallback.fallback_rul(cycle)
    components = fallback.fallback_components(rul)
    weakest, weakest_health = engine_spare(components)
    return {
        "cycle": cycle,
        "rul": rul,
        "rul_raw": float(rul),
        "rul_capped": False,
        "component_health": components,
        "weakest_component": weakest,
        "weakest_component_health": weakest_health,
        "deviation": fallback.fallback_deviation(),
        "top_sensors": fallback.fallback_top_sensors(),
        "component_sensor_map": COMPONENT_SENSOR_MAP,
        "model": {
            "version": fallback.VERSION,
            "dataset": None,
            "mae": None,
            "mae_is_cv": False,
            "s6_imputed": False,
            "fallback": True,
            "degraded": True,       # never guess quietly (docs/14 §9)
            "reason": fallback_on,
        },
        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
    }


__all__ = ["predict", "component_health", "RUL_CAP"]