from abc import ABC, abstractmethod

from a2a.server.events.event_queue import EventQueueLegacy


class QueueManager(ABC):
    """Interface for managing the event queue lifecycles per task."""

    @abstractmethod
    async def add(self, task_id: str, queue: EventQueueLegacy) -> None:
        """Adds a new event queue associated with a task ID."""

    @abstractmethod
    async def get(self, task_id: str) -> EventQueueLegacy | None:
        """Retrieves the event queue for a task ID."""

    @abstractmethod
    async def tap(self, task_id: str) -> EventQueueLegacy | None:
        """Creates a child event queue (tap) for an existing task ID."""

    @abstractmethod
    async def close(self, task_id: str) -> None:
        """Closes and removes the event queue for a task ID."""

    @abstractmethod
    async def create_or_tap(self, task_id: str) -> EventQueueLegacy:
        """Creates a queue if one doesn't exist, otherwise taps the existing one."""


class TaskQueueExists(Exception):  # noqa: N818
    """Exception raised when attempting to add a queue for a task ID that already exists."""


class NoTaskQueue(Exception):  # noqa: N818
    """Exception raised when attempting to access or close a queue for a task ID that does not exist."""
