import asyncio
import logging
import sys

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any, cast

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


class EventQueue(ABC):
    """Base class and factory for EventQueueSource.

    EventQueue provides an abstraction for a queue of events that can be tapped
    by multiple consumers.
    EventQueue maintain main queue and source and maintain child queues in sync.
    GUARANTEE: All sinks (including the default one) will receive events in the exact same order.

    WARNING (Concurrency): All events from all sinks (both the default queue and any
    tapped child queues) must be regularly consumed and marked as done. If any single
    consumer stops processing and its queue reaches capacity, it can block the event
    dispatcher and stall the entire system, causing a widespread deadlock.

    WARNING (Memory Leak): Event queues spawn background tasks. To prevent memory
    and task leaks, all queue objects (both source and sinks) MUST be explicitly
    closed via `await queue.close()` or by using the async context manager (`async with queue:`).
    Child queues are automatically closed when parent queue is closed, but you
    should still close them explicitly to prevent queues from reaching capacity by
    unconsumed events.

    Typical usage:
    queue = EventQueue()
    child_queue1 = await queue.tap()
    child_queue2 = await queue.tap()

    async for event in child_queue1:
        do_some_work(event)
        child_queue1.task_done()
    """

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        """Redirects instantiation to EventQueueLegacy for backwards compatibility."""
        if cls is EventQueue:
            instance = EventQueueLegacy.__new__(EventQueueLegacy)
            EventQueueLegacy.__init__(instance, *args, **kwargs)
            return cast('Self', instance)
        return super().__new__(cls)

    @abstractmethod
    async def enqueue_event(self, event: Event) -> None:
        """Pushes an event into the queue.

        Only main queue can enqueue events. Child queues can only dequeue events.
        """


@trace_class(kind=SpanKind.SERVER)
class EventQueueLegacy(EventQueue):
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

        self._queue: AsyncQueue[Event] = _create_async_queue(
            maxsize=max_queue_size
        )
        self._children: list[EventQueueLegacy] = []
        self._is_closed = False
        self._lock = asyncio.Lock()
        logger.debug('EventQueue initialized.')

    @property
    def queue(self) -> AsyncQueue[Event]:
        """[DEPRECATED] Returns the underlying asyncio.Queue."""
        return self._queue

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

    async def dequeue_event(self) -> Event:
        """Dequeues an event from the queue.

        This implementation expects that dequeue to raise an exception when
        the queue has been closed. In python 3.13+ this is naturally provided
        by the QueueShutDown exception generated when the queue has closed and
        the user is awaiting the queue.get method. Python<=3.12 this needs to
        manage this lifecycle itself. The current implementation can lead to
        blocking if the dequeue_event is called before the EventQueue has been
        closed but when there are no events on the queue. One way to avoid this
        is to use an async Task management solution to cancel the get task if the queue
        has closed or some other condition is met. The implementation of the
        EventConsumer uses an async.wait with a timeout to abort the
        dequeue_event call and retry, when it will return with a closed error.

        Returns:
            The next event from the queue.

        Raises:
            asyncio.QueueShutDown: If the queue has been closed and is empty.
        """
        async with self._lock:
            if self._is_closed and self.queue.empty():
                logger.warning('Queue is closed. Event will not be dequeued.')
                raise QueueShutDown('Queue is closed.')

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

    async def tap(
        self, max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE
    ) -> 'EventQueueLegacy':
        """Taps the event queue to create a new child queue that receives future events.

        Returns:
            A new `EventQueue` instance that will receive all events enqueued
            to this parent queue from this point forward.
        """
        logger.debug('Tapping EventQueue to create a child queue.')
        queue = EventQueueLegacy(max_queue_size=max_queue_size)
        self._children.append(queue)
        return queue

    async def close(self, immediate: bool = False) -> None:
        """Closes the queue for future push events and also closes all child queues.

        Args:
            immediate: If True, immediately flushes the queue, discarding all pending
                events, and causes any currently blocked `dequeue_event` calls to raise
                `QueueShutDown`. If False (default), the queue is marked as closed to new
                events, but existing events can still be dequeued and processed until the
                queue is fully drained.
        """
        logger.debug('Closing EventQueue.')
        async with self._lock:
            if self._is_closed and not immediate:
                return
            self._is_closed = True

        self.queue.shutdown(immediate)

        await asyncio.gather(
            *(child.close(immediate) for child in self._children)
        )
        if not immediate:
            await self.queue.join()

    def is_closed(self) -> bool:
        """Checks if the queue is closed."""
        return self._is_closed
