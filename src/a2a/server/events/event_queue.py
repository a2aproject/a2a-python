import asyncio
import logging
import sys

from a2a.types import (
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

        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=max_queue_size)
        self._children: list[EventQueue] = []
        self._is_closed = False
        self._lock = asyncio.Lock()
        logger.debug('EventQueue initialized.')

    async def enqueue_event(self, event: Event) -> None:
        """Enqueues an event to this queue and all its children.

        Args:
            event: The event object to enqueue.
        """
        async with self._lock:
            if self._is_closed:
                logger.warning('Queue is closed. Event will not be enqueued.')
                return

        logger.debug(f'Enqueuing event of type: {type(event)}')

        # Make sure to use put instead of put_nowait to avoid blocking the event loop.
        await self.queue.put(event)
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
        use a async Task management solution to cancel the get task if the queue
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
                raise asyncio.QueueEmpty('Queue is closed.')

        if no_wait:
            logger.debug('Attempting to dequeue event (no_wait=True).')
            event = self.queue.get_nowait()
            logger.debug(
                f'Dequeued event (no_wait=True) of type: {type(event)}'
            )
            return event

        logger.debug('Attempting to dequeue event (waiting).')
        event = await self.queue.get()
        logger.debug(f'Dequeued event (waited) of type: {type(event)}')
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

    async def close(self, immediate: bool = False) -> None:
        """Closes the queue for future push events and also closes all child queues.

        Once closed, no new events can be enqueued. For Python 3.13+, this will trigger
        `asyncio.QueueShutDown` when the queue is empty and a consumer tries to dequeue.
        For lower versions, the queue will be marked as closed and optionally cleared.

        Args:
            immediate (bool):
                - True: Immediately closes the queue and clears all unprocessed events without waiting for them to be consumed. This is suitable for scenarios where you need to forcefully interrupt and quickly release resources.
                - False (default): Gracefully closes the queue, waiting for all queued events to be processed (i.e., the queue is drained) before closing. This is suitable when you want to ensure all events are handled.

        """
        logger.debug('Closing EventQueue.')
        async with self._lock:
            # If already closed, just return.
            if self._is_closed and not immediate:
                return
            if not self._is_closed:
                self._is_closed = True
        # If using python 3.13 or higher, use the shutdown method
        if sys.version_info >= (3, 13):
            self.queue.shutdown(immediate)
            for child in self._children:
                await child.close(immediate)
        # Otherwise, join the queue
        else:
            if immediate:
                await self.clear_events(True)
                return
            tasks = [asyncio.create_task(self.queue.join())]
            tasks.extend(
                asyncio.create_task(child.close()) for child in self._children
            )
            await asyncio.wait(tasks, return_when=asyncio.ALL_COMPLETED)

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
            while not self.queue.empty():
                try:
                    event = self.queue.get_nowait()
                    logger.debug(
                        f'Discarding unprocessed event of type: {type(event)}, content: {event}'
                    )
                    self.queue.task_done()
                    cleared_count += 1
                except asyncio.QueueEmpty:
                    break

            if cleared_count > 0:
                logger.debug(
                    f'Cleared {cleared_count} unprocessed events from EventQueue.'
                )

        # Clear all child queues (lock released before awaiting child tasks)
        if clear_child_queues and self._children:
            child_tasks = [
                asyncio.create_task(child.clear_events())
                for child in self._children
            ]

            if child_tasks:
                await asyncio.gather(*child_tasks, return_exceptions=True)
