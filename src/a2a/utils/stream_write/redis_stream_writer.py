"""Professional StreamInjector for A2A framework.

A clean, focused class for writing events to Redis streams with proper
A2A serialization and connection management.
"""

import json
import logging

from datetime import datetime, timezone
from types import TracebackType
from typing import Any


try:
    from redis.asyncio import Redis
except ImportError:
    Redis = None

from a2a.types import Message, TaskState, TaskStatus, TaskStatusUpdateEvent


logger = logging.getLogger(__name__)


class RedisStreamInjector:
    """Professional stream injector for A2A framework."""

    def __init__(
        self,
        redis_url: str = 'redis://localhost:6379/0',
        redis_client: Any | None = None,
    ):
        """Initialize the stream injector."""
        # Allow passing a custom redis client (e.g. a fake in tests).
        if Redis is None and redis_client is None:
            raise ImportError(
                'redis package is required. Install with: pip install redis'
            )

        self.redis_url = redis_url
        self._client = redis_client
        self._connected = redis_client is not None

    async def connect(self) -> None:
        """Establish Redis connection."""
        if self._connected:
            return

        try:
            if self._client is None:
                if Redis is None:
                    raise ImportError(
                        'redis package is required. Install with: pip install redis'
                    )
                self._client = Redis.from_url(self.redis_url)
                await self._client.ping()
            self._connected = True
            logger.info('Connected to Redis')
        except Exception:
            logger.exception('Failed to connect to Redis')
            raise

    async def disconnect(self) -> None:
        """Close Redis connection."""
        if self._client and self._connected:
            await self._client.aclose()
            self._client = None
            self._connected = False
            logger.info('Disconnected from Redis')

    async def __aenter__(self) -> 'RedisStreamInjector':
        """Enter the async context manager."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the async context manager."""
        await self.disconnect()

    def _get_stream_key(self, task_id: str) -> str:
        """Get the Redis stream key for a task."""
        if not task_id:
            raise ValueError('task_id cannot be empty')
        stream_key = f'a2a:task:{task_id}'
        logger.debug('Generated stream key: %s', stream_key)
        return stream_key

    def _serialize_event(
        self,
        event_type: str,
        data: dict[str, Any],
    ) -> dict[str, str]:
        """Serialize an event for Redis stream storage to match RedisEventQueue format."""
        # The RedisEventQueue expects events with 'type' and 'payload' fields
        # The payload should be the raw event data that can be parsed by pydantic models
        return {
            'type': event_type,
            'payload': json.dumps(data, default=str),  # Raw event data as JSON
        }

    async def _append_to_stream(
        self, task_id: str, event_data: dict[str, str]
    ) -> str:
        """Append an event to the Redis stream."""
        if not self._connected or not self._client:
            raise RuntimeError('Not connected to Redis. Call connect() first.')

        stream_key = self._get_stream_key(task_id)
        return await self._client.xadd(stream_key, event_data)  # type: ignore

    async def stream_message(
        self, context_id: str, task_id: str, message: dict[str, Any] | Message
    ) -> str:
        """Stream an agent message to the task stream."""
        if not task_id:
            raise ValueError('task_id cannot be empty')
        if not context_id:
            raise ValueError('context_id cannot be empty')

        data = message if isinstance(message, dict) else message.model_dump()

        event_data = self._serialize_event('Message', data)
        return await self._append_to_stream(task_id, event_data)

    async def update_status(
        self,
        context_id: str,
        task_id: str,
        status: dict[str, Any] | TaskStatusUpdateEvent | None = None,
        message: dict[str, Any] | Message | None = None,
        final: bool = False,
    ) -> str:
        """Update task status with optional message."""
        if not task_id:
            raise ValueError('task_id cannot be empty')
        if not context_id:
            raise ValueError('context_id cannot be empty')

        # Handle TaskStatusUpdateEvent model directly
        if isinstance(status, TaskStatusUpdateEvent):
            event_data = self._serialize_event(
                'TaskStatusUpdateEvent',
                status.model_dump(),
            )
            return await self._append_to_stream(task_id, event_data)

        # Extract state and build TaskStatus
        state = 'working'
        if isinstance(status, dict) and 'state' in status:
            state = status['state']

        # Convert to TaskState enum
        try:
            task_state = TaskState(state)
        except ValueError:
            task_state = TaskState.working

        # Handle message
        task_message = None
        if message:
            if isinstance(message, dict):
                task_message = Message(**message)
            else:
                task_message = message
        elif isinstance(status, dict) and 'message' in status:
            msg_data = status['message']
            if isinstance(msg_data, dict):
                task_message = Message(**msg_data)
            elif isinstance(msg_data, Message):
                task_message = msg_data

        # Create TaskStatus
        task_status = TaskStatus(
            state=task_state,
            message=task_message,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Create TaskStatusUpdateEvent
        event = TaskStatusUpdateEvent(
            context_id=context_id,
            task_id=task_id,
            final=final,
            status=task_status,
        )

        event_data = self._serialize_event(
            'TaskStatusUpdateEvent',
            event.model_dump(),
        )
        return await self._append_to_stream(task_id, event_data)

    async def final_message(
        self, context_id: str, task_id: str, message: dict[str, Any] | Message
    ) -> str:
        """Send a final message and mark task as complete."""
        if not task_id:
            raise ValueError('task_id cannot be empty')
        if not context_id:
            raise ValueError('context_id cannot be empty')

        # First send the message
        message_id = await self.stream_message(context_id, task_id, message)

        # Then mark as complete
        await self.update_status(
            context_id, task_id, {'state': 'completed'}, final=True
        )

        return message_id

    async def append_raw(
        self, task_id: str, event_type: str, payload: str
    ) -> str:
        """Append a raw event to the stream."""
        if not task_id:
            raise ValueError('task_id cannot be empty')

        event_data = {
            'type': event_type,
            'payload': payload,
        }
        return await self._append_to_stream(task_id, event_data)

    async def get_latest_event(self, task_id: str) -> dict[str, Any] | None:
        """Get the latest event from a task stream."""
        if not task_id:
            raise ValueError('task_id cannot be empty')

        if not self._connected or not self._client:
            raise RuntimeError('Not connected to Redis. Call connect() first.')

        stream_key = self._get_stream_key(task_id)
        try:
            result = await self._client.xrevrange(stream_key, '+', '-', count=1)
            if result:
                entry_id, fields = result[0]
                return {'id': entry_id, **fields}
        except Exception as e:  # noqa: BLE001
            logger.warning(
                'Failed to get latest event',
                extra={'task_id': task_id, 'error': str(e)},
            )

        return None

    async def get_events_since(self, task_id: str, start_id: str = '0') -> list:
        """Get all events from a task stream since the given ID."""
        if not task_id:
            raise ValueError('task_id cannot be empty')

        if not self._connected or not self._client:
            raise RuntimeError('Not connected to Redis. Call connect() first.')

        stream_key = self._get_stream_key(task_id)
        try:
            result = await self._client.xrange(stream_key, start_id, '+')
            return [{'id': entry_id, **fields} for entry_id, fields in result]
        except Exception as e:  # noqa: BLE001
            logger.warning(
                'Failed to get events',
                extra={'task_id': task_id, 'error': str(e)},
            )
            return []
