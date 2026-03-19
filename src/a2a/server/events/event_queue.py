import asyncio
import logging
import sys

from types import TracebackType
from typing import Any

from typing_extensions import Self


if sys.version_info >= (3, 13):
    from asyncio import Queue as AsyncQueue
    from asyncio import QueueShutDown

    def _create_async_queue(maxsize: int = 0) -> AsyncQueue[Any]:
        """Create a backwards-compatible queue object."""
        return AsyncQueue(maxsize=maxsize)
else:
    import culsans

    from culsans import AsyncQueue  # type: ignore[no-redef]
    from culsans import (
        AsyncQueueShutDown as QueueShutDown,  # type: ignore[no-redef]
    )

    def _create_async_queue(maxsize: int = 0) -> AsyncQueue[Any]:
        """Create a backwards-compatible queue object."""
        return culsans.Queue(maxsize=maxsize).async_q  # type: ignore[no-any-return]


from a2a.types.a2a_pb2 import (
    Message,
    Task,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
)
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


Event = Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
"""Type alias for events that can be enqueued."""

DEFAULT_MAX_QUEUE_SIZE = 1024


@trace_class(kind=SpanKind.SERVER)
class EventQueue:
    """Event queue for A2A responses from agent.

    Acts as a buffer between the agent's asynchronous execution and the
    server's response handling (e.g., streaming via SSE). Supports tapping
    to create child queues that receive the same events.
    """

    def __init__(self, max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE) -> None:
        """Initializes the EventQueue."""
        # Make sure the `asyncio.Queue` is bounded.
        # If it's unbounded (maxsize=0), then `queue.put()` never needs to wait,
        # and so the streaming won't work correctly.
        if max_queue_size <= 0:
            raise ValueError('max_queue_size must be greater than 0')

        self.queue: AsyncQueue[Event] = _create_async_queue(
            maxsize=max_queue_size
        )
        self._children: list[EventQueue] = []
        self._is_closed = False
        self._lock = asyncio.Lock()
        logger.debug('EventQueue initialized.')

    async def __aenter__(self) -> Self:
        """Enters the async context manager, returning the queue itself."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exits the async context manager, ensuring close() is called."""
        await self.close()

    async def enqueue_event(self, event: Event) -> None:
        """Enqueues an event to this queue and all its children.

        Args:
            event: The event object to enqueue.
        """
        async with self._lock:
            if self._is_closed:
                logger.warning('Queue is closed. Event will not be enqueued.')
                return

        logger.debug('Enqueuing event of type: %s', type(event))

        try:
            await self.queue.put(event)
        except QueueShutDown:
            logger.warning('Queue was closed during enqueuing. Event dropped.')
            return

        for child in self._children:
            await child.enqueue_event(event)

    async def dequeue_event(self, no_wait: bool = False) -> Event:
        """Dequeues an event from the queue.

        This implementation expects that dequeue to raise an exception when
        the queue has been closed. In python 3.13+ this is naturally provided
        by the QueueShutDown exception generated when the queue has closed and
        the user is awaiting the queue.get method. Python<=3.12 this needs to
        manage this lifecycle itself. The current implementation can lead to
        blocking if the dequeue_event is called before the EventQueue has been
        closed but when there are no events on the queue. Two ways to avoid this
        are to call this with no_wait = True which won't block, but is the
        callers responsibility to retry as appropriate. Alternatively, one can
        use an async Task management solution to cancel the get task if the queue
        has closed or some other condition is met. The implementation of the
        EventConsumer uses an async.wait with a timeout to abort the
        dequeue_event call and retry, when it will return with a closed error.

        Args:
            no_wait: If True, retrieve an event immediately or raise `asyncio.QueueEmpty`.
                     If False (default), wait until an event is available.

        Returns:
            The next event from the queue.

        Raises:
            asyncio.QueueEmpty: If `no_wait` is True and the queue is empty.
            asyncio.QueueShutDown: If the queue has been closed and is empty.
        """
        async with self._lock:
            if self._is_closed and self.queue.empty():
                logger.warning('Queue is closed. Event will not be dequeued.')
                raise QueueShutDown('Queue is closed.')

        if no_wait:
            logger.debug('Attempting to dequeue event (no_wait=True).')
            event = self.queue.get_nowait()
            logger.debug(
                'Dequeued event (no_wait=True) of type: %s', type(event)
            )
            return event

        logger.debug('Attempting to dequeue event (waiting).')
        event = await self.queue.get()
        logger.debug('Dequeued event (waited) of type: %s', type(event))
        return event

    def task_done(self) -> None:
        """Signals that a formerly enqueued task is complete.

        Used in conjunction with `dequeue_event` to track processed items.
        """
        logger.debug('Marking task as done in EventQueue.')
        self.queue.task_done()

    def tap(self) -> 'EventQueue':
        """Taps the event queue to create a new child queue that receives all future events.

        Returns:
            A new `EventQueue` instance that will receive all events enqueued
            to this parent queue from this point forward.
        """
        logger.debug('Tapping EventQueue to create a child queue.')
        queue = EventQueue()
        self._children.append(queue)
        return queue

    async def close(
        self, immediate: bool = False, clear_parent_events: bool = False
    ) -> None:
        """Closes the queue for future push events and also closes all child queues.

        Args:
            immediate: If True, immediately flushes the queue, discarding all pending
                events, and causes any currently blocked `dequeue_event` calls to raise
                `QueueShutDown`. If False (default), the queue is marked as closed to new
                events, but existing events can still be dequeued and processed until the
                queue is fully drained.
            clear_parent_events: If True, completely clears all pending events from this
                specific parent queue without processing them. This parameter is ignored if
                `immediate=True`.
        """
        logger.debug('Closing EventQueue.')
        async with self._lock:
            if self._is_closed and not immediate:
                return
            self._is_closed = True

        self.queue.shutdown(immediate)

        if clear_parent_events and not immediate:
            await self.clear_events(clear_child_queues=False)

        await asyncio.gather(
            *(child.close(immediate) for child in self._children)
        )
        if not immediate:
            await self.queue.join()

    def is_closed(self) -> bool:
        """Checks if the queue is closed."""
        return self._is_closed

    async def clear_events(self, clear_child_queues: bool = True) -> None:
        """Clears all events from the current queue and optionally all child queues.

        This method removes all pending events from the queue without processing them.
        Child queues can be optionally cleared based on the clear_child_queues parameter.

        Args:
            clear_child_queues: If True (default), clear all child queues as well.
                              If False, only clear the current queue, leaving child queues untouched.
        """
        logger.debug('Clearing all events from EventQueue and child queues.')

        # Clear all events from the queue, even if closed
        cleared_count = 0
        async with self._lock:
            try:
                while True:
                    event = self.queue.get_nowait()
                    logger.debug(
                        'Discarding unprocessed event of type: %s, content: %s',
                        type(event),
                        event,
                    )
                    self.queue.task_done()
                    cleared_count += 1
            except asyncio.QueueEmpty:
                pass
            except QueueShutDown:
                pass

            if cleared_count > 0:
                logger.debug(
                    'Cleared %d unprocessed events from EventQueue.',
                    cleared_count,
                )

        # Clear all child queues (lock released before awaiting child tasks)
        if clear_child_queues and self._children:
            child_tasks = [
                asyncio.create_task(child.clear_events())
                for child in self._children
            ]

            if child_tasks:
                await asyncio.gather(*child_tasks, return_exceptions=True)
