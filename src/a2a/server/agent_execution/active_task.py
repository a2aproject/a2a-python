from __future__ import annotations

import asyncio
import contextlib
import logging

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from a2a.server.agent_execution.agent_executor import AgentExecutor
    from a2a.server.agent_execution.context import RequestContext
    from a2a.server.events.event_queue import Event, EventQueue
    from a2a.server.tasks.push_notification_sender import (
        PushNotificationSender,
    )
    from a2a.server.tasks.task_manager import TaskManager


from a2a.server.events.event_queue import QueueShutDown
from a2a.server.tasks import PushNotificationEvent
from a2a.types.a2a_pb2 import (
    Message,
    Task,
    TaskState,
)
from a2a.utils.errors import TaskAlreadyStartedError


logger = logging.getLogger(__name__)


class ActiveTask:
    """Manages the lifecycle and execution of an active A2A task.

    It coordinates between the agent's execution (the producer), the
    persistence and state management (the TaskManager), and the event
    distribution to subscribers (the consumer).
    """

    def __init__(  # noqa: PLR0913
        self,
        agent_executor: AgentExecutor,
        task_id: str,
        event_queue: EventQueue,
        task_manager: TaskManager,
        push_sender: PushNotificationSender | None = None,
        on_cleanup: Callable[[ActiveTask], None] | None = None,
    ) -> None:
        """Initializes the ActiveTask.

        Args:
            agent_executor: The executor to run the agent logic.
            task_id: The ID of the task.
            event_queue: The queue for events produced by the agent.
            task_manager: The manager for task state and persistence.
            push_sender: Optional sender for push notifications.
            on_cleanup: Optional callback when the task is finished and has no subscribers.
        """
        self._agent_executor = agent_executor
        self._task_id = task_id
        self._event_queue = event_queue
        self._task_manager = task_manager
        self._push_sender = push_sender
        self._on_cleanup = on_cleanup

        self._lock = asyncio.Lock()
        self._producer_task: asyncio.Task[None] | None = None
        self._consumer_task: asyncio.Task[None] | None = None
        self._is_finished = asyncio.Event()
        self._state_changed = asyncio.Condition()
        self._subscribers_count = 0
        self._exception: Exception | None = None

        self._result_available = False
        self._first_result: Task | Message | None = None
        self._message: Message | None = None
        self._interrupted = False
        self._cancelled = False

    @property
    def task_id(self) -> str:
        """The ID of the task."""
        return self._task_id

    async def start(self, request: RequestContext) -> None:
        """Starts the active task background processes.

        Args:
            request: The request context to execute.

        Raises:
            TaskAlreadyStartedError: If the task is already running.
        """
        async with self._lock:
            if self._producer_task is not None:
                raise TaskAlreadyStartedError(
                    f'Task {self._task_id} already started'
                )

            self._producer_task = asyncio.create_task(
                self._run_producer(request), name=f'producer:{self._task_id}'
            )
            self._consumer_task = asyncio.create_task(
                self._run_consumer(), name=f'consumer:{self._task_id}'
            )

    async def _run_producer(self, request: RequestContext) -> None:
        try:
            await self._agent_executor.execute(request, self._event_queue)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception('Producer failed for task %s', self._task_id)
            self._exception = e
        finally:
            async with self._state_changed:
                self._state_changed.notify_all()
            # Important: Non-immediate close to allow consumer to drain
            await self._event_queue.close(immediate=False)

    async def _run_consumer(self) -> None:
        try:
            try:
                while True:
                    # Dequeue event. This raises QueueShutDown when finished.
                    event = await self._event_queue.dequeue_event()

                    try:
                        if isinstance(event, Message):
                            if not self._result_available:
                                self._first_result = event
                                self._result_available = True
                            self._message = event
                        else:
                            await self._task_manager.process(event)

                            # Check for AUTH_REQUIRED or INPUT_REQUIRED or TERMINAL states
                            res = await self._task_manager.get_task()
                            is_interrupted = res and res.status.state in (
                                TaskState.TASK_STATE_AUTH_REQUIRED,
                                TaskState.TASK_STATE_INPUT_REQUIRED,
                            )
                            is_terminal = res and res.status.state in (
                                TaskState.TASK_STATE_COMPLETED,
                                TaskState.TASK_STATE_CANCELED,
                                TaskState.TASK_STATE_FAILED,
                                TaskState.TASK_STATE_REJECTED,
                            )

                            if (
                                not self._result_available
                                and (is_interrupted or is_terminal)
                                and res
                            ):
                                self._first_result = Task()
                                self._first_result.CopyFrom(res)
                                self._result_available = True

                            if is_terminal:
                                self._is_finished.set()

                            if is_interrupted:
                                self._interrupted = True

                            if (
                                self._push_sender
                                and self._task_id
                                and isinstance(event, PushNotificationEvent)
                            ):
                                await self._push_sender.send_notification(
                                    self._task_id, event
                                )

                        async with self._state_changed:
                            self._state_changed.notify_all()
                    finally:
                        self._event_queue.task_done()
            except QueueShutDown:
                pass
        except Exception as e:
            logger.exception('Consumer failed for task %s', self._task_id)
            self._exception = e
        finally:
            self._is_finished.set()
            async with self._state_changed:
                self._state_changed.notify_all()
            await self._maybe_cleanup()

    async def subscribe(self) -> AsyncGenerator[Event, None]:
        """Creates a queue tap and yields events as they are produced."""
        async with self._lock:
            if self._exception:
                raise self._exception
            if self._is_finished.is_set():
                return
            self._subscribers_count += 1

        tapped_queue = await self._event_queue.tap()
        try:
            while True:
                try:
                    if self._exception:
                        raise self._exception

                    # Wait for next event or task completion
                    try:
                        event = await asyncio.wait_for(
                            tapped_queue.dequeue_event(), timeout=0.1
                        )
                    except (asyncio.TimeoutError, TimeoutError):
                        if self._is_finished.is_set():
                            if self._exception:
                                raise self._exception from None
                            break
                        continue

                    try:
                        yield event
                    finally:
                        tapped_queue.task_done()
                except (QueueShutDown, asyncio.CancelledError):
                    if self._exception:
                        raise self._exception from None
                    break
        finally:
            await tapped_queue.close(immediate=True)
            async with self._lock:
                self._subscribers_count -= 1
            await self._maybe_cleanup()

    async def wait(self) -> Task | Message:
        """Waits until a result (terminal task or message) is available.

        Returns:
            The final `Task` or `Message` result.

        Raises:
            Exception: If the agent execution failed.
        """
        while not self._result_available and not self._is_finished.is_set():
            if self._exception:
                raise self._exception
            async with self._state_changed:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        self._state_changed.wait(), timeout=0.1
                    )

        if self._exception:
            raise self._exception

        if self._first_result:
            return self._first_result

        # Fallback to current task from manager if finished
        res = await self._task_manager.get_task()
        if res:
            # Update result_available so subsequent wait() calls are fast
            self._first_result = res
            self._result_available = True
            return res

        if self._message:
            return self._message

        if self._is_finished.is_set():
            raise RuntimeError('Task finished without result or message')

        raise RuntimeError('Wait exited without result')

    async def cancel(self, request: RequestContext) -> Task | Message:
        """Cancels the running active task."""
        async with self._lock:
            if not self._is_finished.is_set() and self._producer_task:
                # We do NOT await self._agent_executor.cancel here
                # because it might take a while and we want to await wait()
                self._producer_task.cancel()
                self._cancelled = True
                try:
                    await self._agent_executor.cancel(
                        request, self._event_queue
                    )
                except Exception as e:
                    logger.exception(
                        'Agent cancel failed for task %s', self._task_id
                    )
                    if not self._exception:
                        self._exception = e
                    async with self._state_changed:
                        self._state_changed.notify_all()
                    raise

        return await self.wait()

    async def _maybe_cleanup(self) -> None:
        """Triggers cleanup if task is finished and has no subscribers."""
        async with self._lock:
            if (
                self._is_finished.is_set()
                and self._subscribers_count == 0
                and self._on_cleanup
            ):
                self._on_cleanup(self)
