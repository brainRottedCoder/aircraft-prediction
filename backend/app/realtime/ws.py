"""WebSocket /ws/fleet — spec item 42 (docs/09 §3)."""
from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from ..core.config import get_settings
from ..core.errors import AuthenticationError
from ..core.security import decode_ws_token
from ..db.session import get_sessionmaker
from ..models.fleet import Aircraft
from .bus import OVERFLOW, bus
from .events import ClientMessage, connection_ready, pong

log = logging.getLogger(__name__)
router = APIRouter()

WS_UNAUTHORIZED = 4401
WS_BAD_ORIGIN = 4408
WS_INTERNAL = 1011
WS_TRY_AGAIN_LATER = 1013

# How often the drain loop wakes to re-check the token even when no events are flowing.
# A paused demo publishes nothing, so without this a socket would sit unverified forever.
TOKEN_CHECK_SECONDS = 30.0


def _expires_at(claims: dict) -> float:
    """Absolute expiry of a decoded token, as a POSIX timestamp.

    The token is verified once at connect. With `jwt_ttl_minutes` at its 720-minute
    default that is up to 12 further hours of authenticated socket after the credential
    itself expired, and nothing would ever notice — a client that never reconnects keeps
    its feed. Re-checking on a timer bounds that to `TOKEN_CHECK_SECONDS`.
    """
    exp = claims.get("exp")
    return float(exp) if exp is not None else float("inf")


@router.websocket("/ws/fleet")
async def ws_fleet(
    websocket: WebSocket,
    token: str | None = Query(default=None),
    aircraft: str | None = Query(default=None),
) -> None:
    settings = get_settings()

    origin = websocket.headers.get("origin")
    if origin and settings.cors_origins and origin not in settings.cors_origins:
        await websocket.close(code=WS_BAD_ORIGIN, reason="origin not allowed")
        return

    try:
        claims = decode_ws_token(token, settings)
    except AuthenticationError:
        await websocket.close(code=WS_UNAUTHORIZED, reason="unauthorized")
        return

    expires_at = _expires_at(claims)

    await websocket.accept()
    queue = bus.subscribe()
    filters: set[str] = set(filter(a.strip() for a in aircraft.split(","))) if aircraft else set()

    # One counter for every frame this socket sends, so `seq` is monotonic in arrival
    # order no matter which task emitted it. Previously `pong` was sent straight from the
    # receive loop with `seq: null`, which made `seq` unusable as an ordering key for any
    # client that wanted one.
    #
    # The lock is held across `send_json`, not just the increment: Starlette gives no
    # ordering guarantee between concurrent senders, so releasing it early could put seq 6
    # on the wire before seq 5 and leave `seq` worse than useless.
    next_seq = 0
    seq_lock = asyncio.Lock()

    async def send(event) -> None:
        nonlocal next_seq
        async with seq_lock:
            next_seq += 1
            await websocket.send_json(event.envelope(next_seq))

    session = get_sessionmaker(settings)
    with session() as db:
        count = len(list(db.scalars(select(Aircraft.id))))
    await websocket.send_json(
        connection_ready(count, settings.demo_mode, settings.demo_tick_seconds).envelope(0)
    )

    async def drain() -> None:
        while True:
            try:
                # Timed so the token is re-checked even when the replay is paused and the
                # bus is quiet. asyncio.Queue.get is cancellation-safe on 3.11: a timeout
                # does not swallow an event published in the same tick.
                event = await asyncio.wait_for(queue.get(), timeout=TOKEN_CHECK_SECONDS)
            except TimeoutError:
                if time.time() >= expires_at:
                    log.info("closing socket for an expired token")
                    await websocket.close(code=WS_UNAUTHORIZED, reason="token expired")
                    return
                continue

            if event is OVERFLOW:
                # Fell behind the fleet. Closing makes the client reconnect and resync
                # from REST rather than sit on a socket that will never update again.
                log.warning("subscriber fell behind — closing with %d", WS_TRY_AGAIN_LATER)
                await websocket.close(code=WS_TRY_AGAIN_LATER, reason="too slow")
                return

            # A filter narrows the per-aircraft stream; it must not swallow fleet-wide
            # events. `spare.reserved` and `alert.acked` carry no `aircraft` key at all, so
            # the old `payload.get("aircraft") not in filters` test dropped both for every
            # filtered subscriber — and unlike engine health, nothing recovers them later
            # because the client never polls those collections.
            aircraft_code = event.payload.get("aircraft")
            if filters and aircraft_code is not None and aircraft_code not in filters:
                continue

            if time.time() >= expires_at:
                await websocket.close(code=WS_UNAUTHORIZED, reason="token expired")
                return

            await send(event)

    pump = asyncio.create_task(drain())
    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except (ValueError, TypeError, RuntimeError):
                # A text frame that is not JSON, or a binary frame. Previously this was
                # outside the inner try, so one bad frame from a buggy client tore the
                # whole connection down with 1011 and burned a reconnect. Unknown *types*
                # were already tolerated; unparseable ones should be too. A real
                # disconnect raises WebSocketDisconnect, which is none of these and so
                # still unwinds to the handler below.
                log.warning("ignoring unparseable client frame")
                continue
            try:
                msg = ClientMessage(**raw)
            except Exception:  # noqa: BLE001
                continue
            if msg.type == "ping":
                # Answered immediately rather than queued behind whatever the replay has
                # buffered: a liveness check that waits on a 1.2 s tick burst is not much
                # of a liveness check. `send` still stamps a proper seq, so the frame is
                # not out of step with the rest of the stream.
                await send(pong())
            elif msg.type == "subscribe":
                filters = set(msg.aircraft)
            elif msg.type == "unsubscribe":
                filters -= set(msg.aircraft)
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("WebSocket error")
        with suppress(RuntimeError):
            await websocket.close(code=WS_INTERNAL)
    finally:
        pump.cancel()
        # Awaiting a cancelled task both waits for it and retrieves its exception. Without
        # this, a `send_json` that failed because the socket was closed concurrently ended
        # the task silently and logged "Task exception was never retrieved".
        with suppress(asyncio.CancelledError, Exception):
            await pump
        bus.unsubscribe(queue)