import asyncio
import sys

from typing import Any
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

import pytest

from a2a.server.events.event_queue import DEFAULT_MAX_QUEUE_SIZE, EventQueue
from a2a.types import (
    A2AError,
    Artifact,
    JSONRPCError,
    Message,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskNotFoundError,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)


MINIMAL_TASK: dict[str, Any] = {
    'id': '123',
    'context_id': 'session-xyz',
    'status': {'state': 'submitted'},
    'kind': 'task',
}
MESSAGE_PAYLOAD: dict[str, Any] = {
    'role': 'agent',
    'parts': [{'text': 'test message'}],
    'message_id': '111',
}


@pytest.fixture
def event_queue() -> EventQueue:
    return EventQueue()


def test_constructor_default_max_queue_size() -> None:
    """Test that the queue is created with the default max size."""
    eq = EventQueue()
    assert eq.queue.maxsize == DEFAULT_MAX_QUEUE_SIZE


def test_constructor_max_queue_size() -> None:
    """Test that the asyncio.Queue is created with the specified max_queue_size."""
    custom_size = 123
    eq = EventQueue(max_queue_size=custom_size)
    assert eq.queue.maxsize == custom_size


def test_constructor_invalid_max_queue_size() -> None:
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
async def test_enqueue_and_dequeue_event(event_queue: EventQueue) -> None:
    """Test that an event can be enqueued and dequeued."""
    event = Message(**MESSAGE_PAYLOAD)
    await event_queue.enqueue_event(event)
    dequeued_event = await event_queue.dequeue_event()
    assert dequeued_event == event


@pytest.mark.asyncio
async def test_dequeue_event_no_wait(event_queue: EventQueue) -> None:
    """Test dequeue_event with no_wait=True."""
    event = Task(**MINIMAL_TASK)
    await event_queue.enqueue_event(event)
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
        status=TaskStatus(state=TaskState.working),
        final=True,
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
        artifact=Artifact(
            artifact_id='11', parts=[Part(TextPart(text='text'))]
        ),
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
        A2AError(TaskNotFoundError()),
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
    child_queue1 = event_queue.tap()
    child_queue2 = event_queue.tap()

    event1 = Message(**MESSAGE_PAYLOAD)
    event2 = Task(**MINIMAL_TASK)

    await event_queue.enqueue_event(event1)
    await event_queue.enqueue_event(event2)

    # Check parent queue
    assert await event_queue.dequeue_event(no_wait=True) == event1
    assert await event_queue.dequeue_event(no_wait=True) == event2

    # Check child queue 1
    assert await child_queue1.dequeue_event(no_wait=True) == event1
    assert await child_queue1.dequeue_event(no_wait=True) == event2

    # Check child queue 2
    assert await child_queue2.dequeue_event(no_wait=True) == event1
    assert await child_queue2.dequeue_event(no_wait=True) == event2


@pytest.mark.asyncio
async def test_enqueue_event_when_closed(
    event_queue: EventQueue, expected_queue_closed_exception: type[Exception]
) -> None:
    """Test that no event is enqueued if the parent queue is closed."""
    await event_queue.close()  # Close the queue first

    event = Message(**MESSAGE_PAYLOAD)
    # Attempt to enqueue, should do nothing or log a warning as per implementation
    await event_queue.enqueue_event(event)

    # Verify the queue is still empty
    with pytest.raises(expected_queue_closed_exception):
        await event_queue.dequeue_event(no_wait=True)

    # Also verify child queues are not affected directly by parent's enqueue attempt when closed
    # (though they would be closed too by propagation)
    child_queue = (
        event_queue.tap()
    )  # Tap after close might be weird, but let's see
    # The current implementation would add it to _children
    # and then child.close() would be called.
    # A more robust test for child propagation is in test_close_propagates
    await (
        child_queue.close()
    )  # ensure child is also seen as closed for this test's purpose
    with pytest.raises(expected_queue_closed_exception):
        await child_queue.dequeue_event(no_wait=True)


@pytest.fixture
def expected_queue_closed_exception() -> type[Exception]:
    if sys.version_info < (3, 13):
        return asyncio.QueueEmpty
    return asyncio.QueueShutDown


@pytest.mark.asyncio
async def test_dequeue_event_closed_and_empty_no_wait(
    event_queue: EventQueue, expected_queue_closed_exception: type[Exception]
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
    event_queue: EventQueue, expected_queue_closed_exception: type[Exception]
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
    #         logger.warning('Queue is closed. Event will not be dequeued.')
    #         raise asyncio.QueueEmpty('Queue is closed.')
    # event = await self.queue.get() -> this line is not reached if closed and empty.

    # So, for the current implementation, it will raise QueueEmpty immediately.
    with pytest.raises(expected_queue_closed_exception):
        await event_queue.dequeue_event(no_wait=False)

    # If the implementation were to change to allow `await self.queue.get()`
    # to be called even when closed (to drain it), then a timeout test would be needed.
    # For now, testing the current behavior.
    # Example of a timeout test if it were to wait:
    # with pytest.raises(asyncio.TimeoutError): # Or QueueEmpty if that's what join/shutdown causes get() to raise
    #     await asyncio.wait_for(event_queue.dequeue_event(no_wait=False), timeout=0.01)


@pytest.mark.asyncio
async def test_tap_creates_child_queue(event_queue: EventQueue) -> None:
    """Test that tap creates a new EventQueue and adds it to children."""
    initial_children_count = len(event_queue._children)

    child_queue = event_queue.tap()

    assert isinstance(child_queue, EventQueue)
    assert child_queue != event_queue  # Ensure it's a new instance
    assert len(event_queue._children) == initial_children_count + 1
    assert child_queue in event_queue._children

    # Test that the new child queue has the default max size (or specific if tap could configure it)
    assert child_queue.queue.maxsize == DEFAULT_MAX_QUEUE_SIZE


@pytest.mark.asyncio
async def test_close_sets_flag_and_handles_internal_queue_old_python(
    event_queue: EventQueue,
) -> None:
    """Test close behavior on Python < 3.13 (using queue.join)."""
    with patch('sys.version_info', (3, 12, 0)):  # Simulate older Python
        # Mock queue.join as it's called in older versions
        event_queue.queue.join = AsyncMock()

        await event_queue.close()

        assert event_queue.is_closed() is True
        event_queue.queue.join.assert_awaited_once()  # waited for drain


@pytest.mark.asyncio
async def test_close_sets_flag_and_handles_internal_queue_new_python(
    event_queue: EventQueue,
) -> None:
    """Test close behavior on Python >= 3.13 (using queue.shutdown)."""
    with patch('sys.version_info', (3, 13, 0)):
        # Inject a dummy shutdown method for non-3.13 runtimes
        from typing import cast

        queue = cast('Any', event_queue.queue)
        queue.shutdown = MagicMock()  # type: ignore[attr-defined]
        await event_queue.close()
        assert event_queue.is_closed() is True
        queue.shutdown.assert_called_once_with(False)


@pytest.mark.asyncio
async def test_close_graceful_py313_waits_for_join_and_children(
    event_queue: EventQueue,
) -> None:
    """For Python >=3.13 and immediate=False, close should shut down(False), then wait for join and children."""
    with patch('sys.version_info', (3, 13, 0)):
        # Arrange
        from typing import cast

        q_any = cast('Any', event_queue.queue)
        q_any.shutdown = MagicMock()  # type: ignore[attr-defined]
        event_queue.queue.join = AsyncMock()

        child = event_queue.tap()
        child.close = AsyncMock()

        # Act
        await event_queue.close(immediate=False)

        # Assert
        event_queue.queue.join.assert_awaited_once()
        child.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_propagates_to_children(event_queue: EventQueue) -> None:
    """Test that close() is called on all child queues."""
    child_queue1 = event_queue.tap()
    child_queue2 = event_queue.tap()

    # Mock the close method of children to verify they are called
    child_queue1.close = AsyncMock()
    child_queue2.close = AsyncMock()

    await event_queue.close()

    child_queue1.close.assert_awaited_once()
    child_queue2.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_idempotent(event_queue: EventQueue) -> None:
    """Test that calling close() multiple times doesn't cause errors and only acts once."""
    # Mock the internal queue's join or shutdown to see how many times it's effectively called
    with patch(
        'sys.version_info', (3, 12, 0)
    ):  # Test with older version logic first
        event_queue.queue.join = AsyncMock()
        await event_queue.close()
        assert event_queue.is_closed() is True
        event_queue.queue.join.assert_called_once()  # Called first time

        # Call close again
        await event_queue.close()
        assert event_queue.is_closed() is True
        event_queue.queue.join.assert_called_once()  # Still only called once

    # Reset for new Python version test
    event_queue_new = EventQueue()  # New queue for fresh state
    with patch('sys.version_info', (3, 13, 0)):
        from typing import cast

        queue = cast('Any', event_queue_new.queue)
        queue.shutdown = MagicMock()  # type: ignore[attr-defined]
        await event_queue_new.close()
        assert event_queue_new.is_closed() is True
        queue.shutdown.assert_called_once()

        await event_queue_new.close()
        assert event_queue_new.is_closed() is True
        queue.shutdown.assert_called_once()  # Still only called once


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
    event1 = Message(**MESSAGE_PAYLOAD)
    event2 = Task(**MINIMAL_TASK)
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
    event_queue: EventQueue,
) -> None:
    """Test that immediate parameter is propagated to child queues."""
    child_queue = event_queue.tap()

    # Add events to both parent and child
    event = Message(**MESSAGE_PAYLOAD)
    await event_queue.enqueue_event(event)

    assert child_queue.is_closed() is False
    assert child_queue.queue.empty() is False

    # close event queue
    await event_queue.close(immediate=True)

    # Verify child queue was called and empty with immediate=True
    assert child_queue.is_closed() is True
    assert child_queue.queue.empty()


@pytest.mark.asyncio
async def test_clear_events_current_queue_only(event_queue: EventQueue) -> None:
    """Test clear_events clears only the current queue when clear_child_queues=False."""
    child_queue = event_queue.tap()
    event1 = Message(**MESSAGE_PAYLOAD)
    event2 = Task(**MINIMAL_TASK)
    await event_queue.enqueue_event(event1)
    await event_queue.enqueue_event(event2)

    # Clear only parent queue
    await event_queue.clear_events(clear_child_queues=False)

    # Verify parent queue is empty
    assert event_queue.queue.empty()

    # Verify child queue still has its event
    assert not child_queue.queue.empty()
    assert child_queue.is_closed() is False

    dequeued_child_event = await child_queue.dequeue_event(no_wait=True)
    assert dequeued_child_event == event1


@pytest.mark.asyncio
async def test_clear_events_with_children(event_queue: EventQueue) -> None:
    """Test clear_events clears both current queue and child queues."""
    # Create child queues and add events
    child_queue1 = event_queue.tap()
    child_queue2 = event_queue.tap()

    # Add events to parent queue
    event1 = Message(**MESSAGE_PAYLOAD)
    event2 = Task(**MINIMAL_TASK)
    await event_queue.enqueue_event(event1)
    await event_queue.enqueue_event(event2)

    # Clear all queues
    await event_queue.clear_events(clear_child_queues=True)

    # Verify all queues are empty
    assert event_queue.queue.empty()
    assert child_queue1.queue.empty()
    assert child_queue2.queue.empty()


@pytest.mark.asyncio
async def test_clear_events_empty_queue(event_queue: EventQueue) -> None:
    """Test clear_events works correctly with empty queue."""
    # Verify queue is initially empty
    assert event_queue.queue.empty()

    # Clear events from empty queue
    await event_queue.clear_events()

    # Verify queue remains empty
    assert event_queue.queue.empty()


@pytest.mark.asyncio
async def test_clear_events_closed_queue(event_queue: EventQueue) -> None:
    """Test clear_events works correctly with closed queue."""
    # Add events and close queue

    with patch('sys.version_info', (3, 12, 0)):  # Simulate older Python
        # Mock queue.join as it's called in older versions
        event_queue.queue.join = AsyncMock()

    event = Message(**MESSAGE_PAYLOAD)
    await event_queue.enqueue_event(event)
    await event_queue.close()

    # Verify queue is closed but not empty
    assert event_queue.is_closed() is True
    assert not event_queue.queue.empty()

    # Clear events from closed queue
    await event_queue.clear_events()

    # Verify queue is now empty
    assert event_queue.queue.empty()
