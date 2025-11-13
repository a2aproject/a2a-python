from typing import Any

import pytest

from a2a.server.tasks import InMemoryTaskStore
from a2a.types import ListTasksParams, Task, TaskState, TaskStatus


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
@pytest.mark.parametrize(
    'params, expected_ids, total_count, next_page_token',
    [
        # No parameters, should return all tasks
        (
            ListTasksParams(),
            ['task-0', 'task-1', 'task-2', 'task-3', 'task-4'],
            5,
            None,
        ),
        # Pagination (first page)
        (
            ListTasksParams(page_size=2, page_token='0'),
            ['task-0', 'task-1'],
            5,
            '1',
        ),
        # Pagination (final page)
        (
            ListTasksParams(page_size=2, page_token='2'),
            ['task-4'],
            5,
            None,
        ),
        # Pagination (out of bounds)
        (
            ListTasksParams(page_size=2, page_token='3'),
            [],
            5,
            None,
        ),
        # Filtering by context_id
        (
            ListTasksParams(context_id='context-1'),
            ['task-1', 'task-3'],
            2,
            None,
        ),
        # Filtering by status
        (
            ListTasksParams(status=TaskState.working),
            ['task-1', 'task-3'],
            2,
            None,
        ),
        # Combined filtering (context_id and status)
        (
            ListTasksParams(context_id='context-0', status=TaskState.submitted),
            ['task-0', 'task-2'],
            2,
            None,
        ),
        # Combined filtering and pagination
        (
            ListTasksParams(
                context_id='context-0', page_size=1, page_token='0'
            ),
            ['task-0'],
            3,
            '1',
        ),
    ],
)
async def test_list_tasks(
    params: ListTasksParams,
    expected_ids: list[str],
    total_count: int,
    next_page_token: str,
) -> None:
    """Test listing tasks with various filters and pagination."""
    store = InMemoryTaskStore()
    task = Task(**MINIMAL_TASK)
    tasks_to_create = [
        task.model_copy(
            update={
                'id': 'task-0',
                'context_id': 'context-0',
                'status': TaskStatus(state=TaskState.submitted),
                'kind': 'task',
            }
        ),
        task.model_copy(
            update={
                'id': 'task-1',
                'context_id': 'context-1',
                'status': TaskStatus(state=TaskState.working),
                'kind': 'task',
            }
        ),
        task.model_copy(
            update={
                'id': 'task-2',
                'context_id': 'context-0',
                'status': TaskStatus(state=TaskState.submitted),
                'kind': 'task',
            }
        ),
        task.model_copy(
            update={
                'id': 'task-3',
                'context_id': 'context-1',
                'status': TaskStatus(state=TaskState.working),
                'kind': 'task',
            }
        ),
        task.model_copy(
            update={
                'id': 'task-4',
                'context_id': 'context-0',
                'status': TaskStatus(state=TaskState.completed),
                'kind': 'task',
            }
        ),
    ]
    for task in tasks_to_create:
        await store.save(task)

    page = await store.list(params)

    retrieved_ids = [task.id for task in page.tasks]
    assert retrieved_ids == expected_ids
    assert page.total_size == total_count
    assert page.next_page_token == next_page_token

    # Cleanup
    for task in tasks_to_create:
        await store.delete(task.id)


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
