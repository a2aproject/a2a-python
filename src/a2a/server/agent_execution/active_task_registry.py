from __future__ import annotations

import asyncio
import logging

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from a2a.server.agent_execution.agent_executor import AgentExecutor
    from a2a.server.context import ServerCallContext
    from a2a.server.tasks.push_notification_sender import PushNotificationSender
    from a2a.server.tasks.task_store import TaskStore

from a2a.server.agent_execution.active_task import ActiveTask
from a2a.server.tasks.task_manager import TaskManager


logger = logging.getLogger(__name__)


class ActiveTaskRegistry:
    """A registry for active ActiveTask instances."""

    def __init__(
        self,
        agent_executor: AgentExecutor,
        task_store: TaskStore,
        push_sender: PushNotificationSender | None = None,
    ):
        self._agent_executor = agent_executor
        self._task_store = task_store
        self._push_sender = push_sender
        self._active_tasks: dict[str, ActiveTask] = {}
        self._lock = asyncio.Lock()
        self._cleanup_tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    async def get_or_create(
        self,
        task_id: str,
        call_context: ServerCallContext,
        context_id: str | None = None,
        create_task_if_missing: bool = False,
    ) -> ActiveTask:
        """Retrieves an existing ActiveTask or creates a new one."""
        async with self._lock:
            if self._closed:
                raise RuntimeError('ActiveTaskRegistry is closed')
            if task_id in self._active_tasks:
                return self._active_tasks[task_id]

            task_manager = TaskManager(
                task_id=task_id,
                context_id=context_id,
                task_store=self._task_store,
                initial_message=None,
                context=call_context,
            )

            active_task = ActiveTask(
                agent_executor=self._agent_executor,
                task_id=task_id,
                task_manager=task_manager,
                push_sender=self._push_sender,
                on_cleanup=self._on_active_task_cleanup,
            )
            self._active_tasks[task_id] = active_task

        await active_task.start(
            call_context=call_context,
            create_task_if_missing=create_task_if_missing,
        )
        return active_task

    def _on_active_task_cleanup(self, active_task: ActiveTask) -> None:
        """Called by ActiveTask when it's finished and has no subscribers."""
        logger.debug('Active task %s cleanup scheduled', active_task.task_id)
        task = asyncio.create_task(self._remove_task(active_task.task_id))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _remove_task(self, task_id: str) -> None:
        async with self._lock:
            self._active_tasks.pop(task_id, None)
            logger.debug('Removed active task for %s from registry', task_id)

    async def get(self, task_id: str) -> ActiveTask | None:
        """Retrieves an existing task."""
        async with self._lock:
            return self._active_tasks.get(task_id)

    async def aclose(self) -> None:
        """Closes the registry and drains all active tasks.

        Marks the registry closed so ``get_or_create`` refuses new work, then
        force-closes every registered ``ActiveTask`` and awaits the in-flight
        ``_remove_task`` cleanup tasks they schedule, so no SDK-owned
        ``asyncio.Task`` is left pending at event-loop shutdown. Safe to call
        multiple times.

        The close flag is set and the active-task snapshot is taken under
        ``_lock``, and the lock is then released before awaiting, because
        ``_remove_task`` re-acquires ``_lock``; holding it while draining
        would deadlock. Marking closed under the same lock prevents a
        concurrent ``get_or_create`` from registering a task that the drain
        would miss.
        """
        async with self._lock:
            self._closed = True
            active_tasks = list(self._active_tasks.values())

        if active_tasks:
            results = await asyncio.gather(
                *(task.aclose() for task in active_tasks),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, Exception):
                    logger.error('Error draining active task', exc_info=result)

        cleanup_tasks = list(self._cleanup_tasks)
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)

        async with self._lock:
            self._active_tasks.clear()
