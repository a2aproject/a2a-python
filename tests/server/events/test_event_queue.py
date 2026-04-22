import asyncio

from typing import Any

import pytest

from a2a.server.events.event_queue import (
    DEFAULT_MAX_QUEUE_SIZE,
    EventQueueLegacy,
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


@pytest.fixture
def event_queue() -> EventQueueLegacy:
    return EventQueueLegacy()


def test_constructor_default_max_queue_size() -> None:
    """Test that the queue is created with the default max size."""
    eq = EventQueueLegacy()
    assert eq.queue.maxsize == DEFAULT_MAX_QUEUE_SIZE


def test_constructor_max_queue_size() -> None:
    """Test that the asyncio.Queue is created with the specified max_queue_size."""
    custom_size = 123
    eq = EventQueueLegacy(max_queue_size=custom_size)
    assert eq.queue.maxsize == custom_size


def test_constructor_invalid_max_queue_size() -> None:
    """Test that a ValueError is raised for non-positive max_queue_size."""
    with pytest.raises(
        ValueError, match='max_queue_size must be greater than 0'
    ):
        EventQueueLegacy(max_queue_size=0)
    with pytest.raises(
        ValueError, match='max_queue_size must be greater than 0'
    ):
        EventQueueLegacy(max_queue_size=-10)


@pytest.mark.asyncio
async def test_event_queue_async_context_manager(
    event_queue: EventQueueLegacy,
) -> None:
    """Test that EventQueueLegacy can be used as an async context manager."""
    async with event_queue as q:
        assert q is event_queue
        assert event_queue.is_closed() is False
    assert event_queue.is_closed() is True


@pytest.mark.asyncio
async def test_event_queue_async_context_manager_on_exception(
    event_queue: EventQueueLegacy,
) -> None:
    """Test that close() is called even when an exception occurs inside the context."""
    with pytest.raises(RuntimeError, match='boom'):
        async with event_queue:
            raise RuntimeError('boom')
    assert event_queue.is_closed() is True


@pytest.mark.asyncio
async def test_enqueue_and_dequeue_event(event_queue: EventQueueLegacy) -> None:
    """Test that an event can be enqueued and dequeued."""
    event = create_sample_message()
    await event_queue.enqueue_event(event)
    dequeued_event = await event_queue.dequeue_event()
    assert dequeued_event == event


@pytest.mark.asyncio
async def test_dequeue_event_wait(event_queue: EventQueueLegacy) -> None:
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
async def test_task_done(event_queue: EventQueueLegacy) -> None:
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
    event_queue: EventQueueLegacy,
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
    event_queue: EventQueueLegacy,
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
    event_queue: EventQueueLegacy,
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
    child_queue = (
        await event_queue.tap()
    )  # Tap after close might be weird, but let's see
    # The current implementation would add it to _children
    # and then child.close() would be called.
    # A more robust test for child propagation is in test_close_propagates
    await (
        child_queue.close()
    )  # ensure child is also seen as closed for this test's purpose
    with pytest.raises(expected_queue_closed_exception):
        await child_queue.dequeue_event()


@pytest.fixture
def expected_queue_closed_exception() -> type[Exception]:
    return QueueShutDown


@pytest.mark.asyncio
async def test_dequeue_event_closed_and_empty_waits_then_raises(
    event_queue: EventQueueLegacy,
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
        await event_queue.dequeue_event()

    # If the implementation were to change to allow `await self.queue.get()`
    # to be called even when closed (to drain it), then a timeout test would be needed.
    # For now, testing the current behavior.
    # Example of a timeout test if it were to wait:
    # with pytest.raises(asyncio.TimeoutError): # Or QueueEmpty if that's what join/shutdown causes get() to raise


@pytest.mark.asyncio
async def test_tap_creates_child_queue(event_queue: EventQueueLegacy) -> None:
    """Test that tap creates a new EventQueueLegacy and adds it to children."""
    initial_children_count = len(event_queue._children)

    child_queue = await event_queue.tap()

    assert isinstance(child_queue, EventQueueLegacy)
    assert child_queue != event_queue  # Ensure it's a new instance
    assert len(event_queue._children) == initial_children_count + 1
    assert child_queue in event_queue._children

    # Test that the new child queue has the default max size (or specific if tap could configure it)
    assert child_queue.queue.maxsize == DEFAULT_MAX_QUEUE_SIZE


@pytest.mark.asyncio
async def test_close_idempotent(event_queue: EventQueueLegacy) -> None:
    await event_queue.close()
    assert event_queue.is_closed() is True
    await event_queue.close()
    assert event_queue.is_closed() is True


@pytest.mark.asyncio
async def test_is_closed_reflects_state(event_queue: EventQueueLegacy) -> None:
    """Test that is_closed() returns the correct state before and after closing."""
    assert event_queue.is_closed() is False  # Initially open

    await event_queue.close()

    assert event_queue.is_closed() is True  # Closed after calling close()


@pytest.mark.asyncio
async def test_close_with_immediate_true(event_queue: EventQueueLegacy) -> None:
    """Test close with immediate=True clears events immediately."""
    # Add some events to the queue
    event1 = create_sample_message()
    event2 = create_sample_task()
    await event_queue.enqueue_event(event1)
    await event_queue.enqueue_event(event2)

    # Verify events are in queue
    assert not event_queue.queue.empty()

    # Close with immediate=True
    await event_queue.close(immediate=True)

    # Verify queue is closed and empty
    assert event_queue.is_closed() is True
    assert event_queue.queue.empty()


@pytest.mark.asyncio
async def test_close_immediate_propagates_to_children(
    event_queue: EventQueueLegacy,
) -> None:
    """Test that immediate parameter is propagated to child queues."""
    child_queue = await event_queue.tap()

    # Add events to both parent and child
    event = create_sample_message()
    await event_queue.enqueue_event(event)

    assert child_queue.is_closed() is False
    assert child_queue.queue.empty() is False

    # close event queue
    await event_queue.close(immediate=True)

    # Verify child queue was called and empty with immediate=True
    assert child_queue.is_closed() is True
    assert child_queue.queue.empty()


@pytest.mark.asyncio
async def test_close_graceful_waits_for_join_and_children(
    event_queue: EventQueueLegacy,
) -> None:
    child = await event_queue.tap()
    await event_queue.enqueue_event(create_sample_message())

    join_reached = asyncio.Event()
    event_queue._queue = QueueJoinWrapper(event_queue.queue, join_reached)
    child._queue = QueueJoinWrapper(child.queue, join_reached)

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
    event_queue: EventQueueLegacy,
) -> None:
    child_queue1 = await event_queue.tap()
    child_queue2 = await event_queue.tap()
    await event_queue.close()
    assert child_queue1.is_closed()
    assert child_queue2.is_closed()


@pytest.mark.xfail(reason='https://github.com/a2aproject/a2a-python/issues/869')
@pytest.mark.asyncio
async def test_enqueue_close_race_condition() -> None:
    queue = EventQueueLegacy()
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
            'Deadlock in close() because enqueue_event put an item during close but before join()'
        )


@pytest.mark.asyncio
async def test_event_queue_dequeue_immediate_false(
    event_queue: EventQueueLegacy,
) -> None:
    msg = create_sample_message()
    await event_queue.enqueue_event(msg)
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
    event_queue: EventQueueLegacy,
) -> None:
    msg = create_sample_message()
    await event_queue.enqueue_event(msg)
    await event_queue.close(immediate=True)
    # The queue is immediately flushed, so dequeue should raise QueueShutDown
    with pytest.raises(QueueShutDown):
        await event_queue.dequeue_event()


@pytest.mark.asyncio
async def test_event_queue_enqueue_when_closed(
    event_queue: EventQueueLegacy,
) -> None:
    await event_queue.close(immediate=True)
    msg = create_sample_message()
    await event_queue.enqueue_event(msg)
    # Enqueue should have returned without doing anything
    with pytest.raises(QueueShutDown):
        await event_queue.dequeue_event()


@pytest.mark.asyncio
async def test_event_queue_shutdown_wakes_getter(
    event_queue: EventQueueLegacy,
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
    event_queue._queue = QueueWrapper()

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
    event_queue: EventQueueLegacy,
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
    event_queue._queue = QueueJoinWrapper(event_queue.queue, join_reached)
    child_queue._queue = QueueJoinWrapper(child_queue.queue, join_reached)

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
