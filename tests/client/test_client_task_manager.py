from unittest.mock import patch

import pytest

from a2a.client.client_task_manager import ClientTaskManager
from a2a.client.errors import (
    A2AClientInvalidArgsError,
    A2AClientInvalidStateError,
)
from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    Role,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


@pytest.fixture
def task_manager() -> ClientTaskManager:
    return ClientTaskManager()


@pytest.fixture
def sample_task() -> Task:
    return Task(
        id='task123',
        context_id='context456',
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )


@pytest.fixture
def sample_message() -> Message:
    return Message(
        message_id='msg1',
        role=Role.ROLE_USER,
        parts=[Part(text='Hello')],
    )


def test_get_task_no_task_id_returns_none(
    task_manager: ClientTaskManager,
) -> None:
    assert task_manager.get_task() is None


def test_get_task_or_raise_no_task_raises_error(
    task_manager: ClientTaskManager,
) -> None:
    with pytest.raises(A2AClientInvalidStateError, match='no current Task'):
        task_manager.get_task_or_raise()


@pytest.mark.asyncio
async def test_process_with_task(
    task_manager: ClientTaskManager, sample_task: Task
) -> None:
    """Test processing a StreamResponse containing a task."""
    event = StreamResponse(task=sample_task)
    result = await task_manager.process(event)
    assert result == sample_task
    assert task_manager.get_task() == sample_task
    assert task_manager._task_id == sample_task.id
    assert task_manager._context_id == sample_task.context_id


@pytest.mark.asyncio
async def test_process_with_task_already_set_raises_error(
    task_manager: ClientTaskManager, sample_task: Task
) -> None:
    """Test that processing a second task raises an error."""
    event = StreamResponse(task=sample_task)
    await task_manager.process(event)
    with pytest.raises(
        A2AClientInvalidArgsError,
        match='Task is already set, create new manager for new tasks.',
    ):
        await task_manager.process(event)


@pytest.mark.asyncio
async def test_process_with_status_update(
    task_manager: ClientTaskManager, sample_task: Task, sample_message: Message
) -> None:
    """Test processing a status update after a task has been set."""
    # First set the task
    task_event = StreamResponse(task=sample_task)
    await task_manager.process(task_event)

    # Now process a status update
    status_update = TaskStatusUpdateEvent(
        task_id=sample_task.id,
        context_id=sample_task.context_id,
        status=TaskStatus(
            state=TaskState.TASK_STATE_COMPLETED, message=sample_message
        ),
        final=True,
    )
    status_event = StreamResponse(status_update=status_update)
    updated_task = await task_manager.process(status_event)

    assert updated_task.status.state == TaskState.TASK_STATE_COMPLETED
    assert len(updated_task.history) == 1
    assert updated_task.history[0].message_id == sample_message.message_id


@pytest.mark.asyncio
async def test_process_with_artifact_update(
    task_manager: ClientTaskManager, sample_task: Task
) -> None:
    """Test processing an artifact update after a task has been set."""
    # First set the task
    task_event = StreamResponse(task=sample_task)
    await task_manager.process(task_event)

    artifact = Artifact(
        artifact_id='art1', parts=[Part(text='artifact content')]
    )
    artifact_update = TaskArtifactUpdateEvent(
        task_id=sample_task.id,
        context_id=sample_task.context_id,
        artifact=artifact,
    )
    artifact_event = StreamResponse(artifact_update=artifact_update)

    with patch(
        'a2a.client.client_task_manager.append_artifact_to_task'
    ) as mock_append:
        updated_task = await task_manager.process(artifact_event)
        mock_append.assert_called_once_with(updated_task, artifact_update)


@pytest.mark.asyncio
async def test_process_creates_task_if_not_exists_on_status_update(
    task_manager: ClientTaskManager,
) -> None:
    """Test that processing a status update creates a task if none exists."""
    status_update = TaskStatusUpdateEvent(
        task_id='new_task',
        context_id='new_context',
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        final=False,
    )
    status_event = StreamResponse(status_update=status_update)
    updated_task = await task_manager.process(status_event)

    assert updated_task is not None
    assert updated_task.id == 'new_task'
    assert updated_task.status.state == TaskState.TASK_STATE_WORKING


@pytest.mark.asyncio
async def test_process_with_message_returns_none(
    task_manager: ClientTaskManager, sample_message: Message
) -> None:
    """Test that processing a message event returns None."""
    event = StreamResponse(msg=sample_message)
    result = await task_manager.process(event)
    assert result is None


def test_update_with_message(
    task_manager: ClientTaskManager, sample_task: Task, sample_message: Message
) -> None:
    """Test updating a task with a new message."""
    updated_task = task_manager.update_with_message(sample_message, sample_task)
    assert len(updated_task.history) == 1
    assert updated_task.history[0].message_id == sample_message.message_id


def test_update_with_message_moves_status_message(
    task_manager: ClientTaskManager, sample_task: Task, sample_message: Message
) -> None:
    """Test that status message is moved to history when updating."""
    status_message = Message(
        message_id='status_msg',
        role=Role.ROLE_AGENT,
        parts=[Part(text='Status')],
    )
    sample_task.status.message.CopyFrom(status_message)

    updated_task = task_manager.update_with_message(sample_message, sample_task)

    # History should contain both status_message and sample_message
    assert len(updated_task.history) == 2
    assert updated_task.history[0].message_id == status_message.message_id
    assert updated_task.history[1].message_id == sample_message.message_id
    # Status message should be cleared
    assert not updated_task.status.HasField('message')
