import pytest

from a2a.server.tasks import InMemoryTaskStore
from a2a.types.a2a_pb2 import Task, TaskState, TaskStatus


def create_minimal_task(task_id: str = 'task-abc', context_id: str = 'session-xyz') -> Task:
    """Create a minimal task for testing."""
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )


@pytest.mark.asyncio
async def test_in_memory_task_store_save_and_get() -> None:
    """Test saving and retrieving a task from the in-memory store."""
    store = InMemoryTaskStore()
    task = create_minimal_task()
    await store.save(task)
    retrieved_task = await store.get('task-abc')
    assert retrieved_task == task


@pytest.mark.asyncio
async def test_in_memory_task_store_get_nonexistent() -> None:
    """Test retrieving a nonexistent task."""
    store = InMemoryTaskStore()
    retrieved_task = await store.get('nonexistent')
    assert retrieved_task is None


@pytest.mark.asyncio
async def test_in_memory_task_store_delete() -> None:
    """Test deleting a task from the store."""
    store = InMemoryTaskStore()
    task = create_minimal_task()
    await store.save(task)
    await store.delete('task-abc')
    retrieved_task = await store.get('task-abc')
    assert retrieved_task is None


@pytest.mark.asyncio
async def test_in_memory_task_store_delete_nonexistent() -> None:
    """Test deleting a nonexistent task."""
    store = InMemoryTaskStore()
    await store.delete('nonexistent')
