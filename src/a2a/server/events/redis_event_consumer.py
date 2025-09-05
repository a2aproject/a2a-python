from __future__ import annotations

import asyncio
import logging

from typing import TYPE_CHECKING, Protocol


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from a2a.utils.telemetry import SpanKind, trace_class


class QueueLike(Protocol):
    """Protocol describing a minimal queue-like object used by consumers.

    It must provide an async `dequeue_event(no_wait: bool)` method and an
    `is_closed()` method.
    """

    async def dequeue_event(self, no_wait: bool = False) -> object:
        """Return the next queued event or raise asyncio.QueueEmpty if none when no_wait is True."""

    def is_closed(self) -> bool:
        """Return True if the underlying queue has been closed."""
        ...


logger = logging.getLogger(__name__)


@trace_class(kind=SpanKind.SERVER)
class RedisEventConsumer:
    """Adapter that provides the same consume semantics for a Redis-backed EventQueue.

    It wraps a RedisEventQueue instance and exposes methods compatible with
    existing code expecting an EventQueue (not strictly required but helpful).
    """

    def __init__(self, queue: QueueLike) -> None:
        """Wrap a queue-like object that exposes dequeue_event and is_closed."""
        self._queue = queue

    async def consume_one(self) -> object:
        """Consume a single event without waiting; raises asyncio.QueueEmpty if none."""
        return await self._queue.dequeue_event(no_wait=True)

    async def consume_all(self) -> AsyncGenerator:
        """Yield events until the queue is closed."""
        while True:
            try:
                event = await self._queue.dequeue_event()
                yield event
                if self._queue.is_closed():
                    break
            except asyncio.QueueEmpty:
                if self._queue.is_closed():
                    break
                continue
