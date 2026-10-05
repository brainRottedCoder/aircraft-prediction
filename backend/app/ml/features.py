"""Window -> 29-feature matrix. Order is asserted against the feature contract.

The contract is whatever the trained model declares — 29 columns for the FD001 models
(docs/08 §3.2):

    op1, op2                                  2   operating settings
    s2..s21 (15, s6 included)                15   sensors
    <sensor>_rollmean5 / _rollstd5 x 6        12   rolling mean AND std

`cycle` is deliberately NOT an input. It correlates with RUL only because the official
test set is truncated early, so feeding it produces a model that scores well and fails on
real data. It is still tracked per aircraft, for the fallback and for provenance.

Two preprocessing variants exist, and the difference matters:

    scaler variant   Phase 1 FD001 model — raw sensors here, StandardScaler applied to
                     the finished matrix by the model store.
    z-score variant  Phase 2 models — each sensor is standardised against its own
                     regime's healthy baseline BEFORE the rolling statistics are taken,
                     because a rolling mean of raw values is not the same feature as a
                     rolling mean of z-scores once more than one regime is involved.

`sensors_to_z` selects between them; passing None gives the raw (scaler) path.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..core.errors import BusinessRuleError

SPEC_SENSORS = ("s2", "s3", "s4", "s7", "s8", "s9", "s11", "s12",
                "s13", "s14", "s15", "s17", "s20", "s21")
SENSORS = SPEC_SENSORS[:3] + ("s6",) + SPEC_SENSORS[3:]   # 15, s6 included
ROLLING_SENSORS = ("s11", "s4", "s9", "s12", "s14", "s7")
ROLLING_WINDOW = 5
ROLLING_SUFFIXES = ("rollmean", "rollstd")
SETTINGS_COLUMNS = ("op1", "op2")
# The extra column a pooled Phase 2 contract adds on top of the 29. Absent from the
# FD001 contracts, present (n_features=30) from any candidate naming more than one
# subset — so it is read from the contract rather than hard-coded into FEATURE_ORDER.
REGIME_COLUMN = "regime_global"
MIN_WINDOW = 5
MAX_WINDOW = 30
# A MAD below this fraction of the sensor's own median is a rounding artefact, not a
# measurement; such sensors are treated as non-informative instead of exploding.
MAD_REL_FLOOR = 1e-4

# Column order is a CONTRACT, not an implementation detail: XGBoost consumes a
# positional matrix, so a reordered list yields plausible-looking wrong predictions.
FEATURE_ORDER: tuple[str, ...] = tuple(
    list(SETTINGS_COLUMNS)
    + list(SENSORS)
    + [
        f"{s}_{suffix}{ROLLING_WINDOW}"
        for s in ROLLING_SENSORS
        for suffix in ROLLING_SUFFIXES
    ]
)
assert len(FEATURE_ORDER) == 29, "the serving contract is 29 features"


def contract_sensors(feature_order: Sequence[str]) -> tuple[str, ...]:
    """Base sensors a contract needs, derived from its own feature_order.

    Not read from the hardcoded SENSORS tuple. A pooled multi-subset contract declares
    a different sensor set per subset and appends sensors that are constant in FD001 but
    live in FD002/FD004 (`s10`, `s16`), so the 29-column FD001 list is not a superset of
    it -- the pooled contract is 32 columns wide and building the FD001 29 produced a
    positional matrix that was silently misaligned, not merely short.
    """
    derived = set()
    for name in feature_order:
        if "_roll" in name:
            derived.add(name.split("_roll", 1)[0])
    skip = set(SETTINGS_COLUMNS) | {REGIME_COLUMN}
    # `_roll` columns are excluded by name, not by membership of their base sensor:
    # `s11_rollmean5` is not itself a key of `derived` (that holds `s11`), so testing
    # against `derived` let every rolling column through as if it were a raw sensor.
    return tuple(
        n for n in feature_order
        if n not in skip and "_roll" not in n
    )


def impute_s6(row: dict[str, Any], s6_median: float) -> tuple[dict[str, float], bool]:
    """Spec item 45 lists 14 sensors; the trained model also uses s6."""
    sensors = dict(row.get("sensors") or {})
    if sensors.get("s6") is None:
        sensors["s6"] = s6_median
        return sensors, True
    return sensors, False


def build_window(
    window: list[dict[str, Any]],
    *,
    s6_median: float,
    absolute_cycles: list[int] | None = None,
    feature_order: Sequence[str] = FEATURE_ORDER,
    sensors_to_z: Callable[[dict[str, float]], dict[str, float]] | None = None,
    regime_global: int = 0,
    regime_column: str = REGIME_COLUMN,
    missing_sensor_values: dict[str, float] | None = None,
) -> tuple[list[list[float]], bool]:
    """Build the model matrix for a window.

    Returns (matrix, s6_imputed). Raises BusinessRuleError on a window too short to
    carry a rolling statistic. `regime_global` is written only when the contract asks
    for that column, so the 29-feature FD001 contract is unaffected.

    The base sensors come from `feature_order`, not from SENSORS. A sensor the contract
    wants but the telemetry lacks is filled from `missing_sensor_values` (its healthy
    median, so the derived z-score is exactly 0.0) and counted as imputed: `s10`/`s16`
    are constant in FD001, so FD001 telemetry does not carry them at all and the pooled
    booster still expects the columns. Filling rather than dropping keeps the matrix
    positionally aligned with the booster, which is the whole point.
    """
    if len(window) < MIN_WINDOW:
        raise BusinessRuleError(
            f"window must contain at least {MIN_WINDOW} cycles, got {len(window)}"
        )
    window = window[-MAX_WINDOW:]

    cycles = absolute_cycles or [int(r.get("cycle", i + 1)) for i, r in enumerate(window)]
    needed = contract_sensors(feature_order)
    imputed = False
    series: list[dict[str, float]] = []
    # `cycles` is zipped in only for the strict length check against `window`; the
    # per-row cycle number is not an input (see the module docstring).
    for row, _cycle in zip(window, cycles, strict=True):
        sensors, was_imputed = impute_s6(row, s6_median)
        imputed = imputed or was_imputed
        values = {}
        for sensor in needed:
            raw = sensors.get(sensor)
            if raw is None:
                # constant in this subset, so never measured; pinned to its healthy
                # median, which is the value the booster was trained to see as z=0
                values[sensor] = float((missing_sensor_values or {}).get(sensor, 0.0))
                imputed = True
            else:
                values[sensor] = float(raw)
        settings = row.get("settings") or {}
        values["op1"] = float(settings.get("setting_1", settings.get("op1", 0.0)))
        values["op2"] = float(settings.get("setting_2", settings.get("op2", 0.0)))
        # z-score each cycle BEFORE any rolling statistic is derived from it
        if sensors_to_z is not None:
            values.update(sensors_to_z(values))
        # A pooled (multi-subset) contract carries a 30th column, `regime_global`.
        # The absolute id is what the booster was trained on — each subset's regime
        # ids are offset so FD002's regime 0 cannot collide with FD001's — so the
        # offset must be applied here rather than by the caller. Written into every
        # cycle of the window: it is constant across an engine in single-regime data,
        # but in six-regime data consecutive cycles can sit in different conditions,
        # so it is computed per cycle by the caller-supplied classifier in predict().
        if regime_column in feature_order:
            values[regime_column] = float(regime_global)
        series.append(values)

    # `feature_order` contains derived (rolling) names that are not keys of `series`,
    # so index only the columns that exist; the rest are computed in the loop below.
    columns = {
        name: [r[name] for r in series]
        for name in feature_order
        if name in series[0]
    }
    matrix: list[list[float]] = []
    for i in range(len(series)):
        row_out: list[float] = []
        for name in feature_order:
            if "_roll" in name:
                sensor, rest = name.split("_roll", 1)
                is_mean = rest.startswith("mean")
                start = max(0, i - ROLLING_WINDOW + 1)
                chunk = columns[sensor][start : i + 1]
                if is_mean:
                    row_out.append(sum(chunk) / len(chunk))
                else:
                    if len(chunk) < 2:
                        row_out.append(0.0)   # ddof=0 at the first cycle, as in training
                    else:
                        m = sum(chunk) / len(chunk)
                        var = sum((v - m) ** 2 for v in chunk) / len(chunk)
                        row_out.append(var ** 0.5)
            else:
                row_out.append(columns[name][i])
        matrix.append(row_out)

    # guards the silent-misprediction failure mode
    assert len(matrix[0]) == len(feature_order), (
        f"built {len(matrix[0])} columns, contract declares {len(feature_order)}"
    )
    return matrix, imputed


def regime_z_scorer(
    stats: dict[str, Any], regime: int = 0, *,
    subset: str | None = None, eps: float = 1e-6,
    sensors: Sequence[str] | None = None,
) -> Callable[[dict[str, float]], dict[str, float]] | None:
    """Build the per-cycle z-score function for a regime, or None if no baseline.

    Accepts both artifact layouts: the Phase 2 `baselines[regime][sensor]` shape and
    the flatter `regime_<n>` mapping. `sensors` overrides the scored set, which the
    pooled contract needs: it declares 17 base sensors, not the FD001 15.
    """
    baseline = regime_block(stats, regime, subset)
    if not baseline:
        # A z-score model fed raw features does not fail — it predicts confidently
        # from the wrong scale. Returning None is that failure; say so instead.
        raise BusinessRuleError(
            f"no baseline block for subset={subset!r} regime={regime}; the model was "
            f"trained on z-scored inputs and cannot be served without them"
        )
    wanted = tuple(sensors) if sensors is not None else SENSORS

    def _score(values: dict[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for sensor in wanted:
            stat = baseline.get(sensor)
            if not stat:
                # A contract sensor the baselines do not describe. It is constant in
                # this subset by construction (that is why it is missing), so its
                # z-score is 0.0 by definition. Skipping it instead left the RAW median
                # in the matrix -- `update()` only adds keys, it never clears them --
                # and the booster then read 1.08 where it was trained to read 0.0.
                out[sensor] = 0.0
                continue
            value = values.get(sensor)
            if value is None:
                continue          # a missing sensor is skipped, not a KeyError
            median = float(stat["median"])
            mad = float(stat.get("mad", 0.0))
            if mad <= max(abs(median) * MAD_REL_FLOOR, eps):
                # Effectively constant at the precision the artifact stores -- s6 has
                # MAD 0.0 after rounding to 6dp, and dividing by it produced |z| in the
                # thousands, which then dominated every average it entered. Such a
                # sensor carries no deviation information, so it scores 0.0: still
                # present in the output, as the API contract requires, but not able to
                # fabricate a signal out of floating-point noise.
                out[sensor] = 0.0
                continue
            out[sensor] = (float(value) - median) / mad
        return out

    return _score


def regime_block(
    stats: dict[str, Any], regime: int = 0, subset: str | None = None
) -> dict[str, Any]:
    """Find the per-sensor baseline block for `regime`, whatever the nesting.

    Four layouts exist in the wild, and guessing wrong is silent — an empty block reads
    as "no sensor deviates", i.e. every component perfectly healthy:

        Phase 1           {"regime_0": {sensor: {median, mad}}}
        Phase 2 (flat)    {"baselines": {"0": {sensor: ...}}}
        Phase 2 (shipped) {"baselines": {"FD001": {"0": {sensor: ...}}}}   <- subset level
        legacy            {"0": {sensor: ...}}

    Rather than enumerate every shape, walk the document and return the first block that
    actually looks like sensor statistics, preferring the requested subset and regime.
    """
    preferred = [f"regime_{regime}", str(regime), regime]
    if subset:
        preferred = [f"{subset}::{key}" for key in preferred] + preferred

    for key in preferred:
        value = _dig(stats, key)
        if _is_sensor_block(value):
            return dict(value)

    # subset level: baselines[subset][regime]
    baselines = stats.get("baselines")
    if isinstance(baselines, dict):
        names = list(baselines)
        subset_level = bool(names) and all(
            _is_sensor_block(_dig(baselines.get(n) or {}, str(regime))) for n in names
        )
        if subset is not None and subset_level and subset not in baselines:
            # Requested subset named and absent: return nothing rather than another
            # subset's baseline. An empty block is caught upstream (deviation becomes
            # empty, health is flagged); silently using FD001's medians while the
            # request said FD004 is the failure mode this whole path exists to stop.
            return {}
        for name in ([subset] if subset in baselines else names):
            block = _dig(baselines.get(name) or {}, str(regime))
            if _is_sensor_block(block):
                return dict(block)

    # last resort: bounded depth-first search for anything that looks like a block
    return _find_sensor_block(stats, depth=3) or {}


def _dig(mapping: Any, key: Any) -> Any:
    return mapping.get(key) if isinstance(mapping, dict) else None


def _is_sensor_block(value: Any) -> bool:
    """A baseline block is a dict of sensors that each carry median/mad."""
    if not isinstance(value, dict) or not value:
        return False
    sample = next(iter(value.values()))
    return isinstance(sample, dict) and "median" in sample and "mad" in sample


def _find_sensor_block(node: Any, depth: int) -> dict[str, Any] | None:
    if depth <= 0 or not isinstance(node, dict):
        return None
    if _is_sensor_block(node):
        return node
    for value in node.values():
        if isinstance(value, dict):
            found = _find_sensor_block(value, depth - 1)
            if found:
                return found
    return None


def classify_regime(
    settings_row: dict[str, float], centroids: Any | None
) -> int:
    """Nearest centroid on the operating settings. FD001 has a single regime."""
    if not centroids:
        return 0
    rows: list[Sequence[float]]
    if isinstance(centroids, dict):
        flat: list[Sequence[float]] = []
        for value in centroids.values():
            if isinstance(value, dict):
                flat.append([float(v) for v in value.get("settings", {}).values()])
            else:
                flat.append([float(x) for x in value])
        rows = flat
    else:
        rows = [[float(x) for x in row] for row in centroids]

    if not rows:
        return 0
    width = len(rows[0])
    s = [
        float(settings_row.get(k, 0.0))
        for k in ("setting_1", "setting_2", "setting_3", "op1", "op2", "op3")
    ][:width]
    s += [0.0] * (width - len(s))

    best, best_d = 0, float("inf")
    for i, centroid in enumerate(rows):
        d = sum((s[j] - centroid[j]) ** 2 for j in range(width))
        if d < best_d:
            best, best_d = i, d
    return int(best)


