from typing import Any

import pytest

from a2a.server.tasks import InMemoryTaskStore
from a2a.types import Task


MINIMAL_TASK: dict[str, Any] = {
    'id': 'task-abc',
    'context_id': 'session-xyz',
    'status': {'state': 'submitted'},
    'kind': 'task',
}


@pytest.mark.asyncio
async def test_in_memory_task_store_save_and_get() -> None:
    """Test saving and retrieving a task from the in-memory store."""
    store = InMemoryTaskStore()
    task = Task(**MINIMAL_TASK)
    await store.save(task)
    retrieved_task = await store.get(MINIMAL_TASK['id'])
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
    task = Task(**MINIMAL_TASK)
    await store.save(task)
    await store.delete(MINIMAL_TASK['id'])
    retrieved_task = await store.get(MINIMAL_TASK['id'])
    assert retrieved_task is None


@pytest.mark.asyncio
async def test_in_memory_task_store_delete_nonexistent() -> None:
    """Test deleting a nonexistent task."""
    store = InMemoryTaskStore()
    await store.delete('nonexistent')
