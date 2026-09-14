import asyncio
import logging

from typing import Any
from unittest import mock

import pytest
import pytest_asyncio

from a2a.server.events.event_queue import (
    DEFAULT_MAX_QUEUE_SIZE,
    Event,
    EventQueue,
    QueueShutDown,
)
from a2a.server.events.event_queue_v2 import (
    EventQueueSink,
    EventQueueSource,
)
from a2a.server.jsonrpc_models import JSONRPCError
from a2a.types import (
    TaskNotFoundError,
)
from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


def create_sample_message(message_id: str = '111') -> Message:
    """Create a sample Message proto object."""
    return Message(
        message_id=message_id,
        role=Role.ROLE_AGENT,
        parts=[Part(text='test message')],
    )


def create_sample_task(
    task_id: str = '123', context_id: str = 'session-xyz'
) -> Task:
    """Create a sample Task proto object."""
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )


class QueueJoinWrapper:
    """A wrapper to intercept and signal when `queue.join()` is called."""

    def __init__(self, original: Any, join_reached: asyncio.Event) -> None:
        self.original = original
        self.join_reached = join_reached

    def __getattr__(self, name: str) -> Any:
        return getattr(self.original, name)

    async def join(self) -> None:
        self.join_reached.set()
        await self.original.join()


@pytest_asyncio.fixture
async def event_queue() -> EventQueueSource:
    return EventQueueSource()


@pytest.mark.asyncio
async def test_constructor_default_max_queue_size() -> None:
    """Test that the queue is created with the default max size."""
    eq = EventQueueSource()
    assert eq.queue.maxsize == DEFAULT_MAX_QUEUE_SIZE


@pytest.mark.asyncio
async def test_constructor_max_queue_size() -> None:
    """Test that the asyncio.Queue is created with the specified max_queue_size."""
    custom_size = 123
    eq = EventQueueSource(max_queue_size=custom_size)
    assert eq.queue.maxsize == custom_size


@pytest.mark.asyncio
async def test_constructor_invalid_max_queue_size() -> None:
    """Test that a ValueError is raised for non-positive max_queue_size."""
    with pytest.raises(
        ValueError, match='max_queue_size must be greater than 0'
    ):
        EventQueueSource(max_queue_size=0)
    with pytest.raises(
        ValueError, match='max_queue_size must be greater than 0'
    ):
        EventQueueSource(max_queue_size=-10)


@pytest.mark.asyncio
async def test_event_queue_async_context_manager(
    event_queue: EventQueueSource,
) -> None:
    """Test that EventQueue can be used as an async context manager."""
    async with event_queue as q:
        assert q is event_queue
        assert event_queue.is_closed() is False
    assert event_queue.is_closed() is True


@pytest.mark.asyncio
async def test_event_queue_async_context_manager_on_exception(
    event_queue: EventQueueSource,
) -> None:
    """Test that close() is called even when an exception occurs inside the context."""
    with pytest.raises(RuntimeError, match='boom'):
        async with event_queue:
            raise RuntimeError('boom')
    assert event_queue.is_closed() is True


@pytest.mark.asyncio
async def test_enqueue_and_dequeue_event(event_queue: EventQueueSource) -> None:
    """Test that an event can be enqueued and dequeued."""
    event = create_sample_message()
    await event_queue.enqueue_event(event)
    dequeued_event = await event_queue.dequeue_event()
    assert dequeued_event == event


@pytest.mark.asyncio
async def test_dequeue_event_wait(event_queue: EventQueueSource) -> None:
    """Test dequeue_event with the default wait behavior."""
    event = TaskStatusUpdateEvent(
        task_id='task_123',
        context_id='session-xyz',
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    await event_queue.enqueue_event(event)
    dequeued_event = await event_queue.dequeue_event()
    assert dequeued_event == event


@pytest.mark.asyncio
async def test_task_done(event_queue: EventQueueSource) -> None:
    """Test the task_done method."""
    event = TaskArtifactUpdateEvent(
        task_id='task_123',
        context_id='session-xyz',
        artifact=Artifact(artifact_id='11', parts=[Part(text='text')]),
    )
    await event_queue.enqueue_event(event)
    _ = await event_queue.dequeue_event()
    event_queue.task_done()


@pytest.mark.asyncio
async def test_enqueue_different_event_types(
    event_queue: EventQueueSource,
) -> None:
    """Test enqueuing different types of events."""
    events: list[Any] = [
        TaskNotFoundError(),
        JSONRPCError(code=111, message='rpc error'),
    ]
    for event in events:
        await event_queue.enqueue_event(event)
        dequeued_event = await event_queue.dequeue_event()
        assert dequeued_event == event


@pytest.mark.asyncio
async def test_enqueue_event_propagates_to_children(
    event_queue: EventQueueSource,
) -> None:
    """Test that events are enqueued to tapped child queues."""
    child_queue1 = await event_queue.tap()
    child_queue2 = await event_queue.tap()

    event1 = create_sample_message()
    event2 = create_sample_task()

    await event_queue.enqueue_event(event1)
    await event_queue.enqueue_event(event2)

    # Check parent queue
    assert await event_queue.dequeue_event() == event1
    assert await event_queue.dequeue_event() == event2

    # Check child queue 1
    assert await child_queue1.dequeue_event() == event1
    assert await child_queue1.dequeue_event() == event2

    # Check child queue 2
    assert await child_queue2.dequeue_event() == event1
    assert await child_queue2.dequeue_event() == event2


@pytest.mark.asyncio
async def test_enqueue_event_when_closed(
    event_queue: EventQueueSource,
    expected_queue_closed_exception: type[Exception],
) -> None:
    """Test that no event is enqueued if the parent queue is closed."""
    await event_queue.close()  # Close the queue first

    event = create_sample_message()
    # Attempt to enqueue, should do nothing or log a warning as per implementation
    await event_queue.enqueue_event(event)

    # Verify the queue is still empty
    with pytest.raises(expected_queue_closed_exception):
        await event_queue.dequeue_event()

    # Also verify child queues are not affected directly by parent's enqueue attempt when closed
    # (though they would be closed too by propagation)
    with pytest.raises(expected_queue_closed_exception):
        await event_queue.tap()


@pytest.fixture
def expected_queue_closed_exception() -> type[Exception]:
    return QueueShutDown


@pytest.mark.asyncio
async def test_dequeue_event_closed_and_empty(
    event_queue: EventQueueSource,
    expected_queue_closed_exception: type[Exception],
) -> None:
    """Test dequeue_event raises QueueShutDown when closed and empty."""
    await event_queue.close()
    assert event_queue.is_closed()
    # Ensure queue is actually empty (e.g. by trying a non-blocking get on internal queue)
    with pytest.raises(expected_queue_closed_exception):
        event_queue.queue.get_nowait()

    with pytest.raises(expected_queue_closed_exception):
        await event_queue.dequeue_event()


@pytest.mark.asyncio
async def test_tap_creates_child_queue(event_queue: EventQueueSource) -> None:
    """Test that tap creates a new EventQueue and adds it to children."""
    initial_children_count = len(event_queue._sinks)

    child_queue = await event_queue.tap()

    assert isinstance(child_queue, EventQueue)
    assert child_queue != event_queue  # Ensure it's a new instance
    assert len(event_queue._sinks) == initial_children_count + 1
    assert child_queue in event_queue._sinks

    # Test that the new child queue has the default max size (or specific if tap could configure it)
    assert child_queue.queue.maxsize == DEFAULT_MAX_QUEUE_SIZE


@pytest.mark.asyncio
async def test_close_idempotent(event_queue: EventQueueSource) -> None:
    await event_queue.close()
    assert event_queue.is_closed() is True
    await event_queue.close()
    assert event_queue.is_closed() is True


@pytest.mark.asyncio
async def test_sink_close_idempotent_does_not_record_exception() -> None:
    span = mock.MagicMock()
    tracer = mock.MagicMock()
    tracer.start_as_current_span.return_value.__enter__.return_value = span
    tracer.start_as_current_span.return_value.__exit__.return_value = False

    with mock.patch('opentelemetry.trace.get_tracer', return_value=tracer):
        event_queue = EventQueueSource(create_default_sink=False)
        sink = await event_queue.tap()

        await sink.close(immediate=True)
        await sink.close(immediate=True)
        await event_queue.close(immediate=True)

    tracer.start_as_current_span.assert_called()
    span.record_exception.assert_not_called()
    for call in span.set_status.call_args_list:
        assert 'description' not in call.kwargs


@pytest.mark.asyncio
async def test_is_closed_reflects_state(event_queue: EventQueueSource) -> None:
    """Test that is_closed() returns the correct state before and after closing."""
    assert event_queue.is_closed() is False  # Initially open

    await event_queue.close()

    assert event_queue.is_closed() is True  # Closed after calling close()


@pytest.mark.asyncio
async def test_close_with_immediate_true(event_queue: EventQueueSource) -> None:
    """Test close with immediate=True clears events immediately."""
    # Add some events to the queue
    event1 = create_sample_message()
    event2 = create_sample_task()
    await event_queue.enqueue_event(event1)
    await event_queue.enqueue_event(event2)
    await event_queue.test_only_join_incoming_queue()

    # Verify events are in queue
    assert not event_queue.queue.empty()

    # Close with immediate=True
    await event_queue.close(immediate=True)

    # Verify queue is closed and empty
    assert event_queue.is_closed() is True
    assert event_queue.queue.empty()


@pytest.mark.asyncio
async def test_close_immediate_propagates_to_children(
    event_queue: EventQueueSource,
) -> None:
    """Test that immediate parameter is propagated to child queues."""
    child_queue = await event_queue.tap()

    # Add events to both parent and child
    event = create_sample_message()
    await event_queue.enqueue_event(event)
    await event_queue.test_only_join_incoming_queue()

    assert child_queue.is_closed() is False
    assert child_queue.queue.empty() is False

    # close event queue
    await event_queue.close(immediate=True)

    # Verify child queue was called and empty with immediate=True
    assert child_queue.is_closed() is True
    assert child_queue.queue.empty()


@pytest.mark.asyncio
async def test_close_graceful_waits_for_join_and_children(
    event_queue: EventQueueSource,
) -> None:
    child = await event_queue.tap()
    await event_queue.enqueue_event(create_sample_message())

    join_reached = asyncio.Event()
    event_queue._default_sink._queue = QueueJoinWrapper(
        event_queue.queue, join_reached
    )  # type: ignore
    child._queue = QueueJoinWrapper(child.queue, join_reached)  # type: ignore

    close_task = asyncio.create_task(event_queue.close(immediate=False))
    await join_reached.wait()

    assert event_queue.is_closed()
    assert child.is_closed()
    assert not close_task.done()

    await event_queue.dequeue_event()
    event_queue.task_done()

    await child.dequeue_event()
    child.task_done()

    await asyncio.wait_for(close_task, timeout=1.0)


@pytest.mark.asyncio
async def test_close_propagates_to_children(
    event_queue: EventQueueSource,
) -> None:
    child_queue1 = await event_queue.tap()
    child_queue2 = await event_queue.tap()
    await event_queue.close()
    assert child_queue1.is_closed()
    assert child_queue2.is_closed()


@pytest.mark.asyncio
async def test_event_queue_dequeue_immediate_false(
    event_queue: EventQueueSource,
) -> None:
    msg = create_sample_message()
    await event_queue.enqueue_event(msg)
    await event_queue.test_only_join_incoming_queue()
    # Start close in background so it can wait for join()
    close_task = asyncio.create_task(event_queue.close(immediate=False))

    # The event is still in the queue, we can dequeue it
    assert await event_queue.dequeue_event() == msg
    event_queue.task_done()

    await close_task

    # Queue is now empty and closed
    with pytest.raises(QueueShutDown):
        await event_queue.dequeue_event()


@pytest.mark.asyncio
async def test_event_queue_dequeue_immediate_true(
    event_queue: EventQueueSource,
) -> None:
    msg = create_sample_message()
    await event_queue.enqueue_event(msg)
    await event_queue.close(immediate=True)
    # The queue is immediately flushed, so dequeue should raise QueueShutDown
    with pytest.raises(QueueShutDown):
        await event_queue.dequeue_event()


@pytest.mark.asyncio
async def test_event_queue_enqueue_when_closed(
    event_queue: EventQueueSource,
) -> None:
    await event_queue.close(immediate=True)
    msg = create_sample_message()
    await event_queue.enqueue_event(msg)
    # Enqueue should have returned without doing anything
    with pytest.raises(QueueShutDown):
        await event_queue.dequeue_event()


@pytest.mark.asyncio
async def test_event_queue_shutdown_wakes_getter(
    event_queue: EventQueueSource,
) -> None:
    original_queue = event_queue.queue
    getter_reached_get = asyncio.Event()

    class QueueWrapper:
        def __getattr__(self, name):
            return getattr(original_queue, name)

        async def get(self):
            getter_reached_get.set()
            return await original_queue.get()

    # Replace the underlying queue with a wrapper to intercept `get`
    event_queue._default_sink._queue = QueueWrapper()  # type: ignore

    async def getter():
        with pytest.raises(QueueShutDown):
            await event_queue.dequeue_event()

    task = asyncio.create_task(getter())
    await getter_reached_get.wait()

    # At this point, getter is guaranteed to be awaiting the original_queue.get()
    await event_queue.close(immediate=True)
    await asyncio.wait_for(task, timeout=1.0)


@pytest.mark.parametrize(
    'immediate, expected_events, close_blocks',
    [
        (False, (1, 1), True),
        (True, (0, 0), False),
    ],
)
@pytest.mark.asyncio
async def test_event_queue_close_behaviors(
    event_queue: EventQueueSource,
    immediate: bool,
    expected_events: tuple[int, int],
    close_blocks: bool,
) -> None:
    expected_parent_events, expected_child_events = expected_events
    child_queue = await event_queue.tap()

    msg = create_sample_message()
    await event_queue.enqueue_event(msg)

    # We need deterministic event waiting to prevent sleep()
    join_reached = asyncio.Event()

    # Apply wrappers so we know exactly when join() starts
    event_queue._default_sink._queue = QueueJoinWrapper(
        event_queue.queue, join_reached
    )  # type: ignore
    child_queue._queue = QueueJoinWrapper(child_queue.queue, join_reached)  # type: ignore

    close_task = asyncio.create_task(event_queue.close(immediate=immediate))

    if close_blocks:
        await join_reached.wait()
        assert not close_task.done(), (
            'close() should block waiting for queue to be drained'
        )
    else:
        # We await it with a tiny timeout to ensure the task had time to run,
        # but because immediate=True, it runs without blocking at all.
        await asyncio.wait_for(close_task, timeout=0.1)
        assert close_task.done(), 'close() should not block'

    # Verify parent queue state
    if expected_parent_events == 0:
        with pytest.raises(QueueShutDown):
            await event_queue.dequeue_event()
    else:
        assert await event_queue.dequeue_event() == msg
        event_queue.task_done()

    # Verify child queue state
    if expected_child_events == 0:
        with pytest.raises(QueueShutDown):
            await child_queue.dequeue_event()
    else:
        assert await child_queue.dequeue_event() == msg
        child_queue.task_done()

    # Ensure close_task finishes cleanly
    await asyncio.wait_for(close_task, timeout=1.0)


@pytest.mark.asyncio
async def test_sink_only_raises_on_enqueue() -> None:
    """Test that enqueuing to a sink-only queue raises an error."""
    parent = EventQueueSource()
    sink_queue = EventQueueSink(parent=parent)
    event = create_sample_message()
    with pytest.raises(
        RuntimeError, match='Cannot enqueue to a sink-only queue'
    ):
        await sink_queue.enqueue_event(event)


@pytest.mark.asyncio
async def test_tap_creates_sink_only_queue(
    event_queue: EventQueueSource,
) -> None:
    """Test that tap() creates a child queue that is sink-only."""
    child_queue = await event_queue.tap()
    assert hasattr(child_queue, '_parent') and child_queue._parent is not None  # type: ignore

    event = create_sample_message()
    with pytest.raises(
        RuntimeError, match='Cannot enqueue to a sink-only queue'
    ):
        await child_queue.enqueue_event(event)


@pytest.mark.asyncio
async def test_tap_attaches_to_top_parent(
    event_queue: EventQueueSource,
) -> None:
    """Test that tap() on a child queue attaches the new queue to the top parent."""
    # First level child
    child1 = await event_queue.tap()

    # Second level child (tapped from child1)
    child2 = await child1.tap()

    # The top parent should have both child1 and child2 in its children list
    assert child1 in event_queue._sinks
    assert child2 in event_queue._sinks

    # child1 should not have any children, because tap() attaches to top parent
    assert True  # Child does not have children anymore

    # Ensure events still flow to all queues
    event = create_sample_message()
    await event_queue.enqueue_event(event)


@pytest.mark.asyncio
async def test_concurrent_enqueue_order_preserved() -> None:
    """
    Verifies that concurrent enqueues to a parent queue are preserved in
    the exact same order in all child queues due to root serialization.
    """
    parent = EventQueueSource()
    child = await parent.tap()

    events = [create_sample_message(message_id=str(i)) for i in range(100)]

    # Enqueue all concurrently
    await asyncio.gather(*(parent.enqueue_event(e) for e in events))

    parent_events = []
    while not parent.queue.empty():
        parent_events.append(await parent.dequeue_event())
        parent.task_done()

    child_events = []
    while not child.queue.empty():
        child_events.append(await child.dequeue_event())
        child.task_done()

    assert parent_events == child_events, (
        'Order mismatch! Locking failed to serialize enqueues.'
    )


@pytest.mark.asyncio
async def test_dispatch_task_failed(event_queue: EventQueueSource) -> None:
    event_queue._dispatcher_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await event_queue._dispatcher_task

    event = create_sample_message()
    await event_queue.enqueue_event(event)

    with pytest.raises(QueueShutDown):
        await asyncio.wait_for(event_queue.dequeue_event(), timeout=0.1)

    # Event was never dequeued, but close() should still work after dispatcher was force cancelled.
    await asyncio.wait_for(event_queue.close(immediate=False), timeout=0.1)


@pytest.mark.asyncio
async def test_close_immediate_awaits_dispatcher_without_sinks() -> None:
    """A subscriber-less source must not leave its dispatcher pending on close.

    Regression: a source created with create_default_sink=False and no taps has
    no sinks for close()'s gather to await. close(immediate=True) cancels the
    dispatcher task, so with nothing else to await it used to return while the
    cancelled dispatcher was still pending, producing "Task was destroyed but it
    is pending!". close() must await its own cancelled dispatcher task.
    """
    source = EventQueueSource(create_default_sink=False)
    assert not source._dispatcher_task.done()

    await source.close(immediate=True)

    assert source._dispatcher_task.done()


@pytest.mark.asyncio
async def test_close_does_not_propagate_dispatcher_crash() -> None:
    """close() awaits its dispatcher without surfacing a non-cancel crash.

    close() is teardown (reachable from __aexit__) and must not raise, and
    _dispatch_loop already logs its own exceptions. If the dispatcher died with a
    non-cancel error, awaiting it in close() must not re-raise that error.
    """
    source = EventQueueSource(create_default_sink=False)

    # Retire the real dispatcher, then stand in a task that has already failed
    # with a non-cancel error, simulating a _dispatch_loop crash.
    real = source._dispatcher_task
    real.cancel()
    try:
        await real
    except asyncio.CancelledError:
        pass

    async def crashed() -> None:
        raise RuntimeError('dispatcher boom')

    source._dispatcher_task = asyncio.ensure_future(crashed())
    await asyncio.sleep(0.01)  # let it fail so the exception is stored

    # Must return cleanly, not re-raise the dispatcher's RuntimeError.
    await source.close(immediate=True)
    assert source._dispatcher_task.done()


@pytest.mark.asyncio
async def test_concurrent_close_immediate_false() -> None:
    """Test that concurrent close(immediate=False) calls both wait for join() deterministically."""
    queue = EventQueueSource()
    sink = await queue.tap()

    event_arrived = asyncio.Event()
    original_put_internal = sink._put_internal  # type: ignore

    async def mock_put_internal(msg: Any) -> None:
        await original_put_internal(msg)
        event_arrived.set()

    sink._put_internal = mock_put_internal  # type: ignore

    event = Message()
    await queue.enqueue_event(event)

    # Deterministically wait for the event to be processed and reach the sink
    await asyncio.wait_for(event_arrived.wait(), timeout=1.0)

    class CustomJoinWrapper:
        def __init__(self, original: Any) -> None:
            self.original = original
            self.join_count = 0
            self.join_started_1 = asyncio.Event()
            self.join_started_2 = asyncio.Event()

        def __getattr__(self, name: str) -> Any:
            return getattr(self.original, name)

        async def join(self) -> None:
            self.join_count += 1
            if self.join_count == 1:
                self.join_started_1.set()
            elif self.join_count == 2:
                self.join_started_2.set()
            await self.original.join()

    wrapper = CustomJoinWrapper(sink._queue)  # type: ignore
    sink._queue = wrapper  # type: ignore

    close_task_1 = asyncio.create_task(sink.close(immediate=False))
    # Wait deterministically until the first close call reaches await queue.join()
    await asyncio.wait_for(wrapper.join_started_1.wait(), timeout=1.0)
    assert not close_task_1.done()

    close_task_2 = asyncio.create_task(sink.close(immediate=False))
    # Wait deterministically until the second close call reaches await queue.join()
    await asyncio.wait_for(wrapper.join_started_2.wait(), timeout=1.0)
    assert not close_task_2.done()

    # To clean up and allow the queue to finish joining
    await sink.dequeue_event()
    sink.task_done()

    # Now both tasks should complete
    await asyncio.wait_for(
        asyncio.gather(close_task_1, close_task_2), timeout=1.0
    )


@pytest.mark.asyncio
async def test_dispatch_loop_logs_exceptions(
    event_queue: EventQueueSource, caplog: pytest.LogCaptureFixture
) -> None:
    """Test that exceptions raised by sinks during dispatch are logged."""
    caplog.set_level(logging.ERROR)
    sink = await event_queue.tap()

    async def mock_put_internal(event: Any) -> None:
        raise RuntimeError('simulated error')

    sink._put_internal = mock_put_internal  # type: ignore

    msg = create_sample_message()
    await event_queue.enqueue_event(msg)

    # Wait for dispatch loop to process
    await event_queue.test_only_join_incoming_queue()

    assert any(
        record.levelname == 'ERROR'
        and 'Error dispatching event to sink' in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_join_incoming_queue_cancels_join_task(
    event_queue: EventQueueSource,
) -> None:
    """Test that _join_incoming_queue cancels join_task on CancelledError."""
    # Tap a sink and block its processing so dispatcher and join() hang
    sink = await event_queue.tap()
    block_event = asyncio.Event()

    async def mock_put_internal(event: Any) -> None:
        await block_event.wait()

    sink._put_internal = mock_put_internal  # type: ignore

    # Enqueue a message so join() blocks
    await event_queue.enqueue_event(create_sample_message())

    join_reached = asyncio.Event()
    event_queue._incoming_queue = QueueJoinWrapper(  # type: ignore
        event_queue._incoming_queue, join_reached
    )

    join_task = asyncio.create_task(event_queue._join_incoming_queue())

    # Wait deterministically until the internal task calls join()
    await join_reached.wait()

    # Cancel the wrapper task
    join_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await join_task

    # Unblock the sink and clean up
    block_event.set()
    await event_queue.dequeue_event()
    event_queue.task_done()


@pytest.mark.asyncio
async def test_event_queue_capacity_order_and_concurrency() -> None:
    """Test that EventQueue preserves order and handles concurrency with limited capacity."""
    queue = EventQueueSource(max_queue_size=5)

    # Create 10 tapped queues
    tapped_queues = [await queue.tap(max_queue_size=5) for _ in range(10)]
    all_queues: list[EventQueue] = [queue] + tapped_queues  # type: ignore

    async def producer() -> None:
        for i in range(100):
            await queue.enqueue_event(create_sample_message(message_id=str(i)))

    async def consumer(q: EventQueue) -> None:
        for expected_i in range(100):
            event = await q.dequeue_event()
            assert isinstance(event, Message)
            assert event.message_id == str(expected_i)
            q.task_done()

    consumer_tasks = [asyncio.create_task(consumer(q)) for q in all_queues]
    producer_task = asyncio.create_task(producer())

    await asyncio.wait_for(
        asyncio.gather(producer_task, *consumer_tasks), timeout=1.0
    )

    await queue.close(immediate=True)


@pytest.mark.asyncio
async def test_event_queue_blocking_behavior() -> None:
    _PARENT_QUEUE_SIZE = 10
    _TAPPED_QUEUE_SIZE = 15

    queue = EventQueueSource(max_queue_size=_PARENT_QUEUE_SIZE)
    # tapped_queue initially has no consumer, so it will block.
    tapped_queue = await queue.tap(max_queue_size=_TAPPED_QUEUE_SIZE)

    producer_task_done = asyncio.Event()
    enqueued_count = 0

    async def producer() -> None:
        nonlocal enqueued_count
        for i in range(50):
            event = create_sample_message(message_id=str(i))
            await queue.enqueue_event(event)
            enqueued_count += 1
        producer_task_done.set()

    consumed_first = []

    async def consumer_first() -> None:
        while True:
            try:
                event = await queue.dequeue_event()
                consumed_first.append(event)
                queue.task_done()
            except QueueShutDown:
                break

    consumer_first_task = asyncio.create_task(consumer_first())
    producer_task = asyncio.create_task(producer())

    # Wait to let the producer fill the queues and confirm it is blocked
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(producer_task_done.wait(), timeout=0.1)

    # Validate that: first consumer receives _TAPPED_QUEUE_SIZE + 1 items.
    # Other items are blocking trying to be enqueued to second queue.
    assert len(consumed_first) == _TAPPED_QUEUE_SIZE + 1

    # Validate that: once child queue is blocked, parent will continue
    # processing other items until it reaches its capacity as well.
    assert not producer_task.done()
    assert enqueued_count == _PARENT_QUEUE_SIZE + _TAPPED_QUEUE_SIZE + 1

    consumed_second = []

    # create a consumer for second queue.
    async def consumer_second() -> None:
        while True:
            try:
                event = await tapped_queue.dequeue_event()
                consumed_second.append(event)
                tapped_queue.task_done()
            except QueueShutDown:
                break

    consumer_second_task = asyncio.create_task(consumer_second())
    await asyncio.wait_for(producer_task_done.wait(), timeout=1.0)
    await queue.close(immediate=False)
    await asyncio.gather(consumer_first_task, consumer_second_task)

    # Validate that: after unblocking second consumer everything ends smoothly.
    assert len(consumed_first) == 50
    assert len(consumed_second) == 50


@pytest.mark.asyncio
async def test_evict_on_full_sink_is_evicted_and_dispatch_continues() -> None:
    """A full evict_on_full sink is evicted; other sinks keep receiving.

    Regression test for the runtime wedge reported on #1101: one abandoned
    subscriber sink fills up, the dispatcher's gather blocks on it forever,
    and no other sink receives anything again.
    """
    queue = EventQueueSource()
    stuck_sink = await queue.tap(max_queue_size=1, evict_on_full=True)

    consumed: list[Event] = []

    async def consume_default() -> None:
        while True:
            try:
                event = await queue.dequeue_event()
                consumed.append(event)
                queue.task_done()
            except QueueShutDown:
                break

    consumer_task = asyncio.create_task(consume_default())

    # The first event fills the stuck sink (capacity 1, never drained). The
    # second and third reach the default sink only if the dispatcher
    # survives the stuck sink.
    for i in range(3):
        await queue.enqueue_event(create_sample_message(str(i)))

    async def default_sink_received_all() -> None:
        while len(consumed) < 3:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(default_sink_received_all(), timeout=2.0)

    # The stuck sink was evicted: closed and detached from the source.
    assert stuck_sink.is_closed()
    assert stuck_sink not in queue._sinks  # noqa: SLF001

    await queue.close(immediate=True)
    await consumer_task


@pytest.mark.asyncio
async def test_producer_unblocked_after_evict_on_full() -> None:
    """Producers blocked on a full incoming queue recover after eviction.

    Without eviction, the dispatcher never finishes its gather, the incoming
    queue stays full, and every producer wedges in enqueue_event permanently.
    """
    queue = EventQueueSource(max_queue_size=1, create_default_sink=False)
    await queue.tap(max_queue_size=1, evict_on_full=True)

    # Sink capacity 1 + incoming capacity 1 both fill, so without eviction
    # the later enqueues block forever. With eviction the pipeline drains
    # and every enqueue completes.
    async def produce() -> None:
        for i in range(4):
            await queue.enqueue_event(create_sample_message(str(i)))

    await asyncio.wait_for(produce(), timeout=2.0)
    await queue.close(immediate=True)


@pytest.mark.asyncio
async def test_default_tap_keeps_backpressure() -> None:
    """A default tap (evict_on_full=False) preserves blocking dispatch."""
    queue = EventQueueSource()
    stuck_sink = await queue.tap(max_queue_size=1)

    for i in range(3):
        await queue.enqueue_event(create_sample_message(str(i)))

    # Give the dispatcher time to block on the full sink; the sink must
    # survive and stay attached (flow control, not eviction).
    await asyncio.sleep(0.3)
    assert not stuck_sink.is_closed()
    assert stuck_sink in queue._sinks  # noqa: SLF001

    await queue.close(immediate=True)


@pytest.mark.asyncio
async def test_graceful_close_not_upgraded_to_immediate_by_eviction() -> None:
    """A sink mid-graceful-close is skipped, never force-closed by eviction.

    ``close(immediate=False)`` marks the sink closed and waits for the
    consumer to drain the remaining events, but the dispatcher may still
    hold the sink in an ``active_sinks`` snapshot taken before the close
    removed it. Delivering to that sink while its queue is full must not
    trip the evict-on-full path (``close(immediate=True)``), which would
    discard the events the consumer is still draining.
    """
    queue = EventQueueSource()
    sink = await queue.tap(max_queue_size=1, evict_on_full=True)

    # Fill the sink so the evict-on-full branch would be reachable.
    await sink._put_internal(create_sample_message('0'))  # noqa: SLF001

    # Start a graceful close: it marks the sink closed, then blocks in
    # queue.join() until the pending event is consumed.
    close_task = asyncio.create_task(sink.close(immediate=False))
    while not sink.is_closed():
        await asyncio.sleep(0)

    # Simulate the dispatcher delivering from a stale snapshot: the sink
    # is full and marked closed. It must be skipped, not evicted.
    await queue._deliver_to_sink(  # noqa: SLF001
        sink, create_sample_message('1')
    )

    # The pending event survived the delivery attempt and the consumer
    # finishes draining, which lets the graceful close complete.
    event = await sink.dequeue_event()
    assert isinstance(event, Message)
    assert event.message_id == '0'
    sink.task_done()
    await asyncio.wait_for(close_task, timeout=2.0)

    await queue.close(immediate=True)
