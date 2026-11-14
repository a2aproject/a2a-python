from abc import ABC, abstractmethod
from dataclasses import dataclass

from a2a.server.cluster.version import TaskVersion
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
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
        event: Event | None,
        prev: Task | None,
        prev_version: TaskVersion,
        context: ServerCallContext,
    ) -> TaskVersion:
        """Persists `task` and returns its new version.

        Args:
            task: The task state to persist.
            event: The event that produced this state, or `None` for a direct
                write.
            prev: The task as previously read, for implementations that diff.
            prev_version: The version `task` was derived from. Implementations
                MUST raise `ConcurrentTaskModificationError` if the currently
                stored version differs. `TaskVersion.MISSING` marks a first
                write. A write moving `task` to CANCELED overwrites a
                non-terminal stored task without a version check, and raises
                `ConcurrentTaskModificationError` if the stored task is already
                terminal or absent.
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
        event: Event | None,
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
