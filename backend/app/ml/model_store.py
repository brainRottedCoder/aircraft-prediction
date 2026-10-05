"""Loads the XGBoost artifact once at startup (docs/08 §5).

Native XGBoost via Booster.save_model — no ONNX. The manifest is asserted against the
booster so a feature-order mismatch fails loudly at load, not silently at predict time.
"""
from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..core.config import Settings, get_settings
from ..core.errors import ModelUnavailableError

log = logging.getLogger(__name__)


@dataclass
class ModelHandle:
    """A loaded model plus whichever preprocessing variant it was trained with.

    The two shipped variants differ, so the handle carries the discriminator rather
    than the caller guessing (docs/08 §3.2.1):

        requires_scaler=True   Phase 1 FD001 model. Raw features in, StandardScaler
                               applied to the finished matrix by `transform`.
        requires_scaler=False  Phase 2 models. Features are z-scored against the
                               per-regime healthy baseline *before* the rolling
                               statistics are derived, so there is no scaler to apply.
    """

    booster: Any = None
    contract: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    scaler: Any = None
    ready: bool = False
    warm: bool = False
    error: str | None = None
    gain_cache: dict[str, float] = field(default_factory=dict)

    # `manifest` is the historical attribute name for the contract document.
    @property
    def manifest(self) -> dict[str, Any]:
        return self.contract

    @property
    def feature_order(self) -> list[str]:
        order = self.contract.get("feature_order") or []
        return [str(x) for x in order]

    @property
    def n_features(self) -> int:
        return int(self.contract.get("n_features", len(self.feature_order)))

    @property
    def requires_scaler(self) -> bool:
        return bool(self.contract.get("requires_scaler", self.scaler is not None))

    @property
    def version(self) -> str:
        return str(
            self.contract.get("version")
            or self.contract.get("candidate")
            or "fallback"
        )

    @property
    def dataset(self) -> str | None:
        subsets = self.contract.get("subsets")
        if isinstance(subsets, list) and subsets:
            return "+".join(str(s) for s in subsets)
        return self.contract.get("dataset")

    @property
    def mae(self) -> float | None:
        """Headline error for responses.

        The full-data refit has no holdout, so there is no holdout MAE to report; the
        cross-validated RMSE stands in for it. Looking only for a "mae" key left this
        None for exactly the model we serve.
        """
        sources = [self.contract, self.contract.get("metrics") or {},
                   self.contract.get("holdout") or {}]
        for src in sources:
            value = src.get("mae")
            if value is not None:
                return float(value)
        for src in sources:
            cv = src.get("cv_rmse") or {}
            if cv.get("mean") is not None:
                return float(cv["mean"])
        return None

    @property
    def mae_is_cv(self) -> bool:
        """True when `mae` is really a cross-validated RMSE, not a holdout MAE."""
        return not any(
            (src.get("mae") is not None)
            for src in (self.contract, self.contract.get("metrics") or {},
                        self.contract.get("holdout") or {})
        )

    def transform(self, matrix: list[list[float]]) -> Any:
        """Apply the post-window transform the model was trained with.

        No-op for the z-score variant, which was standardised during feature
        construction because the rolling statistics had to be derived from z-scores.
        """
        import numpy as np

        arr = np.asarray(matrix, dtype=np.float32)
        if self.scaler is not None:
            arr = self.scaler.transform(arr)
        return arr

    def predict(self, matrix: list[list[float]]) -> Any:
        """Predict RUL for the last row of an already-transformed matrix.

        Two native-Booster requirements are centralised here because both fail in a way
        that looks like a bad prediction rather than a bad call:

        * `predict` needs a DMatrix, not a bare ndarray;
        * a booster saved from a NAMED frame validates its inputs, so the DMatrix must
          carry `feature_names`. Omitting them raised, and the caller fell back to
          `rul = 125 - cycle` for every request — which is how a fully broken model
          still returned plausible-looking numbers.
        """
        import xgboost as xgb

        arr = self.transform(matrix)
        names = self.feature_order or None
        dmat = xgb.DMatrix(arr, feature_names=names)
        return self.booster.predict(dmat)

    @property
    def subsets(self) -> list[str]:
        raw = self.contract.get("subsets")
        if isinstance(raw, list) and raw:
            return [str(s) for s in raw]
        dataset = self.contract.get("dataset")
        return [str(dataset)] if dataset else []

    @property
    def subset(self) -> str | None:
        """Subset key for multi-subset baselines, when the contract names exactly one.

        A pooled contract names four, so this is None and callers must supply the
        subset themselves. It used to fall back to None implicitly and the baselines
        lookup then searched the document, returned FD001's block for every subset,
        and every prediction was scored against the wrong healthy baseline.
        """
        subsets = self.subsets
        return subsets[0] if len(subsets) == 1 else None

    @property
    def regime_column(self) -> str:
        return str(self.contract.get("regime_column") or "regime_global")

    @property
    def base_sensors(self) -> tuple[str, ...]:
        """Base sensors this contract needs, from its own feature_order.

        The pooled contract declares 17 (the FD001 15 plus `s10` and `s16`, which are
        constant in FD001 but live in the other subsets). Reading it off the contract
        rather than off a module constant is what keeps the matrix aligned with the
        booster.
        """
        from .features import contract_sensors

        return contract_sensors(self.feature_order) if self.feature_order else ()

    def regime_offset(self, subset: str | None) -> int:
        """Global-id offset for `subset`, from the baselines artifact.

        Regime ids in a pooled model are namespaced so subset A's regime 3 is not
        subset B's regime 3. Without this the classifier's local id would be fed to a
        booster trained on offset ids.
        """
        offsets = (self.stats or {}).get("offsets")
        if not isinstance(offsets, dict):
            return 0
        if subset in offsets:
            return int(offsets[subset])
        return 0

    def centroids_for(self, subset: str | None) -> Any:
        """Regime centroids for one subset, from the baselines artifact."""
        centroids = (self.stats or {}).get("centroids")
        if isinstance(centroids, dict):
            if subset in centroids:
                return centroids[subset]
            return None
        return centroids

    def z_scorer(
        self, regime: int = 0, subset: str | None = None
    ) -> Callable[[dict[str, float]], dict[str, float]] | None:
        """Per-cycle z-score function for `regime`, or None for the scaler variant."""
        from .features import regime_z_scorer

        if self.requires_scaler:
            return None
        return regime_z_scorer(self.stats, regime, subset=subset or self.subset,
                               sensors=self.base_sensors)

    def missing_sensor_defaults(
        self, subset: str | None = None
    ) -> dict[str, float]:
        """Healthy median for each contract sensor absent from `subset`'s baseline.

        `s10` and `s16` are constant in FD001, so FD001's baseline block omits them —
        but FD002/FD004 carry medians for them. Those are the values the pooled booster
        was trained to see, and feeding them yields a z-score of exactly 0.0.

        Taking one median across all sensors instead would put ~554 into a column whose
        true value is 1.08: positionally valid, numerically nonsense, and it would only
        surface as a quietly wrong RUL. Anything with no median in any subset falls back
        to 0.0 and is zeroed by the MAD floor anyway.
        """
        from .features import regime_block

        own = regime_block(self.stats, 0, subset or self.subset) or {}
        wanted = [s for s in self.base_sensors if s not in own]
        if not wanted:
            return {}
        out: dict[str, float] = {}
        for sensor in wanted:
            for candidate in (subset or self.subset, *(self.subsets or ())):
                if not candidate:
                    continue
                block = regime_block(self.stats, 0, candidate)
                stat = (block or {}).get(sensor)
                if stat and stat.get("median") is not None:
                    out[sensor] = float(stat["median"])
                    break
            else:
                # No subset describes it. 0.0 is the only honest value, and the
                # z-scorer zeroes it regardless through the MAD floor.
                out[sensor] = 0.0
        return out

    @property
    def degraded(self) -> bool:
        """True when we are answering from the deterministic fallback."""
        return not self.ready

    def describe(self) -> dict[str, Any]:
        return {
            "loaded": self.ready,
            "degraded": self.degraded,
            "version": self.version,
            "dataset": self.dataset,
            "mae": self.mae,
            "fallback": not self.ready,
            **({"error": self.error} if self.error else {}),
        }


_handle = ModelHandle()
_lock = threading.Lock()


def handle() -> ModelHandle:
    return _handle


def load(settings: Settings | None = None, *, force: bool = False) -> ModelHandle:
    """Idempotent, thread-safe, called from the FastAPI lifespan.

    `force=True` re-reads the artifact set from disk even when a model is already
    loaded, which is the only way to pick up a newly staged booster without
    restarting the container. The artifact directory is a bind mount, so files
    appear the moment they are copied in — but nothing watched for them, and the
    app kept answering from `rul = 125 - cycle` with no signal that it had.
    """
    global _handle
    s = settings or get_settings()
    with _lock:
        if _handle.ready and not force:
            return _handle
        previous = _handle
        try:
            import xgboost as xgb  # imported lazily so the app boots without it

            _warn_on_split_artifact_set(s)
            booster = xgb.Booster()
            booster.load_model(Path(s.ml_model_path_resolved))
            contract = _read_json(s.ml_contract_path_resolved)
            # The CONTRACT names its own baselines file; the config guess is only a
            # fallback. Guessing here silently pointed at a filename the notebook never
            # writes, which made the whole model unloadable.
            stats_path = _resolve_stats_path(s, contract)
            stats = _read_json(stats_path) if stats_path else {}

            declared = int(contract.get("n_features", len(contract.get("feature_order", []))))
            if booster.num_features() != declared:
                raise ModelUnavailableError(
                    f"model has {booster.num_features()} features, "
                    f"contract declares {declared}"
                )

            scaler = _load_scaler(s)
            if scaler is not None and not contract.get("requires_scaler", True):
                log.info("contract says requires_scaler=false but a scaler was supplied; "
                         "ignoring the scaler")
                scaler = None

            # metrics live beside the model, not inside the contract; fold the headline
            # numbers in so responses can report them
            # xgboost_fd001_full.json sits beside metrics_fd001_full.json
            metrics_name = contract.get("metrics_file") or Path(
                s.ml_model_path_resolved
            ).name.replace("xgboost_", "metrics_")
            metrics_path = Path(s.ml_contract_path_resolved).parent / metrics_name
            if metrics_path.exists():
                contract = {**contract, "metrics": _read_json(metrics_path)}

            _handle = ModelHandle(
                booster=booster, contract=contract, stats=stats,
                scaler=scaler, ready=True,
            )
            log.info(
                "ML model loaded: version=%s features=%d requires_scaler=%s",
                _handle.version, declared, _handle.requires_scaler,
            )
        except Exception as exc:  # noqa: BLE001 — degrade, never crash the app
            reason = str(exc).strip().splitlines()[0][:200]
            if previous.ready:
                # A forced reload failed but we are already serving a working model.
                # Swapping in the fallback here would turn a bad deploy of a new
                # artifact into a live outage, so keep the booster and report the
                # failure through /healthz instead.
                log.error("ML reload failed (%s); still serving the previously "
                          "loaded model", reason)
                _handle = replace(previous, error=reason)
                return _handle
            log.warning("ML artifact unavailable (%s); running deterministic fallback", reason)
            _handle = ModelHandle(ready=False, error=reason)
            if not s.ml_fallback:
                raise ModelUnavailableError(reason) from exc
        return _handle


def reload(settings: Settings | None = None) -> ModelHandle:
    """Force a fresh read of the artifact set, then warm the new booster.

    Warm on success only: warming a handle that failed to load would run the
    fallback path and report a warm model that was never exercised.
    """
    h = load(settings, force=True)
    if h.ready:
        warmup(h)
    return h


def warmup(h: ModelHandle | None = None) -> None:
    """Run one inference so the first real request is not the slow one."""
    from .inference import predict

    h = h or handle()
    if not h.ready:
        return
    try:
        predict([{"cycle": 1 + i, "settings": {"setting_1": 0.0, "setting_2": 0.0,
                                               "setting_3": 100.0},
                  "sensors": {}} for i in range(30)])
        h.warm = True
    except Exception as exc:  # noqa: BLE001
        log.warning("warmup failed: %s", exc)


def _warn_on_split_artifact_set(settings: Settings) -> None:
    """Complain when the model and its contract come from different directories.

    `ml_variant` resolves model + contract + baselines as a set, but any of the three
    can be overridden individually. A stale FDT_ML_MODEL_PATH therefore produced a
    model path pointing at a file that did not exist while the contract still resolved
    to the staged full-data artifact — the loader failed, the app fell back to
    rul = 125 - cycle, and nothing in the logs said the config was half-overridden.
    The degradation was correct behaviour on a broken configuration, which is exactly
    why it needed to be visible.
    """
    model = Path(settings.ml_model_path_resolved)
    contract = Path(settings.ml_contract_path_resolved)
    if not contract.name:
        return
    if model.parent != contract.parent:
        log.error(
            "ML artifact set is split across directories: model=%s contract=%s. "
            "Both are normally derived from FDT_ML_VARIANT=%r; an explicit "
            "FDT_ML_MODEL_PATH overrides only one of them. Unset it.",
            model, contract, settings.ml_variant,
        )
    if not model.exists():
        log.error(
            "ML model artifact missing: %s (variant=%r). Stage it with "
            "`python -m scripts.stage_ml_artifacts --variant %s`, or the API will "
            "answer every request from the deterministic fallback.",
            model, settings.ml_variant, settings.ml_variant,
        )


def _resolve_stats_path(settings: Settings, contract: dict[str, Any]) -> str:
    """Explicit override > filename named by the contract > variant default."""
    if settings.ml_stats_path is not None:
        return settings.ml_stats_path
    named = contract.get("regime_baselines_file") or contract.get("stats_file")
    if named:
        return str(Path(settings.ml_contract_path_resolved).parent / str(named))
    return settings.ml_stats_path_resolved


def _load_scaler(settings: Settings) -> Any:
    """Pickle-load the StandardScaler, if the configured variant ships one."""
    import pickle

    path = getattr(settings, "ml_scaler_path_resolved", "") or ""
    if not path or not Path(path).exists():
        return None
    with open(path, "rb") as fh:
        blob = pickle.load(fh)
    if isinstance(blob, dict):          # {"scaler": ..., "features": [...]}
        return blob.get("scaler")
    return blob


def _read_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise ModelUnavailableError(f"missing artifact {p}")
    data: dict[str, Any] = json.loads(p.read_text())
    return data


def gain_scores(h: ModelHandle | None = None) -> dict[str, float]:
    """Global feature gain, cached at load — feeds top_sensors attribution.

    A Booster trained from a bare matrix has no feature names, so `get_score` returns
    positional keys `f0`, `f1`, ... Mapping them back through the contract is what makes
    attribution addressable by sensor name; without it `top_sensors` silently comes back
    empty rather than raising.
    """
    handle_ = h or handle()
    if not handle_.ready:
        return {}
    if not handle_.gain_cache:
        order = handle_.feature_order
        raw: dict[str, Any] = handle_.booster.get_score(importance_type="gain")
        cache: dict[str, float] = {}
        for key, value in raw.items():
            name = str(key)
            if name.startswith("f") and name[1:].isdigit():
                index = int(name[1:])
                name = order[index] if index < len(order) else name
            cache[name] = float(value)
        handle_.gain_cache = cache
    return handle_.gain_cache