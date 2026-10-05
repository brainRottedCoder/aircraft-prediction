"""Model-store loading and the two preprocessing variants (docs/08 §3.2.1).

Each test here pins a bug that was live in this module at least once. All three
failed *silently* — the app kept answering, just with wrong values — which is the
failure mode the contract assertion exists to prevent.
"""
from __future__ import annotations

import json
import pickle
from pathlib import Path

import numpy as np
import pytest
import xgboost as xgb

from app.core.config import Settings
from app.ml import model_store
from app.ml.features import FEATURE_ORDER
from app.ml.model_store import ModelHandle


class _StubScaler:
    """Picklable stand-in: the only interface app.ml relies on is .transform()."""

    def __init__(self, value: float = 42.0):
        self.value = value

    def transform(self, x):
        return np.asarray(x) * 0 + self.value


def _booster(tmp_path: Path, name: str = "xgboost_fd001_full.json") -> xgb.Booster:
    """A real booster over the 29-column contract, trained on throwaway data."""
    rng = np.random.default_rng(0)
    x = rng.normal(size=(300, len(FEATURE_ORDER)))
    y = x[:, 0] * 2.0 + rng.normal(scale=0.1, size=300)
    bst = xgb.train(
        {"objective": "reg:squarederror", "max_depth": 4, "eta": 0.3, "seed": 7,
         "tree_method": "hist", "verbosity": 0},
        xgb.DMatrix(x, label=y),
        num_boost_round=8,
    )
    path = tmp_path / name
    bst.save_model(str(path))
    return bst


def _contract(tmp_path: Path, *, requires_scaler: bool) -> Path:
    path = tmp_path / "feature_contract_fd001_full.json"
    path.write_text(json.dumps({
        "candidate": "FD001", "subsets": ["FD001"],
        "model_file": "xgboost_fd001_full.json",
        "feature_order": list(FEATURE_ORDER), "n_features": len(FEATURE_ORDER),
        "requires_scaler": requires_scaler, "rul_cap": 125,
    }))
    return path


def _baselines(tmp_path: Path, layout: str) -> Path:
    block = {s: {"median": 100.0, "mad": 10.0}
             for s in FEATURE_ORDER if "_roll" not in s and s not in ("op1", "op2")}
    payload = ({"healthy_cycles": 20, "regime_0": block} if layout == "phase1"
               else {"healthy_cycles": 20, "baselines": {"0": block}})
    path = tmp_path / "regime_baselines_fd001_full.json"
    path.write_text(json.dumps(payload))
    return path


def _settings(tmp_path: Path, contract: Path, stats: Path | None,
              scaler: Path | None = None) -> Settings:
    return Settings(
        ml_variant="full",
        ml_model_path=str(tmp_path / "xgboost_fd001_full.json"),
        ml_contract_path=str(contract),
        ml_stats_path=str(stats) if stats else "",
        ml_scaler_path=str(scaler) if scaler else "",
    )


@pytest.fixture(autouse=True)
def _reset_handle():
    model_store._handle = ModelHandle(ready=False)
    yield
    model_store._handle = ModelHandle(ready=False)


# ── loading ───────────────────────────────────────────────────────────────────
def test_loads_a_29_feature_contract(tmp_path):
    _booster(tmp_path)
    contract = _contract(tmp_path, requires_scaler=True)
    h = model_store.load(_settings(tmp_path, contract, None))
    assert h.ready is True
    assert h.n_features == 29
    assert h.feature_order == list(FEATURE_ORDER)


def test_load_is_rejected_when_the_contract_disagrees_with_the_model(tmp_path):
    """A feature-count mismatch must fail loudly, never serve wrong numbers."""
    _booster(tmp_path)
    contract = _contract(tmp_path, requires_scaler=True)
    data = json.loads(contract.read_text())
    data["n_features"] = 22                      # the old, obsolete contract
    contract.write_text(json.dumps(data))
    h = model_store.load(_settings(tmp_path, contract, None))
    assert h.ready is False
    assert "22" in (h.error or "")


def test_missing_artifact_degrades_to_fallback(tmp_path):
    h = model_store.load(Settings(ml_model_path=str(tmp_path / "nope.json"),
                                  ml_contract_path=str(tmp_path / "nope.json"),
                                  ml_stats_path=""))
    assert h.ready is False
    assert h.error


# ── picking up an artifact staged after boot ──────────────────────────────────
def test_a_staged_artifact_is_picked_up_without_a_restart(tmp_path):
    """The artifact dir is a bind mount, so files appear without a restart.

    The lifespan calls `load` exactly once, so a booster staged into an already-running
    container was never read and the API went on answering rul = 125 - cycle. `reload`
    is the operation that closes that gap.
    """
    settings = Settings(ml_model_path=str(tmp_path / "xgboost_fd001_full.json"),
                        ml_contract_path=str(tmp_path / "feature_contract_fd001_full.json"),
                        ml_stats_path="")
    assert model_store.load(settings).ready is False       # booted with nothing staged

    _booster(tmp_path)
    _contract(tmp_path, requires_scaler=True)

    h = model_store.reload(settings)
    assert h.ready is True
    assert h.n_features == len(FEATURE_ORDER)


def test_a_failed_reload_keeps_serving_the_model_already_in_memory(tmp_path):
    """A bad deploy must not become an outage by swapping a good booster for the fallback."""
    _booster(tmp_path)
    contract = _contract(tmp_path, requires_scaler=True)
    settings = _settings(tmp_path, contract, None)
    assert model_store.load(settings).ready is True

    (tmp_path / "xgboost_fd001_full.json").unlink()
    h = model_store.reload(settings)

    assert h.ready is True                                   # still answering
    assert h.error and "No such file" in h.error             # and saying why
    assert h.describe()["fallback"] is False


def test_reload_replaces_a_loaded_handle_rather_than_returning_the_cache(tmp_path):
    _booster(tmp_path)
    contract = _contract(tmp_path, requires_scaler=True)
    settings = _settings(tmp_path, contract, None)
    first = model_store.load(settings)

    _booster(tmp_path)                                        # restage, as retraining would
    second = model_store.reload(settings)

    assert second is not first
    assert second.booster is not first.booster


# ── the DMatrix trap ──────────────────────────────────────────────────────────
def test_predict_accepts_a_plain_matrix(tmp_path):
    """A native Booster rejects a bare ndarray. This returned a fallback, not a crash."""
    _booster(tmp_path)
    contract = _contract(tmp_path, requires_scaler=True)
    h = model_store.load(_settings(tmp_path, contract, None))
    out = h.predict([[0.0] * 29, [1.0] * 29])
    assert out is not None and len(out) == 2


# ── gain attribution ──────────────────────────────────────────────────────────
def test_gain_keys_are_sensor_names_not_positional(tmp_path):
    """`get_score` returns f0..f28 for a nameless booster; unaddressable if unmapped."""
    _booster(tmp_path)
    contract = _contract(tmp_path, requires_scaler=True)
    h = model_store.load(_settings(tmp_path, contract, None))
    gain = model_store.gain_scores(h)
    assert gain, "gain must not be empty"
    assert all(not (k.startswith("f") and k[1:].isdigit()) for k in gain)
    assert set(gain) <= set(FEATURE_ORDER)


# ── preprocessing variants ────────────────────────────────────────────────────
def test_scaler_variant_applies_the_scaler(tmp_path):
    _booster(tmp_path)
    contract = _contract(tmp_path, requires_scaler=True)
    scaler = tmp_path / "scaler.pkl"
    with open(scaler, "wb") as fh:
        pickle.dump({"scaler": _StubScaler(42.0), "features": list(FEATURE_ORDER)}, fh)

    h = model_store.load(_settings(tmp_path, contract, None, scaler))
    assert h.requires_scaler is True
    assert np.allclose(h.transform([[1.0] * 29]), 42.0)
    assert h.z_scorer(0) is None          # scaler variant: no in-window z-scoring


def test_zscore_variant_never_applies_a_scaler(tmp_path):
    _booster(tmp_path)
    contract = _contract(tmp_path, requires_scaler=False)
    h = model_store.load(_settings(tmp_path, contract, _baselines(tmp_path, "phase2")))
    assert h.requires_scaler is False
    assert h.transform([[1.0] * 29]).tolist() == [[1.0] * 29]
    scorer = h.z_scorer(0)
    assert scorer is not None, "the z-score variant must score sensors in-window"


def test_a_supplied_scaler_is_ignored_when_the_contract_says_otherwise(tmp_path):
    _booster(tmp_path)
    contract = _contract(tmp_path, requires_scaler=False)
    scaler = tmp_path / "scaler.pkl"
    with open(scaler, "wb") as fh:
        pickle.dump(_StubScaler(-1.0), fh)

    h = model_store.load(_settings(tmp_path, contract, _baselines(tmp_path, "phase2"), scaler))
    assert h.scaler is None
    assert h.requires_scaler is False


def test_both_baseline_layouts_produce_a_z_scorer(tmp_path):
    """Phase 1 nests under regime_<n>; Phase 2 under baselines[<n>]."""
    _booster(tmp_path)
    contract = _contract(tmp_path, requires_scaler=False)
    for layout in ("phase1", "phase2"):
        model_store._handle = ModelHandle(ready=False)
        h = model_store.load(_settings(tmp_path, contract, _baselines(tmp_path, layout)))
        assert h.z_scorer(0) is not None, f"{layout} layout yielded no z-scorer"


# ── settings resolution ───────────────────────────────────────────────────────
# `_env_file=None` matters here: Settings reads the developer's real .env, so a local
# FDT_ML_MODEL_PATH would silently override the variant under test and these
# assertions would fail (or, worse, pass) depending on the machine. These tests are
# about the resolution RULE, so they must not inherit ambient configuration.
@pytest.mark.parametrize("variant,expect_scaler", [("full", False), ("holdout", True)])
def test_variant_resolves_the_right_filenames(variant, expect_scaler):
    s = Settings(_env_file=None, ml_variant=variant)
    assert s.ml_model_path_resolved.endswith(
        "xgboost_fd001_full.json" if variant == "full" else "xgboost_fd001_rul.json")
    assert s.ml_contract_path_resolved.endswith("feature_contract_fd001_full.json"
                                                if variant == "full" else "feature_contract.json")
    assert bool(s.ml_scaler_path_resolved) is expect_scaler


def test_unknown_variant_is_rejected():
    with pytest.raises(ValueError):
        # assigned, not just touched: a bare attribute access is a no-op statement
        # that ruff (correctly) flags, and pytest.raises would pass vacuously if the
        # property were ever removed from this line.
        _ = Settings(_env_file=None, ml_variant="nonsense").ml_model_path_resolved


def test_explicit_empty_override_means_no_artifact():
    """None derives from the variant; "" means "not supplied".

    Conflating the two made it impossible to run a variant without its baselines file,
    which only surfaced once the placeholder artifacts were removed.
    """
    # the shipped contract names its own baselines file, so there is no default to guess
    assert Settings().ml_stats_path_resolved == ""
    assert Settings(ml_stats_path="").ml_stats_path_resolved == ""
    assert Settings(ml_stats_path="/tmp/x.json").ml_stats_path_resolved == "/tmp/x.json"


def test_contract_names_the_stats_file_when_no_override_is_given(tmp_path):
    """Guessing the filename made the real model unloadable; the contract wins."""
    _booster(tmp_path)
    contract = tmp_path / "feature_contract_fd001_full.json"
    contract.write_text(json.dumps({
        "candidate": "FD001", "subsets": ["FD001"], "n_features": len(FEATURE_ORDER),
        "feature_order": list(FEATURE_ORDER), "requires_scaler": False,
        "regime_baselines_file": "regime_baselines_fd001.json",
    }))
    stats = tmp_path / "regime_baselines_fd001.json"      # the name the contract uses
    block = {c: {"median": 100.0, "mad": 10.0}
             for c in FEATURE_ORDER if "_roll" not in c and c not in ("op1", "op2")}
    stats.write_text(json.dumps({"healthy_cycles": 20, "baselines": {"FD001": {"0": block}}}))
    s = Settings(ml_variant="full",
                 ml_model_path=str(tmp_path / "xgboost_fd001_full.json"),
                 ml_contract_path=str(contract))
    assert model_store._resolve_stats_path(s, json.loads(contract.read_text())) == str(stats)
    # and the shipped three-level layout must resolve too
    h = model_store.load(s)
    assert h.ready and h.z_scorer(0) is not None


def test_missing_optional_stats_does_not_block_loading(tmp_path):
    _booster(tmp_path)
    contract = _contract(tmp_path, requires_scaler=True)
    scaler = tmp_path / "scaler.pkl"
    with open(scaler, "wb") as fh:
        pickle.dump(_StubScaler(3.0), fh)
    h = model_store.load(_settings(tmp_path, contract, None, scaler))
    assert h.ready is True


def test_named_booster_receives_feature_names(tmp_path):
    """A booster saved from a named frame rejects unnamed input.

    Without this the DMatrix validation raised on every request and inference silently
    returned the fallback for the whole test set.
    """
    names = list(FEATURE_ORDER)
    x = np.random.default_rng(1).normal(size=(200, len(names)))
    y = x[:, 0] * 2 + np.random.default_rng(2).normal(scale=0.1, size=200)
    bst = xgb.train(
        {"objective": "reg:squarederror", "max_depth": 4, "eta": 0.3, "seed": 7,
         "verbosity": 0},
        xgb.DMatrix(x, label=y, feature_names=names),
        num_boost_round=5,
    )
    path = tmp_path / "xgboost_fd001_full.json"
    bst.save_model(str(path))
    contract = _contract(tmp_path, requires_scaler=True)
    h = model_store.load(_settings(tmp_path, contract, None))
    assert h.ready and bst.feature_names == h.feature_order
    assert h.predict([[0.5] * len(names)]) is not None   # would raise unnamed
