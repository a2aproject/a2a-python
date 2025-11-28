from typing import Any
from unittest.mock import AsyncMock

import pytest

from a2a.server.tasks import TaskManager
from a2a.types import InvalidParamsError
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


# Create proto task instead of dict
def create_minimal_task(
    task_id: str = 'task-abc',
    context_id: str = 'session-xyz',
) -> Task:
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )


MINIMAL_TASK_ID = 'task-abc'
MINIMAL_CONTEXT_ID = 'session-xyz'


@pytest.fixture
def mock_task_store() -> AsyncMock:
    """Fixture for a mock TaskStore."""
    return AsyncMock()


@pytest.fixture
def task_manager(mock_task_store: AsyncMock) -> TaskManager:
    """Fixture for a TaskManager with a mock TaskStore."""
    return TaskManager(
        task_id=MINIMAL_TASK_ID,
        context_id=MINIMAL_CONTEXT_ID,
        task_store=mock_task_store,
        initial_message=None,
    )


@pytest.mark.parametrize('invalid_task_id', ['', 123])
def test_task_manager_invalid_task_id(
    mock_task_store: AsyncMock, invalid_task_id: Any
):
    """Test that TaskManager raises ValueError for an invalid task_id."""
    with pytest.raises(ValueError, match='Task ID must be a non-empty string'):
        TaskManager(
            task_id=invalid_task_id,
            context_id='test_context',
            task_store=mock_task_store,
            initial_message=None,
        )


@pytest.mark.asyncio
async def test_get_task_existing(
    task_manager: TaskManager, mock_task_store: AsyncMock
) -> None:
    """Test getting an existing task."""
    expected_task = create_minimal_task()
    mock_task_store.get.return_value = expected_task
    retrieved_task = await task_manager.get_task()
    assert retrieved_task == expected_task
    mock_task_store.get.assert_called_once_with(MINIMAL_TASK_ID, None)


@pytest.mark.asyncio
async def test_get_task_nonexistent(
    task_manager: TaskManager, mock_task_store: AsyncMock
) -> None:
    """Test getting a nonexistent task."""
    mock_task_store.get.return_value = None
    retrieved_task = await task_manager.get_task()
    assert retrieved_task is None
    mock_task_store.get.assert_called_once_with(MINIMAL_TASK_ID, None)


@pytest.mark.asyncio
async def test_save_task_event_new_task(
    task_manager: TaskManager, mock_task_store: AsyncMock
) -> None:
    """Test saving a new task."""
    task = create_minimal_task()
    await task_manager.save_task_event(task)
    mock_task_store.save.assert_called_once_with(task, None)


@pytest.mark.asyncio
async def test_save_task_event_status_update(
    task_manager: TaskManager, mock_task_store: AsyncMock
) -> None:
    """Test saving a status update for an existing task."""
    initial_task = create_minimal_task()
    mock_task_store.get.return_value = initial_task
    new_status = TaskStatus(
        state=TaskState.TASK_STATE_WORKING,
        message=Message(
            role=Role.ROLE_AGENT,
            parts=[Part(text='content')],
            message_id='message-id',
        ),
    )
    event = TaskStatusUpdateEvent(
        task_id=MINIMAL_TASK_ID,
        context_id=MINIMAL_CONTEXT_ID,
        status=new_status,
        final=False,
    )
    await task_manager.save_task_event(event)
    # Verify save was called and the task has updated status
    call_args = mock_task_store.save.call_args
    assert call_args is not None
    saved_task = call_args[0][0]
    assert saved_task.status.state == TaskState.TASK_STATE_WORKING


@pytest.mark.asyncio
async def test_save_task_event_artifact_update(
    task_manager: TaskManager, mock_task_store: AsyncMock
) -> None:
    """Test saving an artifact update for an existing task."""
    initial_task = create_minimal_task()
    mock_task_store.get.return_value = initial_task
    new_artifact = Artifact(
        artifact_id='artifact-id',
        name='artifact1',
        parts=[Part(text='content')],
    )
    event = TaskArtifactUpdateEvent(
        task_id=MINIMAL_TASK_ID,
        context_id=MINIMAL_CONTEXT_ID,
        artifact=new_artifact,
    )
    await task_manager.save_task_event(event)
    # Verify save was called and the task has the artifact
    call_args = mock_task_store.save.call_args
    assert call_args is not None
    saved_task = call_args[0][0]
    assert len(saved_task.artifacts) == 1
    assert saved_task.artifacts[0].artifact_id == 'artifact-id'


@pytest.mark.asyncio
async def test_save_task_event_metadata_update(
    task_manager: TaskManager, mock_task_store: AsyncMock
) -> None:
    """Test saving an updated metadata for an existing task."""
    initial_task = create_minimal_task()
    mock_task_store.get.return_value = initial_task
    new_metadata = {'meta_key_test': 'meta_value_test'}

    event = TaskStatusUpdateEvent(
        task_id=MINIMAL_TASK_ID,
        context_id=MINIMAL_CONTEXT_ID,
        metadata=new_metadata,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        final=False,
    )
    await task_manager.save_task_event(event)

    updated_task = mock_task_store.save.call_args.args[0]
    assert updated_task.metadata == new_metadata


@pytest.mark.asyncio
async def test_ensure_task_existing(
    task_manager: TaskManager, mock_task_store: AsyncMock
) -> None:
    """Test ensuring an existing task."""
    expected_task = create_minimal_task()
    mock_task_store.get.return_value = expected_task
    event = TaskStatusUpdateEvent(
        task_id=MINIMAL_TASK_ID,
        context_id=MINIMAL_CONTEXT_ID,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        final=False,
    )
    retrieved_task = await task_manager.ensure_task(event)
    assert retrieved_task == expected_task
    mock_task_store.get.assert_called_once_with(MINIMAL_TASK_ID, None)


@pytest.mark.asyncio
async def test_ensure_task_nonexistent(
    mock_task_store: AsyncMock,
) -> None:
    """Test ensuring a nonexistent task (creates a new one)."""
    mock_task_store.get.return_value = None
    task_manager_without_id = TaskManager(
        task_id=None,
        context_id=None,
        task_store=mock_task_store,
        initial_message=None,
    )
    event = TaskStatusUpdateEvent(
        task_id='new-task',
        context_id='some-context',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        final=False,
    )
    new_task = await task_manager_without_id.ensure_task(event)
    assert new_task.id == 'new-task'
    assert new_task.context_id == 'some-context'
    assert new_task.status.state == TaskState.TASK_STATE_SUBMITTED
    mock_task_store.save.assert_called_once_with(new_task, None)
    assert task_manager_without_id.task_id == 'new-task'
    assert task_manager_without_id.context_id == 'some-context'


def test_init_task_obj(task_manager: TaskManager) -> None:
    """Test initializing a new task object."""
    new_task = task_manager._init_task_obj('new-task', 'new-context')  # type: ignore
    assert new_task.id == 'new-task'
    assert new_task.context_id == 'new-context'
    assert new_task.status.state == TaskState.TASK_STATE_SUBMITTED
    assert new_task.history == []


@pytest.mark.asyncio
async def test_save_task(
    task_manager: TaskManager, mock_task_store: AsyncMock
) -> None:
    """Test saving a task."""
    task = create_minimal_task()
    await task_manager._save_task(task)  # type: ignore
    mock_task_store.save.assert_called_once_with(task, None)


@pytest.mark.asyncio
async def test_save_task_event_mismatched_id_raises_error(
    task_manager: TaskManager,
) -> None:
    """Test that save_task_event raises ServerError on task ID mismatch."""
    # The task_manager is initialized with 'task-abc'
    mismatched_task = Task(
        id='wrong-id',
        context_id='session-xyz',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )

    with pytest.raises(ServerError) as exc_info:
        await task_manager.save_task_event(mismatched_task)
    assert isinstance(exc_info.value.error, InvalidParamsError)


@pytest.mark.asyncio
async def test_save_task_event_new_task_no_task_id(
    mock_task_store: AsyncMock,
) -> None:
    """Test saving a task event without task id in TaskManager."""
    task_manager_without_id = TaskManager(
        task_id=None,
        context_id=None,
        task_store=mock_task_store,
        initial_message=None,
    )
    task = Task(
        id='new-task-id',
        context_id='some-context',
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    await task_manager_without_id.save_task_event(task)
    mock_task_store.save.assert_called_once_with(task, None)
    assert task_manager_without_id.task_id == 'new-task-id'
    assert task_manager_without_id.context_id == 'some-context'
    # initial submit should be updated to working
    assert task.status.state == TaskState.TASK_STATE_WORKING


@pytest.mark.asyncio
async def test_get_task_no_task_id(
    mock_task_store: AsyncMock,
) -> None:
    """Test getting a task when task_id is not set in TaskManager."""
    task_manager_without_id = TaskManager(
        task_id=None,
        context_id='some-context',
        task_store=mock_task_store,
        initial_message=None,
    )
    retrieved_task = await task_manager_without_id.get_task()
    assert retrieved_task is None
    mock_task_store.get.assert_not_called()


@pytest.mark.asyncio
async def test_save_task_event_no_task_existing(
    mock_task_store: AsyncMock,
) -> None:
    """Test saving an event when no task exists and task_id is not set."""
    task_manager_without_id = TaskManager(
        task_id=None,
        context_id=None,
        task_store=mock_task_store,
        initial_message=None,
    )
    mock_task_store.get.return_value = None
    event = TaskStatusUpdateEvent(
        task_id='event-task-id',
        context_id='some-context',
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        final=True,
    )
    await task_manager_without_id.save_task_event(event)
    # Check if a new task was created and saved
    call_args = mock_task_store.save.call_args
    assert call_args is not None
    saved_task = call_args[0][0]
    assert saved_task.id == 'event-task-id'
    assert saved_task.context_id == 'some-context'
    assert saved_task.status.state == TaskState.TASK_STATE_COMPLETED
    assert task_manager_without_id.task_id == 'event-task-id'
    assert task_manager_without_id.context_id == 'some-context'
