"""The feature builder must satisfy the trained model's contract (docs/08 §3.2).

These tests run without the artifact: they pin the 29-column contract and the
preprocessing variants, which is what the artifact-dependent tests in
tests/ml/test_model_quality.py check the real model against.
"""
from __future__ import annotations

import pytest

from app.core.errors import BusinessRuleError
from app.ml.features import (
    FEATURE_ORDER,
    MAX_WINDOW,
    MIN_WINDOW,
    ROLLING_SENSORS,
    SENSORS,
    build_window,
    classify_regime,
    contract_sensors,
    regime_z_scorer,
)

S6_MEDIAN = 21.61


def row(cycle: int, **overrides):
    sensors = {
        "s2": 642.7, "s3": 1590.5, "s4": 1400.0 + cycle, "s6": 21.6,
        "s7": 1525.0, "s8": 2538.0, "s9": 2455.0, "s11": 521.9,
        "s12": 1.88, "s13": 2538.0, "s14": 11.72, "s15": 522.2,
        "s17": 641.2, "s20": 542.7, "s21": 2388.0,
    }
    sensors.update(overrides.pop("sensors", {}))
    return {
        "cycle": cycle,
        "settings": {"setting_1": 0.0, "setting_2": 0.0, "setting_3": 0.0},
        "sensors": sensors,
        **overrides,
    }


# ── the contract itself ───────────────────────────────────────────────────────
def test_contract_is_twenty_nine_features():
    assert len(FEATURE_ORDER) == 29
    assert len(FEATURE_ORDER) == len(set(FEATURE_ORDER)), "duplicate column name"


def test_contract_excludes_cycle():
    """cycle correlates with RUL only because the test set is truncated early."""
    assert "cycle" not in FEATURE_ORDER
    assert not any(c.startswith("cycle") for c in FEATURE_ORDER)


def test_contract_starts_with_settings_then_sensors():
    assert FEATURE_ORDER[:2] == ("op1", "op2")
    assert list(FEATURE_ORDER[2:17]) == list(SENSORS)
    assert len(SENSORS) == 15 and "s6" in SENSORS


def test_contract_carries_rolling_mean_and_std_for_every_rolling_sensor():
    for sensor in ROLLING_SENSORS:
        assert f"{sensor}_rollmean5" in FEATURE_ORDER
        assert f"{sensor}_rollstd5" in FEATURE_ORDER


def test_rolling_columns_come_last_and_in_a_stable_order():
    rolling = [c for c in FEATURE_ORDER if "_roll" in c]
    assert len(rolling) == 12
    assert FEATURE_ORDER[-12:] == tuple(rolling)


# ── matrix construction ───────────────────────────────────────────────────────
def test_matrix_shape_matches_the_contract():
    matrix, _ = build_window([row(c) for c in range(1, 31)], s6_median=S6_MEDIAN)
    assert len(matrix) == 30
    assert all(len(r) == len(FEATURE_ORDER) for r in matrix)


def test_matrix_columns_follow_the_contract_order():
    matrix, _ = build_window([row(c) for c in range(1, 31)], s6_median=S6_MEDIAN)
    s4_index = FEATURE_ORDER.index("s4")
    roll_index = FEATURE_ORDER.index("s4_rollmean5")
    assert matrix[-1][s4_index] == pytest.approx(row(30)["sensors"]["s4"])
    assert matrix[-1][roll_index] == pytest.approx(
        sum(row(c)["sensors"]["s4"] for c in range(26, 31)) / 5
    )


def test_settings_come_from_the_settings_block():
    rows = [row(c) for c in range(1, 7)]
    for r in rows:
        r["settings"] = {"setting_1": 0.4, "setting_2": 0.62, "setting_3": 0.9}
    matrix, _ = build_window(rows, s6_median=S6_MEDIAN)
    assert matrix[0][0] == pytest.approx(0.4)
    assert matrix[0][1] == pytest.approx(0.62)


def test_rolling_mean_is_a_trailing_average():
    rows = [row(c) for c in range(1, 7)]
    matrix, _ = build_window(rows, s6_median=S6_MEDIAN)
    idx = FEATURE_ORDER.index("s9_rollmean5")
    assert matrix[-1][idx] == pytest.approx(
        sum(r["sensors"]["s9"] for r in rows[-5:]) / 5
    )


def test_rolling_std_is_zero_at_the_first_cycle():
    """ddof=0 during training, so the first cycle must have std 0 and not NaN."""
    rows = [row(c, sensors={"s9": 2455.0 + 3 * c}) for c in range(1, 7)]
    matrix, _ = build_window(rows, s6_median=S6_MEDIAN)
    idx = FEATURE_ORDER.index("s9_rollstd5")
    assert matrix[0][idx] == 0.0            # single sample -> variance 0
    assert matrix[-1][idx] > 0.0
    # ddof=0 over the trailing 5 values: deviations -6,-3,0,3,6 -> var 18
    assert matrix[-1][idx] == pytest.approx(18.0 ** 0.5, rel=1e-6)


def test_absolute_cycles_do_not_reach_the_matrix():
    """Consequence of excluding `cycle`: absolute cycle number is provenance only.

    It is still validated and threaded through, and the rules engine uses it for
    do-by-cycle, but it is not a model input — that was the leakage vector.
    """
    rows = [row(i) for i in range(1, 7)]
    with_cycles, _ = build_window(
        rows, s6_median=S6_MEDIAN, absolute_cycles=[195, 196, 197, 198, 199, 200],
    )
    without, _ = build_window(rows, s6_median=S6_MEDIAN)
    assert with_cycles == without
    assert len(with_cycles[0]) == 29
    assert not any(abs(c - 200.0) < 1e-6 for c in with_cycles[-1])


def test_window_is_truncated_to_thirty():
    matrix, _ = build_window([row(c) for c in range(1, 51)], s6_median=S6_MEDIAN)
    assert len(matrix) == MAX_WINDOW


@pytest.mark.parametrize("size", [0, 1, MIN_WINDOW - 1])
def test_short_window_is_rejected(size):
    with pytest.raises(BusinessRuleError):
        build_window([row(c) for c in range(1, size + 1)], s6_median=S6_MEDIAN)


def test_missing_s6_is_imputed_and_flagged():
    rows = [row(c) for c in range(1, 7)]
    for r in rows:
        r["sensors"].pop("s6")
    matrix, imputed = build_window(rows, s6_median=S6_MEDIAN)
    assert imputed is True
    assert matrix[0][FEATURE_ORDER.index("s6")] == pytest.approx(S6_MEDIAN)


def test_present_s6_is_not_flagged():
    _, imputed = build_window([row(c) for c in range(1, 7)], s6_median=S6_MEDIAN)
    assert imputed is False


# ── preprocessing variants ───────────────────────────────────────────────────
def test_raw_path_leaves_sensor_values_untouched():
    matrix, _ = build_window([row(c) for c in range(1, 7)], s6_median=S6_MEDIAN)
    assert matrix[0][FEATURE_ORDER.index("s4")] == pytest.approx(1401.0)


def test_z_scorer_standardises_before_the_rolling_statistics():
    """The z-score variant must z-score first: rolling z != z of rolling."""
    stats = {"regime_0": {s: {"median": 100.0, "mad": 10.0} for s in SENSORS}}
    scorer = regime_z_scorer(stats, 0)
    assert scorer is not None
    rows = [row(c) for c in range(1, 7)]

    raw, _ = build_window(rows, s6_median=S6_MEDIAN)
    zscored, _ = build_window(rows, s6_median=S6_MEDIAN, sensors_to_z=scorer)

    assert zscored[0][FEATURE_ORDER.index("s4")] == pytest.approx(
        (1401.0 - 100.0) / 10.0
    )
    roll = FEATURE_ORDER.index("s4_rollmean5")
    # window is cycles 1..6, so the trailing 5 are cycles 2..6
    expected = sum((1400.0 + c - 100.0) / 10.0 for c in range(2, 7)) / 5
    assert zscored[-1][roll] == pytest.approx(expected)
    assert zscored[-1][roll] != pytest.approx(raw[-1][roll])


def test_z_scorer_guards_a_zero_mad():
    stats = {"regime_0": {s: {"median": 5.0, "mad": 0.0} for s in SENSORS}}
    scorer = regime_z_scorer(stats, 0)
    out = scorer(dict.fromkeys(SENSORS, 5.0))
    assert all(v == 0.0 for v in out.values())   # finite, not a ZeroDivisionError


def test_z_scorer_refuses_to_build_without_a_baseline():
    """Raw features into a z-score model predict confidently from the wrong scale.

    Returning None here used to mean "no z-scoring", i.e. silently feeding raw sensors
    to a booster trained on z-scores. Raising is the only safe answer.
    """
    with pytest.raises(BusinessRuleError):
        regime_z_scorer({}, 0)


def test_z_scorer_reads_the_phase_two_baseline_layout():
    """Phase 2 nests baselines under regime ids as strings, not `regime_<n>`."""
    stats = {"baselines": {"0": {s: {"median": 100.0, "mad": 10.0} for s in SENSORS}}}
    scorer = regime_z_scorer(stats, 0)
    assert scorer is not None
    assert scorer(dict.fromkeys(SENSORS, 110.0))["s4"] == pytest.approx(1.0)


# ── regime classification ─────────────────────────────────────────────────────
def test_single_regime_when_there_are_no_centroids():
    assert classify_regime({"setting_1": 0.5}, None) == 0


def test_nearest_centroid_wins():
    centroids = [[0.0, 0.0, 0.0], [0.25, 0.62, 0.92], [1.0, 0.62, 0.25]]
    assert classify_regime({"setting_1": 0.25, "setting_2": 0.62,
                            "setting_3": 0.92}, centroids) == 1
    assert classify_regime({"setting_1": 1.0, "setting_2": 0.62,
                            "setting_3": 0.25}, centroids) == 2


# ── multi-subset (pooled) contracts ───────────────────────────────────────────
# A candidate trained on FD001+FD002+FD003+FD004 declares 30 features: the same 29
# plus regime_global. These pin the two ways that goes wrong silently -- the missing
# 30th column, and baselines resolved from the wrong subset.
def test_pooled_contract_adds_the_regime_column():
    order = list(FEATURE_ORDER) + ["regime_global"]
    window = [row(c) for c in range(1, 31)]
    matrix, _ = build_window(window, s6_median=S6_MEDIAN, feature_order=order,
                             regime_global=7)
    assert len(matrix[0]) == 30
    assert [r[-1] for r in matrix] == [7.0] * 30


def test_the_29_feature_contract_ignores_the_regime_column():
    """FD001 contracts must stay 29 wide; the regime column is opt-in via the contract."""
    window = [row(c) for c in range(1, 31)]
    matrix, _ = build_window(window, s6_median=S6_MEDIAN, regime_global=7)
    assert len(matrix[0]) == 29


def test_baselines_resolve_per_subset():
    """The pooled artifact nests baselines[subset][regime]; FD004 must not read FD001."""
    stats = {"baselines": {
        "FD001": {"0": {s: {"median": 100.0, "mad": 10.0} for s in SENSORS}},
        "FD004": {"0": {s: {"median": 1000.0, "mad": 1.0} for s in SENSORS}},
    }}
    values = dict.fromkeys(SENSORS, 110.0)
    assert regime_z_scorer(stats, 0, subset="FD001")(values)["s4"] == pytest.approx(1.0)
    assert regime_z_scorer(stats, 0, subset="FD004")(values)["s4"] == pytest.approx(-890.0)


def test_an_unknown_subset_gets_no_baseline_rather_than_another_subsets():
    """Silently borrowing FD001's medians for an FD004 request is the bug this prevents."""
    stats = {"baselines": {
        "FD001": {"0": {s: {"median": 100.0, "mad": 10.0} for s in SENSORS}},
        "FD004": {"0": {s: {"median": 1000.0, "mad": 1.0} for s in SENSORS}},
    }}
    with pytest.raises(BusinessRuleError):
        regime_z_scorer(stats, 0, subset="FD002")


# ── per-subset sensor sets (the pooled 32-column contract) ─────────────────────
# The pooled candidate declares 32 features: the FD001 29, plus regime_global, plus
# s10 and s16 -- sensors that are constant in FD001 and so are absent from FD001
# telemetry and from FD001's baseline block, yet the booster still expects the columns.
def test_contract_sensors_are_derived_from_the_feature_order():
    order = list(FEATURE_ORDER) + ["regime_global", "s10", "s16"]
    found = contract_sensors(order)
    # 17 raw sensors, and crucially no rolling column leaking in as a raw sensor
    assert len(found) == 17
    assert "s10" in found and "s16" in found
    assert not any("_roll" in s for s in found)
    assert not any(s in found for s in ("op1", "op2", "regime_global"))


def test_contract_sensors_matches_the_fd001_contract():
    assert contract_sensors(FEATURE_ORDER) == SENSORS


def test_a_sensor_absent_from_telemetry_is_filled_not_dropped():
    """Dropping it would shift every later column left by one — silently."""
    order = list(FEATURE_ORDER) + ["s10"]
    window = [{**row(c)} for c in range(1, 31)]
    matrix, imputed = build_window(window, s6_median=S6_MEDIAN, feature_order=order,
                                   missing_sensor_values={"s10": 1.08})
    assert imputed is True
    assert len(matrix[0]) == len(order) == 30
    assert [r[-1] for r in matrix] == [1.08] * 30


def test_z_scorer_zeroes_a_sensor_the_baselines_do_not_describe():
    """It is constant in that subset, so z=0.0 — and 0.0 must REPLACE the raw median.

    `update()` only adds keys, so a skipped sensor kept its raw median and the booster
    read 1.08 where it was trained to read 0.0.
    """
    stats = {"baselines": {"FD001": {"0": {s: {"median": 100.0, "mad": 10.0}
                                          for s in SENSORS}}}}
    scorer = regime_z_scorer(stats, 0, subset="FD001", sensors=(*SENSORS, "s10", "s16"))
    out = scorer({**dict.fromkeys(SENSORS, 110.0), "s10": 1.08, "s16": 0.02})
    assert out["s10"] == 0.0 and out["s16"] == 0.0
    assert out["s4"] == pytest.approx(1.0)
