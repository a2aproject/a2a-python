import asyncio

from a2a.server.events.event_queue import EventQueue
from a2a.server.events.queue_manager import (
    NoTaskQueue,
    QueueManager,
    TaskQueueExists,
)
from a2a.utils.telemetry import SpanKind, trace_class


@trace_class(kind=SpanKind.SERVER)
class InMemoryQueueManager(QueueManager):
    """InMemoryQueueManager is used for a single binary management.

    This implements the `QueueManager` interface using in-memory storage for event
    queues. It requires all incoming interactions for a given task ID to hit the
    same binary instance.

    This implementation is suitable for single-instance deployments but needs
    a distributed approach for scalable deployments.
    """

    def __init__(self) -> None:
        """Initializes the InMemoryQueueManager."""
        self._task_queue: dict[str, EventQueue] = {}
        self._lock = asyncio.Lock()

    async def add(self, task_id: str, queue: EventQueue) -> None:
        """Adds a new event queue for a task ID.

        Raises:
            TaskQueueExists: If a queue for the given `task_id` already exists.
        """
        async with self._lock:
            if task_id in self._task_queue:
                raise TaskQueueExists
            self._task_queue[task_id] = queue

    async def get(self, task_id: str) -> EventQueue | None:
        """Retrieves the event queue for a task ID.

        Returns:
            The `EventQueue` instance for the `task_id`, or `None` if not found.
        """
        async with self._lock:
            if task_id not in self._task_queue:
                return None
            return self._task_queue[task_id]

    async def tap(self, task_id: str) -> EventQueue | None:
        """Taps the event queue for a task ID to create a child queue.

        Returns:
            A new child `EventQueue` instance, or `None` if the task ID is not found.
        """
        async with self._lock:
            if task_id not in self._task_queue:
                return None
            return self._task_queue[task_id].tap()

    async def close(self, task_id: str) -> None:
        """Closes and removes the event queue for a task ID.

        Raises:
            NoTaskQueue: If no queue exists for the given `task_id`.
        """
        async with self._lock:
            if task_id not in self._task_queue:
                raise NoTaskQueue
            queue = self._task_queue.pop(task_id)
            await queue.close()

    async def create_or_tap(self, task_id: str) -> EventQueue:
        """Creates a new event queue for a task ID if one doesn't exist, otherwise taps the existing one.

        Returns:
            A new or child `EventQueue` instance for the `task_id`.
        """
        async with self._lock:
            if task_id not in self._task_queue:
                queue = EventQueue()
                self._task_queue[task_id] = queue
                return queue
            return self._task_queue[task_id].tap()
