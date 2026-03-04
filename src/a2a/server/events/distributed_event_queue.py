"""DistributedEventQueue — EventQueue with SNS fan-out for multi-instance A2A."""

import asyncio
import json
import logging

from collections.abc import Awaitable, Callable
from typing import Any

from a2a.server.events.event_queue import (
    DEFAULT_MAX_QUEUE_SIZE,
    Event,
    EventQueue,
)
from a2a.types import (
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)


logger = logging.getLogger(__name__)

# Wire-format type tag used for graceful queue close across instances.
_CLOSE_TYPE = 'close'
_EVENT_TYPE = 'event'

# Map of ``kind`` discriminator → concrete Pydantic model class.
_KIND_TO_TYPE: dict[str, type[Event]] = {
    'message': Message,
    'task': Task,
    'artifact-update': TaskArtifactUpdateEvent,
    'status-update': TaskStatusUpdateEvent,
}


def _serialize_event(
    event: Event,
    task_id: str,
    instance_id: str,
) -> str:
    """Serializes an event into the SNS wire-format JSON string.

    Args:
        event: The event to serialize.
        task_id: The task ID this event belongs to.
        instance_id: The originating instance ID (for deduplication).

    Returns:
        A JSON string suitable for use as an SNS ``Message`` payload.
    """
    payload: dict[str, Any] = {
        'instance_id': instance_id,
        'task_id': task_id,
        'type': _EVENT_TYPE,
        'event_kind': event.kind,
        'event_data': event.model_dump(mode='json'),
    }
    return json.dumps(payload)


def _serialize_close(task_id: str, instance_id: str) -> str:
    """Serializes a close signal into the SNS wire-format JSON string.

    Args:
        task_id: The task ID whose queue is being closed.
        instance_id: The originating instance ID.

    Returns:
        A JSON string suitable for use as an SNS ``Message`` payload.
    """
    payload: dict[str, Any] = {
        'instance_id': instance_id,
        'task_id': task_id,
        'type': _CLOSE_TYPE,
    }
    return json.dumps(payload)


def deserialize_wire_message(
    raw: str,
) -> dict[str, Any]:
    """Parses a raw SNS/SQS wire-format JSON string.

    Args:
        raw: The raw JSON string from an SQS message body.

    Returns:
        The parsed wire-format dictionary. The caller is responsible for
        routing based on the ``type`` field (``'event'`` or ``'close'``).

    Raises:
        ValueError: If the JSON is malformed or the ``type`` field is absent.
    """
    try:
        msg: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f'Malformed wire message: {raw!r}') from exc
    if 'type' not in msg:
        raise ValueError(f"Wire message missing 'type' field: {msg!r}")
    return msg


def decode_event(msg: dict[str, Any]) -> Event | None:
    """Decodes an event from a parsed wire-format dictionary.

    Args:
        msg: A parsed wire-format dictionary with ``event_kind`` and
            ``event_data`` fields.

    Returns:
        The decoded Event, or ``None`` if the ``kind`` is unrecognized.
    """
    kind = msg.get('event_kind')
    event_data = msg.get('event_data')
    if kind is None or event_data is None:
        logger.warning('Wire message missing event_kind or event_data: %s', msg)
        return None
    event_cls = _KIND_TO_TYPE.get(kind)
    if event_cls is None:
        logger.warning('Unknown event kind in wire message: %s', kind)
        return None
    return event_cls.model_validate(event_data)


class DistributedEventQueue(EventQueue):
    """EventQueue subclass that publishes events to SNS for multi-instance delivery.

    When ``enqueue_event`` is called by an agent handler, the event is:

    1. Enqueued locally (for the current instance's SSE stream), **and**
    2. Published asynchronously to SNS (for fan-out to all other instances).

    When the SQS poller on a remote instance receives the SNS notification, it
    calls ``enqueue_local`` directly — bypassing SNS re-publication — to avoid
    infinite broadcast loops.

    Args:
        publish_fn: Async callable ``(message: str) -> None`` that publishes
            the serialized wire message to SNS. Provided by
            :class:`SnsQueueManager` and injected at construction time.
        task_id: The task ID this queue serves.
        instance_id: The unique ID of the local instance (used for
            deduplication of self-published messages).
        max_queue_size: Maximum number of events to buffer locally.
            Defaults to ``DEFAULT_MAX_QUEUE_SIZE``.
    """

    def __init__(
        self,
        publish_fn: Callable[[str], Awaitable[None]],
        task_id: str,
        instance_id: str,
        *,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
    ) -> None:
        """Initializes the DistributedEventQueue."""
        super().__init__(max_queue_size=max_queue_size)
        self._publish_fn = publish_fn
        self._task_id = task_id
        self._instance_id = instance_id
        logger.debug(
            'DistributedEventQueue initialized (task_id=%s, instance=%s).',
            task_id,
            instance_id,
        )

    async def enqueue_event(self, event: Event) -> None:
        """Enqueues the event locally and publishes it to SNS.

        The SNS publish is fire-and-forget (``asyncio.create_task``) so that
        local delivery is never delayed by network I/O.

        Args:
            event: The event to enqueue and broadcast.
        """
        await super().enqueue_event(event)
        asyncio.create_task(self._publish_event(event))  # noqa: RUF006

    async def enqueue_local(self, event: Event) -> None:
        """Enqueues an event locally without re-publishing to SNS.

        Called by the SQS poller when delivering a remote event to this
        instance. Using this method prevents the event from being
        re-broadcast back to SNS, which would create an infinite loop.

        Args:
            event: The event received from the SQS queue.
        """
        await super().enqueue_event(event)

    async def close(self, immediate: bool = False) -> None:
        """Closes the queue locally and publishes a close signal to SNS.

        The close signal allows other instances to also close their local
        queues for the same task, ensuring clean shutdown across the cluster.

        Args:
            immediate: If ``True``, discard buffered events immediately
                rather than waiting for them to drain.
        """
        if not self.is_closed():
            asyncio.create_task(self._publish_close())  # noqa: RUF006
        await super().close(immediate=immediate)

    async def _publish_event(self, event: Event) -> None:
        """Fire-and-forget coroutine: serializes and publishes one event.

        Args:
            event: The event to publish.
        """
        try:
            message = _serialize_event(event, self._task_id, self._instance_id)
            await self._publish_fn(message)
            logger.debug(
                'Event published to SNS (task_id=%s, kind=%s).',
                self._task_id,
                event.kind,
            )
        except Exception:
            logger.exception(
                'Failed to publish event to SNS (task_id=%s).', self._task_id
            )

    async def _publish_close(self) -> None:
        """Fire-and-forget coroutine: publishes the close signal to SNS."""
        try:
            message = _serialize_close(self._task_id, self._instance_id)
            await self._publish_fn(message)
            logger.debug(
                'Close signal published to SNS (task_id=%s).', self._task_id
            )
        except Exception:
            logger.exception(
                'Failed to publish close signal to SNS (task_id=%s).',
                self._task_id,
            )
