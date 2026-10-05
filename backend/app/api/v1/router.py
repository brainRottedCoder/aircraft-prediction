"""Aggregates every v1 router."""
from fastapi import APIRouter

from . import auth, fleet, maintenance, ops, telemetry

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(fleet.router)
api_router.include_router(maintenance.router)
api_router.include_router(telemetry.router)
api_router.include_router(ops.router)

__all__ = ["api_router"]
