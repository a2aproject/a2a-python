import asyncio
import logging

from a2a.server.context import ServerCallContext
from a2a.server.tasks.task_store import TaskStore, TasksPage
from a2a.types import ListTasksParams, Task
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE


logger = logging.getLogger(__name__)


class InMemoryTaskStore(TaskStore):
    """In-memory implementation of TaskStore.

    Stores task objects in a dictionary in memory. Task data is lost when the
    server process stops.
    """

    def __init__(self) -> None:
        """Initializes the InMemoryTaskStore."""
        logger.debug('Initializing InMemoryTaskStore')
        self.tasks: dict[str, Task] = {}
        self.lock = asyncio.Lock()

    async def save(
        self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        """Saves or updates a task in the in-memory store."""
        async with self.lock:
            self.tasks[task.id] = task
            logger.debug('Task %s saved successfully.', task.id)

    async def get(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        """Retrieves a task from the in-memory store by ID."""
        async with self.lock:
            logger.debug('Attempting to get task with id: %s', task_id)
            task = self.tasks.get(task_id)
            if task:
                logger.debug('Task %s retrieved successfully.', task_id)
            else:
                logger.debug('Task %s not found in store.', task_id)
            return task

    async def list(
        self,
        params: ListTasksParams,
        context: ServerCallContext | None = None,
    ) -> TasksPage:
        """Retrieves a list of tasks from the store."""
        async with self.lock:
            tasks = list(self.tasks.values())

        # Apply filtering
        if params.context_id:
            tasks = [
                task for task in tasks if task.context_id == params.context_id
            ]
        if params.status is not None:
            tasks = [
                task for task in tasks if task.status.state == params.status
            ]

        # Apply pagination
        total_size = len(tasks)
        page_token = int(params.page_token) if params.page_token else 0
        page_size = params.page_size or DEFAULT_LIST_TASKS_PAGE_SIZE
        tasks = tasks[page_token * page_size : (page_token + 1) * page_size]

        next_page_token = (
            str(page_token + 1)
            if (page_token + 1) * page_size < total_size
            else ''
        )

        return TasksPage(
            next_page_token=next_page_token,
            tasks=tasks,
            total_size=total_size,
        )

    async def delete(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        """Deletes a task from the in-memory store by ID."""
        async with self.lock:
            logger.debug('Attempting to delete task with id: %s', task_id)
            if task_id in self.tasks:
                del self.tasks[task_id]
                logger.debug('Task %s deleted successfully.', task_id)
            else:
                logger.warning(
                    'Attempted to delete nonexistent task with id: %s', task_id
                )
