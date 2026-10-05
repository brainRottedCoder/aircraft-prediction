"""Storage retention — the replay engine's write amplification, bounded.

Why this exists
---------------
`replay.py` commits one transaction per aircraft per tick, and each writes an
`engine_telemetry`, `component_health`, `health_snapshot` and `ml_prediction` row
(`write_engine_prediction`), and *nothing else in the app ever deletes them*. `alert` is
naturally bounded by `uq_alert_live`, but these four tables are append-only by design,
which on a developer machine is exactly right and on a managed instance is a
disk-exhaustion bug.

Measured, not estimated: `pg_total_relation_size` over a running instance puts it at
~5.4 KB per aircraft-tick across the four tables — `ml_prediction` dominates at ~3.4 KB
a row, its two JSONB columns plus indexes. At the default 1.2 s tick across the eight
seeded aircraft that is ~36 KB/s, ~3 GB a day, ~93 GB a month.

That is what makes this load-bearing for the free-tier deployment this repo targets
(Render + Aiven free, 1 GB). Because there is no storage cap on that plan, running out
fails as a wall of raised exceptions per tick rather than as a clean degradation, and the
replay loop's `except` keeps it alive while every tick does nothing useful.

Why the horizon is generous
---------------------------
The only reader is `fleet_repo.engine_history`, which asks for the most recent N
*cycles* (not a time range), and one C-MAPSS engine lifetime is ~176 ticks — about
3.5 minutes at the default tick. The 1-hour default therefore retains every cycle an
engine will ever produce, roughly seventeen times over, so the deletion is invisible to
every query in the app. Drop it much below `ml_window`-many ticks (~30 s) and the history
chart would start rendering gaps.

Deleting is safe against the replay writer for a specific reason: all four inserts go
through `on_conflict_do_update` on `(aircraft_id, cycle)`. Removing rows can only
*remove* conflict targets, never introduce one, so an engine that wraps back to a
pruned cycle inserts cleanly instead of updating.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.orm import InstrumentedAttribute

from ..core.config import Settings, get_settings
from ..core.logging import Timer
from ..db.session import get_sessionmaker
from ..models.telemetry import (
    ComponentHealth,
    EngineTelemetry,
    HealthSnapshot,
    MlPrediction,
)

log = logging.getLogger(__name__)

# Floor on the pass interval. A 1 GB Postgres cannot be starved of connections by a
# delete loop, but it also gains nothing from one, and the floor keeps a
# misconfiguration (interval=0.01) from turning this into a busy loop.
MIN_INTERVAL_SECONDS = 1.0

# (model, timestamp column). Data rather than four near-identical functions so that
# adding a table to the replay write set is a one-line change here.
#
# `ml_prediction` is the odd one out: it stamps `created_at`, the other three
# `recorded_at`.
TARGETS: tuple[tuple[Any, InstrumentedAttribute], ...] = (
    (EngineTelemetry, EngineTelemetry.recorded_at),
    (ComponentHealth, ComponentHealth.recorded_at),
    (HealthSnapshot, HealthSnapshot.recorded_at),
    (MlPrediction, MlPrediction.created_at),
)


def prune_once(
    settings: Settings | None = None, *, now: datetime | None = None
) -> dict[str, int]:
    """Delete rows older than the horizon. Returns per-table counts; `{}` if disabled.

    Blocking: call it from a thread. The cutoff is an aware UTC datetime, so it is sent
    as an absolute instant and compared correctly against the `timestamptz` columns
    regardless of the session time zone.
    """
    s = settings or get_settings()
    if s.telemetry_retention_hours <= 0:
        return {}

    cutoff = (now or datetime.now(UTC)) - timedelta(hours=s.telemetry_retention_hours)
    counts: dict[str, int] = {}
    session = get_sessionmaker(s)
    with session() as db:
        for model, column in TARGETS:
            # One set-based DELETE per table. Deleting row-by-row here would be the
            # single most expensive thing the service does.
            result = db.execute(delete(model).where(column < cutoff))
            counts[model.__tablename__] = max(0, result.rowcount or 0)
        db.commit()
    return counts


class RetentionPruner:
    """Periodic `prune_once` on a task, mirroring the replay engine's lifecycle."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.task: asyncio.Task | None = None
        self.passes = 0

    @property
    def enabled(self) -> bool:
        return self.settings.telemetry_retention_hours > 0

    async def start(self) -> None:
        if not self.enabled:
            log.info(
                "retention disabled (telemetry_retention_hours=%s) — the time-series "
                "tables grow without bound",
                self.settings.telemetry_retention_hours,
            )
            return
        if self.task is None:
            self.task = asyncio.create_task(self._loop(), name="retention")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    async def prune_now(self) -> dict[str, int]:
        return await asyncio.to_thread(prune_once, self.settings)

    async def _loop(self) -> None:
        # Sleep first: a pass during startup competes with loading the model, parsing
        # C-MAPSS and the first-boot seed for a 0.1 vCPU instance.
        interval = max(MIN_INTERVAL_SECONDS, self.settings.retention_interval_seconds)
        while True:
            await asyncio.sleep(interval)
            try:
                with Timer() as t:
                    counts = await self.prune_now()
            except Exception:  # noqa: BLE001 — a failed pass must not kill the loop
                log.exception("retention pass failed")
                continue
            self.passes += 1
            log.info(
                "retention pass %d in %sms: %s",
                self.passes, t.ms,
                ", ".join(f"{k}={v}" for k, v in counts.items()) or "nothing to delete",
            )

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self.task is not None,
            "passes": self.passes,
            "retention_hours": self.settings.telemetry_retention_hours,
            "interval_seconds": self.settings.retention_interval_seconds,
        }


def status_of(pruner: RetentionPruner | None) -> dict[str, Any]:
    """Status for a pruner that may not exist.

    The pruner is per-app state created in the lifespan rather than a module singleton:
    its asyncio task belongs to the event loop of the app that started it, and a
    module-level instance would be shared by every app built in the process — stopping
    it from a second app's loop raises "attached to a different loop". Reports rather
    than fails when there is nothing to report.
    """
    return pruner.status() if pruner is not None else {"enabled": False, "running": False}
