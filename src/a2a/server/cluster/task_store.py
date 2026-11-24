from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from a2a.server.cluster.version import TaskVersion
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import StoredEvent
from a2a.server.tasks.task_store import TaskStore
from a2a.types.a2a_pb2 import ListTasksRequest, ListTasksResponse, Task


@dataclass(frozen=True)
class StoredTask:
    """A task together with the version it was read at."""

    task: Task
    version: TaskVersion


class ConcurrentTaskModificationError(Exception):
    """Raised by `VersionedTaskStore.save` when `prev_version` is stale."""

    def __init__(self, task_id: str) -> None:
        super().__init__(
            f'Task {task_id} was modified concurrently by another writer'
        )
        self.task_id = task_id


class VersionedTaskStore(ABC):
    """A `TaskStore` variant with snapshot versioning to prevent concurrent re-writes."""

    @abstractmethod
    async def save(
        self,
        task: Task,
        *,
        event: StoredEvent | None,
        prev: Task | None,
        prev_version: TaskVersion,
        context: ServerCallContext,
    ) -> TaskVersion:
        """Persists `task` and returns its new version.

        Args:
            task: The task state to persist.
            event: The event that produced this state, or `None` for a direct
                write. Carries the same `generation` as `task`.
            prev: The task as previously read, for implementations that diff.
            prev_version: The version `task` was derived from. Implementations
                MUST raise `ConcurrentTaskModificationError` if the currently
                stored version differs. `TaskVersion.MISSING` marks a first
                write.
            context: The server call context (used to resolve the owner).

        Returns:
            The new `TaskVersion` for the persisted task.

        Raises:
            ConcurrentTaskModificationError: If `prev_version` is stale.
        """

    @abstractmethod
    async def get(
        self, task_id: str, context: ServerCallContext
    ) -> StoredTask | None:
        """Retrieves a task with its version, or `None` if it does not exist."""

    @abstractmethod
    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        """Retrieves a list of tasks from the store."""

    @abstractmethod
    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        """Deletes a task from the store by ID."""

    async def update(
        self,
        task_id: str,
        change: Callable[[Task], StoredEvent | None],
        *,
        context: ServerCallContext,
    ) -> StoredTask | None:
        """Applies `change` to the current task and saves the result.

        `change` edits the task it is given and returns the event for the
        write, or `None` to leave the task as it is. It always sees the
        latest state, so the write never discards another writer's change.
        This implementation re-reads and calls `change` again when another
        writer gets in first; stores that can lock the task should override
        it so `change` applies on the first attempt.

        Returns:
            The task after the change, or `None` if the task does not exist.
        """
        while True:
            stored = await self.get(task_id, context)
            if stored is None:
                return None
            task = Task()
            task.CopyFrom(stored.task)
            event = change(task)
            if event is None:
                return stored
            try:
                version = await self.save(
                    task,
                    event=event,
                    prev=stored.task,
                    prev_version=stored.version,
                    context=context,
                )
            except ConcurrentTaskModificationError:
                continue
            return StoredTask(task, version)


class LegacyTaskStoreAdapter(VersionedTaskStore):
    """Runs an unversioned `TaskStore` under the `VersionedTaskStore` interface."""

    def __init__(self, store: TaskStore) -> None:
        self._store = store

    @property
    def store(self) -> TaskStore:
        """The wrapped task store."""
        return self._store

    async def save(
        self,
        task: Task,
        *,
        event: StoredEvent | None,
        prev: Task | None,
        prev_version: TaskVersion,
        context: ServerCallContext,
    ) -> TaskVersion:
        """Saves via the wrapped store; ignores version args, returns MISSING."""
        del event, prev, prev_version  # unversioned store ignores these
        await self._store.save(task, context)
        return TaskVersion.MISSING

    async def get(
        self, task_id: str, context: ServerCallContext
    ) -> StoredTask | None:
        """Gets from the wrapped store, pairing the result with MISSING."""
        task = await self._store.get(task_id, context)
        if task is None:
            return None
        return StoredTask(task, TaskVersion.MISSING)

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        """Lists tasks via the wrapped store."""
        return await self._store.list(params, context)

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        """Deletes a task via the wrapped store."""
        await self._store.delete(task_id, context)
