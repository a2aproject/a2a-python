from __future__ import annotations

import asyncio
import contextlib
import logging

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

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

    @property
    def task_id(self) -> str:
        """The ID of the task."""
        return self._task_id

    async def start(
        self,
        request: RequestContext,
        setup_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Starts the active task background processes.

        Args:
            request: The request context to execute.
            setup_callback: Optional async callback executed before starting the producer, while the lock is held.

        Raises:
            TaskAlreadyStartedError: If the task is already running.
        """
        logger.debug('ActiveTask[%s]: Starting', self._task_id)
        async with self._lock:
            if self._producer_task is not None:
                logger.debug(
                    'ActiveTask[%s]: Already started, ignoring start request',
                    self._task_id,
                )
                raise TaskAlreadyStartedError(
                    f'Task {self._task_id} already started'
                )

            if setup_callback:
                logger.debug('ActiveTask[%s]: Executing setup callback', self._task_id)
                try:
                    await setup_callback()
                except Exception:
                    logger.debug(
                        'ActiveTask[%s]: Setup callback failed, cleaning up',
                        self._task_id,
                    )
                    self._is_finished.set()
                    if self._subscribers_count == 0 and self._on_cleanup:
                        self._on_cleanup(self)
                    raise

            self._producer_task = asyncio.create_task(
                self._run_producer(request), name=f'producer:{self._task_id}'
            )
            self._consumer_task = asyncio.create_task(
                self._run_consumer(), name=f'consumer:{self._task_id}'
            )
            logger.debug('ActiveTask[%s]: Background tasks created', self._task_id)

    async def _run_producer(self, request: RequestContext) -> None:
        logger.debug('Producer[%s]: Started', self._task_id)
        try:
            close_immediately = False
            try:
                await self._agent_executor.execute(request, self._event_queue)
                logger.debug(
                    'Producer[%s]: Execution finished successfully',
                    self._task_id,
                )
            except asyncio.CancelledError:
                logger.debug('Producer[%s]: Cancelled', self._task_id)
                # close_immediately = True
                raise
            except Exception as e:
                logger.exception('Producer[%s]: Failed', self._task_id)
                self._exception = e
            finally:
                async with self._state_changed:
                    self._state_changed.notify_all()
                # Important: Non-immediate close to allow consumer to drain
                logger.debug(
                    'Producer[%s]: Closing event queue immediately=%s',
                    self._task_id,
                    close_immediately,
                )
                await self._event_queue.close(immediate=close_immediately)
        finally:
            logger.debug('Producer[%s]: Completed', self._task_id)

    async def _run_consumer(self) -> None:
        logger.debug('Consumer[%s]: Started', self._task_id)
        try:
            try:
                try:
                    while True:
                        # Dequeue event. This raises QueueShutDown when finished.
                        event = await self._event_queue.dequeue_event()
                        logger.debug(
                            'Consumer[%s]: Dequeued event %s',
                            self._task_id,
                            type(event).__name__,
                        )

                        try:
                            if isinstance(event, Message):
                                if not self._result_available:
                                    logger.debug(
                                        'Consumer[%s]: Setting first result as Message',
                                        self._task_id,
                                    )
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
                                    logger.debug(
                                        'Consumer[%s]: Setting first result as Task (state=%s)',
                                        self._task_id,
                                        res.status.state,
                                    )
                                    self._first_result = Task()
                                    self._first_result.CopyFrom(res)
                                    self._result_available = True

                                if is_terminal:
                                    logger.debug(
                                        'Consumer[%s]: Reached terminal state %s',
                                        self._task_id,
                                        res.status.state if res else 'unknown',
                                    )
                                    self._is_finished.set()

                                if is_interrupted:
                                    logger.debug(
                                        'Consumer[%s]: Interrupted with state %s',
                                        self._task_id,
                                        res.status.state if res else 'unknown',
                                    )

                                if (
                                    self._push_sender
                                    and self._task_id
                                    and isinstance(event, PushNotificationEvent)
                                ):
                                    logger.debug(
                                        'Consumer[%s]: Sending push notification',
                                        self._task_id,
                                    )
                                    await self._push_sender.send_notification(
                                        self._task_id, event
                                    )

                            async with self._state_changed:
                                self._state_changed.notify_all()
                        finally:
                            self._event_queue.task_done()
                except QueueShutDown:
                    logger.debug(
                        'Consumer[%s]: Event queue shut down', self._task_id
                    )
            except Exception as e:
                logger.exception('Consumer[%s]: Failed', self._task_id)
                self._exception = e
            finally:
                self._is_finished.set()
                async with self._state_changed:
                    self._state_changed.notify_all()
                logger.debug('Consumer[%s]: Finishing', self._task_id)
                await self._maybe_cleanup()
        finally:
            logger.debug('Consumer[%s]: Completed', self._task_id)

    async def subscribe(self) -> AsyncGenerator[Event, None]:
        """Creates a queue tap and yields events as they are produced."""
        logger.debug('Subscribe[%s]: New subscriber', self._task_id)
        async with self._lock:
            if self._exception:
                logger.debug(
                    'Subscribe[%s]: Failed, exception already set',
                    self._task_id,
                )
                raise self._exception
            if self._is_finished.is_set():
                logger.debug(
                    'Subscribe[%s]: Finished, already finished',
                    self._task_id,
                )
                return
            self._subscribers_count += 1
            logger.debug(
                'Subscribe[%s]: Subscribers count: %d',
                self._task_id,
                self._subscribers_count,
            )

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
            logger.debug('Subscribe[%s]: Unsubscribing', self._task_id)
            await tapped_queue.close(immediate=True)
            async with self._lock:
                self._subscribers_count -= 1
                logger.debug(
                    'Subscribe[%s]: Subscribers count: %d',
                    self._task_id,
                    self._subscribers_count,
                )
            await self._maybe_cleanup()

    async def wait(self) -> Task | Message:
        """Waits until a result (terminal task or message) is available.

        Returns:
            The final `Task` or `Message` result.

        Raises:
            Exception: If the agent execution failed.
        """
        logger.debug('Wait[%s]: Waiting for result', self._task_id)
        while not self._result_available and not self._is_finished.is_set():
            if self._exception:
                logger.debug(
                    'Wait[%s]: Failed, exception set', self._task_id
                )
                raise self._exception
            async with self._state_changed:
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(
                        self._state_changed.wait(), timeout=0.1
                    )

        if self._exception:
            logger.debug('Wait[%s]: Failed, exception set', self._task_id)
            raise self._exception

        if self._first_result:
            logger.debug('Wait[%s]: Returning first result', self._task_id)
            return self._first_result

        # Fallback to current task from manager if finished
        logger.debug(
            'Wait[%s]: Falling back to TaskManager', self._task_id
        )
        res = await self._task_manager.get_task()
        if res:
            logger.debug('Wait[%s]: Returning Task from manager', self._task_id)
            # Update result_available so subsequent wait() calls are fast
            self._first_result = res
            self._result_available = True
            return res

        if self._message:
            logger.debug('Wait[%s]: Returning Message', self._task_id)
            return self._message

        if self._is_finished.is_set():
            logger.debug(
                'Wait[%s]: Task finished without result or message', self._task_id
            )
            raise RuntimeError('Task finished without result or message')

        logger.debug('Wait[%s]: Exited without result', self._task_id)
        raise RuntimeError('Wait exited without result')

    async def cancel(self, request: RequestContext) -> Task | Message:
        """Cancels the running active task."""
        logger.debug('Cancel[%s]: Cancelling task', self._task_id)
        async with self._lock:
            # if self._producer_task and not self._producer_task.done():
            if not self._is_finished.is_set() and self._producer_task:
                logger.debug('Cancel[%s]: Cancelling producer task', self._task_id)
                # We do NOT await self._agent_executor.cancel here
                # because it might take a while and we want to await wait()
                self._producer_task.cancel()
                try:
                    await self._agent_executor.cancel(
                        request, self._event_queue
                    )
                except Exception as e:
                    logger.exception(
                        'Cancel[%s]: Agent cancel failed', self._task_id
                    )
                    if not self._exception:
                        self._exception = e
                    async with self._state_changed:
                        self._state_changed.notify_all()
                    raise
            else:
                logger.debug(
                    'Cancel[%s]: Task already finished or producer not started, not cancelling',
                    self._task_id,
                )

        return await self.wait()

    async def _maybe_cleanup(self) -> None:
        """Triggers cleanup if task is finished and has no subscribers."""
        async with self._lock:
            if (
                self._is_finished.is_set()
                and self._subscribers_count == 0
                and self._on_cleanup
            ):
                logger.debug('Cleanup[%s]: Triggering cleanup', self._task_id)
                self._on_cleanup(self)
