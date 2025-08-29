from typing import Any

import pytest

from a2a.server.tasks import TaskManager
from a2a.types import (
    InvalidParamsError,
    TaskState,
)
from a2a.utils.errors import ServerError
from tests.builders import (
    ArtifactUpdateEventBuilder,
    StatusUpdateEventBuilder,
)
from tests.fixtures import (
    artifact_builder,
    event_queue,
    http_client,
    message_builder,
    push_config_store,
    submitted_task,
    task_builder,
    task_manager,
    task_manager_factory,
    task_store,
)
from tests.test_doubles import InMemoryTaskStore


@pytest.mark.parametrize('invalid_task_id', ['', 123])
def test_task_manager_invalid_task_id(
    task_store: InMemoryTaskStore, invalid_task_id: Any
):
    """Test that TaskManager raises ValueError for an invalid task_id."""
    with pytest.raises(ValueError, match='Task ID must be a non-empty string'):
        TaskManager(
            task_id=invalid_task_id,
            context_id='test_context',
            task_store=task_store,
            initial_message=None,
        )


@pytest.mark.asyncio
async def test_get_task_existing(
    task_manager_factory, task_store: InMemoryTaskStore, submitted_task
):
    """Test retrieving an existing task from the task store."""
    task_manager = task_manager_factory(
        task_id=submitted_task.id, context_id=submitted_task.context_id
    )
    task_store.set_task(submitted_task)

    retrieved_task = await task_manager.get_task()

    assert retrieved_task == submitted_task
    task_store.assert_get_called(times=1)


@pytest.mark.asyncio
async def test_get_task_nonexistent(
    task_manager: TaskManager, task_store: InMemoryTaskStore
):
    """Test retrieving a non-existent task returns None."""
    retrieved_task = await task_manager.get_task()

    assert retrieved_task is None
    task_store.assert_get_called(times=1)


@pytest.mark.asyncio
async def test_save_task_event_new_task(
    task_manager_factory, task_store: InMemoryTaskStore, task_builder
):
    """Test saving a new task event to the task store."""
    task = task_builder.with_id('task-abc').build()
    task_manager = task_manager_factory(task_id=None, context_id=None)

    await task_manager.save_task_event(task)

    task_store.assert_save_called(times=1)
    task_store.assert_saved(task.id)


@pytest.mark.asyncio
async def test_save_task_event_status_update(
    task_manager_factory,
    task_store: InMemoryTaskStore,
    task_builder,
    message_builder,
):
    """Test saving a status update event for an existing task."""
    initial_task = (
        task_builder.with_id('task-abc').with_context_id('context-xyz').build()
    )
    task_store.set_task(initial_task)
    task_manager = task_manager_factory(
        task_id='task-abc', context_id='context-xyz'
    )

    status_message = message_builder.as_agent().with_text('Working...').build()
    event = (
        StatusUpdateEventBuilder()
        .for_task('task-abc')
        .with_state(TaskState.working)
        .with_message(status_message)
        .build()
    )
    event.context_id = 'context-xyz'

    await task_manager.save_task_event(event)

    saved_task = task_store.get_saved_task('task-abc')
    assert saved_task.status.state == TaskState.working
    assert saved_task.status.message == status_message
    assert (
        saved_task.history is None
    )  # History contains previous messages, not current
    task_store.assert_save_called(times=1)


@pytest.mark.asyncio
async def test_save_task_event_artifact_update(
    task_manager_factory,
    task_store: InMemoryTaskStore,
    task_builder,
    artifact_builder,
):
    """Test saving an artifact update event for an existing task."""
    initial_task = (
        task_builder.with_id('task-abc').with_context_id('context-xyz').build()
    )
    task_store.set_task(initial_task)
    task_manager = task_manager_factory(
        task_id='task-abc', context_id='context-xyz'
    )

    new_artifact = (
        artifact_builder.with_id('artifact-id')
        .with_name('artifact1')
        .with_text('content')
        .build()
    )

    event = (
        ArtifactUpdateEventBuilder()
        .for_task('task-abc')
        .with_artifact(new_artifact)
        .build()
    )
    event.context_id = 'context-xyz'

    await task_manager.save_task_event(event)

    saved_task = task_store.get_saved_task('task-abc')
    assert saved_task.artifacts == [new_artifact]
    task_store.assert_save_called(times=1)


@pytest.mark.asyncio
async def test_save_task_event_metadata_update(
    task_manager_factory, task_store: InMemoryTaskStore, task_builder
):
    """Test saving a metadata update event for an existing task."""
    initial_task = (
        task_builder.with_id('task-abc').with_context_id('context-xyz').build()
    )
    task_store.set_task(initial_task)
    task_manager = task_manager_factory(
        task_id='task-abc', context_id='context-xyz'
    )

    event = (
        StatusUpdateEventBuilder()
        .for_task('task-abc')
        .with_state(TaskState.working)
        .with_metadata(meta_key_test='meta_value_test')
        .build()
    )
    event.context_id = 'context-xyz'

    await task_manager.save_task_event(event)

    saved_task = task_store.get_saved_task('task-abc')
    assert saved_task.metadata == {'meta_key_test': 'meta_value_test'}


@pytest.mark.asyncio
async def test_ensure_task_existing(
    task_manager_factory, task_store: InMemoryTaskStore, submitted_task
):
    """Test ensuring a task that already exists in the store."""
    task_store.set_task(submitted_task)
    task_manager = task_manager_factory(
        task_id=submitted_task.id, context_id=submitted_task.context_id
    )

    event = (
        StatusUpdateEventBuilder()
        .for_task(submitted_task.id)
        .with_state(TaskState.working)
        .build()
    )

    retrieved_task = await task_manager.ensure_task(event)

    assert retrieved_task.id == submitted_task.id
    assert retrieved_task.status.state == submitted_task.status.state
    task_store.assert_get_called(times=1)


@pytest.mark.asyncio
async def test_ensure_task_nonexistent(
    task_store: InMemoryTaskStore, task_manager_factory
):
    """Test ensuring a task that does not exist creates a new one."""
    task_manager = task_manager_factory(task_id=None, context_id=None)

    event = (
        StatusUpdateEventBuilder()
        .for_task('new-task')
        .with_state(TaskState.submitted)
        .build()
    )
    event.context_id = 'some-context'

    new_task = await task_manager.ensure_task(event)

    assert new_task.id == 'new-task'
    assert new_task.context_id == 'some-context'
    assert new_task.status.state == TaskState.submitted
    task_store.assert_save_called(times=1)
    assert task_manager.task_id == 'new-task'
    assert task_manager.context_id == 'some-context'


def test_init_task_obj(task_manager: TaskManager):
    """Test initializing a new task object with default values."""
    new_task = task_manager._init_task_obj('new-task', 'new-context')

    assert new_task.id == 'new-task'
    assert new_task.context_id == 'new-context'
    assert new_task.status.state == TaskState.submitted
    assert new_task.history == []


@pytest.mark.asyncio
async def test_save_task(
    task_manager: TaskManager, task_store: InMemoryTaskStore, submitted_task
):
    """Test saving a task directly to the task store."""
    await task_manager._save_task(submitted_task)

    task_store.assert_save_called(times=1)
    task_store.assert_saved(submitted_task.id)


@pytest.mark.asyncio
async def test_save_task_event_mismatched_id_raises_error(
    task_manager: TaskManager, task_builder
):
    """Test that saving a task with mismatched ID raises an error."""
    mismatched_task = (
        task_builder.with_id('wrong-id').with_context_id('session-xyz').build()
    )

    with pytest.raises(ServerError) as exc_info:
        await task_manager.save_task_event(mismatched_task)
    assert isinstance(exc_info.value.error, InvalidParamsError)


@pytest.mark.asyncio
async def test_save_task_event_new_task_no_task_id(
    task_store: InMemoryTaskStore, task_manager_factory, task_builder
):
    """Test saving a new task event when task manager has no task_id."""
    task_manager = task_manager_factory(task_id=None, context_id=None)

    task = (
        task_builder.with_id('new-task-id')
        .with_context_id('some-context')
        .with_state(TaskState.working)
        .build()
    )

    await task_manager.save_task_event(task)

    task_store.assert_save_called(times=1)
    task_store.assert_saved(task.id)
    assert task_manager.task_id == 'new-task-id'
    assert task_manager.context_id == 'some-context'
    assert task.status.state == TaskState.working


@pytest.mark.asyncio
async def test_get_task_no_task_id(
    task_store: InMemoryTaskStore, task_manager_factory
):
    """Test get_task returns None when task manager has no task_id."""
    task_manager = task_manager_factory(task_id=None, context_id='some-context')

    retrieved_task = await task_manager.get_task()

    assert retrieved_task is None
    task_store.assert_get_called(times=0)


@pytest.mark.asyncio
async def test_save_task_event_no_task_existing(
    task_store: InMemoryTaskStore, task_manager_factory
):
    """Test saving an event when no task exists creates a new task."""
    task_manager = task_manager_factory(task_id=None, context_id=None)

    event = (
        StatusUpdateEventBuilder()
        .for_task('event-task-id')
        .with_state(TaskState.completed)
        .as_final()
        .build()
    )
    event.context_id = 'some-context'

    await task_manager.save_task_event(event)

    saved_task = task_store.get_saved_task('event-task-id')
    assert saved_task.id == 'event-task-id'
    assert saved_task.context_id == 'some-context'
    assert saved_task.status.state == TaskState.completed
    assert task_manager.task_id == 'event-task-id'
    assert task_manager.context_id == 'some-context'
