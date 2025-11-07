import asyncio
import uuid

from unittest.mock import AsyncMock, Mock, patch

import pytest

from a2a.server.events import EventQueue
from a2a.server.id_generator import IDGenerator
from a2a.server.tasks import TaskUpdater
from a2a.types import (
    Message,
    Part,
    Role,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
    TextPart,
)


@pytest.fixture
def event_queue() -> AsyncMock:
    """Create a mock event queue for testing."""
    return AsyncMock(spec=EventQueue)


@pytest.fixture
def task_updater(event_queue: AsyncMock) -> TaskUpdater:
    """Create a TaskUpdater instance for testing."""
    return TaskUpdater(
        event_queue=event_queue,
        task_id='test-task-id',
        context_id='test-context-id',
    )


@pytest.fixture
def sample_message() -> Message:
    """Create a sample message for testing."""
    return Message(
        role=Role.agent,
        task_id='test-task-id',
        context_id='test-context-id',
        message_id='test-message-id',
        parts=[Part(root=TextPart(text='Test message'))],
    )


@pytest.fixture
def sample_parts() -> list[Part]:
    """Create sample parts for testing."""
    return [Part(root=TextPart(text='Test part'))]


def test_init(event_queue: AsyncMock) -> None:
    """Test that TaskUpdater initializes correctly."""
    task_updater = TaskUpdater(
        event_queue=event_queue,
        task_id='test-task-id',
        context_id='test-context-id',
    )

    assert task_updater.event_queue == event_queue
    assert task_updater.task_id == 'test-task-id'
    assert task_updater.context_id == 'test-context-id'


@pytest.mark.asyncio
async def test_update_status_without_message(
    task_updater: TaskUpdater, event_queue: AsyncMock
) -> None:
    """Test updating status without a message."""
    await task_updater.update_status(TaskState.working)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.task_id == 'test-task-id'
    assert event.context_id == 'test-context-id'
    assert event.final is False
    assert event.status.state == TaskState.working
    assert event.status.message is None


@pytest.mark.asyncio
async def test_update_status_with_message(
    task_updater: TaskUpdater, event_queue: AsyncMock, sample_message: Message
) -> None:
    """Test updating status with a message."""
    await task_updater.update_status(TaskState.working, message=sample_message)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.task_id == 'test-task-id'
    assert event.context_id == 'test-context-id'
    assert event.final is False
    assert event.status.state == TaskState.working
    assert event.status.message == sample_message


@pytest.mark.asyncio
async def test_update_status_final(
    task_updater: TaskUpdater, event_queue: AsyncMock
) -> None:
    """Test updating status with final=True."""
    await task_updater.update_status(TaskState.completed, final=True)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.final is True
    assert event.status.state == TaskState.completed


@pytest.mark.asyncio
async def test_add_artifact_with_custom_id_and_name(
    task_updater: TaskUpdater, event_queue: AsyncMock, sample_parts: list[Part]
) -> None:
    """Test adding an artifact with a custom ID and name."""
    await task_updater.add_artifact(
        parts=sample_parts,
        artifact_id='custom-artifact-id',
        name='Custom Artifact',
    )

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskArtifactUpdateEvent)
    assert event.artifact.artifact_id == 'custom-artifact-id'
    assert event.artifact.name == 'Custom Artifact'
    assert event.artifact.parts == sample_parts


@pytest.mark.asyncio
async def test_add_artifact_generates_id(
    task_updater: TaskUpdater, event_queue: AsyncMock, sample_parts: list[Part]
) -> None:
    """Test add_artifact generates an ID if artifact_id is None."""
    known_uuid = uuid.UUID('12345678-1234-5678-1234-567812345678')
    with patch('uuid.uuid4', return_value=known_uuid):
        await task_updater.add_artifact(parts=sample_parts, artifact_id=None)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskArtifactUpdateEvent)
    assert event.artifact.artifact_id == str(known_uuid)
    assert event.artifact.parts == sample_parts
    assert event.append is None
    assert event.last_chunk is None


@pytest.mark.asyncio
async def test_add_artifact_generates_custom_id(
    event_queue: AsyncMock, sample_parts: list[Part]
) -> None:
    """Test add_artifact uses a custom ID generator when provided."""
    artifact_id_generator = Mock(spec=IDGenerator)
    artifact_id_generator.generate.return_value = 'custom-artifact-id'
    task_updater = TaskUpdater(
        event_queue=event_queue,
        task_id='test-task-id',
        context_id='test-context-id',
        artifact_id_generator=artifact_id_generator,
    )

    await task_updater.add_artifact(parts=sample_parts, artifact_id=None)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]
    assert isinstance(event, TaskArtifactUpdateEvent)
    assert event.artifact.artifact_id == 'custom-artifact-id'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'append_val, last_chunk_val',
    [
        (False, False),
        (True, True),
        (True, False),
        (False, True),
    ],
)
async def test_add_artifact_with_append_last_chunk(
    task_updater: TaskUpdater,
    event_queue: AsyncMock,
    sample_parts: list[Part],
    append_val: bool,
    last_chunk_val: bool,
) -> None:
    """Test add_artifact with append and last_chunk flags."""
    await task_updater.add_artifact(
        parts=sample_parts,
        artifact_id='id1',
        append=append_val,
        last_chunk=last_chunk_val,
    )

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskArtifactUpdateEvent)
    assert event.artifact.artifact_id == 'id1'
    assert event.artifact.parts == sample_parts
    assert event.append == append_val
    assert event.last_chunk == last_chunk_val


@pytest.mark.asyncio
async def test_complete_without_message(
    task_updater: TaskUpdater, event_queue: AsyncMock
) -> None:
    """Test marking a task as completed without a message."""
    await task_updater.complete()

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.completed
    assert event.final is True
    assert event.status.message is None


@pytest.mark.asyncio
async def test_complete_with_message(
    task_updater: TaskUpdater, event_queue: AsyncMock, sample_message: Message
) -> None:
    """Test marking a task as completed with a message."""
    await task_updater.complete(message=sample_message)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.completed
    assert event.final is True
    assert event.status.message == sample_message


@pytest.mark.asyncio
async def test_submit_without_message(
    task_updater: TaskUpdater, event_queue: AsyncMock
) -> None:
    """Test marking a task as submitted without a message."""
    await task_updater.submit()

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.submitted
    assert event.final is False
    assert event.status.message is None


@pytest.mark.asyncio
async def test_submit_with_message(
    task_updater: TaskUpdater, event_queue: AsyncMock, sample_message: Message
) -> None:
    """Test marking a task as submitted with a message."""
    await task_updater.submit(message=sample_message)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.submitted
    assert event.final is False
    assert event.status.message == sample_message


@pytest.mark.asyncio
async def test_start_work_without_message(
    task_updater: TaskUpdater, event_queue: AsyncMock
) -> None:
    """Test marking a task as working without a message."""
    await task_updater.start_work()

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.working
    assert event.final is False
    assert event.status.message is None


@pytest.mark.asyncio
async def test_start_work_with_message(
    task_updater: TaskUpdater, event_queue: AsyncMock, sample_message: Message
) -> None:
    """Test marking a task as working with a message."""
    await task_updater.start_work(message=sample_message)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.working
    assert event.final is False
    assert event.status.message == sample_message


def test_new_agent_message(
    task_updater: TaskUpdater, sample_parts: list[Part]
) -> None:
    """Test creating a new agent message."""
    with patch(
        'uuid.uuid4',
        return_value=uuid.UUID('12345678-1234-5678-1234-567812345678'),
    ):
        message = task_updater.new_agent_message(parts=sample_parts)

    assert message.role == Role.agent
    assert message.task_id == 'test-task-id'
    assert message.context_id == 'test-context-id'
    assert message.message_id == '12345678-1234-5678-1234-567812345678'
    assert message.parts == sample_parts
    assert message.metadata is None


def test_new_agent_message_with_metadata(
    task_updater: TaskUpdater, sample_parts: list[Part]
) -> None:
    """Test creating a new agent message with metadata and final=True."""
    metadata = {'key': 'value'}

    with patch(
        'uuid.uuid4',
        return_value=uuid.UUID('12345678-1234-5678-1234-567812345678'),
    ):
        message = task_updater.new_agent_message(
            parts=sample_parts, metadata=metadata
        )

    assert message.role == Role.agent
    assert message.task_id == 'test-task-id'
    assert message.context_id == 'test-context-id'
    assert message.message_id == '12345678-1234-5678-1234-567812345678'
    assert message.parts == sample_parts
    assert message.metadata == metadata


def test_new_agent_message_with_custom_id_generator(
    event_queue: AsyncMock, sample_parts: list[Part]
) -> None:
    """Test creating a new agent message with a custom message ID generator."""
    message_id_generator = Mock(spec=IDGenerator)
    message_id_generator.generate.return_value = 'custom-message-id'
    task_updater = TaskUpdater(
        event_queue=event_queue,
        task_id='test-task-id',
        context_id='test-context-id',
        message_id_generator=message_id_generator,
    )

    message = task_updater.new_agent_message(parts=sample_parts)

    assert message.message_id == 'custom-message-id'


@pytest.mark.asyncio
async def test_failed_without_message(
    task_updater: TaskUpdater, event_queue: AsyncMock
) -> None:
    """Test marking a task as failed without a message."""
    await task_updater.failed()

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.failed
    assert event.final is True
    assert event.status.message is None


@pytest.mark.asyncio
async def test_failed_with_message(
    task_updater: TaskUpdater, event_queue: AsyncMock, sample_message: Message
) -> None:
    """Test marking a task as failed with a message."""
    await task_updater.failed(message=sample_message)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.failed
    assert event.final is True
    assert event.status.message == sample_message


@pytest.mark.asyncio
async def test_reject_without_message(
    task_updater: TaskUpdater, event_queue: AsyncMock
) -> None:
    """Test marking a task as rejected without a message."""
    await task_updater.reject()

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.rejected
    assert event.final is True
    assert event.status.message is None


@pytest.mark.asyncio
async def test_reject_with_message(
    task_updater: TaskUpdater, event_queue: AsyncMock, sample_message: Message
) -> None:
    """Test marking a task as rejected with a message."""
    await task_updater.reject(message=sample_message)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.rejected
    assert event.final is True
    assert event.status.message == sample_message


@pytest.mark.asyncio
async def test_requires_input_without_message(
    task_updater: TaskUpdater, event_queue: AsyncMock
) -> None:
    """Test marking a task as input required without a message."""
    await task_updater.requires_input()

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.input_required
    assert event.final is False
    assert event.status.message is None


@pytest.mark.asyncio
async def test_requires_input_with_message(
    task_updater: TaskUpdater, event_queue: AsyncMock, sample_message: Message
) -> None:
    """Test marking a task as input required with a message."""
    await task_updater.requires_input(message=sample_message)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.input_required
    assert event.final is False
    assert event.status.message == sample_message


@pytest.mark.asyncio
async def test_requires_input_final_true(
    task_updater: TaskUpdater, event_queue: AsyncMock
) -> None:
    """Test marking a task as input required with final=True."""
    await task_updater.requires_input(final=True)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.input_required
    assert event.final is True
    assert event.status.message is None


@pytest.mark.asyncio
async def test_requires_input_with_message_and_final(
    task_updater: TaskUpdater, event_queue: AsyncMock, sample_message: Message
) -> None:
    """Test marking a task as input required with message and final=True."""
    await task_updater.requires_input(message=sample_message, final=True)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.input_required
    assert event.final is True
    assert event.status.message == sample_message


@pytest.mark.asyncio
async def test_requires_auth_without_message(
    task_updater: TaskUpdater, event_queue: AsyncMock
) -> None:
    """Test marking a task as auth required without a message."""
    await task_updater.requires_auth()

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.auth_required
    assert event.final is False
    assert event.status.message is None


@pytest.mark.asyncio
async def test_requires_auth_with_message(
    task_updater: TaskUpdater, event_queue: AsyncMock, sample_message: Message
) -> None:
    """Test marking a task as auth required with a message."""
    await task_updater.requires_auth(message=sample_message)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.auth_required
    assert event.final is False
    assert event.status.message == sample_message


@pytest.mark.asyncio
async def test_requires_auth_final_true(
    task_updater: TaskUpdater, event_queue: AsyncMock
) -> None:
    """Test marking a task as auth required with final=True."""
    await task_updater.requires_auth(final=True)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.auth_required
    assert event.final is True
    assert event.status.message is None


@pytest.mark.asyncio
async def test_requires_auth_with_message_and_final(
    task_updater: TaskUpdater, event_queue: AsyncMock, sample_message: Message
) -> None:
    """Test marking a task as auth required with message and final=True."""
    await task_updater.requires_auth(message=sample_message, final=True)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.auth_required
    assert event.final is True
    assert event.status.message == sample_message


@pytest.mark.asyncio
async def test_cancel_without_message(
    task_updater: TaskUpdater, event_queue: AsyncMock
) -> None:
    """Test marking a task as cancelled without a message."""
    await task_updater.cancel()

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.canceled
    assert event.final is True
    assert event.status.message is None


@pytest.mark.asyncio
async def test_cancel_with_message(
    task_updater: TaskUpdater, event_queue: AsyncMock, sample_message: Message
) -> None:
    """Test marking a task as cancelled with a message."""
    await task_updater.cancel(message=sample_message)

    event_queue.enqueue_event.assert_called_once()
    event = event_queue.enqueue_event.call_args[0][0]

    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.status.state == TaskState.canceled
    assert event.final is True
    assert event.status.message == sample_message


@pytest.mark.asyncio
async def test_update_status_raises_error_if_terminal_state_reached(
    task_updater: TaskUpdater, event_queue: AsyncMock
) -> None:
    await task_updater.complete()
    event_queue.reset_mock()
    with pytest.raises(RuntimeError):
        await task_updater.start_work()
    event_queue.enqueue_event.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_updates_race_condition(
    event_queue: AsyncMock,
) -> None:
    task_updater = TaskUpdater(
        event_queue=event_queue,
        task_id='test-task-id',
        context_id='test-context-id',
    )
    tasks = [
        task_updater.complete(),
        task_updater.failed(),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, RuntimeError)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert event_queue.enqueue_event.call_count == 1


@pytest.mark.asyncio
async def test_reject_concurrently_with_complete(
    event_queue: AsyncMock,
) -> None:
    """Test for race conditions when reject and complete are called concurrently."""
    task_updater = TaskUpdater(
        event_queue=event_queue,
        task_id='concurrent-task',
        context_id='concurrent-context',
    )

    tasks = [
        task_updater.reject(),
        task_updater.complete(),
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, RuntimeError)]

    assert len(successes) == 1
    assert len(failures) == 1

    assert event_queue.enqueue_event.call_count == 1

    event = event_queue.enqueue_event.call_args[0][0]
    assert isinstance(event, TaskStatusUpdateEvent)
    assert event.final is True
    assert event.status.state in [TaskState.rejected, TaskState.completed]
