import asyncio
import contextlib
import logging
import sys

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


class EventQueue:
    """Base class and factory for EventQueueSource."""

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        """Redirects instantiation to EventQueueSource for backwards compatibility."""
        print(f'EventQueue.__new__ called: {cls}')
        if cls is EventQueue:
            return cast('Self', EventQueueSource.__new__(EventQueueSource, *args, **kwargs))
        return super().__new__(cls)

    @property
    def queue(self) -> AsyncQueue[Event]:
        """Returns the underlying asyncio.Queue.

        NOTE: Interacting directly with this property is error-prone for concurrency
        bugs and should generally be restricted to tests or specific edge-case workarounds.
        """
        raise NotImplementedError

    async def enqueue_event(self, event: Event) -> None:
        """Pushes an event into the queue."""
        raise NotImplementedError

    async def dequeue_event(self, no_wait: bool = False) -> Event:
        """Pulls an event from the queue."""
        raise NotImplementedError

    def task_done(self) -> None:
        """Signals that a formerly enqueued task is complete."""
        raise NotImplementedError

    async def tap(
        self, max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE
    ) -> 'EventQueue':
        """Creates a child queue that receives all future events."""
        raise NotImplementedError

    async def close(self, immediate: bool = False) -> None:
        """Closes the queue and all its child sinks."""
        raise NotImplementedError

    def is_closed(self) -> bool:
        """Checks if the queue is closed.

        NOTE: Relying on this for enqueue logic introduces race conditions.
        It is maintained primarily for backwards compatibility, workarounds for
        Python 3.10/3.12 async queues in consumers, and for the test suite.
        """
        raise NotImplementedError

    async def __aenter__(self) -> Self:
        """Enters the async context manager, returning the queue itself."""
        return self  # type: ignore

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exits the async context manager, ensuring close() is called."""
        await self.close()


@trace_class(kind=SpanKind.SERVER)
class EventQueueSource(EventQueue):
    """The Parent EventQueue.

    Acts as the single entry point for producers. Events pushed here are buffered
    in `_incoming_queue` and distributed to all child Sinks by a background dispatcher task.
    """

    def __init__(self, max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE) -> None:
        """Initializes the EventQueueSource."""
        if max_queue_size <= 0:
            raise ValueError('max_queue_size must be greater than 0')

        self._incoming_queue: AsyncQueue[Event] = _create_async_queue(
            maxsize=max_queue_size
        )
        self._lock = asyncio.Lock()
        self._sinks: set[EventQueueSink] = set()
        self._is_closed = False

        # Internal sink for backward compatibility
        self._default_sink = EventQueueSink(
            parent=self, max_queue_size=max_queue_size
        )
        self._sinks.add(self._default_sink)
        self._dispatcher_task = asyncio.create_task(self._dispatch_loop())

        logger.debug('EventQueueSource initialized.')
        print(f'EventQueueSource initialized: {self}')

    @property
    def queue(self) -> AsyncQueue[Event]:
        """Returns the underlying asyncio.Queue of the default sink."""
        return self._default_sink.queue

    async def _dispatch_loop(self) -> None:
        try:
            while True:
                event = await self._incoming_queue.get()

                async with self._lock:
                    active_sinks = list(self._sinks)

                if active_sinks:
                    await asyncio.gather(
                        *(
                            sink._put_internal(event)  # noqa: SLF001
                            for sink in active_sinks
                        ),
                        return_exceptions=True,
                    )

                self._incoming_queue.task_done()
        except asyncio.CancelledError:
            logger.debug('EventQueueSource._dispatch_loop() cancelled %s', self)
        except QueueShutDown:
            logger.debug('EventQueueSource._dispatch_loop() shutdown %s', self)
        except Exception:
            logger.exception(
                'EventQueueSource._dispatch_loop() failed %s', self
            )
            raise
        finally:
            logger.debug('EventQueueSource._dispatch_loop() finished %s', self)

    async def _join_incoming_queue(self) -> None:
        """Helper to wait for join() while monitoring the dispatcher task."""
        if self._dispatcher_task.done():
            logger.warning(
                'Dispatcher task is not running. Cannot wait for event dispatch.'
            )
            return

        join_task = asyncio.create_task(self._incoming_queue.join())
        done, _pending = await asyncio.wait(
            [join_task, self._dispatcher_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        if join_task in done:
            return

        # Dispatcher task finished before join()
        join_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await join_task

        try:
            if self._dispatcher_task.exception():
                logger.error(
                    'Dispatcher task failed. Events may be lost.',
                    exc_info=self._dispatcher_task.exception(),
                )
            else:
                logger.warning(
                    'Dispatcher task finished unexpectedly. Events may be lost.'
                )
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            logger.warning(
                'Dispatcher task was cancelled or finished. Events may be lost.'
            )

    async def tap(
        self, max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE
    ) -> 'EventQueue':
        """Taps the event queue to create a new child queue that receives all future events."""
        async with self._lock:
            if self._is_closed:
                raise QueueShutDown('Cannot tap a closed EventQueueSource.')
            sink = EventQueueSink(parent=self, max_queue_size=max_queue_size)
            self._sinks.add(sink)
            return sink

    async def remove_sink(self, sink: 'EventQueueSink') -> None:
        """Removes a sink from the source's internal list."""
        async with self._lock:
            self._sinks.remove(sink)

    async def enqueue_event(self, event: Event) -> None:
        """Enqueues an event to this queue and all its children."""
        logger.debug('Enqueuing event of type: %s', type(event))
        try:
            # NO LOCK NEEDED. We rely entirely on the underlying queue's atomic shutdown.
            await self._incoming_queue.put(event)
            # await self._join_incoming_queue()
        except QueueShutDown:
            logger.warning('Queue was closed during enqueuing. Event dropped.')
            return

    async def dequeue_event(self, no_wait: bool = False) -> Event:
        """Dequeues an event from the default internal sink queue."""
        return await self._default_sink.dequeue_event(no_wait=no_wait)

    def task_done(self) -> None:
        """Signals that a formerly enqueued task is complete via the default internal sink queue."""
        self._default_sink.task_done()

    async def close(self, immediate: bool = False) -> None:
        """Closes the queue for future push events and also closes all child sinks."""
        logger.debug('Closing EventQueueSource: immediate=%s', immediate)
        async with self._lock:
            self._is_closed = True
            sinks_to_close = list(self._sinks)

        self._incoming_queue.shutdown(immediate=immediate)

        if immediate:
            self._dispatcher_task.cancel()
            await asyncio.gather(
                *(sink.close(immediate=True) for sink in sinks_to_close)
            )
        else:
            # Wait for all already-enqueued events to be dispatched
            await self._join_incoming_queue()
            self._dispatcher_task.cancel()
            await asyncio.gather(
                *(sink.close(immediate=False) for sink in sinks_to_close)
            )

    def is_closed(self) -> bool:
        """Checks if the queue is closed."""
        return self._is_closed

    async def test_only_join_incoming_queue(self) -> None:
        await self._join_incoming_queue()


class EventQueueSink(EventQueue):
    """The Child EventQueue.

    Acts as a read-only consumer endpoint. Events are pushed here exclusively
    by the parent EventQueueSource's dispatcher task.
    """

    def __init__(
        self,
        parent: EventQueueSource,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
    ) -> None:
        """Initializes the EventQueueSink."""
        if max_queue_size <= 0:
            raise ValueError('max_queue_size must be greater than 0')

        self._parent = parent
        self._queue: AsyncQueue[Event] = _create_async_queue(
            maxsize=max_queue_size
        )
        self._is_closed = False
        self._lock = asyncio.Lock()

        logger.debug('EventQueueSink initialized.')

    @property
    def queue(self) -> AsyncQueue[Event]:
        """Returns the underlying asyncio.Queue of this sink."""
        return self._queue

    async def _put_internal(self, event: Event) -> None:
        with contextlib.suppress(QueueShutDown):
            await self._queue.put(event)

    async def enqueue_event(self, event: Event) -> None:
        """Sinks are read-only and cannot have events directly enqueued to them."""
        raise RuntimeError('Cannot enqueue to a sink-only queue')

    async def dequeue_event(self, no_wait: bool = False) -> Event:
        """Dequeues an event from the sink queue."""
        async with self._lock:
            if self._is_closed and self._queue.empty():
                logger.warning('Queue is closed. Event will not be dequeued.')
                raise QueueShutDown('Queue is closed.')

        if no_wait:
            logger.debug('Attempting to dequeue event (no_wait=True).')
            event = self._queue.get_nowait()
            logger.debug(
                'Dequeued event (no_wait=True) of type: %s', type(event)
            )
            return event

        logger.debug('Attempting to dequeue event (waiting).')
        event = await self._queue.get()
        logger.debug('Dequeued event (waited) of type: %s', type(event))
        return event

    def task_done(self) -> None:
        """Signals that a formerly enqueued task is complete in this sink queue."""
        logger.debug('Marking task as done in EventQueueSink.')
        self._queue.task_done()

    async def tap(
        self, max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE
    ) -> 'EventQueue':
        """Taps the event queue to create a new child queue that receives all future events."""
        # Delegate tap to the parent source so all sinks are flat under the source
        return await self._parent.tap(max_queue_size=max_queue_size)

    async def close(self, immediate: bool = False) -> None:
        """Closes the child sink queue."""
        logger.debug('Closing EventQueueSink.')
        async with self._lock:
            if not self._is_closed:
                self._is_closed = True

        try:
            await self._parent.remove_sink(self)
        except KeyError:
            # Guarantee idempotency.
            pass

        # Atomic shutdown of the consumer queue
        self._queue.shutdown(immediate=immediate)

        if not immediate:
            await self._queue.join()

    def is_closed(self) -> bool:
        """Checks if the sink queue is closed."""
        return self._is_closed
