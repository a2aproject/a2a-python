from __future__ import annotations

import logging

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from a2a.server.context import ServerCallContext
from a2a.server.tasks.task_store import TaskStore
from a2a.types.a2a_pb2 import ListTasksRequest, ListTasksResponse, Task


logger = logging.getLogger(__name__)


class CopyingTaskStoreAdapter(TaskStore):
    """An adapter that ensures deep copies of tasks are passed to and returned from the underlying TaskStore.

    This prevents accidental shared mutable state bugs where code modifies a Task object
    retrieved from the store without explicitly saving it, which hides missing save calls.
    """

    def __init__(self, underlying_store: TaskStore):
        self._store = underlying_store

    async def save(self, task: Task, context: ServerCallContext) -> None:
        """Saves a copy of the task to the underlying store."""
        task_copy = Task()
        task_copy.CopyFrom(task)
        await self._store.save(task_copy, context)

    async def get(
        self, task_id: str, context: ServerCallContext
    ) -> Task | None:
        """Retrieves a task from the underlying store and returns a copy."""
        task = await self._store.get(task_id, context)
        if task is None:
            return None
        task_copy = Task()
        task_copy.CopyFrom(task)
        return task_copy

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        """Retrieves a list of tasks from the underlying store and returns a copy."""
        response = await self._store.list(params, context)
        response_copy = ListTasksResponse()
        response_copy.CopyFrom(response)
        return response_copy

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        """Deletes a task from the underlying store."""
        await self._store.delete(task_id, context)
