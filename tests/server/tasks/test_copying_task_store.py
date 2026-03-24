from __future__ import annotations

import unittest
import pytest

from unittest.mock import AsyncMock

from a2a.server.context import ServerCallContext
from a2a.server.tasks.copying_task_store import CopyingTaskStoreAdapter
from a2a.server.tasks.task_store import TaskStore
from a2a.types.a2a_pb2 import (
    ListTasksRequest,
    ListTasksResponse,
    Task,
    TaskState,
)


@pytest.mark.asyncio
async def test_copying_task_store_save():
    """Test that the adapter makes a copy of the task when saving."""
    mock_store = AsyncMock(spec=TaskStore)
    adapter = CopyingTaskStoreAdapter(mock_store)

    original_task = Task(
        id='test_task', status={'state': TaskState.TASK_STATE_WORKING}
    )
    context = ServerCallContext()

    await adapter.save(original_task, context)

    # Verify underlying store was called
    mock_store.save.assert_awaited_once()

    # Get the saved task
    saved_task = mock_store.save.call_args[0][0]
    saved_context = mock_store.save.call_args[0][1]

    # Verify context is passed correctly
    assert saved_context is context

    # Verify content is identical
    assert saved_task.id == original_task.id
    assert saved_task.status.state == original_task.status.state

    # Verify it is a COPY, not the same reference
    assert saved_task is not original_task


@pytest.mark.asyncio
async def test_copying_task_store_get():
    """Test that the adapter returns a copy of the task retrieved."""
    mock_store = AsyncMock(spec=TaskStore)
    adapter = CopyingTaskStoreAdapter(mock_store)

    stored_task = Task(
        id='test_task', status={'state': TaskState.TASK_STATE_WORKING}
    )
    mock_store.get.return_value = stored_task
    context = ServerCallContext()

    retrieved_task = await adapter.get('test_task', context)

    # Verify underlying store was called
    mock_store.get.assert_awaited_once_with('test_task', context)

    # Verify retrieved task has identical content
    assert retrieved_task is not None
    assert retrieved_task.id == stored_task.id
    assert retrieved_task.status.state == stored_task.status.state

    # Verify it is a COPY, not the same reference
    assert retrieved_task is not stored_task


@pytest.mark.asyncio
async def test_copying_task_store_get_none():
    """Test that the adapter properly returns None when no task is found."""
    mock_store = AsyncMock(spec=TaskStore)
    adapter = CopyingTaskStoreAdapter(mock_store)

    mock_store.get.return_value = None
    context = ServerCallContext()

    retrieved_task = await adapter.get('test_task', context)

    # Verify underlying store was called
    mock_store.get.assert_awaited_once_with('test_task', context)
    assert retrieved_task is None


@pytest.mark.asyncio
async def test_copying_task_store_list():
    """Test that the adapter returns a copy of the list response."""
    mock_store = AsyncMock(spec=TaskStore)
    adapter = CopyingTaskStoreAdapter(mock_store)

    task1 = Task(id='test_task_1')
    task2 = Task(id='test_task_2')
    stored_response = ListTasksResponse(tasks=[task1, task2])
    mock_store.list.return_value = stored_response
    context = ServerCallContext()
    request = ListTasksRequest(page_size=10)

    retrieved_response = await adapter.list(request, context)

    # Verify underlying store was called
    mock_store.list.assert_awaited_once_with(request, context)

    # Verify retrieved response has identical content
    assert len(retrieved_response.tasks) == 2
    assert retrieved_response.tasks[0].id == 'test_task_1'
    assert retrieved_response.tasks[1].id == 'test_task_2'

    # Verify it is a COPY, not the same reference
    assert retrieved_response is not stored_response
    # Also verify inner tasks are copies
    assert retrieved_response.tasks[0] is not task1
    assert retrieved_response.tasks[1] is not task2


@pytest.mark.asyncio
async def test_copying_task_store_delete():
    """Test that the adapter calls delete on underlying store."""
    mock_store = AsyncMock(spec=TaskStore)
    adapter = CopyingTaskStoreAdapter(mock_store)
    context = ServerCallContext()

    await adapter.delete('test_task', context)

    # Verify underlying store was called
    mock_store.delete.assert_awaited_once_with('test_task', context)
