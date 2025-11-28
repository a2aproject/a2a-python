import asyncio

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from a2a.server.events.event_consumer import EventConsumer, QueueClosed
from a2a.server.events.event_queue import EventQueue
from a2a.types import (
    InternalError,
    JSONRPCError,
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
from a2a.utils.errors import ServerError


def create_sample_message(message_id: str = '111') -> Message:
    """Create a sample Message proto object."""
    return Message(
        message_id=message_id,
        role=Role.ROLE_AGENT,
        parts=[Part(text='test message')],
    )


def create_sample_task(task_id: str = '123', context_id: str = 'session-xyz') -> Task:
    """Create a sample Task proto object."""
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )


@pytest.fixture
def mock_event_queue():
    return AsyncMock(spec=EventQueue)


@pytest.fixture
def event_consumer(mock_event_queue: EventQueue):
    return EventConsumer(queue=mock_event_queue)


def test_init_logs_debug_message(mock_event_queue: EventQueue):
    """Test that __init__ logs a debug message."""
    # Patch the logger instance within the module where EventConsumer is defined
    with patch('a2a.server.events.event_consumer.logger') as mock_logger:
        EventConsumer(queue=mock_event_queue)  # Instantiate to trigger __init__
        mock_logger.debug.assert_called_once_with('EventConsumer initialized')


@pytest.mark.asyncio
async def test_consume_one_task_event(
    event_consumer: MagicMock,
    mock_event_queue: MagicMock,
):
    task_event = create_sample_task()
    mock_event_queue.dequeue_event.return_value = task_event
    result = await event_consumer.consume_one()
    assert result == task_event
    mock_event_queue.task_done.assert_called_once()


@pytest.mark.asyncio
async def test_consume_one_message_event(
    event_consumer: MagicMock,
    mock_event_queue: MagicMock,
):
    message_event = create_sample_message()
    mock_event_queue.dequeue_event.return_value = message_event
    result = await event_consumer.consume_one()
    assert result == message_event
    mock_event_queue.task_done.assert_called_once()


@pytest.mark.asyncio
async def test_consume_one_a2a_error_event(
    event_consumer: MagicMock,
    mock_event_queue: MagicMock,
):
    error_event = InternalError()
    mock_event_queue.dequeue_event.return_value = error_event
    result = await event_consumer.consume_one()
    assert result == error_event
    mock_event_queue.task_done.assert_called_once()


@pytest.mark.asyncio
async def test_consume_one_jsonrpc_error_event(
    event_consumer: MagicMock,
    mock_event_queue: MagicMock,
):
    error_event = JSONRPCError(code=123, message='Some Error')
    mock_event_queue.dequeue_event.return_value = error_event
    result = await event_consumer.consume_one()
    assert result == error_event
    mock_event_queue.task_done.assert_called_once()


@pytest.mark.asyncio
async def test_consume_one_queue_empty(
    event_consumer: MagicMock,
    mock_event_queue: MagicMock,
):
    mock_event_queue.dequeue_event.side_effect = asyncio.QueueEmpty
    try:
        result = await event_consumer.consume_one()
        assert result is not None
    except ServerError:
        pass
    mock_event_queue.task_done.assert_not_called()


@pytest.mark.asyncio
async def test_consume_all_multiple_events(
    event_consumer: MagicMock,
    mock_event_queue: MagicMock,
):
    events: list[Any] = [
        create_sample_task(),
        TaskArtifactUpdateEvent(
            task_id='task_123',
            context_id='session-xyz',
            artifact=Artifact(
                artifact_id='11', parts=[Part(text='text')]
            ),
        ),
        TaskStatusUpdateEvent(
            task_id='task_123',
            context_id='session-xyz',
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            final=True,
        ),
    ]
    cursor = 0

    async def mock_dequeue() -> Any:
        nonlocal cursor
        if cursor < len(events):
            event = events[cursor]
            cursor += 1
            return event
        return None

    mock_event_queue.dequeue_event = mock_dequeue
    consumed_events: list[Any] = []
    async for event in event_consumer.consume_all():
        consumed_events.append(event)
    assert len(consumed_events) == 3
    assert consumed_events[0] == events[0]
    assert consumed_events[1] == events[1]
    assert consumed_events[2] == events[2]
    assert mock_event_queue.task_done.call_count == 3


@pytest.mark.asyncio
async def test_consume_until_message(
    event_consumer: MagicMock,
    mock_event_queue: MagicMock,
):
    events: list[Any] = [
        create_sample_task(),
        TaskArtifactUpdateEvent(
            task_id='task_123',
            context_id='session-xyz',
            artifact=Artifact(
                artifact_id='11', parts=[Part(text='text')]
            ),
        ),
        create_sample_message(),
        TaskStatusUpdateEvent(
            task_id='task_123',
            context_id='session-xyz',
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            final=True,
        ),
    ]
    cursor = 0

    async def mock_dequeue() -> Any:
        nonlocal cursor
        if cursor < len(events):
            event = events[cursor]
            cursor += 1
            return event
        return None

    mock_event_queue.dequeue_event = mock_dequeue
    consumed_events: list[Any] = []
    async for event in event_consumer.consume_all():
        consumed_events.append(event)
    assert len(consumed_events) == 3
    assert consumed_events[0] == events[0]
    assert consumed_events[1] == events[1]
    assert consumed_events[2] == events[2]
    assert mock_event_queue.task_done.call_count == 3


@pytest.mark.asyncio
async def test_consume_message_events(
    event_consumer: MagicMock,
    mock_event_queue: MagicMock,
):
    events = [
        create_sample_message(),
        create_sample_message(message_id='222'),  # Another message (final doesn't exist in proto)
    ]
    cursor = 0

    async def mock_dequeue() -> Any:
        nonlocal cursor
        if cursor < len(events):
            event = events[cursor]
            cursor += 1
            return event
        return None

    mock_event_queue.dequeue_event = mock_dequeue
    consumed_events: list[Any] = []
    async for event in event_consumer.consume_all():
        consumed_events.append(event)
    # Upon first Message the stream is closed.
    assert len(consumed_events) == 1
    assert consumed_events[0] == events[0]
    assert mock_event_queue.task_done.call_count == 1


@pytest.mark.asyncio
async def test_consume_all_raises_stored_exception(
    event_consumer: EventConsumer,
):
    """Test that consume_all raises an exception if _exception is set."""
    sample_exception = RuntimeError('Simulated agent error')
    event_consumer._exception = sample_exception

    with pytest.raises(RuntimeError, match='Simulated agent error'):
        async for _ in event_consumer.consume_all():
            pass  # Should not reach here


@pytest.mark.asyncio
async def test_consume_all_stops_on_queue_closed_and_confirmed_closed(
    event_consumer: EventConsumer, mock_event_queue: AsyncMock
):
    """Test consume_all stops if QueueClosed is raised and queue.is_closed() is True."""
    # Simulate the queue raising QueueClosed (which is asyncio.QueueEmpty or QueueShutdown)
    mock_event_queue.dequeue_event.side_effect = QueueClosed(
        'Queue is empty/closed'
    )
    # Simulate the queue confirming it's closed
    mock_event_queue.is_closed.return_value = True

    consumed_events = []
    async for event in event_consumer.consume_all():
        consumed_events.append(event)  # Should not happen

    assert (
        len(consumed_events) == 0
    )  # No events should be consumed as it breaks on QueueClosed
    mock_event_queue.dequeue_event.assert_called_once()  # Should attempt to dequeue once
    mock_event_queue.is_closed.assert_called_once()  # Should check if closed


@pytest.mark.asyncio
async def test_consume_all_continues_on_queue_empty_if_not_really_closed(
    event_consumer: EventConsumer, mock_event_queue: AsyncMock
):
    """Test that QueueClosed with is_closed=False allows loop to continue via timeout."""
    final_event = create_sample_message(message_id='final_event_id')

    # Setup dequeue_event behavior:
    # 1. Raise QueueClosed (e.g., asyncio.QueueEmpty)
    # 2. Return the final_event
    # 3. Raise QueueClosed again (to terminate after final_event)
    dequeue_effects = [
        QueueClosed('Simulated temporary empty'),
        final_event,
        QueueClosed('Queue closed after final event'),
    ]
    mock_event_queue.dequeue_event.side_effect = dequeue_effects

    # Setup is_closed behavior:
    # 1. False when QueueClosed is first raised (so loop doesn't break)
    # 2. True after final_event is processed and QueueClosed is raised again
    is_closed_effects = [False, True]
    mock_event_queue.is_closed.side_effect = is_closed_effects

    # Patch asyncio.wait_for used inside consume_all
    # The goal is that the first QueueClosed leads to a TimeoutError inside consume_all,
    # the loop continues, and then the final_event is fetched.

    # To reliably test the timeout behavior within consume_all, we adjust the consumer's
    # internal timeout to be very short for the test.
    event_consumer._timeout = 0.001

    consumed_events = []
    async for event in event_consumer.consume_all():
        consumed_events.append(event)

    assert len(consumed_events) == 1
    assert consumed_events[0] == final_event

    # Dequeue attempts:
    # 1. Raises QueueClosed (is_closed=False, leads to TimeoutError, loop continues)
    # 2. Returns final_event (which is a Message, causing consume_all to break)
    assert (
        mock_event_queue.dequeue_event.call_count == 2
    )  # Only two calls needed

    # is_closed calls:
    # 1. After first QueueClosed (returns False)
    # The second QueueClosed is not reached because Message breaks the loop.
    assert mock_event_queue.is_closed.call_count == 1


@pytest.mark.asyncio
async def test_consume_all_handles_queue_empty_when_closed_python_version_agnostic(
    event_consumer: EventConsumer, mock_event_queue: AsyncMock, monkeypatch
):
    """Ensure consume_all stops with no events when queue is closed and dequeue_event raises asyncio.QueueEmpty (Python version-agnostic)."""
    # Make QueueClosed a distinct exception (not QueueEmpty) to emulate py3.13 semantics
    from a2a.server.events import event_consumer as ec

    class QueueShutDown(Exception):
        pass

    monkeypatch.setattr(ec, 'QueueClosed', QueueShutDown, raising=True)

    # Simulate queue reporting closed while dequeue raises QueueEmpty
    mock_event_queue.dequeue_event.side_effect = asyncio.QueueEmpty(
        'closed/empty'
    )
    mock_event_queue.is_closed.return_value = True

    consumed_events = []
    async for event in event_consumer.consume_all():
        consumed_events.append(event)

    assert consumed_events == []
    mock_event_queue.dequeue_event.assert_called_once()
    mock_event_queue.is_closed.assert_called_once()


@pytest.mark.asyncio
async def test_consume_all_continues_on_queue_empty_when_not_closed(
    event_consumer: EventConsumer, mock_event_queue: AsyncMock, monkeypatch
):
    """Ensure consume_all continues after asyncio.QueueEmpty when queue is open, yielding the next (final) event."""
    # First dequeue raises QueueEmpty (transient empty), then a final Message arrives
    final = create_sample_message(message_id='final')
    mock_event_queue.dequeue_event.side_effect = [
        asyncio.QueueEmpty('temporarily empty'),
        final,
    ]
    mock_event_queue.is_closed.return_value = False

    # Make the polling responsive in tests
    event_consumer._timeout = 0.001

    consumed = []
    async for ev in event_consumer.consume_all():
        consumed.append(ev)

    assert consumed == [final]
    assert mock_event_queue.dequeue_event.call_count == 2
    mock_event_queue.is_closed.assert_called_once()


def test_agent_task_callback_sets_exception(event_consumer: EventConsumer):
    """Test that agent_task_callback sets _exception if the task had one."""
    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.cancelled.return_value = False
    mock_task.done.return_value = True
    sample_exception = ValueError('Task failed')
    mock_task.exception.return_value = sample_exception

    event_consumer.agent_task_callback(mock_task)

    assert event_consumer._exception == sample_exception
    mock_task.exception.assert_called_once()


def test_agent_task_callback_no_exception(event_consumer: EventConsumer):
    """Test that agent_task_callback does nothing if the task has no exception."""
    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.cancelled.return_value = False
    mock_task.done.return_value = True
    mock_task.exception.return_value = None  # No exception

    event_consumer.agent_task_callback(mock_task)

    assert event_consumer._exception is None  # Should remain None
    mock_task.exception.assert_called_once()


def test_agent_task_callback_cancelled_task(event_consumer: EventConsumer):
    """Test that agent_task_callback does nothing if the task has no exception."""
    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.cancelled.return_value = True
    mock_task.done.return_value = True
    sample_exception = ValueError('Task still running')
    mock_task.exception.return_value = sample_exception

    event_consumer.agent_task_callback(mock_task)

    assert event_consumer._exception is None  # Should remain None
    mock_task.exception.assert_not_called()


def test_agent_task_callback_not_done_task(event_consumer: EventConsumer):
    """Test that agent_task_callback does nothing if the task has no exception."""
    mock_task = MagicMock(spec=asyncio.Task)
    mock_task.cancelled.return_value = False
    mock_task.done.return_value = False
    sample_exception = ValueError('Task is cancelled')
    mock_task.exception.return_value = sample_exception

    event_consumer.agent_task_callback(mock_task)

    assert event_consumer._exception is None  # Should remain None
    mock_task.exception.assert_not_called()


from pydantic import ValidationError


@pytest.mark.asyncio
async def test_consume_all_handles_validation_error(
    event_consumer: EventConsumer, mock_event_queue: AsyncMock
):
    """Test that consume_all gracefully handles a pydantic.ValidationError."""
    # Simulate dequeue_event raising a ValidationError
    mock_event_queue.dequeue_event.side_effect = [
        ValidationError.from_exception_data(title='Test Error', line_errors=[]),
        asyncio.CancelledError,  # To stop the loop for the test
    ]

    with patch(
        'a2a.server.events.event_consumer.logger.error'
    ) as logger_error_mock:
        with pytest.raises(asyncio.CancelledError):
            async for _ in event_consumer.consume_all():
                pass

        # Check that the specific error was logged and the consumer continued
        logger_error_mock.assert_called_once()
        assert (
            'Invalid event format received' in logger_error_mock.call_args[0][0]
        )
