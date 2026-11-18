"""In-process event bus + Server-Sent Events stream (GET /api/v1/events).

One uvicorn process (the LXC runs a single worker), so an in-memory bus is
enough. It sits behind `EventBus.publish` / `EventBus.subscribe`: replacing it
with Redis or PostgreSQL LISTEN/NOTIFY later only touches this module.

* Events are typed (`EVENT_TYPES`) and numbered: the id is the SSE `id:`, so a
  reconnecting browser sends `Last-Event-ID` and gets what it missed from a
  ring buffer (the last `BUFFER_SIZE` events).
* `publish()` is thread-safe and never blocks: sync endpoints (threadpool),
  the planning scheduler and SQLAlchemy hooks all publish.
* A slow client never blocks the others: its queue is bounded, overflowing
  events are dropped for that client (it can re-sync with Last-Event-ID).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

EVENT_TYPES = frozenset(
    {
        "disk.added",
        "disk.removed",
        "disk.updated",
        "agent.online",
        "agent.offline",
        "run.progress",
        "run.log",
        "run.finished",
        "inbox.updated",
    }
)
BUFFER_SIZE = 500
CLIENT_QUEUE_SIZE = 1000
HEARTBEAT_SECONDS = 15.0
# Streams end after this long; EventSource reconnects at once with
# Last-Event-ID. Keeps proxies and half-dead connections from piling up.
MAX_STREAM_SECONDS = 3600.0
RETRY_MILLISECONDS = 3000


@dataclass(frozen=True)
class Event:
    id: int
    type: str
    data: dict[str, Any]
    at: float

    def to_sse(self) -> str:
        payload = json.dumps({"type": self.type, "at": self.at, "data": self.data}, default=str, separators=(",", ":"))
        return f"id: {self.id}\nevent: {self.type}\ndata: {payload}\n\n"


class _Subscriber:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=CLIENT_QUEUE_SIZE)

    def offer(self, event: Event) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            logger.warning("SSE client too slow: event %s dropped", event.id)


class EventBus:
    def __init__(self, buffer_size: int = BUFFER_SIZE) -> None:
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._buffer: deque[Event] = deque(maxlen=buffer_size)
        self._subscribers: set[_Subscriber] = set()
        # Ids restart at 1 when the process restarts: a client presenting an id
        # from a previous process (greater than anything here) gets no replay.
        self.boot_id = int(time.time())

    def publish(self, event_type: str, data: dict[str, Any] | None = None) -> Event:
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Unknown event type {event_type!r}")
        with self._lock:
            event = Event(id=next(self._ids), type=event_type, data=dict(data or {}), at=time.time())
            self._buffer.append(event)
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            try:
                subscriber.loop.call_soon_threadsafe(subscriber.offer, event)
            except RuntimeError:  # loop closed: the client is gone
                self._discard(subscriber)
        return event

    def since(self, last_id: int | None) -> list[Event]:
        with self._lock:
            if last_id is None:
                return []
            latest = self._buffer[-1].id if self._buffer else 0
            if last_id > latest:  # id from a previous process
                return []
            return [event for event in self._buffer if event.id > last_id]

    def subscribe(self) -> _Subscriber:
        subscriber = _Subscriber(asyncio.get_running_loop())
        with self._lock:
            self._subscribers.add(subscriber)
        return subscriber

    def _discard(self, subscriber: _Subscriber) -> None:
        with self._lock:
            self._subscribers.discard(subscriber)

    unsubscribe = _discard

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def reset(self) -> None:
        """Tests only."""
        with self._lock:
            self._ids = itertools.count(1)
            self._buffer.clear()
            self._subscribers.clear()


bus = EventBus()


def publish(event_type: str, data: dict[str, Any] | None = None) -> None:
    """Fire and forget: an event problem must never break the caller."""
    try:
        bus.publish(event_type, data)
    except Exception:
        logger.exception("Could not publish event %s", event_type)


def parse_last_event_id(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


async def event_stream(
    event_bus: EventBus,
    last_event_id: int | None,
    *,
    is_disconnected: Callable[[], Awaitable[bool]],
    heartbeat_seconds: float | None = None,
    max_seconds: float | None = None,
) -> AsyncIterator[str]:
    """SSE body: retry hint, replay after Last-Event-ID, live events, and a
    `: ping` comment every `heartbeat_seconds` of silence."""
    heartbeat = HEARTBEAT_SECONDS if heartbeat_seconds is None else heartbeat_seconds
    deadline = time.monotonic() + (MAX_STREAM_SECONDS if max_seconds is None else max_seconds)
    subscriber = event_bus.subscribe()  # before the replay: nothing falls in between
    try:
        yield f"retry: {RETRY_MILLISECONDS}\n: connected\n\n"
        sent = last_event_id or 0
        for event in event_bus.since(last_event_id):
            sent = event.id
            yield event.to_sse()
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or await is_disconnected():
                return
            try:
                event = await asyncio.wait_for(subscriber.queue.get(), timeout=min(heartbeat, remaining))
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if event.id <= sent:  # already replayed
                continue
            sent = event.id
            yield event.to_sse()
    finally:
        event_bus.unsubscribe(subscriber)
