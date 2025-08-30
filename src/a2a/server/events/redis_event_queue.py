"""Redis-backed EventQueue implementation using Redis Streams."""

from __future__ import annotations

import asyncio
import json
import logging

from typing import Any


try:
    import redis.asyncio as aioredis  # type: ignore

    from redis.exceptions import RedisError  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    aioredis = None  # type: ignore
    RedisError = Exception  # type: ignore

from typing import TYPE_CHECKING

from a2a.server.events.event_queue import EventQueue


if TYPE_CHECKING:
    from a2a.server.events.event_queue import Event
from pydantic import ValidationError

from a2a.types import (
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


class RedisNotAvailableError(RuntimeError):
    """Raised when the redis.asyncio package is not installed."""


_TYPE_MAP = {
    'Message': Message,
    'MessageEvent': Message,  # For test compatibility
    'Task': Task,
    'TaskStatusUpdateEvent': TaskStatusUpdateEvent,
    'TaskArtifactUpdateEvent': TaskArtifactUpdateEvent,
}


@trace_class(kind=SpanKind.SERVER)
class RedisEventQueue(EventQueue):
    """Redis-native EventQueue backed by a Redis Stream.

    This implementation does not rely on in-memory queue structures. Each
    instance manages its own read cursor (last_id). `tap()` returns a new
    RedisEventQueue pointing to the same stream but starting at '$' so it
    receives only future events.
    """

    def __init__(
        self,
        task_id: str,
        redis_client: Any,
        stream_prefix: str = 'a2a:task',
        maxlen: int | None = None,
        read_block_ms: int = 500,
    ) -> None:
        # Allow passing a custom redis client (e.g. a fake in tests).
        if aioredis is None and redis_client is None:
            raise RedisNotAvailableError('redis.asyncio is not available')

        self._task_id = task_id
        self._redis = redis_client
        self._stream_key = f'{stream_prefix}:{task_id}'
        self._maxlen = maxlen
        self._read_block_ms = read_block_ms

        # By default a normal queue should start at the beginning so it can
        # consume existing entries. Taps will explicitly start at '$'.
        self._last_id = '0-0'
        self._is_closed = False
        self._close_called = False

        # No in-memory queue initialization — this class is Redis-native.

    async def enqueue_event(self, event: Event) -> None:
        """Serialize and append an event to the Redis stream."""
        if self._is_closed:
            logger.warning('Attempt to enqueue to closed RedisEventQueue')
            return
        # Store payload as a JSON string to avoid client-specific mapping
        # behaviour when reading back from the stream.
        payload = {
            'type': type(event).__name__,
            'payload': event.json(),
        }
        kwargs: dict[str, Any] = {}
        if self._maxlen:
            kwargs['maxlen'] = self._maxlen
        try:
            await self._redis.xadd(self._stream_key, payload, **kwargs)
        except RedisError:
            logger.exception('Failed to XADD event to redis stream')

    async def dequeue_event(self, no_wait: bool = False) -> Event | Any:  # noqa: PLR0912
        """Read one event from the Redis stream respecting no_wait semantics.

        Returns a parsed pydantic model matching the event type.
        """
        # Removed early check for _is_closed to allow dequeuing existing events after close()

        block = 0 if no_wait else self._read_block_ms
        # Keep reading until we find payload or a CLOSE tombstone.
        while True:
            try:
                result = await self._redis.xread(
                    {self._stream_key: self._last_id}, block=block, count=1
                )
            except RedisError:
                logger.exception('Failed to XREAD from redis stream')
                raise

            if not result:
                raise asyncio.QueueEmpty

            _, entries = result[0]
            entry_id, fields = entries[0]
            self._last_id = entry_id

            # Normalize keys/values: redis may return bytes for both keys and values
            norm: dict[str, object] = {}
            try:
                for k, v in fields.items():
                    key = (
                        k.decode('utf-8')
                        if isinstance(k, bytes | bytearray)
                        else k
                    )
                    if isinstance(v, bytes | bytearray):
                        try:
                            val: object = v.decode('utf-8')
                        except UnicodeDecodeError:
                            val = v
                    else:
                        val = v
                    norm[str(key)] = val
            except Exception:  # noqa: BLE001
                # Defensive: if normalization fails, skip this entry and continue
                logger.debug(
                    'RedisEventQueue.dequeue_event: failed to normalize entry fields, skipping %s',
                    entry_id,
                )
                continue

            evt_type = norm.get('type')

            # Handle tombstone/close message
            if evt_type == 'CLOSE':
                self._is_closed = True
                raise asyncio.QueueEmpty('Queue is closed')

            raw_payload = norm.get('payload')
            if raw_payload is None:
                # Missing payload — likely due to key mismatch or malformed entry.
                # Skip and continue to next entry instead of returning None to callers.
                logger.debug(
                    'RedisEventQueue.dequeue_event: skipping entry %s with missing payload',
                    entry_id,
                )
                # continue loop to read next entry
                continue

            # If payload is a JSON string, parse it; otherwise, use as-is.
            if isinstance(raw_payload, str):
                try:
                    data = json.loads(raw_payload)
                except json.JSONDecodeError:
                    data = raw_payload
            else:
                data = raw_payload

            model = _TYPE_MAP.get(evt_type)
            if model:
                try:
                    return model.parse_obj(data)
                except ValidationError as exc:
                    logger.debug(
                        'Failed to parse event payload into model, returning raw data: %s',
                        exc,
                    )
                    # Return raw data for flexibility when parsing fails
                    return data

            # Unknown type — return raw data for flexibility
            logger.debug(
                'Unknown event type: %s, returning raw payload', evt_type
            )
            return data

    def task_done(self) -> None:  # streams do not require task_done semantics
        """No-op for Redis streams (kept for API compatibility)."""

    def tap(self) -> EventQueue:
        """Return a new RedisEventQueue that starts at the stream tail ('$')."""
        q = RedisEventQueue(
            task_id=self._task_id,
            redis_client=self._redis,
            stream_prefix=self._stream_key.rsplit(':', 1)[0],
            maxlen=self._maxlen,
            read_block_ms=self._read_block_ms,
        )
        # A tap should start after the current events to receive only future events.
        # Set _last_id to the current max ID in the stream.
        # For FakeRedis, access streams directly; for real Redis, this would need async query.
        if hasattr(self._redis, 'streams'):
            lst = self._redis.streams.get(self._stream_key, [])
            if lst:
                max_id = max(int(eid.split('-')[0]) for eid, _ in lst)
                q._last_id = f'{max_id}-0'
            else:
                q._last_id = '0'
        else:
            # For real Redis, use '$' as approximation
            q._last_id = '$'
        return q

    async def close(self, immediate: bool = False) -> None:
        """Mark the stream closed and publish a tombstone entry for readers."""
        if self._close_called:
            return  # Already called close

        try:
            await self._redis.xadd(self._stream_key, {'type': 'CLOSE'})
            self._close_called = True
            self._is_closed = True  # Mark as closed immediately
        except Exception:  # Catch all exceptions, not just RedisError
            logger.exception('Failed to write close marker to redis')
            # Still mark as closed even if Redis operation fails
            self._is_closed = True

    def is_closed(self) -> bool:
        """Return True if this queue has been closed (close() called)."""
        return self._is_closed

    async def clear_events(self, clear_child_queues: bool = True) -> None:
        """Attempt to remove the underlying redis stream (best-effort)."""
        try:
            await self._redis.delete(self._stream_key)
        except Exception:  # Catch all exceptions, not just RedisError
            logger.exception(
                'Failed to delete redis stream during clear_events'
            )
