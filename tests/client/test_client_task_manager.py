from unittest.mock import AsyncMock, Mock, patch

import pytest

from a2a.client.client_task_manager import ClientTaskManager
from a2a.client.errors import (
    A2AClientInvalidArgsError,
    A2AClientInvalidStateError,
)
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)


@pytest.fixture
def task_manager() -> ClientTaskManager:
    return ClientTaskManager()


@pytest.fixture
def sample_task() -> Task:
    return Task(
        id='task123',
        context_id='context456',
        status=TaskStatus(state=TaskState.working),
        history=[],
        artifacts=[],
    )


@pytest.fixture
def sample_message() -> Message:
    return Message(
        message_id='msg1',
        role=Role.user,
        parts=[Part(root=TextPart(text='Hello'))],
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
async def test_save_task_event_with_task(
    task_manager: ClientTaskManager, sample_task: Task
) -> None:
    await task_manager.save_task_event(sample_task)
    assert task_manager.get_task() == sample_task
    assert task_manager._task_id == sample_task.id
    assert task_manager._context_id == sample_task.context_id


@pytest.mark.asyncio
async def test_save_task_event_with_task_already_set_raises_error(
    task_manager: ClientTaskManager, sample_task: Task
) -> None:
    await task_manager.save_task_event(sample_task)
    with pytest.raises(
        A2AClientInvalidArgsError,
        match='Task is already set, create new manager for new tasks.',
    ):
        await task_manager.save_task_event(sample_task)


@pytest.mark.asyncio
async def test_save_task_event_with_status_update(
    task_manager: ClientTaskManager, sample_task: Task, sample_message: Message
) -> None:
    await task_manager.save_task_event(sample_task)
    status_update = TaskStatusUpdateEvent(
        task_id=sample_task.id,
        context_id=sample_task.context_id,
        status=TaskStatus(state=TaskState.completed, message=sample_message),
        final=True,
    )
    updated_task = await task_manager.save_task_event(status_update)
    assert updated_task.status.state == TaskState.completed
    assert updated_task.history == [sample_message]


@pytest.mark.asyncio
async def test_save_task_event_with_artifact_update(
    task_manager: ClientTaskManager, sample_task: Task
) -> None:
    await task_manager.save_task_event(sample_task)
    artifact = Artifact(
        artifact_id='art1', parts=[Part(root=TextPart(text='artifact content'))]
    )
    artifact_update = TaskArtifactUpdateEvent(
        task_id=sample_task.id,
        context_id=sample_task.context_id,
        artifact=artifact,
    )

    with patch(
        'a2a.client.client_task_manager.append_artifact_to_task'
    ) as mock_append:
        updated_task = await task_manager.save_task_event(artifact_update)
        mock_append.assert_called_once_with(updated_task, artifact_update)


@pytest.mark.asyncio
async def test_save_task_event_creates_task_if_not_exists(
    task_manager: ClientTaskManager,
) -> None:
    status_update = TaskStatusUpdateEvent(
        task_id='new_task',
        context_id='new_context',
        status=TaskStatus(state=TaskState.working),
        final=False,
    )
    updated_task = await task_manager.save_task_event(status_update)
    assert updated_task is not None
    assert updated_task.id == 'new_task'
    assert updated_task.status.state == TaskState.working


@pytest.mark.asyncio
async def test_process_with_task_event(
    task_manager: ClientTaskManager, sample_task: Task
) -> None:
    with patch.object(
        task_manager, 'save_task_event', new_callable=AsyncMock
    ) as mock_save:
        await task_manager.process(sample_task)
        mock_save.assert_called_once_with(sample_task)


@pytest.mark.asyncio
async def test_process_with_non_task_event(
    task_manager: ClientTaskManager,
) -> None:
    with patch.object(
        task_manager, 'save_task_event', new_callable=Mock
    ) as mock_save:
        non_task_event = 'not a task event'
        await task_manager.process(non_task_event)
        mock_save.assert_not_called()


def test_update_with_message(
    task_manager: ClientTaskManager, sample_task: Task, sample_message: Message
) -> None:
    updated_task = task_manager.update_with_message(sample_message, sample_task)
    assert updated_task.history == [sample_message]


def test_update_with_message_moves_status_message(
    task_manager: ClientTaskManager, sample_task: Task, sample_message: Message
) -> None:
    status_message = Message(
        message_id='status_msg',
        role=Role.agent,
        parts=[Part(root=TextPart(text='Status'))],
    )
    sample_task.status.message = status_message
    updated_task = task_manager.update_with_message(sample_message, sample_task)
    assert updated_task.history == [status_message, sample_message]
    assert updated_task.status.message is None
