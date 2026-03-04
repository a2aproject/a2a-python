"""SnsQueueManager — distributed QueueManager using SNS/SQS fan-out."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    import aioboto3

    from a2a.server.events.event_queue import EventQueue

from a2a.server.events.distributed_event_queue import (
    DistributedEventQueue,
    decode_event,
    deserialize_wire_message,
)
from a2a.server.events.queue_manager import (
    NoTaskQueue,
    QueueManager,
    TaskQueueExists,
)


logger = logging.getLogger(__name__)


class SnsQueueManager(QueueManager):
    """Distributed QueueManager backed by AWS SNS/SQS fan-out.

    Suitable for multi-instance A2A server deployments (e.g. ECS auto-scaling
    groups). Each instance runs a background SQS polling loop that receives
    events published by other instances via an SNS topic, and routes them to
    the appropriate local :class:`DistributedEventQueue`.

    When the local agent calls :meth:`create_or_tap`, the returned
    :class:`DistributedEventQueue` will publish every enqueued event to SNS,
    ensuring all other instances receive a copy. The SQS poller on those
    instances calls :meth:`~DistributedEventQueue.enqueue_local` to deliver
    the event without re-triggering SNS publication (preventing loops).

    Usage::

        manager = SnsQueueManager(
            topic_arn='arn:aws:sns:us-east-1:123456789012:a2a-events',
            sqs_queue_url='https://sqs.us-east-1.amazonaws.com/123/a2a-instance-xyz',
        )
        await manager.start()
        try:
            # hand manager to DefaultRequestHandler …
        finally:
            await manager.stop()

    Requires the ``[aws]`` optional extra::

        pip install "a2a-sdk[aws]"

    Args:
        topic_arn: ARN of the shared SNS topic.
        sqs_queue_url: URL of *this* instance's SQS queue (created by
            :class:`QueueLifecycleManager`).
        instance_id: Unique ID for this instance. Used to deduplicate
            messages this instance itself published. If *None*, a random UUID
            is generated.
        region_name: AWS region (e.g. ``'us-east-1'``).
        session: Optional pre-created ``aioboto3.Session``. If *None*, a
            new default session is created on first use.
        poll_interval_seconds: Seconds to wait between SQS polling cycles
            when no messages are received. Defaults to ``1.0``.
        max_messages: Maximum number of SQS messages to receive per call
            (1-10, AWS limit). Defaults to ``10``.
        visibility_timeout_seconds: SQS ``VisibilityTimeout`` for each
            ``ReceiveMessage`` call. Defaults to ``30``.
    """

    def __init__(  # noqa: PLR0913
        self,
        topic_arn: str,
        sqs_queue_url: str,
        *,
        instance_id: str | None = None,
        region_name: str = 'us-east-1',
        session: aioboto3.Session | None = None,
        poll_interval_seconds: float = 1.0,
        max_messages: int = 10,
        visibility_timeout_seconds: int = 30,
    ) -> None:
        """Initializes the SnsQueueManager."""
        try:
            import aioboto3  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                'To use SnsQueueManager, install the aws extra: '
                'pip install "a2a-sdk[aws]"'
            ) from exc

        self._topic_arn = topic_arn
        self._sqs_queue_url = sqs_queue_url
        self._instance_id = instance_id or str(uuid.uuid4())
        self._region_name = region_name
        self._session = session or aioboto3.Session()
        self._poll_interval_seconds = poll_interval_seconds
        self._max_messages = max_messages
        self._visibility_timeout_seconds = visibility_timeout_seconds

        self._task_queues: dict[str, EventQueue] = {}
        self._lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._poll_task: asyncio.Task[None] | None = None

        logger.debug(
            'SnsQueueManager created (instance_id=%s, topic=%s, queue=%s).',
            self._instance_id,
            topic_arn,
            sqs_queue_url,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Starts the background SQS polling loop.

        Must be called once before the manager is used. Call :meth:`stop`
        to gracefully shut down the poller.
        """
        if self._poll_task is not None and not self._poll_task.done():
            logger.warning('SnsQueueManager.start() called more than once.')
            return
        self._stop_event.clear()
        self._poll_task = asyncio.create_task(
            self._poll_loop(), name=f'sns-queue-manager-{self._instance_id}'
        )
        logger.info(
            'SnsQueueManager started (instance_id=%s).', self._instance_id
        )

    async def stop(self) -> None:
        """Stops the SQS polling loop and waits for it to finish.

        Safe to call even if :meth:`start` was never called.
        """
        self._stop_event.set()
        if self._poll_task is not None:
            try:
                await self._poll_task
            except Exception:
                logger.exception(
                    'SQS polling task raised an exception during shutdown.'
                )
            self._poll_task = None
        logger.info(
            'SnsQueueManager stopped (instance_id=%s).', self._instance_id
        )

    # ------------------------------------------------------------------
    # QueueManager interface
    # ------------------------------------------------------------------

    async def add(self, task_id: str, queue: EventQueue) -> None:
        """Adds an already-created EventQueue for *task_id*.

        Raises:
            TaskQueueExists: If a queue for *task_id* already exists.
        """
        async with self._lock:
            if task_id in self._task_queues:
                raise TaskQueueExists
            self._task_queues[task_id] = queue
        logger.debug('Queue added for task %s.', task_id)

    async def get(self, task_id: str) -> EventQueue | None:
        """Returns the local queue for *task_id*, or ``None`` if not found."""
        async with self._lock:
            return self._task_queues.get(task_id)

    async def tap(self, task_id: str) -> EventQueue | None:
        """Creates a child queue tap for *task_id*.

        Returns:
            A new child :class:`EventQueue`, or ``None`` if *task_id* is not
            found locally.
        """
        async with self._lock:
            queue = self._task_queues.get(task_id)
            if queue is None:
                return None
            return queue.tap()

    async def close(self, task_id: str) -> None:
        """Closes and removes the local queue for *task_id*.

        Raises:
            NoTaskQueue: If no queue exists for *task_id*.
        """
        async with self._lock:
            if task_id not in self._task_queues:
                raise NoTaskQueue
            queue = self._task_queues.pop(task_id)
        await queue.close()
        logger.debug('Queue closed and removed for task %s.', task_id)

    async def create_or_tap(self, task_id: str) -> EventQueue:
        """Creates or taps a :class:`DistributedEventQueue` for *task_id*.

        If no queue exists for *task_id*, a new :class:`DistributedEventQueue`
        is created. Its ``enqueue_event`` method will publish events to SNS
        so that all other instances receive them via their SQS queues.

        If a queue already exists, a child tap is returned instead.

        Args:
            task_id: The task ID to create or tap a queue for.

        Returns:
            A new or child :class:`EventQueue` for *task_id*.
        """
        async with self._lock:
            if task_id not in self._task_queues:
                queue = DistributedEventQueue(
                    publish_fn=self._sns_publish,
                    task_id=task_id,
                    instance_id=self._instance_id,
                )
                self._task_queues[task_id] = queue
                logger.debug(
                    'DistributedEventQueue created for task %s.', task_id
                )
                return queue
            return self._task_queues[task_id].tap()

    # ------------------------------------------------------------------
    # SNS publish helper (injected into DistributedEventQueue)
    # ------------------------------------------------------------------

    async def _sns_publish(self, message: str) -> None:
        """Publishes a serialized wire message to the SNS topic.

        Args:
            message: JSON string in the distributed wire format.
        """
        async with self._session.client(
            'sns', region_name=self._region_name
        ) as sns:
            await sns.publish(TopicArn=self._topic_arn, Message=message)
        logger.debug('Published message to SNS topic %s.', self._topic_arn)

    # ------------------------------------------------------------------
    # SQS polling loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Background coroutine that polls SQS and routes incoming events.

        Uses long-polling (``WaitTimeSeconds=1``) where supported.  Between
        polling cycles, the loop sleeps for ``poll_interval_seconds`` using an
        :class:`asyncio.Event` that :meth:`stop` can set to cancel the sleep
        immediately (preventing memory leaks from unresolved Promises).
        """
        logger.debug(
            'SQS polling loop starting (instance_id=%s, queue=%s).',
            self._instance_id,
            self._sqs_queue_url,
        )
        async with self._session.client(
            'sqs', region_name=self._region_name
        ) as sqs:
            while not self._stop_event.is_set():
                try:
                    await self._poll_once(sqs)
                except Exception:
                    logger.exception('Error during SQS polling cycle.')

                # Cancellable sleep: wait returns True if stop was signalled.
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self._poll_interval_seconds,
                    )
                    # stop_event was set — exit immediately.
                    break
                except asyncio.TimeoutError:
                    # Normal path: timeout elapsed, continue polling.
                    pass

        logger.debug(
            'SQS polling loop stopped (instance_id=%s).', self._instance_id
        )

    async def _poll_once(self, sqs: Any) -> None:
        """Performs a single SQS ReceiveMessage call and processes results.

        Args:
            sqs: An open aioboto3 SQS client.
        """
        response = await sqs.receive_message(
            QueueUrl=self._sqs_queue_url,
            MaxNumberOfMessages=self._max_messages,
            WaitTimeSeconds=1,
            VisibilityTimeout=self._visibility_timeout_seconds,
        )
        messages = response.get('Messages', [])
        if not messages:
            return

        receipt_handles = []
        for msg in messages:
            receipt_handles.append(msg['ReceiptHandle'])
            try:
                await self._handle_sqs_message(msg)
            except Exception:
                logger.exception(
                    'Failed to handle SQS message %s.', msg.get('MessageId')
                )

        # Batch-delete processed messages.
        if receipt_handles:
            entries = [
                {'Id': str(i), 'ReceiptHandle': rh}
                for i, rh in enumerate(receipt_handles)
            ]
            await sqs.delete_message_batch(
                QueueUrl=self._sqs_queue_url, Entries=entries
            )

    async def _handle_sqs_message(self, sqs_msg: dict[str, Any]) -> None:  # noqa: PLR0911
        """Parses one SQS message and routes the event to the local queue.

        SNS wraps messages in a notification envelope when
        ``RawMessageDelivery`` is not enabled. This handler supports both
        raw delivery and the SNS notification envelope.

        Args:
            sqs_msg: A single SQS message dictionary from ReceiveMessage.
        """
        # Use message ID in warnings to avoid logging potentially sensitive
        # message body content, which may include personal user data.
        msg_id = sqs_msg.get('MessageId', '<no-id>')
        body_str = sqs_msg.get('Body', '{}')

        try:
            body: dict[str, Any] = json.loads(body_str)
        except json.JSONDecodeError:
            logger.warning(
                'SQS message body is not valid JSON (msg_id=%s).', msg_id
            )
            return

        # Unwrap SNS notification envelope if present.
        if body.get('Type') == 'Notification':
            inner_str = body.get('Message', '{}')
            try:
                wire_msg = deserialize_wire_message(inner_str)
            except ValueError:
                logger.warning(
                    'Malformed inner SNS message (msg_id=%s).', msg_id
                )
                return
        else:
            # Raw delivery — body itself is the wire message.
            try:
                wire_msg = deserialize_wire_message(body_str)
            except ValueError:
                logger.warning(
                    'Malformed raw SQS message body (msg_id=%s).', msg_id
                )
                return

        # Deduplicate: ignore messages we published ourselves.
        if wire_msg.get('instance_id') == self._instance_id:
            logger.debug(
                'Ignoring message from self (instance_id=%s).',
                self._instance_id,
            )
            return

        task_id: str = wire_msg.get('task_id', '')
        msg_type: str = wire_msg.get('type', '')

        async with self._lock:
            queue = self._task_queues.get(task_id)

        if queue is None:
            logger.debug(
                'No local queue for task %s — message discarded.', task_id
            )
            return

        if msg_type == 'close':
            await queue.close()
            async with self._lock:
                self._task_queues.pop(task_id, None)
            logger.debug('Queue closed for task %s via remote signal.', task_id)
            return

        if msg_type == 'event':
            event = decode_event(wire_msg)
            if event is None:
                logger.warning('Could not decode event for task %s.', task_id)
                return
            if isinstance(queue, DistributedEventQueue):
                await queue.enqueue_local(event)
            else:
                await queue.enqueue_event(event)
            logger.debug(
                'Event delivered locally for task %s (kind=%s).',
                task_id,
                wire_msg.get('event_kind'),
            )
            return

        logger.warning(
            'Unknown message type %r for task %s — discarded.',
            msg_type,
            task_id,
        )
