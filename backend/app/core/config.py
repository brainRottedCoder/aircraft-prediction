"""Application settings. Every env var is prefixed FDT_ (docs/02 §6.1)."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, ClassVar

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Committed placeholder. `docker compose` makes the real value mandatory and
# `_reject_insecure_production` refuses to start with it in production.
DEV_JWT_SECRET = "dev-only-insecure-secret"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FDT_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Fleet Digital Twin API"
    # "development" | "staging" | "production"
    environment: str = "development"
    log_level: str = "INFO"

    # storage
    database_url: str = "postgresql+psycopg://fdt:fdt@localhost:5432/fdt"
    db_echo: bool = False
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # auth
    # The default is a development placeholder and the app refuses to boot on it
    # outside development — see `_validate` below. Never ship a real secret here.
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_ttl_minutes: int = 720

    # http
    # NoDecode: a comma-separated env string must not be JSON-parsed first
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    # demo / realtime
    demo_mode: bool = True
    demo_tick_seconds: float = 1.2
    ws_queue_size: int = 256

    # storage retention
    # The replay engine appends four rows per aircraft per tick and nothing else ever
    # deletes them. Measured against Postgres, that is ~5.4 KB per aircraft-tick
    # (`ml_prediction`'s two JSONB columns dominate) — ~36 KB/s across the seeded fleet,
    # or ~93 GB a month. See services/retention.py.
    #
    # 1 h is ~141 MB steady state on the 1 GB plan this deploys to, and still ~17x the
    # ~3.5 min wall-clock lifetime of one engine's cycle range, so the history chart sees
    # no gaps. Raising it is linear: 3 h would already be ~400 MB, 40% of the disk.
    #
    # 0 disables the pruner — correct on a developer machine, a disk-exhaustion bug on a
    # small managed instance.
    telemetry_retention_hours: float = 1.0
    retention_interval_seconds: float = 300.0

    # Seed the fleet on boot when the database is empty. For deployments where nobody
    # can shell in and run `python -m app.seed.run`. No-op once any aircraft exists, so
    # it costs one cheap COUNT on every start rather than a full re-seed.
    seed_on_boot: bool = False

    # domain
    rul_cap: int = 125

    # ml — artifacts as produced by Model_training_249.ipynb (docs/08 §4).
    # ml_variant selects which model generation to serve:
    #   "all"      pooled over FD001-FD004, 32 features (the 29 plus regime_global),
    #              z-scored per regime against four subsets of baselines
    #   "holdout"  the 80/20 model, StandardScaler preprocessing (Phase 1)
    #   "full"     the FD001-only refit, per-regime z-score preprocessing (Phase 2)
    #
    # "all" is the default because it is the one we actually stage and serve: "full"
    # and "holdout" describe directories nothing writes any more, so falling back to
    # either resolved to a missing path and answered from rul = 125 - cycle.
    ml_variant: str = "all"
    ml_dataset: str = "FD001"
    # None  -> derive the filename from ml_variant (the normal case)
    # ""    -> explicitly no such artifact (e.g. the z-score variant ships no scaler)
    ml_model_path: str | None = None
    ml_contract_path: str | None = None
    ml_scaler_path: str | None = None
    ml_stats_path: str | None = None
    ml_fallback: bool = True
    ml_window: int = 30
    s6_median_fd001: float = 21.6098

    # paths
    # Resolved relative to the process CWD; the container sets WORKDIR /app and the
    # Dockerfile asserts the directory exists, so a misconfigured mount fails the
    # build instead of silently degrading the replay engine to an empty dataset.
    data_dir: Path = Path("data")

    # The built Vite bundle, served by this process when there is no nginx in front of
    # it (see the `_mount_frontend` note in app/main.py). Resolved relative to the
    # process CWD, exactly like `data_dir` above, so on a single-service deployment it
    # must be set to an absolute path — the process runs from `backend/` to satisfy
    # alembic's relative `script_location`, where `frontend/dist` does not resolve.
    # Unset or missing means "serve the API only"; it is never an error.
    web_dist: Path = Path("frontend/dist")

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_database_url(cls, v: object) -> object:
        """Force the SQLAlchemy 2 driver onto any PostgreSQL URL.

        Hosted providers hand out a bare libpq URL — Render, Aiven, Neon, Supabase and
        Heroku all issue `postgres://…` — while SQLAlchemy 2 will not load `psycopg2` for
        that scheme unless it happens to be installed, and this project pins `psycopg`
        (v3). The failure is a bare `ModuleNotFoundError` or a `Can't load plugin` at
        startup, which reads as a broken build rather than a copied URL.

        Normalising here means an operator can paste the provider's URL verbatim instead
        of hand-editing the scheme, which is a step that silently breaks the deploy when
        mistyped. An explicit `postgresql+psycopg://` (what .env.example ships) is
        unchanged, and any other driver the operator chose on purpose is left alone.
        """
        if not isinstance(v, str):
            return v
        for bare in ("postgres://", "postgresql://"):
            if v.startswith(bare):
                return f"postgresql+psycopg://{v[len(bare):]}"
        return v

    @model_validator(mode="after")
    def _reject_insecure_production(self) -> Settings:
        """Refuse to serve real traffic signed with the committed dev secret.

        A default that silently works in production is how a demo repo turns into a
        token-forging incident. `docker compose` already makes FDT_JWT_SECRET mandatory;
        this closes the same hole for a bare `uvicorn`/`make run`.

        Note what is deliberately *not* checked: `demo_mode`. It selects a telemetry source
        and is not a security property. It was previously rejected in production, which
        combined with the replay gating on `demo_mode` to make every combination broken —
        production was a dead twin, and the only working configuration was `staging`. See
        docker-compose.prod.yml and docs/11 §13.4.
        """
        if self.environment == "production" and self.jwt_secret == DEV_JWT_SECRET:
            raise ValueError(
                "FDT_JWT_SECRET still holds the development placeholder while "
                "FDT_ENVIRONMENT=production. Generate one with "
                "`openssl rand -hex 32`."
            )
        return self

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def ml_dir(self) -> Path:
        return self.data_dir / "ml" / self.ml_variant

    # Filenames differ per generation. Both are resolved from ml_dir unless the
    # operator overrides them explicitly.
    # ClassVar: this is a lookup table, not a field. Without it pydantic turns the
    # dict into a private attribute, so `Settings._ML_FILES` stops being iterable
    # and anything deriving a variant list from it breaks at import.
    _ML_FILES: ClassVar[dict[str, tuple[str, str, str, str]]] = {
        # The contract for this variant names its own baselines file
        # (regime_baselines_fd001.json), so no default is guessed here.
        "full": ("xgboost_fd001_full.json", "feature_contract_fd001_full.json",
                 "", ""),
        "holdout": ("xgboost_fd001_rul.json", "feature_contract.json",
                    "scaler_fd001.pkl", "baseline_stats.json"),
        # The pooled candidate: trained on FD001+FD002+FD003+FD004, 30 features
        # (the 29 plus regime_global), z-scored against four subsets of baselines. Its
        # contract names its own baselines file, so none is guessed here.
        "all": ("xgboost_all_full.json", "feature_contract_all_full.json", "", ""),
    }

    def _ml_file(self, index: int, override: str | None) -> str:
        # `None` means "derive from the variant"; an explicit "" means "not supplied",
        # which is different and must be honoured — otherwise a variant whose default
        # is a real file can never be configured to run without it.
        if override is not None:
            return override
        try:
            names = self._ML_FILES[self.ml_variant]
        except KeyError as exc:  # pragma: no cover - guarded by the validator below
            raise ValueError(
                f"unknown ml_variant {self.ml_variant!r}; "
                f"expected one of {sorted(self._ML_FILES)}"
            ) from exc
        name = names[index]
        return str(self.ml_dir / name) if name else ""

    @property
    def ml_model_path_resolved(self) -> str:
        return self._ml_file(0, self.ml_model_path)

    @property
    def ml_contract_path_resolved(self) -> str:
        return self._ml_file(1, self.ml_contract_path)

    @property
    def ml_scaler_path_resolved(self) -> str:
        return self._ml_file(2, self.ml_scaler_path)

    @property
    def ml_stats_path_resolved(self) -> str:
        """Baselines: per-regime for the z-score variant, single regime otherwise."""
        return self._ml_file(3, self.ml_stats_path)

    @property
    def cmapss_dir(self) -> Path:
        return self.data_dir / "cmapss"

    def cmapss_file(self, subset: str, kind: str) -> Path:
        """Path of one raw C-MAPSS file: kind is train | test | RUL."""
        return self.cmapss_dir / f"{kind}_{subset}.txt"

    @property
    def replay_subset(self) -> str:
        """Subset the demo replays. Not the same thing as the model's training breadth.

        A model may be trained on FD001+FD002+FD003+FD004 while every aircraft still
        binds 1:1 to an FD001 engine, so this stays the single subset the fleet reads.
        It is what the baselines are looked up under and what regime ids are assigned in.
        """
        return self.ml_dataset


@lru_cache
def get_settings() -> Settings:
    return Settings()