"""Operational endpoints — health, readiness, seed, demo controls."""
from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy import text

from ...db.session import get_sessionmaker
from ...ml import model_store
from ...realtime.bus import bus
from ...realtime.replay import engine as replay
from ...repositories import fleet_repo as repo
from ...schemas.ops import DemoStatus, Healthz, Readyz, SeedResult
from ...services.retention import status_of
from ..deps import CommanderOnly, DbSession

router = APIRouter(tags=["ops"])


def _db_ok() -> bool:
    try:
        with get_sessionmaker()() as db:
            db.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


@router.get("/healthz", response_model=Healthz, include_in_schema=False)
def healthz(request: Request):
    """Liveness plus an honest statement of whether we are actually serving a model.

    A maintenance system that silently substitutes `rul = 125 - cycle` for a real
    prediction is more dangerous than one that is down, so a degraded model is surfaced
    in `status` rather than hidden behind an "ok".
    """
    model = model_store.handle().describe()
    db_ok = _db_ok()
    return {
        # db_ok is excluded deliberately: an unreachable database is a separate
        # operational signal from a degraded model, and folding it in here made a
        # perfectly good model report "degraded" during integration tests.
        #
        # `error` counts as degraded even when a model is still loaded: that is the
        # shape a failed forced reload takes, where the booster we are serving is
        # real but no longer the one on disk.
        "status": "degraded" if (model.get("degraded") or model.get("error")) else "ok",
        "db": db_ok,
        "model": model,
        "replay": replay.status(),
        # Reported, not asserted. A disabled or stalled pruner is how a small managed
        # Postgres fills up silently while every other field here stays green.
        "retention": status_of(getattr(request.app.state, "retention", None)),
    }


@router.get("/readyz", response_model=Readyz, include_in_schema=False)
def readyz(db: DbSession):
    """Ready means a real model is loaded. Serving the fallback is NOT ready.

    Set FDT_ML_FALLBACK=false to turn a missing artifact into a hard failure instead.
    """
    handle = model_store.handle()
    seeded = repo.count_aircraft(db)
    return {
        "status": "ready" if (seeded and handle.ready) else "starting",
        "aircraft_seeded": seeded,
        "db": True,
        "model_loaded": handle.ready,
        "model_degraded": handle.degraded,
        "model_error": handle.error,
    }


@router.post("/api/v1/seed/run", response_model=SeedResult)
def run_seed(user: CommanderOnly):
    from ...seed.run import run

    return {"status": "seeded", "counts": run()}


@router.post("/api/v1/ml/reload")
def ml_reload(user: CommanderOnly):
    """Re-read the ML artifact set from disk and warm it.

    The artifact directory is a bind mount, so a freshly staged booster is visible
    immediately — but the model is otherwise only read once, in the lifespan. Staging
    a model into a running container therefore had no effect and the API went on
    serving `rul = 125 - cycle` with a perfectly healthy `status`. This is the
    operation that makes staging observable.

    Never raises for a bad artifact: a failed reload keeps the model already in
    memory and reports the reason, because swapping a working booster for the
    fallback would turn a bad deploy into an outage. The response says which of the
    two happened — check `reloaded` and `loaded`, not just the status code.
    """
    before = model_store.handle()
    after = model_store.reload()
    return {
        "reloaded": after is not before,
        "loaded": after.ready,
        "previous_version": before.version,
        "model": after.describe(),
    }


@router.get("/api/v1/demo/status", response_model=DemoStatus)
def demo_status(user: CommanderOnly):
    return replay.status()


@router.post("/api/v1/demo/pause")
def demo_pause(user: CommanderOnly):
    replay.pause()
    return replay.status()


@router.post("/api/v1/demo/resume")
def demo_resume(user: CommanderOnly):
    replay.resume()
    return replay.status()


@router.post("/api/v1/demo/tick")
async def demo_tick(user: CommanderOnly):
    replay.pause()
    await replay.tick_once()
    return replay.status()


@router.get("/api/v1/audit")
def audit_trail(
    db: DbSession,
    user: CommanderOnly,
    entity: str | None = None,
    limit: int = 100,
):
    from ...models.auth import AuditLog

    stmt = db.query(AuditLog)
    if entity:
        stmt = stmt.filter(AuditLog.entity == entity)
    stmt = stmt.order_by(AuditLog.at.desc()).limit(limit)
    return {
        "items": [
            {"at": a.at, "actor": a.actor_name, "entity": a.entity,
             "entity_id": a.entity_id, "action": a.action,
             "before": a.before, "after": a.after, "request_id": a.request_id}
            for a in stmt.all()
        ],
        "subscribers": bus.subscriber_count,
    }