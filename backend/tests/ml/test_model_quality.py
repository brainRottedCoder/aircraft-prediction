"""ML quality gate (docs/08 §11) and the seed integrity checks (docs/10 Phase 2).

Quality assertions that need the trained artifact are skipped until Phase 0
produces it; everything else runs unconditionally.
"""
from __future__ import annotations

import json
import pathlib

import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.ml import model_store
from app.ml.features import FEATURE_ORDER, SENSORS, contract_sensors
from app.models.alert import Alert
from app.models.auth import User
from app.models.fleet import Aircraft, AircraftPart, Part
from app.models.maintenance import Agency, Spare, WorkOrder
from app.models.reference import AircraftRef, ComponentRef, FlightOpsMonthly

# Artifact paths come from the loader's own resolution, so these tests follow the
# staged variant instead of pinning one. They used to hardcode `data/ml/full/` and
# therefore failed with FileNotFoundError — not a skip — whenever a different variant
# was the one actually staged, and silently skipped when `full` happened to be absent.
_STAGED = next(
    (v for v in sorted(Settings(_env_file=None)._ML_FILES)
     if pathlib.Path(Settings(_env_file=None, ml_variant=v).ml_model_path_resolved).exists()),
    None,
)
_SETTINGS = Settings(_env_file=None, ml_variant=_STAGED) if _STAGED else Settings(
    _env_file=None
)
ARTIFACT = pathlib.Path(_SETTINGS.ml_model_path_resolved)
CONTRACT = pathlib.Path(_SETTINGS.ml_contract_path_resolved)
# the baselines file: the contract names it when it has one
STATS = pathlib.Path(
    _SETTINGS.ml_dir
    / (json.loads(CONTRACT.read_text()).get("regime_baselines_file", "")
       if CONTRACT.exists() else "")
) if CONTRACT.exists() else pathlib.Path(str(_SETTINGS.ml_dir / "none"))
has_artifact = pytest.mark.skipif(
    not ARTIFACT.exists(),
    reason="no ML variant staged: copy models/multi/*_full.json from Drive "
           "(see docs/08 §4 and docs/12 §8)",
)
# derived the same way model_store derives it: xgboost_X.json -> metrics_X.json
METRICS = pathlib.Path(
    ARTIFACT.name.replace("xgboost_", "metrics_") if ARTIFACT.exists() else "metrics_missing"
)


# ── artifact integrity ─────────────────────────────────────────────────────────
@has_artifact
def test_contract_declares_the_trained_feature_vector():
    """`cycle` must be absent — it is a leakage vector. Width is variant-dependent.

    The FD001 contract is 29 columns; a pooled multi-subset contract is wider because
    it keeps sensors that are constant in FD001 but live in FD002/FD004 (`s10`, `s16`)
    and adds `regime_global`. Asserting 29 unconditionally passed only while the FD001
    variant happened to be staged.
    """
    contract = json.loads(CONTRACT.read_text())
    order = contract["feature_order"]
    assert contract["n_features"] == len(order)
    assert "cycle" not in order
    assert len(order) == len(set(order)), "duplicate column name"
    # the contract's own width must be what the builder can actually produce
    implied = (
        len(contract_sensors(order))                     # raw sensor columns
        + sum(1 for c in order if c in ("op1", "op2"))   # settings
        + sum(1 for c in order if c == contract.get("regime_column", "regime_global"))
        + sum(1 for c in order if "_roll" in c)          # derived
    )
    assert implied == contract["n_features"], (
        f"feature_order implies {implied} columns, contract declares "
        f"{contract['n_features']}"
    )
    if contract.get("subsets") == ["FD001"]:
        assert order == list(FEATURE_ORDER)


@has_artifact
def test_contract_excludes_the_identifiers_and_target():
    contract = json.loads(CONTRACT.read_text())
    for column in ("unit", "cycle", "RUL"):
        assert column not in contract["feature_order"]


@has_artifact
def test_metrics_meet_the_gate():
    """Thresholds anchored to the measured model (docs/08 §11)."""
    metrics = json.loads((STATS.parent / METRICS.name).read_text())
    holdout = metrics["cv_rmse"]
    assert metrics["rows_trained"] > 0 and metrics["engines_trained"] > 0
    assert metrics["has_holdout"] is False
    assert metrics["selection"]["official_test_used_for_selection"] is False
    assert 0 < holdout["mean"] <= 21.0          # CV RMSE, 5-fold grouped


def handle_base_sensors() -> set[str]:
    """Raw sensor columns the staged contract declares."""
    contract = json.loads(CONTRACT.read_text())
    return set(contract_sensors(contract["feature_order"]))


def _measured_sensors(subset: str) -> set[str]:
    """Sensors the raw telemetry of `subset` actually carries on cycle 1.

    Read from the data rather than hardcoded, so it tracks the file that is staged.
    """
    from app.seed.cmapss import _read_train

    path = _SETTINGS.cmapss_file(subset, "train")
    if not path.exists():
        return set(SENSORS)
    units = _read_train(path, subset)
    first = next(iter(units.values()), {})
    row = first.get(1) or (next(iter(first.values())) if first else {})
    return set((row or {}).get("sensors") or SENSORS)


@has_artifact
def test_model_store_loads_and_reports_metrics():
    handle = model_store.load()
    assert handle.ready
    assert handle.version
    assert handle.mae is not None
    # width is variant-dependent: 29 for FD001, 32 for the pooled candidate. What
    # must hold is that the booster and the contract agree.
    assert handle.booster.num_features() == handle.n_features
    assert len(handle.feature_order) == handle.n_features


@has_artifact
def test_baseline_stats_cover_every_sensor():
    payload = json.loads(STATS.read_text())
    # use the same lookup the service uses, so a layout change cannot pass here and
    # fail at runtime (the shipped file nests baselines[subset][regime])
    from app.ml.features import regime_block

    block = regime_block(payload, 0, _SETTINGS.replay_subset)
    # Every sensor the replayed subset actually measures must have a baseline. The
    # contract may name more (the pooled one adds s10/s16, constant in FD001), and those
    # are legitimately absent from FD001's block -- asserting the two sets are equal
    # would demand a baseline for a sensor FD001 never measures.
    measured = set(_measured_sensors(_SETTINGS.replay_subset))
    contract_s = handle_base_sensors()
    assert set(block) == measured & contract_s, sorted((measured & contract_s) ^ set(block))
    assert set(block) >= set(SENSORS), sorted(set(SENSORS) - set(block))
    assert all("median" in v and "mad" in v for v in block.values())
    # and anything the contract wants but this subset does not measure must be absent,
    # not zero-filled, so the omission stays visible instead of being papered over
    absent = contract_s - measured
    assert not (set(block) & absent), sorted(set(block) & absent)


@has_artifact
def test_deviation_scores_are_produced_for_every_sensor(client, auth):
    from tests.integration.test_telemetry_ml import _predict_window

    body = client.post("/api/v1/internal/ml/predict", headers=auth("officer"),
                       json={"window": _predict_window(), "persist": False}).json()
    assert set(body["deviation"]) == set(SENSORS)
    assert body["model"]["fallback"] is False


@has_artifact
def test_rul_never_leaves_the_cap(client, auth):
    from tests.integration.test_telemetry_ml import _predict_window

    for start in (1, 50, 100):
        window = _predict_window()
        for row in window:
            row["cycle"] += start
        body = client.post("/api/v1/internal/ml/predict", headers=auth("officer"),
                           json={"window": window, "persist": False}).json()
        assert 0 <= body["rul"] <= 125


@has_artifact
def test_predictions_are_deterministic(client, auth):
    from tests.integration.test_telemetry_ml import _predict_window

    results = [
        client.post("/api/v1/internal/ml/predict", headers=auth("officer"),
                    json={"window": _predict_window(), "persist": False}).json()
        for _ in range(10)
    ]
    for result in results:
        result.pop("latency_ms")
    assert all(result == results[0] for result in results)


# ── seed integrity (docs/10 Phase 2 exit criteria) ─────────────────────────────
def test_reference_data_row_counts_match_the_csvs(db):
    assert db.scalar(select(func.count()).select_from(AircraftRef)) == 100
    assert db.scalar(select(func.count()).select_from(ComponentRef)) == 600
    assert db.scalar(select(func.count()).select_from(FlightOpsMonthly)) == 3300
    # count only the seeded agencies, so a test that provisions its own cannot break this
    assert db.scalar(
        select(func.count()).select_from(Agency).where(
            Agency.agency_ref_id.in_([f"MA00{i}" for i in range(1, 7)])
        )
    ) == 6
    assert db.scalar(select(func.count()).select_from(Spare)) == 40


def test_fleet_is_eight_aircraft_bound_to_distinct_engine_units(db):
    aircraft = list(db.scalars(select(Aircraft).order_by(Aircraft.code)))
    assert len(aircraft) == 8
    assert [a.code for a in aircraft] == [f"Fighter-0{i}" for i in range(1, 9)]
    assert len({a.cmapss_unit_id for a in aircraft}) == 8


def test_demo_offsets_stagger_the_fleet(db):
    """Rule of thumb from docs/09 §1.3: aircraft must not fail in lockstep."""
    offsets = [a.demo_offset for a in db.scalars(select(Aircraft))]
    assert len(set(offsets)) >= 5
    assert max(offsets) >= 120


def test_every_aircraft_has_exactly_five_parts(db):
    counts = dict(db.execute(
        select(AircraftPart.aircraft_id, func.count())
        .group_by(AircraftPart.aircraft_id)
    ).all())
    assert len(counts) == 8
    assert set(counts.values()) == {5}


def test_only_the_engine_is_not_simulated(db):
    for part in db.scalars(select(Part)):
        assert part.is_simulated == (part.code != "engine")
        assert part.method == ("rul_xgb_fd001" if part.code == "engine"
                               else "maintenance_burden_v1")


def test_part_codes_are_the_five_specified(db):
    assert {p.code for p in db.scalars(select(Part))} == {
        "engine", "radar", "gear", "hyd", "fuel"
    }
    assert {p.sort_order for p in db.scalars(select(Part))} == {1, 2, 3, 4, 5}


def test_health_is_within_range_everywhere(db):
    lo, hi = db.execute(
        select(func.min(AircraftPart.health), func.max(AircraftPart.health))
    ).one()
    assert 0.0 <= lo <= hi <= 1.0


def test_rul_is_within_the_cap_everywhere(db):
    lo, hi = db.execute(
        select(func.min(Aircraft.rul), func.max(Aircraft.rul))
    ).one()
    assert 0 <= lo <= hi <= 125


def test_three_demo_users_exist_with_the_right_roles(db):
    roles = {u.username: u.role.value for u in db.scalars(select(User))}
    assert roles == {"commander": "commander",
                     "officer": "maintenance_officer",
                     "viewer": "viewer"}


def test_passwords_are_bcrypt_hashed(db):
    for user in db.scalars(select(User)):
        assert user.password_hash.startswith("$2b$")
        assert "commander123" not in user.password_hash


def test_alerts_only_exist_for_non_healthy_parts(db):
    for alert in db.scalars(select(Alert)):
        assert alert.level in ("watch", "critical")


def test_seed_is_idempotent(db):
    """Re-running the loaders must not duplicate or drop rows."""
    from app.seed.run import run

    assert run() == {"status": 0}
    assert db.scalar(select(func.count()).select_from(AircraftRef)) == 100
    assert db.scalar(select(func.count()).select_from(Aircraft)) == 8
    assert db.scalar(select(func.count()).select_from(Spare)) == 40


def test_work_orders_never_violate_the_partial_unique_index(db):
    """uq_wo_open_aircraft_part: one open order per aircraft+part."""
    rows = db.execute(
        select(WorkOrder.aircraft_id, WorkOrder.part_id, func.count())
        .where(WorkOrder.status != "done")
        .group_by(WorkOrder.aircraft_id, WorkOrder.part_id)
        .having(func.count() > 1)
    ).all()
    assert rows == []
