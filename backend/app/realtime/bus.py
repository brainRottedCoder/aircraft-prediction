"""In-process event bus. One API process, so no Redis (docs/02 §4)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from ..core.config import get_settings
from .events import Event

log = logging.getLogger(__name__)


class _Overflow:
    """Sentinel telling a consumer its queue overflowed and it is being cut off.

    Delivered through the subscriber's own queue, so the `drain()` task wakes up
    instead of blocking forever on `queue.get()`. Identity (`is`), not equality.
    """

    __slots__ = ()

    def __repr__(self) -> str:                      # pragma: no cover — debug aid
        return "<overflow>"


OVERFLOW = _Overflow()


class EventBus:
    def __init__(self, maxsize: int | None = None) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()
        self._maxsize = maxsize or get_settings().ws_queue_size
        self._dropped = 0
        self._loop: asyncio.AbstractEventLoop | None = None

    def subscribe(self) -> asyncio.Queue[Event]:
        q: asyncio.Queue[Event] = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event]) -> None:
        self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def dropped(self) -> int:
        return self._dropped

    def bind_loop(self) -> None:
        """Called from the lifespan so sync routes can publish too."""
        self._loop = asyncio.get_running_loop()

    def publish_soon(self, event: Event) -> None:
        """Fire-and-forget publish that works from a sync (threadpool) handler.

        FastAPI runs `def` handlers in a worker thread, where there is no running
        loop, so `get_running_loop()` alone is not enough.
        """
        loop = self._loop
        if loop is None or not loop.is_running():
            log.warning("dropping %s: no event loop bound", event.type)
            return
        try:
            running: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            running = None                      # sync handler on a worker thread
        if running is loop:
            loop.create_task(self.publish(event))
        else:
            asyncio.run_coroutine_threadsafe(self.publish(event), loop)

    async def publish(self, event: Event) -> None:
        """Fan out. A slow consumer is cut off rather than allowed to grow memory.

        Dropping the queue from the fan-out is not enough on its own: the consumer's
        drain task would sit on `queue.get()` forever, the socket would stay open, and
        the client would show a live connection while its numbers silently froze. So an
        overflowed subscriber also gets the OVERFLOW sentinel pushed into its queue (making
        room for it first, since the queue is full by definition) and the consumer turns
        that into a close (docs/09 §4).
        """
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                self._dropped += 1
                self._subscribers.discard(q)
                # Already unsubscribed, so nothing else will fill it. Freeing one slot
                # (or finding it already drained) guarantees the put below cannot fail.
                with suppress(asyncio.QueueEmpty):
                    q.get_nowait()
                q.put_nowait(OVERFLOW)
                log.warning("closing slow WebSocket consumer (%d total)", self._dropped)


bus = EventBus()