from __future__ import annotations

import asyncio
import logging

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from a2a.server.agent_execution.agent_executor import AgentExecutor
    from a2a.server.context import ServerCallContext
    from a2a.server.tasks.push_notification_sender import PushNotificationSender
    from a2a.server.tasks.task_store import TaskStore
    from a2a.types.a2a_pb2 import Message

from a2a.server.agent_execution.active_task import ActiveTask
from a2a.server.events.event_queue import EventQueue
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

    async def get_or_create(
        self,
        task_id: str,
        context: ServerCallContext | None = None,
        initial_message: Message | None = None,
    ) -> ActiveTask:
        """Retrieves an existing ActiveTask or creates a new one."""
        async with self._lock:
            if task_id in self._active_tasks:
                return self._active_tasks[task_id]

            event_queue = EventQueue()
            task_manager = TaskManager(
                task_id=task_id,
                context_id=None,
                task_store=self._task_store,
                initial_message=initial_message,
                context=context,
            )

            active_task = ActiveTask(
                agent_executor=self._agent_executor,
                task_id=task_id,
                event_queue=event_queue,
                task_manager=task_manager,
                push_sender=self._push_sender,
                on_cleanup=self._on_active_task_cleanup,
            )
            self._active_tasks[task_id] = active_task
            return active_task

    def _on_active_task_cleanup(self, active_task: ActiveTask) -> None:
        """Called by ActiveTask when it's finished and has no subscribers."""
        task = asyncio.create_task(self._remove_task(active_task.task_id))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _remove_task(self, task_id: str) -> None:
        async with self._lock:
            self._active_tasks.pop(task_id, None)
            logger.debug('Removed active task for %s from registry', task_id)

    async def get(self, task_id: str) -> ActiveTask | None:
        """Retrieves an existing ActiveTask if it exists."""
        async with self._lock:
            return self._active_tasks.get(task_id)
