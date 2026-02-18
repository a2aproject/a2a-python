import asyncio
import logging

from collections import defaultdict
from datetime import datetime, timezone

from a2a.server.context import ServerCallContext
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.server.tasks.task_store import TaskStore, TasksPage
from a2a.types import ListTasksParams, Task
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE
from a2a.utils.task import decode_page_token, encode_page_token


logger = logging.getLogger(__name__)


class InMemoryTaskStore(TaskStore):
    """In-memory implementation of TaskStore.

    Stores task objects in a nested dictionary in memory, keyed by owner then task_id.
    Task data is lost when the server process stops.
    """

    def __init__(
        self,
        owner_resolver: OwnerResolver = resolve_user_scope,
    ) -> None:
        """Initializes the InMemoryTaskStore."""
        logger.debug('Initializing InMemoryTaskStore')
        self.tasks: dict[str, dict[str, Task]] = defaultdict(dict)
        self.lock = asyncio.Lock()
        self.owner_resolver = owner_resolver

    async def save(
        self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        """Saves or updates a task in the in-memory store for the resolved owner."""
        owner = self.owner_resolver(context)

        async with self.lock:
            self.tasks[owner][task.id] = task
            logger.debug(
                'Task %s for owner %s saved successfully.', task.id, owner
            )

    async def get(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        """Retrieves a task from the in-memory store by ID, for the given owner."""
        owner = self.owner_resolver(context)
        async with self.lock:
            logger.debug(
                'Attempting to get task with id: %s for owner: %s',
                task_id,
                owner,
            )
            owner_tasks = self.tasks.get(owner)
            if owner_tasks:
                task = owner_tasks.get(task_id)
                if task:
                    logger.debug(
                        'Task %s retrieved successfully for owner %s.',
                        task_id,
                        owner,
                    )
                    return task
            logger.debug(
                'Task %s not found in store for owner %s.', task_id, owner
            )
            return None

    async def list(
        self,
        params: ListTasksParams,
        context: ServerCallContext | None = None,
    ) -> TasksPage:
        """Retrieves a list of tasks from the store, for the given owner."""
        owner = self.owner_resolver(context)
        logger.debug('Listing tasks for owner %s with params %s', owner, params)

        async with self.lock:
            owner_tasks = self.tasks.get(owner, {})
            tasks = list(owner_tasks.values())

        # Filter tasks
        if params.context_id:
            tasks = [
                task for task in tasks if task.context_id == params.context_id
            ]
        if params.status and params.status != 'unknown':
            tasks = [
                task for task in tasks if task.status.state == params.status
            ]
        if params.last_updated_after:
            last_updated_after_iso = datetime.fromtimestamp(
                params.last_updated_after / 1000, tz=timezone.utc
            ).isoformat()
            tasks = [
                task
                for task in tasks
                if (
                    task.status.timestamp
                    and task.status.timestamp >= last_updated_after_iso
                )
            ]

        # Order tasks by last update time. To ensure stable sorting, in cases where timestamps are null or not unique, do a second order comparison of IDs.
        tasks.sort(
            key=lambda task: (
                task.status.timestamp is not None,
                task.status.timestamp,
                task.id,
            ),
            reverse=True,
        )

        # Paginate tasks
        total_size = len(tasks)
        start_idx = 0
        if params.page_token:
            start_task_id = decode_page_token(params.page_token)
            valid_token = False
            for i, task in enumerate(tasks):
                if task.id == start_task_id:
                    start_idx = i
                    valid_token = True
                    break
            if not valid_token:
                raise ValueError(f'Invalid page token: {params.page_token}')
        end_idx = start_idx + (params.page_size or DEFAULT_LIST_TASKS_PAGE_SIZE)
        next_page_token = (
            encode_page_token(tasks[end_idx].id)
            if end_idx < total_size
            else None
        )
        tasks = tasks[start_idx:end_idx]

        return TasksPage(
            next_page_token=next_page_token,
            tasks=tasks,
            total_size=total_size,
        )

    async def delete(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        """Deletes a task from the in-memory store by ID, for the given owner."""
        owner = self.owner_resolver(context)
        async with self.lock:
            logger.debug(
                'Attempting to delete task with id: %s for owner %s',
                task_id,
                owner,
            )
            if owner in self.tasks and task_id in self.tasks[owner]:
                del self.tasks[owner][task_id]
                logger.debug(
                    'Task %s deleted successfully for owner %s.', task_id, owner
                )
                if not self.tasks[owner]:
                    del self.tasks[owner]
                    logger.debug('Removed empty owner %s from store.', owner)
            else:
                logger.warning(
                    'Attempted to delete nonexistent task with id: %s for owner %s',
                    task_id,
                    owner,
                )
