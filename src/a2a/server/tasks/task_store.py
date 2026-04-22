from abc import ABC, abstractmethod

from a2a.server.context import ServerCallContext
from a2a.types.a2a_pb2 import ListTasksRequest, ListTasksResponse, Task


class TaskStore(ABC):
    """Agent Task Store interface.

    Defines the methods for persisting and retrieving `Task` objects.
    """

    @abstractmethod
    async def save(self, task: Task, context: ServerCallContext) -> None:
        """Saves or updates a task in the store."""

    @abstractmethod
    async def get(
        self, task_id: str, context: ServerCallContext
    ) -> Task | None:
        """Retrieves a task from the store by ID."""

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
