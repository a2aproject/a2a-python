from abc import ABC, abstractmethod

from pydantic import BaseModel

from a2a.server.context import ServerCallContext
from a2a.types import ListTasksParams, Task


class TasksPage(BaseModel):
    """Page with tasks."""

    next_page_token: str | None = None
    tasks: list[Task]
    total_size: int


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
    async def list(
        self,
        params: ListTasksParams,
        context: ServerCallContext | None = None,
    ) -> TasksPage:
        """Retrieves a list of tasks from the store."""

    @abstractmethod
    async def delete(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        """Deletes a task from the store by ID."""
