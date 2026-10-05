"""Event-bus fan-out policy: a slow consumer must be told, not silently abandoned."""
from __future__ import annotations

import asyncio

from app.realtime.bus import OVERFLOW, EventBus
from app.realtime.events import cycle_tick


def test_a_healthy_consumer_receives_every_event():
    async def run() -> list:
        bus = EventBus(maxsize=8)
        q = bus.subscribe()
        for i in range(5):
            await bus.publish(cycle_tick(i))
        return [q.get_nowait() for _ in range(5)]

    received = asyncio.run(run())
    assert [e.payload["cycle"] for e in received] == [0, 1, 2, 3, 4]
    assert OVERFLOW not in received


def test_an_overflowed_consumer_receives_the_overflow_sentinel():
    """The invariant the 1013 close depends on.

    Overflow used to discard the queue from the fan-out and nothing else. The consumer's
    drain task then blocked forever on `queue.get()`: the socket stayed open, the UI kept
    claiming "Backend Live", and engine numbers silently froze — because an open socket
    never trips the false→true transition that triggers a REST resync. The sentinel is
    what lets `ws.drain()` wake up and close.
    """
    async def run():
        bus = EventBus(maxsize=2)
        q = bus.subscribe()
        for i in range(6):                     # 2 fit, the rest overflow
            await bus.publish(cycle_tick(i))
        return bus, q

    bus, q = asyncio.run(run())

    assert bus.subscriber_count == 0, "overflowed queue must leave the fan-out"
    assert bus.dropped >= 1

    # Drain whatever survived the squeeze; the sentinel must be the tail.
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert items, "overflowed consumer must still be woken"
    assert items[-1] is OVERFLOW, "sentinel must be last, so a partial frame is not mistaken for data"


def test_overflow_wakes_a_consumer_that_has_not_drained_at_all():
    """A queue nobody reads still ends up holding the sentinel, not just the earlier events."""
    async def run():
        bus = EventBus(maxsize=1)
        q = bus.subscribe()
        await bus.publish(cycle_tick(1))       # fills it
        await bus.publish(cycle_tick(2))       # overflows, evicts, queues OVERFLOW
        return q

    q = asyncio.run(run())
    assert q.qsize() == 1
    assert q.get_nowait() is OVERFLOW


def test_slow_consumer_does_not_starve_the_others():
    """One bad socket must not stop delivery to everyone else."""
    async def run():
        bus = EventBus(maxsize=2)
        slow = bus.subscribe()                # nobody ever reads this one
        healthy = bus.subscribe()
        for i in range(4):
            await bus.publish(cycle_tick(i))
            healthy.get_nowait()              # the fast consumer keeps up
        return slow, healthy, bus

    slow, healthy, bus = asyncio.run(run())

    assert bus.subscriber_count == 1, "only the healthy subscriber stays subscribed"
    assert bus.dropped == 1, "exactly one consumer was cut"
    assert healthy.empty(), "the fast consumer received every event"
    # Making room for the sentinel evicts one queued event, so the sentinel is the tail
    # rather than the only item. The connection is being closed; the lost frame does not
    # matter, but the wake-up does.
    assert slow.get_nowait() is not OVERFLOW
    assert slow.get_nowait() is OVERFLOW, "the slow one is told, not just dropped"