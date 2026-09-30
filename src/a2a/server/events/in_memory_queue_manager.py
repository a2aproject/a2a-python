import threading

from a2a.server.events.event_queue import EventQueueLegacy
from a2a.server.events.queue_manager import (
    NoTaskQueue,
    QueueManager,
    TaskQueueExists,
)
from a2a.utils.telemetry import SpanKind, trace_class


@trace_class(
    kind=SpanKind.SERVER,
    exclude_list=['add', 'get', 'create_or_tap'],
)
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
        self._task_queue: dict[str, EventQueueLegacy] = {}
        self._lock = threading.RLock()

    async def add(self, task_id: str, queue: EventQueueLegacy) -> None:
        """Adds a new event queue for a task ID.

        Raises:
            TaskQueueExists: If a queue for the given `task_id` already exists.
        """
        with self._lock:
            if task_id in self._task_queue:
                raise TaskQueueExists
            self._task_queue[task_id] = queue

    async def get(self, task_id: str) -> EventQueueLegacy | None:
        """Retrieves the event queue for a task ID.

        Returns:
            The `EventQueueLegacy` instance for the `task_id`, or `None` if not found.
        """
        with self._lock:
            return self._task_queue.get(task_id)

    async def tap(self, task_id: str) -> EventQueueLegacy | None:
        """Taps the event queue for a task ID to create a child queue.

        Returns:
            A new child `EventQueueLegacy` instance, or `None` if the task ID is not found.
        """
        with self._lock:
            queue = self._task_queue.get(task_id)
        if queue is None:
            return None
        return await queue.tap()

    async def close(self, task_id: str) -> None:
        """Closes and removes the event queue for a task ID.

        Raises:
            NoTaskQueue: If no queue exists for the given `task_id`.
        """
        with self._lock:
            queue = self._task_queue.pop(task_id, None)
        if queue is None:
            raise NoTaskQueue
        await queue.close()

    async def create_or_tap(self, task_id: str) -> EventQueueLegacy:
        """Creates a new event queue for a task ID if one doesn't exist, otherwise taps the existing one.

        Returns:
            A new or child `EventQueueLegacy` instance for the `task_id`.
        """
        with self._lock:
            queue = self._task_queue.get(task_id)
            if queue is None:
                queue = EventQueueLegacy()
                self._task_queue[task_id] = queue
                return queue
        return await queue.tap()
