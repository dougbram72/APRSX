"""In-process pub/sub used to push live events to WebSocket clients."""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from typing import Any, Iterator

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self, queue_size: int = 200) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._queue_size = queue_size

    @contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(self._queue_size)
        self._subscribers.add(q)
        try:
            yield q
        finally:
            self._subscribers.discard(q)

    def publish(self, type: str, data: Any) -> None:
        event = {"type": type, "data": data}
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # A stalled client must never block packet handling.
                log.warning("dropping event for slow subscriber")
