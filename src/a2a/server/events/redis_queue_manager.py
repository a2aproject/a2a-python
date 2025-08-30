from __future__ import annotations

import logging

from typing import TYPE_CHECKING, Any

from a2a.server.events.queue_manager import QueueManager


if TYPE_CHECKING:
    from a2a.server.events.event_queue import EventQueue

logger = logging.getLogger(__name__)

# Import RedisEventQueue at module level to avoid repeated imports
try:
    from a2a.server.events.redis_event_queue import RedisEventQueue
except ImportError:
    RedisEventQueue = None  # type: ignore


class RedisQueueManager(QueueManager):
    """QueueManager implementation backed by Redis streams.

    This manager creates RedisEventQueue instances on-demand without maintaining
    local state, making it suitable for distributed environments like Kubernetes.
    All coordination happens through Redis streams.
    """

    def __init__(
        self, redis_client: Any, stream_prefix: str = 'a2a:task'
    ) -> None:
        self._redis = redis_client
        self._stream_prefix = stream_prefix

    async def add(self, task_id: str, queue: EventQueue) -> None:
        """Add is not supported in distributed Redis setup.

        In a distributed environment, we can't reliably add preexisting queue
        instances. Use create_or_tap() instead to create Redis-backed queues.
        """
        raise NotImplementedError(
            'add() is not supported in distributed Redis setup. '
            'Use create_or_tap() to create Redis-backed queues.'
        )

    async def get(self, task_id: str) -> EventQueue | None:
        """Get is not supported in distributed Redis setup.

        In a distributed environment, we can't reliably retrieve existing queue
        instances from other pods. Use create_or_tap() instead.
        """
        raise NotImplementedError(
            'get() is not supported in distributed Redis setup. '
            'Use create_or_tap() to create or tap Redis-backed queues.'
        )

    async def tap(self, task_id: str) -> EventQueue | None:
        """Create a tap (read-only view) of an existing Redis stream.

        This creates a new RedisEventQueue instance that starts reading from
        the current end of the stream, receiving only future events.
        """
        if RedisEventQueue is None:
            raise RuntimeError(
                'RedisEventQueue is not available. Cannot create tap. '
                'Please check Redis configuration.'
            )

        # Create a new queue instance for this stream
        queue = RedisEventQueue(
            task_id=task_id,
            redis_client=self._redis,
            stream_prefix=self._stream_prefix,
        )
        # Return a tap that starts from the current end
        return queue.tap()

    async def close(self, task_id: str) -> None:
        """Close the Redis stream for a task.

        This marks the stream as closed in Redis, which will cause all
        readers to receive a CLOSE event.
        """
        if RedisEventQueue is None:
            raise RuntimeError(
                'RedisEventQueue is not available. Cannot close stream. '
                'Please check Redis configuration.'
            )

        # Check if stream already has a CLOSE entry
        stream_key = f'{self._stream_prefix}:{task_id}'
        try:
            # Get the last entry to check if it's already closed
            result = await self._redis.xrevrange(stream_key, '+', '-', count=1)
            if result and result[0][1].get('type') == 'CLOSE':
                # Stream is already closed, no need to add another CLOSE entry
                return
        except Exception as exc:  # noqa: BLE001
            # If we can't check (e.g., stream doesn't exist), proceed with closing
            logger.debug('Could not check if stream is already closed: %s', exc)

        # Create a temporary queue instance just to close the stream
        queue = RedisEventQueue(
            task_id=task_id,
            redis_client=self._redis,
            stream_prefix=self._stream_prefix,
        )
        try:
            await queue.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug('Failed to close queue: %s', exc)

    async def create_or_tap(self, task_id: str) -> EventQueue:
        """Create a new RedisEventQueue or return a tap if stream exists.

        In distributed setup, we always create new instances. If the Redis
        stream already exists, the new queue will start reading from the
        beginning. Use tap() if you want to start from the current end.
        """
        logger.info('create_or_tap called with task_id: %s', task_id)
        logger.info('RedisEventQueue value: %s', RedisEventQueue)
        logger.info('RedisEventQueue type: %s', type(RedisEventQueue))

        if RedisEventQueue is None:
            logger.error('RedisEventQueue is None - import failed!')
            raise RuntimeError(
                'RedisEventQueue is not available. This indicates a critical '
                'configuration issue. Please ensure Redis dependencies are '
                'properly installed and configured.'
            )

        logger.info('Creating RedisEventQueue instance...')
        return RedisEventQueue(
            task_id=task_id,
            redis_client=self._redis,
            stream_prefix=self._stream_prefix,
        )
