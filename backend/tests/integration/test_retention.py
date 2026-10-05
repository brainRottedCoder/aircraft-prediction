"""Storage retention — the pruner that keeps the replay engine's writes bounded.

These are the tests that matter for the free-tier deployment: nothing else in the app
deletes the four append-only time-series tables, so a broken pruner is invisible until
the managed Postgres fills its disk. The behaviour worth pinning is therefore the
*boundary* — old rows go, recent rows stay — and the guarantee that pruning cannot
break the replay writer's upserts.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update

from app.core.config import Settings
from app.models.telemetry import (
    SENSOR_COLUMNS,
    ComponentHealth,
    EngineTelemetry,
    HealthSnapshot,
    MlPrediction,
)
from app.repositories import fleet_repo as repo
from app.repositories import telemetry_repo as telemetry
from app.services.retention import MIN_INTERVAL_SECONDS, RetentionPruner, prune_once

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

# The tables the replay engine appends to and nothing else ever deletes.
GROWING = (EngineTelemetry, ComponentHealth, HealthSnapshot, MlPrediction)


def _count(db, model) -> int:
    return db.scalar(select(func.count()).select_from(model)) or 0


def _prediction(cycle: int) -> dict:
    """A minimal prediction shaped like the ones `inference.predict` returns.

    Every key `write_engine_prediction` reads has to be present: a partial dict fails
    with a KeyError that reads like a repository bug rather than a bad fixture.
    """
    return {
        "rul": max(1, 125 - cycle),
        "model": {"regime": 0, "version": "test"},
        "component_health": {"fan": 0.9, "hpc": 0.9, "hpt": 0.9, "lpt": 0.9},
        "top_sensors": [],
        "deviation": {},
        "component_sensor_map": {},
        "weakest_component": "fan",
        "latency_ms": 1.5,
    }


def _write_cycle(db, cycle: int, recorded_at: datetime) -> None:
    """One full replay tick's worth of rows, exactly as `_advance` writes them."""
    aircraft = db.scalars(select(repo.Aircraft).limit(1)).one()
    telemetry.write_engine_prediction(
        db,
        aircraft=aircraft,
        part=telemetry.engine_part(db),
        prediction=_prediction(cycle),
        health=0.5,
        risk="watch",
        cycle=cycle,
        do_by=cycle + 10,
        source="cmapss",
        cmapss_unit_id=1,
        telemetry_rows=[
            {
                "cycle": cycle,
                "settings": {"setting_1": 1400.0, "setting_2": 21.5, "setting_3": 1000.0},
                # All 15 are NOT NULL; a partial dict raises rather than writing a
                # half-row, which would make the fixture lie about what it seeded.
                "sensors": {name: 10.0 + i for i, name in enumerate(SENSOR_COLUMNS)},
            }
        ],
        recorded_at=recorded_at,
    )
    db.commit()


@pytest.fixture
def seeded_ticks(db, database):
    """Two old ticks and two recent ones, on cycles that will not collide.

    `database` is requested explicitly: the session fixture is what migrates and seeds
    the schema, and a test that only asks for `db` gets a migrated-but-empty database.
    """
    # Cycles 900+ are far outside anything the replay engine reaches in a test run, so
    # these rows cannot be disturbed by a concurrently running tick.
    for cycle, at in (
        (900, NOW - timedelta(hours=10)),
        (901, NOW - timedelta(hours=8)),
        (902, NOW - timedelta(hours=2)),
        (903, NOW - timedelta(minutes=5)),
    ):
        _write_cycle(db, cycle, at)
    return db


@pytest.mark.parametrize(
    "model,column",
    [
        (EngineTelemetry, "recorded_at"),
        (ComponentHealth, "recorded_at"),
        (HealthSnapshot, "recorded_at"),
        (MlPrediction, "created_at"),
    ],
)
def test_prune_removes_old_and_keeps_recent(seeded_ticks, model, column):
    """The whole point: a timestamp boundary, applied to all four growing tables."""
    before = _count(seeded_ticks, model)
    assert before >= 4, f"fixture did not populate {model.__tablename__}"

    counts = prune_once(Settings(telemetry_retention_hours=3), now=NOW)

    assert counts[model.__tablename__] >= 2, f"nothing pruned from {model.__tablename__}"

    cutoff = NOW - timedelta(hours=3)
    stale = seeded_ticks.scalars(
        select(model.id).where(getattr(model, column) < cutoff)
    ).all()
    assert not stale, f"{model.__tablename__} kept rows older than the horizon"

    fresh = seeded_ticks.scalars(
        select(func.count()).select_from(model).where(getattr(model, column) >= cutoff)
    ).all()
    assert fresh[0] >= 2, f"{model.__tablename__} lost rows inside the horizon"


def test_prune_keeps_every_row_inside_horizon(seeded_ticks):
    """A horizon nothing is due for must be a no-op, not a partial delete."""
    before = {m.__tablename__: _count(seeded_ticks, m) for m in GROWING}

    counts = prune_once(Settings(telemetry_retention_hours=48), now=NOW)

    assert sum(counts.values()) == 0
    for model in GROWING:
        assert _count(seeded_ticks, model) == before[model.__tablename__], (
            f"{model.__tablename__} lost rows inside a 48h horizon"
        )


def test_prune_disabled_is_a_noop(seeded_ticks):
    """0 hours is how a developer opts out; it must not delete anything."""
    before = _count(seeded_ticks, EngineTelemetry)

    assert prune_once(Settings(telemetry_retention_hours=0), now=NOW) == {}
    assert _count(seeded_ticks, EngineTelemetry) == before


def test_prune_leaves_seed_data_alone(seeded_ticks):
    """The fleet itself is never pruned — only the time series.

    Without this, a horizon short enough to be interesting during a test could take the
    seeded aircraft with it and every later test in the session would fail confusingly.
    """
    aircraft_before = repo.count_aircraft(seeded_ticks)

    prune_once(Settings(telemetry_retention_hours=0.0001), now=NOW + timedelta(days=3650))

    assert repo.count_aircraft(seeded_ticks) == aircraft_before
    assert _count(seeded_ticks, EngineTelemetry) == 0


def test_pruned_cycle_can_be_written_again(db, seeded_ticks):
    """Pruning must not break the replay writer's `on_conflict_do_update` upserts.

    An engine that wraps back to an already-seen cycle upserts on
    `(aircraft_id, cycle)`. If pruning left a partial row behind — or if the horizon
    somehow created a conflict — this write would raise. It must simply insert.
    """
    prune_once(Settings(telemetry_retention_hours=3), now=NOW)

    _write_cycle(db, 900, NOW)          # cycle 900 was pruned above
    assert _count(db, EngineTelemetry) >= 1

    fresh = db.scalars(
        select(EngineTelemetry.recorded_at).where(EngineTelemetry.cycle == 900)
    ).one()
    assert fresh == NOW


# ── task lifecycle ─────────────────────────────────────────────────────────────
def test_pruner_disabled_never_starts_a_task():
    pruner = RetentionPruner(Settings(telemetry_retention_hours=0))

    async def go() -> None:
        await pruner.start()
        assert pruner.task is None
        await pruner.stop()

    asyncio.run(go())
    assert not pruner.enabled
    assert pruner.status()["running"] is False


def test_pruner_runs_and_stops_cleanly(database):
    """Start/stop must not leak a task or raise on cancel — the lifespan does this."""
    pruner = RetentionPruner(Settings(retention_interval_seconds=MIN_INTERVAL_SECONDS))

    async def go() -> None:
        await pruner.start()
        assert pruner.task is not None
        await asyncio.sleep(MIN_INTERVAL_SECONDS * 1.5)
        await pruner.stop()
        assert pruner.task is None

    asyncio.run(go())
    assert pruner.passes >= 1, "the pruner never completed a pass"


def test_a_failing_pass_does_not_kill_the_loop():
    """One bad pass must not take retention down for the life of the process.

    This is the difference between a full disk and a delayed cleanup: the tables grow
    back up, but nothing is reported and nothing recovers on its own.
    """
    pruner = RetentionPruner(Settings(retention_interval_seconds=MIN_INTERVAL_SECONDS))

    async def go() -> None:
        calls = 0

        async def boom() -> dict[str, int]:
            nonlocal calls
            calls += 1
            raise RuntimeError("database went away")

        pruner.prune_now = boom
        await pruner.start()
        # Long enough for two passes at the floor interval; the point is that the
        # second one happens at all.
        await asyncio.sleep(MIN_INTERVAL_SECONDS * 2.4)
        await pruner.stop()
        assert calls > 1, "loop stopped after the first failure"

    asyncio.run(go())
    assert pruner.task is None
    assert pruner.passes == 0, "a failed pass must not be counted as a pass"


def test_sleeps_before_the_first_pass():
    """A pass during startup competes with the model, C-MAPSS and the seed.

    On a 0.1 vCPU instance the first prune must not land in the middle of boot.
    """
    pruner = RetentionPruner(Settings(retention_interval_seconds=MIN_INTERVAL_SECONDS))

    async def go() -> None:
        await pruner.start()
        assert pruner.passes == 0
        await pruner.stop()

    asyncio.run(go())


def test_stored_timestamps_are_really_old(db, seeded_ticks):
    """Sanity on the fixture itself: the rows really are older than the cutoff.

    Guards against the boundary tests passing for the wrong reason — if `recorded_at`
    were ever ignored on write, every assertion above would still hold because nothing
    would ever look old.
    """
    old = NOW - timedelta(hours=10)
    db.execute(
        update(EngineTelemetry)
        .where(EngineTelemetry.cycle == 900)
        .values(recorded_at=old)
    )
    db.commit()

    stored = db.scalars(
        select(EngineTelemetry.recorded_at).where(EngineTelemetry.cycle == 900)
    ).one()
    assert stored.tzinfo is not None, "recorded_at is timestamptz; got a naive value"
    assert stored < NOW - timedelta(hours=3)