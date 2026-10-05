"""WebSocket event schemas — spec item 42."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

PROTOCOL_VERSION = 1


class Event(BaseModel):
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    seq: int | None = None
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def envelope(self, seq: int | None = None) -> dict:
        return {"type": self.type, "payload": self.payload, "seq": seq,
                "ts": self.ts.isoformat()}


# ── server → client ────────────────────────────────────────────────────────────
def connection_ready(aircraft: int, demo_mode: bool, tick: float) -> Event:
    return Event(type="connection.ready", payload={
        "aircraft": aircraft, "demo_mode": demo_mode, "tick_seconds": tick,
        "protocol_version": PROTOCOL_VERSION,
    })


def cycle_tick(cycle: int) -> Event:
    return Event(type="cycle.tick", payload={"cycle": cycle})


def health_updated(aircraft: str, aircraft_id: int, part: str,
                   health: float, risk: str, rul: int | None, cycle: int,
                   model: dict[str, Any] | None = None) -> Event:
    """`model` carries the per-prediction provenance from `inference.predict`.

    A per-tick fallback (a rejected window, a failed inference) leaves the model
    loaded and /healthz green, so the health event is the only place the client can
    learn that *this* number came from `rul = 125 - cycle` instead of the booster.
    """
    payload: dict[str, Any] = {
        "aircraft": aircraft, "aircraft_id": aircraft_id, "part": part,
        "health": health, "risk": risk, "rul": rul, "cycle": cycle,
    }
    if model is not None:
        payload["model"] = {
            "fallback": bool(model.get("fallback")),
            "degraded": bool(model.get("degraded")),
            "version": model.get("version"),
            "reason": model.get("reason"),
        }
    return Event(type="health.updated", payload=payload)


def alert_raised(alert: dict) -> Event:
    return Event(type="alert.raised", payload=alert)


def alert_acked(payload: dict) -> Event:
    return Event(type="alert.acked", payload=payload)


def work_order_created(payload: dict) -> Event:
    return Event(type="work_order.created", payload=payload)


def work_order_updated(payload: dict) -> Event:
    return Event(type="work_order.updated", payload=payload)


def spare_reserved(payload: dict) -> Event:
    return Event(type="spare.reserved", payload=payload)


def booking_created(payload: dict) -> Event:
    return Event(type="booking.created", payload=payload)


def pong() -> Event:
    return Event(type="pong", payload={})


# ── client → server ────────────────────────────────────────────────────────────
class ClientMessage(BaseModel):
    type: Literal["subscribe", "unsubscribe", "ping"]
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def aircraft(self) -> list[str]:
        return list(self.payload.get("aircraft") or [])