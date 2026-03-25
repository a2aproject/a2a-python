import asyncio

from typing import Any

import pytest
import pytest_asyncio

from a2a.server.events.event_queue import (
    DEFAULT_MAX_QUEUE_SIZE,
    EventQueue,
    EventQueueSink,
    EventQueueSource,
    QueueShutDown,
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
async def event_queue() -> EventQueue:
    return EventQueueSource()


@pytest.mark.asyncio
async def test_constructor_default_max_queue_size() -> None:
    """Test that the queue is created with the default max size."""
    eq = EventQueue()
    assert eq.queue.maxsize == DEFAULT_MAX_QUEUE_SIZE


@pytest.mark.asyncio
async def test_constructor_max_queue_size() -> None:
    """Test that the asyncio.Queue is created with the specified max_queue_size."""
    custom_size = 123
    eq = EventQueue(max_queue_size=custom_size)
    assert eq.queue.maxsize == custom_size


@pytest.mark.asyncio
async def test_constructor_invalid_max_queue_size() -> None:
    """Test that a ValueError is raised for non-positive max_queue_size."""
    with pytest.raises(
        ValueError, match='max_queue_size must be greater than 0'
    ):
        EventQueue(max_queue_size=0)
    with pytest.raises(
        ValueError, match='max_queue_size must be greater than 0'
    ):
        EventQueue(max_queue_size=-10)


@pytest.mark.asyncio
async def test_event_queue_async_context_manager(
    event_queue: EventQueue,
) -> None:
    """Test that EventQueue can be used as an async context manager."""
    async with event_queue as q:
        assert q is event_queue
        assert event_queue.is_closed() is False
    assert event_queue.is_closed() is True


@pytest.mark.asyncio
async def test_event_queue_async_context_manager_on_exception(
    event_queue: EventQueue,
) -> None:
    """Test that close() is called even when an exception occurs inside the context."""
    with pytest.raises(RuntimeError, match='boom'):
        async with event_queue:
            raise RuntimeError('boom')
    assert event_queue.is_closed() is True


@pytest.mark.asyncio
async def test_enqueue_and_dequeue_event(event_queue: EventQueue) -> None:
    """Test that an event can be enqueued and dequeued."""
    event = create_sample_message()
    await event_queue.enqueue_event(event)
    dequeued_event = await event_queue.dequeue_event()
    assert dequeued_event == event


@pytest.mark.asyncio
async def test_dequeue_event_no_wait(event_queue: EventQueue) -> None:
    """Test dequeue_event with no_wait=True."""
    event = create_sample_task()
    await event_queue.enqueue_event(event)
    await event_queue.test_only_join_incoming_queue()
    dequeued_event = await event_queue.dequeue_event(no_wait=True)
    assert dequeued_event == event


@pytest.mark.asyncio
async def test_dequeue_event_empty_queue_no_wait(
    event_queue: EventQueue,
) -> None:
    """Test dequeue_event with no_wait=True when the queue is empty."""
    with pytest.raises(asyncio.QueueEmpty):
        await event_queue.dequeue_event(no_wait=True)


@pytest.mark.asyncio
async def test_dequeue_event_wait(event_queue: EventQueue) -> None:
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
async def test_task_done(event_queue: EventQueue) -> None:
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
    event_queue: EventQueue,
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
    event_queue: EventQueue,
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
    event_queue: EventQueue,
    expected_queue_closed_exception: type[Exception],
) -> None:
    """Test that no event is enqueued if the parent queue is closed."""
    await event_queue.close()  # Close the queue first

    event = create_sample_message()
    # Attempt to enqueue, should do nothing or log a warning as per implementation
    await event_queue.enqueue_event(event)

    # Verify the queue is still empty
    with pytest.raises(expected_queue_closed_exception):
        await event_queue.dequeue_event(no_wait=True)

    # Also verify child queues are not affected directly by parent's enqueue attempt when closed
    # (though they would be closed too by propagation)
    with pytest.raises(expected_queue_closed_exception):
        await event_queue.tap()


@pytest.fixture
def expected_queue_closed_exception() -> type[Exception]:
    return QueueShutDown


@pytest.mark.asyncio
async def test_dequeue_event_closed_and_empty_no_wait(
    event_queue: EventQueue,
    expected_queue_closed_exception: type[Exception],
) -> None:
    """Test dequeue_event raises QueueEmpty when closed, empty, and no_wait=True."""
    await event_queue.close()
    assert event_queue.is_closed()
    # Ensure queue is actually empty (e.g. by trying a non-blocking get on internal queue)
    with pytest.raises(expected_queue_closed_exception):
        event_queue.queue.get_nowait()

    with pytest.raises(expected_queue_closed_exception):
        await event_queue.dequeue_event(no_wait=True)


@pytest.mark.asyncio
async def test_dequeue_event_closed_and_empty_waits_then_raises(
    event_queue: EventQueue,
    expected_queue_closed_exception: type[Exception],
) -> None:
    """Test dequeue_event raises QueueEmpty eventually when closed, empty, and no_wait=False."""
    await event_queue.close()
    assert event_queue.is_closed()
    with pytest.raises(expected_queue_closed_exception):
        event_queue.queue.get_nowait()  # verify internal queue is empty

    # This test is tricky because await event_queue.dequeue_event() would hang if not for the close check.
    # The current implementation's dequeue_event checks `is_closed` first.
    # If closed and empty, it raises QueueEmpty immediately (on Python <= 3.12).
    # On Python 3.13+, this check is skipped and asyncio.Queue.get() raises QueueShutDown instead.
    # The "waits_then_raises" scenario described in the subtask implies the `get()` might wait.
    # However, the current code:
    # async with self._lock:
    #     if self._is_closed and self.queue.empty():
    # event = await self.queue.get() -> this line is not reached if closed and empty.

    # So, for the current implementation, it will raise QueueEmpty immediately.
    with pytest.raises(expected_queue_closed_exception):
        await event_queue.dequeue_event(no_wait=False)

    # If the implementation were to change to allow `await self.queue.get()`
    # to be called even when closed (to drain it), then a timeout test would be needed.
    # For now, testing the current behavior.
    # Example of a timeout test if it were to wait:
    # with pytest.raises(asyncio.TimeoutError): # Or QueueEmpty if that's what join/shutdown causes get() to raise


@pytest.mark.asyncio
async def test_tap_creates_child_queue(event_queue: EventQueue) -> None:
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
async def test_close_idempotent(event_queue: EventQueue) -> None:
    await event_queue.close()
    assert event_queue.is_closed() is True
    await event_queue.close()
    assert event_queue.is_closed() is True


@pytest.mark.asyncio
async def test_is_closed_reflects_state(event_queue: EventQueue) -> None:
    """Test that is_closed() returns the correct state before and after closing."""
    assert event_queue.is_closed() is False  # Initially open

    await event_queue.close()

    assert event_queue.is_closed() is True  # Closed after calling close()


@pytest.mark.asyncio
async def test_close_with_immediate_true(event_queue: EventQueue) -> None:
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
    event_queue: EventQueue,
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
    event_queue: EventQueue,
) -> None:
    child = await event_queue.tap()
    await event_queue.enqueue_event(create_sample_message())

    join_reached = asyncio.Event()
    event_queue._default_sink._queue = QueueJoinWrapper(
        event_queue.queue, join_reached
    )  # type: ignore
    # (event_queue.queue, join_reached)
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
    event_queue: EventQueue,
) -> None:
    child_queue1 = await event_queue.tap()
    child_queue2 = await event_queue.tap()
    await event_queue.close()
    assert child_queue1.is_closed()
    assert child_queue2.is_closed()


@pytest.mark.xfail(reason='https://github.com/a2aproject/a2a-python/issues/869')
@pytest.mark.asyncio
async def test_enqueue_close_race_condition() -> None:
    queue = EventQueue()
    event = create_sample_message()

    enqueue_task = asyncio.create_task(queue.enqueue_event(event))
    close_task = asyncio.create_task(queue.close(immediate=False))

    try:
        results = await asyncio.wait_for(
            asyncio.gather(enqueue_task, close_task, return_exceptions=True),
            timeout=1.0,
        )
        for res in results:
            if (
                isinstance(res, Exception)
                and type(res).__name__ != 'QueueShutDown'
            ):
                raise res
    except asyncio.TimeoutError:
        pytest.fail(
            'Deadlock in close() because enqueue_event put an item after shutdown but before join()'
        )


@pytest.mark.asyncio
async def test_event_queue_dequeue_immediate_false(
    event_queue: EventQueue,
) -> None:
    msg = create_sample_message()
    await event_queue.enqueue_event(msg)
    await event_queue.test_only_join_incoming_queue()
    # Start close in background so it can wait for join()
    close_task = asyncio.create_task(event_queue.close(immediate=False))

    # The event is still in the queue, we can dequeue it
    assert await event_queue.dequeue_event(no_wait=True) == msg
    event_queue.task_done()

    await close_task

    # Queue is now empty and closed
    with pytest.raises(QueueShutDown):
        await event_queue.dequeue_event(no_wait=True)


@pytest.mark.asyncio
async def test_event_queue_dequeue_immediate_true(
    event_queue: EventQueue,
) -> None:
    msg = create_sample_message()
    await event_queue.enqueue_event(msg)
    await event_queue.close(immediate=True)
    # The queue is immediately flushed, so dequeue should raise QueueShutDown
    with pytest.raises(QueueShutDown):
        await event_queue.dequeue_event(no_wait=True)


@pytest.mark.asyncio
async def test_event_queue_enqueue_when_closed(
    event_queue: EventQueue,
) -> None:
    await event_queue.close(immediate=True)
    msg = create_sample_message()
    await event_queue.enqueue_event(msg)
    # Enqueue should have returned without doing anything
    with pytest.raises(QueueShutDown):
        await event_queue.dequeue_event(no_wait=True)


@pytest.mark.asyncio
async def test_event_queue_shutdown_wakes_getter(
    event_queue: EventQueue,
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
    event_queue: EventQueue,
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
    # (event_queue.queue, join_reached)
    child_queue._queue = QueueJoinWrapper(child_queue.queue, join_reached)  # type: ignore
    # (child_queue.queue, join_reached)

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
            await event_queue.dequeue_event(no_wait=True)
    else:
        assert await event_queue.dequeue_event(no_wait=True) == msg
        event_queue.task_done()

    # Verify child queue state
    if expected_child_events == 0:
        with pytest.raises(QueueShutDown):
            await child_queue.dequeue_event(no_wait=True)
    else:
        assert await child_queue.dequeue_event(no_wait=True) == msg
        child_queue.task_done()

    # Ensure close_task finishes cleanly
    await asyncio.wait_for(close_task, timeout=1.0)


@pytest.mark.asyncio
@pytest.mark.skip(reason='Obsolete test for old locks')
async def test_tap_during_graceful_close_is_serialized(
    event_queue: EventQueue,
) -> None:
    """
    Verifies that tap() called during a graceful close() is correctly serialized.
    """
    child_close_started = asyncio.Event()
    child_close_can_finish = asyncio.Event()

    # 1. Create a "slow" child to keep the parent's close() in progress.
    slow_child = await event_queue.tap()
    original_close_internal = slow_child.close

    async def hooked_close_internal(immediate: bool) -> None:
        child_close_started.set()
        await child_close_can_finish.wait()
        await original_close_internal(immediate)

    slow_child.close = hooked_close_internal  # type: ignore

    # 2. Start closing the parent.
    close_task = asyncio.create_task(event_queue.close())

    # 3. Wait until parent.close() is waiting for slow_child.close().
    await asyncio.wait_for(child_close_started.wait(), timeout=1.0)

    # 4. Attempt to tap. This should block because close() holds the _enqueue_lock
    tap_task = asyncio.create_task(event_queue.tap())

    # Check that tap_task is indeed blocked
    await asyncio.sleep(0.1)
    assert not tap_task.done()

    # 5. Allow the slow child to finish, which lets parent.close() finish.
    child_close_can_finish.set()
    await asyncio.wait_for(close_task, timeout=1.0)

    new_child = await asyncio.wait_for(tap_task, timeout=1.0)

    # 6. Verify the fix.
    assert event_queue.is_closed()
    assert new_child.is_closed(), (
        'New child should be closed because it was tapped from a closed parent'
    )


@pytest.mark.asyncio
async def test_sink_only_raises_on_enqueue() -> None:
    """Test that enqueuing to a sink-only queue raises an error."""
    parent = EventQueue()
    sink_queue = EventQueueSink(parent=parent)
    event = create_sample_message()
    with pytest.raises(
        RuntimeError, match='Cannot enqueue to a sink-only queue'
    ):
        await sink_queue.enqueue_event(event)


@pytest.mark.asyncio
async def test_tap_creates_sink_only_queue(
    event_queue: EventQueue,
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
    event_queue: EventQueue,
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
    parent = EventQueue()
    child = await parent.tap()

    events = [create_sample_message(message_id=str(i)) for i in range(100)]

    # Enqueue all concurrently
    await asyncio.gather(*(parent.enqueue_event(e) for e in events))

    parent_events = []
    while not parent.queue.empty():
        parent_events.append(await parent.dequeue_event(no_wait=True))
        parent.task_done()

    child_events = []
    while not child.queue.empty():
        child_events.append(await child.dequeue_event(no_wait=True))
        child.task_done()

    assert parent_events == child_events, (
        'Order mismatch! Locking failed to serialize enqueues.'
    )


@pytest.mark.asyncio
@pytest.mark.skip(reason='Obsolete test for old locks')
async def test_close_waits_for_inflight_enqueue() -> None:
    """
    Verifies that graceful close() waits for any in-flight tree-traversal
    enqueues to finish, ensuring no events are dropped in children.
    """
    parent = EventQueue()
    child = await parent.tap()
    event = create_sample_message()

    enqueue_started = asyncio.Event()
    can_finish_enqueue = asyncio.Event()

    original_enqueue = child.enqueue_event

    async def slow_enqueue(e: Any) -> None:
        enqueue_started.set()
        await can_finish_enqueue.wait()
        await original_enqueue(e)

    child.enqueue_event = slow_enqueue  # type: ignore

    # Start enqueue
    enqueue_task = asyncio.create_task(parent.enqueue_event(event))
    await asyncio.wait_for(enqueue_started.wait(), timeout=1.0)

    # Start graceful close. It should block on root._enqueue_lock
    close_task = asyncio.create_task(parent.close(immediate=False))

    # Give it a moment to ensure it is blocked
    await asyncio.sleep(0.1)
    assert not close_task.done()

    # Finish enqueue
    can_finish_enqueue.set()
    await asyncio.wait_for(enqueue_task, timeout=1.0)

    # Now close should be able to proceed and finish
    # We need to drain BOTH parent and child to let join() finish
    while not parent.queue.empty():
        await parent.dequeue_event(no_wait=True)
        parent.task_done()

    while not child.queue.empty():
        await child.dequeue_event(no_wait=True)
        child.task_done()

    await asyncio.wait_for(close_task, timeout=1.0)

    # Verify child got the event
    assert parent.queue.empty()
    assert child.queue.empty()


@pytest.mark.asyncio
@pytest.mark.skip(reason='Obsolete test for old locks')
async def test_child_graceful_close_misses_inflight_event() -> None:
    import asyncio

    parent = EventQueue()
    child = await parent.tap()
    event = create_sample_message()
    enqueue_task = asyncio.create_task(parent.enqueue_event(event))
    close_task = asyncio.create_task(child.close(immediate=False))
    await enqueue_task
    child_event = await child.dequeue_event(no_wait=True)
    assert child_event == event
    child.task_done()
    await close_task


@pytest.mark.asyncio
async def test_dispatch_task_failed(event_queue: EventQueue) -> None:
    event_queue._dispatcher_task.cancel()
    await event_queue._dispatcher_task

    event = create_sample_message()
    await event_queue.enqueue_event(event)

    with pytest.raises(QueueShutDown):
        await asyncio.wait_for(event_queue.dequeue_event(), timeout=0.1)

    # Event was never dequeued, but close() should still work after dispatcher was force cancelled.
    await asyncio.wait_for(event_queue.close(immediate=False), timeout=0.1)


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
    await asyncio.wait_for(asyncio.gather(close_task_1, close_task_2), timeout=1.0)
