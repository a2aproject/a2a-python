from abc import ABC, abstractmethod

from a2a.server.context import ServerCallContext
from a2a.types import Task


class TaskStore(ABC):
    """Agent Task Store interface.

    Defines the methods for persisting and retrieving `Task` objects.
    """

    @abstractmethod
    async def save(
        self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        """Saves or updates a task in the store."""

    @abstractmethod
    async def get(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        """Retrieves a task from the store by ID."""

    @abstractmethod
    async def delete(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        """Deletes a task from the store by ID."""
