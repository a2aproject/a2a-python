import asyncio
import contextlib
import logging

from types import TracebackType

from typing_extensions import Self

from a2a.server.events.event_queue import (
    DEFAULT_MAX_QUEUE_SIZE,
    AsyncQueue,
    Event,
    EventQueue,
    QueueShutDown,
    create_async_queue,
)
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


@trace_class(kind=SpanKind.SERVER)
class EventQueueSource(EventQueue):
    """The Parent EventQueue.

    Acts as the single entry point for producers. Events pushed here are buffered
    in `_incoming_queue` and distributed to all child Sinks by a background dispatcher task.
    """

    def __init__(
        self,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        create_default_sink: bool = True,
    ) -> None:
        """Initializes the EventQueueSource."""
        if max_queue_size <= 0:
            raise ValueError('max_queue_size must be greater than 0')

        self._incoming_queue: AsyncQueue[Event] = create_async_queue(
            maxsize=max_queue_size
        )
        self._lock = asyncio.Lock()
        self._sinks: set[EventQueueSink] = set()
        self._is_closed = False

        # Internal sink for backward compatibility
        self._default_sink: EventQueueSink | None
        if create_default_sink:
            self._default_sink = EventQueueSink(
                parent=self, max_queue_size=max_queue_size
            )
            self._sinks.add(self._default_sink)
        else:
            self._default_sink = None

        self._dispatcher_task = asyncio.create_task(self._dispatch_loop())

        self._dispatcher_task_expected_to_cancel = False

        logger.debug('EventQueueSource initialized.')

    @property
    def queue(self) -> AsyncQueue[Event]:
        """Returns the underlying asyncio.Queue of the default sink."""
        if self._default_sink is None:
            raise ValueError('No default sink available.')
        return self._default_sink.queue

    async def _deliver_to_sink(
        self, sink: 'EventQueueSink', event: Event
    ) -> None:
        """Puts one event into one sink, evicting an evict-on-full sink if full.

        A sink whose consumer stopped draining (an abandoned subscriber) fills
        up and, with a blocking put, would stall this dispatcher's ``gather``
        forever: the incoming queue then fills, producers wedge in
        ``enqueue_event``, and the task's event flow never recovers. Sinks
        tapped with ``evict_on_full=True`` are therefore treated as broadcast
        observers: when full, the sink is force-closed and detached so
        dispatch to the remaining sinks continues and blocked producers
        recover. Sinks with ``evict_on_full=False`` (including the default
        sink) keep the blocking put, which is the documented flow-control
        contract.

        The ``full()`` pre-check is race-free: this dispatcher is the only
        writer to a sink queue and consumers only read, so the queue cannot
        grow between the check and the put.
        """
        if sink._evict_on_full and sink.queue.full():  # noqa: SLF001
            logger.warning(
                'Evicting event queue sink %r: its queue is full and the '
                'consumer is not draining it. Closing the sink so dispatch '
                'to the remaining sinks continues.',
                sink,
            )
            await sink.close(immediate=True)
            return
        await sink._put_internal(event)  # noqa: SLF001

    async def _dispatch_loop(self) -> None:
        try:
            while True:
                event = await self._incoming_queue.get()

                async with self._lock:
                    active_sinks = list(self._sinks)

                if active_sinks:
                    results = await asyncio.gather(
                        *(
                            self._deliver_to_sink(sink, event)
                            for sink in active_sinks
                        ),
                        return_exceptions=True,
                    )
                    for result in results:
                        if isinstance(result, Exception):
                            logger.error(
                                'Error dispatching event to sink',
                                exc_info=result,
                            )

                self._incoming_queue.task_done()
        except asyncio.CancelledError:
            logger.debug(
                'EventQueueSource._dispatch_loop() for %s was cancelled',
                self,
            )
            if not self._dispatcher_task_expected_to_cancel:
                # This should only happen on forced shutdown (e.g. tests, server forced stop, etc).
                logger.info(
                    'EventQueueSource._dispatch_loop() for %s was cancelled without '
                    'calling EventQueue.close() first.',
                    self,
                )
                async with self._lock:
                    self._is_closed = True
                    sinks_to_close = list(self._sinks)

                self._incoming_queue.shutdown(immediate=True)
                await asyncio.gather(
                    *(sink.close(immediate=True) for sink in sinks_to_close)
                )
            raise
        except QueueShutDown:
            logger.debug('EventQueueSource._dispatch_loop() shutdown %s', self)
        except Exception:
            logger.exception(
                'EventQueueSource._dispatch_loop() failed %s', self
            )
            raise
        finally:
            logger.debug('EventQueueSource._dispatch_loop() Completed %s', self)

    async def _join_incoming_queue(self) -> None:
        """Helper to wait for join() while monitoring the dispatcher task."""
        if self._dispatcher_task.done():
            logger.warning(
                'Dispatcher task is not running. Cannot wait for event dispatch.'
            )
            return

        join_task = asyncio.create_task(self._incoming_queue.join())
        try:
            done, _pending = await asyncio.wait(
                [join_task, self._dispatcher_task],
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            join_task.cancel()
            raise

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
        self,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        evict_on_full: bool = False,
    ) -> 'EventQueueSink':
        """Taps the event queue to create a new child queue that receives future events.

        Note: The tapped queue may receive some old events if the incoming event
        queue is lagging behind and hasn't dispatched them yet.

        Args:
            max_queue_size: Bound for the new sink's queue.
            evict_on_full: When True, the dispatcher treats this sink as a
                broadcast observer: if its queue is full at delivery time, the
                sink is force-closed and detached instead of blocking dispatch
                (and, transitively, every producer) behind it. When False (the
                default), a full sink back-pressures dispatch, preserving the
                documented flow-control behavior. Use True for sinks whose
                consumer may be abandoned, such as remote subscribers.
        """
        async with self._lock:
            if self._is_closed:
                raise QueueShutDown('Cannot tap a closed EventQueueSource.')
            sink = EventQueueSink(
                parent=self,
                max_queue_size=max_queue_size,
                evict_on_full=evict_on_full,
            )
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
            await self._incoming_queue.put(event)
        except QueueShutDown:
            logger.warning('Queue was closed during enqueuing. Event dropped.')
            return

    async def dequeue_event(self) -> Event:
        """Pulls an event from the default internal sink queue."""
        if self._default_sink is None:
            raise ValueError('No default sink available.')
        return await self._default_sink.dequeue_event()

    def task_done(self) -> None:
        """Signals that a work on dequeued event is complete via the default internal sink queue."""
        if self._default_sink is None:
            raise ValueError('No default sink available.')
        self._default_sink.task_done()

    async def close(self, immediate: bool = False) -> None:
        """Closes the queue and all its child sinks.

        It is safe to call it multiple times.
        If immediate is True, the queue will be closed without waiting for all events to be processed.
        If immediate is False, the queue will be closed after all events are processed (and confirmed with task_done() calls).

        WARNING: Closing the parent queue with immediate=False is a deadlock risk if there are unconsumed events
        in any of the child sinks and the consumer has crashed without draining its queue.
        It is highly recommended to wrap graceful shutdowns with a timeout, e.g.,
        `asyncio.wait_for(queue.close(immediate=False), timeout=...)`.
        """
        logger.debug('Closing EventQueueSource: immediate=%s', immediate)
        async with self._lock:
            # No more tap() allowed.
            self._is_closed = True
            # No more new events can be enqueued.
            self._incoming_queue.shutdown(immediate=immediate)
            sinks_to_close = list(self._sinks)

        if immediate:
            self._dispatcher_task_expected_to_cancel = True
            self._dispatcher_task.cancel()
            await asyncio.gather(
                *(sink.close(immediate=True) for sink in sinks_to_close)
            )
        else:
            # Wait for all already-enqueued events to be dispatched
            await self._join_incoming_queue()
            self._dispatcher_task_expected_to_cancel = True
            self._dispatcher_task.cancel()
            await asyncio.gather(
                *(sink.close(immediate=False) for sink in sinks_to_close)
            )

        # Both branches above cancel the dispatcher task, but the sink gather
        # only awaits the sinks. Await the cancelled dispatcher here so it is
        # never left pending when close() returns -- e.g. a source created with
        # create_default_sink=False and no taps has no sinks for the gather to
        # await, which would otherwise leave the cancelled dispatcher pending
        # and trigger "Task was destroyed but it is pending!".
        #
        # Suppress the expected CancelledError and any error the dispatcher
        # raised while shutting down: close() is teardown (reachable from
        # __aexit__) and must not raise, and _dispatch_loop already logs its own
        # exceptions, so a dispatcher failure stays observable without
        # propagating out of close().
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await self._dispatcher_task

    def is_closed(self) -> bool:
        """[DEPRECATED] Checks if the queue is closed.

        NOTE: Relying on this for enqueue logic introduces race conditions.
        It is maintained primarily for backwards compatibility, workarounds for
        Python 3.10/3.12 async queues in consumers, and for the test suite.
        """
        return self._is_closed

    async def test_only_join_incoming_queue(self) -> None:
        """Wait for incoming queue to be fully processed."""
        await self._join_incoming_queue()

    async def __aenter__(self) -> Self:
        """Enters the async context manager, returning the queue itself.

        WARNING: See `__aexit__` for important deadlock risks associated with
        exiting this context manager if unconsumed events remain.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exits the async context manager, ensuring close() is called.

        WARNING: The context manager calls `close(immediate=False)` by default.
        If a consumer exits the `async with` block early (e.g., due to an exception
        or an explicit `break`) while unconsumed events remain in the queue,
        `__aexit__` will deadlock waiting for `task_done()` to be called on those events.
        """
        await self.close()


class EventQueueSink(EventQueue):
    """The Child EventQueue.

    Acts as a read-only consumer endpoint. Events are pushed here exclusively
    by the parent EventQueueSource's dispatcher task.
    """

    def __init__(
        self,
        parent: EventQueueSource,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        evict_on_full: bool = False,
    ) -> None:
        """Initializes the EventQueueSink.

        Args:
            parent: The EventQueueSource this sink belongs to.
            max_queue_size: Bound for this sink's queue.
            evict_on_full: Dispatch policy for this sink; see
                ``EventQueueSource.tap``.
        """
        if max_queue_size <= 0:
            raise ValueError('max_queue_size must be greater than 0')

        self._parent = parent
        self._evict_on_full = evict_on_full
        self._queue: AsyncQueue[Event] = create_async_queue(
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

    async def dequeue_event(self) -> Event:
        """Pulls an event from the sink queue."""
        logger.debug('Attempting to dequeue event (waiting).')
        event = await self._queue.get()
        logger.debug('Dequeued event: %s', event)
        return event

    def task_done(self) -> None:
        """Signals that a work on dequeued event is complete in this sink queue."""
        logger.debug('Marking task as done in EventQueueSink.')
        self._queue.task_done()

    async def tap(
        self,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
        evict_on_full: bool = False,
    ) -> 'EventQueueSink':
        """Creates a child queue that receives future events.

        Note: The tapped queue may receive some old events if the incoming event
        queue is lagging behind and hasn't dispatched them yet.
        """
        # Delegate tap to the parent source so all sinks are flat under the source
        return await self._parent.tap(
            max_queue_size=max_queue_size, evict_on_full=evict_on_full
        )

    async def close(self, immediate: bool = False) -> None:
        """Closes the child sink queue.

        It is safe to call it multiple times.
        If immediate is True, the queue will be closed without waiting for all events to be processed.
        If immediate is False, the queue will be closed after all events are processed (and confirmed with task_done() calls).
        """
        logger.debug('Closing EventQueueSink.')
        async with self._lock:
            self._is_closed = True
            self._queue.shutdown(immediate=immediate)

        # Ignore KeyError (close have to be idempotent).
        with contextlib.suppress(KeyError):
            await self._parent.remove_sink(self)

        if not immediate:
            await self._queue.join()

    def is_closed(self) -> bool:
        """[DEPRECATED] Checks if the queue is closed.

        NOTE: Relying on this for enqueue logic introduces race conditions.
        It is maintained primarily for backwards compatibility, workarounds for
        Python 3.10/3.12 async queues in consumers, and for the test suite.
        """
        return self._is_closed

    async def __aenter__(self) -> Self:
        """Enters the async context manager, returning the queue itself.

        WARNING: See `__aexit__` for important deadlock risks associated with
        exiting this context manager if unconsumed events remain.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exits the async context manager, ensuring close() is called.

        WARNING: The context manager calls `close(immediate=False)` by default.
        If a consumer exits the `async with` block early (e.g., due to an exception
        or an explicit `break`) while unconsumed events remain in the queue,
        `__aexit__` will deadlock waiting for `task_done()` to be called on those events.
        """
        await self.close()
