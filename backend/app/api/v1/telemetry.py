"""Telemetry ingest (spec 40) and internal ML predict (spec 41).

Transport only. Use cases live in `app.services.telemetry_service`, persistence in
`app.repositories.telemetry_repo`.
"""
from __future__ import annotations

from fastapi import APIRouter

from ...schemas.ops import PredictRequest, PredictResponse, TelemetryAccepted, TelemetryBatch
from ...services import telemetry_service
from ..deps import CanMutate, DbSession

router = APIRouter(tags=["telemetry"], prefix="/api/v1")


@router.post("/telemetry", response_model=TelemetryAccepted, status_code=202)
def ingest_telemetry(db: DbSession, user: CanMutate, batch: TelemetryBatch):
    """One aircraft per batch (spec 40). Upserts, infers, persists, publishes."""
    return telemetry_service.ingest(db, batch.aircraft, batch.rows)


@router.post("/internal/ml/predict", response_model=PredictResponse)
def predict(db: DbSession, user: CanMutate, payload: PredictRequest):
    """Score a caller-supplied window against the live model."""
    return telemetry_service.predict(db, payload)