from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from a2a.server.cluster.version import TaskVersion
from a2a.server.events.event_queue import Event


@dataclass(frozen=True)
class VersionedEvent:
    """An event together with the task version produced by applying it."""

    event: Event
    version: TaskVersion


class TaskEventStream(ABC):
    """Delivers task events across replicas."""

    @abstractmethod
    async def publish(self, task_id: str, event: VersionedEvent) -> None:
        """Publishes one event for `task_id` to all replicas."""

    @abstractmethod
    def subscribe(
        self, task_id: str, *, after: TaskVersion
    ) -> AsyncGenerator[VersionedEvent, None]:
        """Yields events for `task_id` newer than `after`."""

    @abstractmethod
    async def destroy(self, task_id: str) -> None:
        """Releases resources for a task that has reached a terminal state."""
