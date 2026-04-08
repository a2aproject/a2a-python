# ruff: noqa: TRY301, SLF001
from __future__ import annotations

import asyncio
import logging
import uuid

from typing import TYPE_CHECKING, cast

from a2a.server.agent_execution.context import RequestContext


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from a2a.server.agent_execution.agent_executor import AgentExecutor
    from a2a.server.context import ServerCallContext
    from a2a.server.tasks.push_notification_sender import (
        PushNotificationSender,
    )
    from a2a.server.tasks.task_manager import TaskManager

from a2a.server.events.event_queue_v2 import (
    AsyncQueue,
    Event,
    EventQueueSource,
    QueueShutDown,
    _create_async_queue,
)
from a2a.server.tasks import PushNotificationEvent
from a2a.types.a2a_pb2 import (
    Message,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import (
    InvalidParamsError,
    TaskNotFoundError,
)


logger = logging.getLogger(__name__)


TERMINAL_TASK_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_REJECTED,
}
INTERRUPTED_TASK_STATES = {
    TaskState.TASK_STATE_AUTH_REQUIRED,
    TaskState.TASK_STATE_INPUT_REQUIRED,
}


class _RequestCompleted:
    def __init__(self, request_id: uuid.UUID):
        self.request_id = request_id


class ActiveTask:
    """Manages the lifecycle and execution of an active A2A task.

    It coordinates between the agent's execution (the producer), the
    persistence and state management (the TaskManager), and the event
    distribution to subscribers (the consumer).

    Concurrency Guarantees:
    - This class is designed to be highly concurrent. It manages an internal
      producer-consumer model using `asyncio.Task`s.
    - `self._lock` (asyncio.Lock) ensures mutually exclusive access for critical
      lifecycle state changes, such as starting the task, subscribing, and
      determining if cleanup is safe to trigger.

      mutation to the observable result state (like `_exception`,
      or `_is_finished`) notifies waiting coroutines (like `wait()`).
    - `self._is_finished` (asyncio.Event) provides a thread-safe, non-blocking way
      for external observers and internal loops to check if the ActiveTask has
      permanently ceased execution and closed its queues.
    """

    def __init__(
        self,
        agent_executor: AgentExecutor,
        task_id: str,
        task_manager: TaskManager,
        push_sender: PushNotificationSender | None = None,
        on_cleanup: Callable[[ActiveTask], None] | None = None,
    ) -> None:
        """Initializes the ActiveTask.

        Args:
            agent_executor: The executor to run the agent logic (producer).
            task_id: The unique identifier of the task being managed.
            task_manager: The manager for task state and database persistence.
            push_sender: Optional sender for out-of-band push notifications.
            on_cleanup: Optional callback triggered when the task is fully finished
                        and the last subscriber has disconnected. Used to prune
                        the task from the ActiveTaskRegistry.
        """
        # --- Core Dependencies ---
        self._agent_executor = agent_executor
        self._task_id = task_id
        self._event_queue_agent = EventQueueSource()
        self._event_queue_subscribers = EventQueueSource(
            create_default_sink=False
        )
        self._task_manager = task_manager
        self._push_sender = push_sender
        self._on_cleanup = on_cleanup

        # --- Synchronization Primitives ---
        # `_lock` protects structural lifecycle changes: start(), subscribe() counting,
        # and _maybe_cleanup() race conditions.
        self._lock = asyncio.Lock()

        # `_request_lock` protects parallel request processing.
        self._request_lock = asyncio.Lock()

        # _task_created is set when initial version of task is stored in DB.
        self._task_created = asyncio.Event()

        # `_is_finished` is set EXACTLY ONCE when the consumer loop exits, signifying
        # the absolute end of the task's active lifecycle.
        self._is_finished = asyncio.Event()

        # --- Lifecycle State ---
        # The background task executing the agent logic.
        self._producer_task: asyncio.Task[None] | None = None
        # The background task reading from _event_queue and updating the DB.
        self._consumer_task: asyncio.Task[None] | None = None

        # Tracks how many active SSE/gRPC streams are currently tailing this task.
        # Protected by `_lock`.
        self._reference_count = 0

        # Holds any fatal exception that crashed the producer or consumer.
        # TODO: Synchronize exception handling (ideally mix it in the queue).
        self._exception: Exception | None = None

        # Queue for incoming requests
        self._request_queue: AsyncQueue[tuple[RequestContext, uuid.UUID]] = (
            _create_async_queue()
        )

    @property
    def task_id(self) -> str:
        """The ID of the task."""
        return self._task_id

    async def enqueue_request(
        self, request_context: RequestContext
    ) -> uuid.UUID:
        """Enqueues a request for the active task to process."""
        request_id = uuid.uuid4()
        await self._request_queue.put((request_context, request_id))
        return request_id

    async def start(
        self,
        call_context: ServerCallContext,
        create_task_if_missing: bool = False,
    ) -> None:
        """Starts the active task background processes.

        Concurrency Guarantee:
        Uses `self._lock` to ensure the producer and consumer tasks are strictly
        singleton instances for the lifetime of this ActiveTask.
        """
        logger.debug('ActiveTask[%s]: Starting', self._task_id)
        async with self._lock:
            if self._is_finished.is_set():
                raise InvalidParamsError(
                    f'Task {self._task_id} is already completed. Cannot start it again.'
                )

            if (
                self._producer_task is not None
                and self._consumer_task is not None
            ):
                logger.debug(
                    'ActiveTask[%s]: Already started, ignoring start request',
                    self._task_id,
                )
                return

            logger.debug(
                'ActiveTask[%s]: Executing setup (call_context: %s, create_task_if_missing: %s)',
                self._task_id,
                call_context,
                create_task_if_missing,
            )
            try:
                self._task_manager._call_context = call_context
                task = await self._task_manager.get_task()
                logger.debug('TASK (start): %s', task)

                if task:
                    if task.status.state in TERMINAL_TASK_STATES:
                        raise InvalidParamsError(
                            message=f'Task {task.id} is in terminal state: {task.status.state}'
                        )
                else:
                    if not create_task_if_missing:
                        raise TaskNotFoundError

                    # New task. Create and save it so it's not "missing" if queried immediately
                    # (especially important for return_immediately=True)
                    if self._task_manager.context_id is None:
                        raise ValueError('Context ID is required for new tasks')
                    task = self._task_manager._init_task_obj(
                        self._task_id,
                        self._task_manager.context_id,
                    )
                    await self._task_manager.save_task_event(task)
                    if self._push_sender:
                        await self._push_sender.send_notification(task.id, task)

            except Exception:
                logger.debug(
                    'ActiveTask[%s]: Setup failed, cleaning up',
                    self._task_id,
                )
                self._is_finished.set()
                if self._reference_count == 0 and self._on_cleanup:
                    self._on_cleanup(self)
                raise

            # Spawn the background tasks that drive the lifecycle.
            self._reference_count += 1
            self._producer_task = asyncio.create_task(
                self._run_producer(), name=f'producer:{self._task_id}'
            )
            self._consumer_task = asyncio.create_task(
                self._run_consumer(), name=f'consumer:{self._task_id}'
            )
            logger.debug(
                'ActiveTask[%s]: Background tasks created', self._task_id
            )

    async def _run_producer(self) -> None:
        """Executes the agent logic.

        This method encapsulates the external `AgentExecutor.execute` call. It ensures
        that regardless of how the agent finishes (success, unhandled exception, or
        cancellation), the underlying `_event_queue` is safely closed, which signals
        the consumer to wind down.

        Concurrency Guarantee:
        Runs as a detached asyncio.Task. Safe to cancel.
        """
        logger.debug('Producer[%s]: Started', self._task_id)
        try:
            active = True
            while active:
                (
                    request_context,
                    request_id,
                ) = await self._request_queue.get()
                await self._request_lock.acquire()
                # TODO: Should we create task manager every time?
                self._task_manager._call_context = request_context.call_context
                request_context.current_task = (
                    await self._task_manager.get_task()
                )

                message = request_context.message
                if message:
                    request_context.current_task = (
                        self._task_manager.update_with_message(
                            message,
                            cast('Task', request_context.current_task),
                        )
                    )
                    await self._task_manager.save_task_event(
                        request_context.current_task
                    )
                self._task_created.set()
                logger.debug(
                    'Producer[%s]: Executing agent task %s',
                    self._task_id,
                    request_context.current_task,
                )

                try:
                    await self._agent_executor.execute(
                        request_context, self._event_queue_agent
                    )
                    logger.debug(
                        'Producer[%s]: Execution finished successfully',
                        self._task_id,
                    )
                except QueueShutDown:
                    logger.debug(
                        'Producer[%s]: Request queue shut down', self._task_id
                    )
                    raise
                except asyncio.CancelledError:
                    logger.debug('Producer[%s]: Cancelled', self._task_id)
                    raise
                except Exception as e:
                    logger.exception(
                        'Producer[%s]: Execution failed',
                        self._task_id,
                    )
                    async with self._lock:
                        await self._mark_task_as_failed(e)
                    active = False
                finally:
                    logger.debug(
                        'Producer[%s]: Enqueuing request completed event',
                        self._task_id,
                    )
                    # TODO: Hide from external consumers
                    await self._event_queue_agent.enqueue_event(
                        cast('Event', _RequestCompleted(request_id))
                    )
                    self._request_queue.task_done()
        finally:
            self._request_queue.shutdown(immediate=True)
            await self._event_queue_agent.close(immediate=False)
            await self._event_queue_subscribers.close(immediate=False)
            logger.debug('Producer[%s]: Completed', self._task_id)

    async def _run_consumer(self) -> None:  # noqa: PLR0915, PLR0912
        """Consumes events from the agent and updates system state.

        This continuous loop dequeues events emitted by the producer, updates the
        database via `TaskManager`, and intercepts critical task states (e.g.,
        INPUT_REQUIRED, COMPLETED, FAILED) to cache the final result.

        Concurrency Guarantee:
        Runs as a detached asyncio.Task. The loop ends gracefully when the producer
        closes the queue (raising `QueueShutDown`). Upon termination, it formally sets
        `_is_finished`, unblocking all global subscribers and wait() calls.
        """
        logger.debug('Consumer[%s]: Started', self._task_id)
        try:
            try:
                try:
                    while True:
                        # Dequeue event. This raises QueueShutDown when finished.
                        logger.debug(
                            'Consumer[%s]: Waiting for event',
                            self._task_id,
                        )
                        event = await self._event_queue_agent.dequeue_event()
                        logger.debug(
                            'Consumer[%s]: Dequeued event %s',
                            self._task_id,
                            type(event).__name__,
                        )

                        try:
                            if isinstance(event, _RequestCompleted):
                                logger.debug(
                                    'Consumer[%s]: Request completed',
                                    self._task_id,
                                )
                                self._request_lock.release()
                            elif isinstance(event, Message):
                                logger.debug(
                                    'Consumer[%s]: Setting result to Message: %s',
                                    self._task_id,
                                    event,
                                )
                            else:
                                # Save structural events (like TaskStatusUpdate) to DB.
                                # TODO: Create task manager every time ?
                                self._task_manager.context_id = event.context_id
                                await self._task_manager.process(event)

                                # Check for AUTH_REQUIRED or INPUT_REQUIRED or TERMINAL states
                                new_task = await self._task_manager.get_task()
                                if new_task is None:
                                    raise RuntimeError(
                                        f'Task {self.task_id} not found'
                                    )
                                is_interrupted = (
                                    new_task.status.state
                                    in INTERRUPTED_TASK_STATES
                                )
                                is_terminal = (
                                    new_task.status.state
                                    in TERMINAL_TASK_STATES
                                )

                                # If we hit a breakpoint or terminal state, lock in the result.
                                if is_interrupted or is_terminal:
                                    logger.debug(
                                        'Consumer[%s]: Setting first result as Task (state=%s)',
                                        self._task_id,
                                        new_task.status.state,
                                    )

                                if is_terminal:
                                    logger.debug(
                                        'Consumer[%s]: Reached terminal state %s',
                                        self._task_id,
                                        new_task.status.state,
                                    )
                                    if not self._is_finished.is_set():
                                        async with self._lock:
                                            # TODO: what about _reference_count when task is failing?
                                            self._reference_count -= 1
                                    # _maybe_cleanup() is called in finally block.

                                    # Terminate the ActiveTask globally.
                                    self._is_finished.set()
                                    self._request_queue.shutdown(immediate=True)

                                if is_interrupted:
                                    logger.debug(
                                        'Consumer[%s]: Interrupted with state %s',
                                        self._task_id,
                                        new_task.status.state,
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
                        finally:
                            await self._event_queue_subscribers.enqueue_event(
                                event
                            )
                            self._event_queue_agent.task_done()
                except QueueShutDown:
                    logger.debug(
                        'Consumer[%s]: Event queue shut down', self._task_id
                    )
            except Exception as e:
                logger.exception('Consumer[%s]: Failed', self._task_id)
                async with self._lock:
                    await self._mark_task_as_failed(e)
            finally:
                # The consumer is dead. The ActiveTask is permanently finished.
                self._is_finished.set()
                self._request_queue.shutdown(immediate=True)

                logger.debug('Consumer[%s]: Finishing', self._task_id)
                await self._maybe_cleanup()
        finally:
            logger.debug('Consumer[%s]: Completed', self._task_id)

    async def subscribe(  # noqa: PLR0912, PLR0915
        self,
        *,
        request: RequestContext | None = None,
        include_initial_task: bool = False,
    ) -> AsyncGenerator[Event, None]:
        """Creates a queue tap and yields events as they are produced.

        Concurrency Guarantee:
        Uses `_lock` to safely increment and decrement `_reference_count`.
        Safely detaches its queue tap when the client disconnects or the task finishes,
        triggering `_maybe_cleanup()` to potentially garbage collect the ActiveTask.
        """
        logger.debug('Subscribe[%s]: New subscriber', self._task_id)

        async with self._lock:
            if self._exception:
                logger.debug(
                    'Subscribe[%s]: Failed, exception already set',
                    self._task_id,
                )
                raise self._exception
            if self._is_finished.is_set():
                raise InvalidParamsError(
                    f'Task {self._task_id} is already completed.'
                )
            self._reference_count += 1
            logger.debug(
                'Subscribe[%s]: Subscribers count: %d',
                self._task_id,
                self._reference_count,
            )

        tapped_queue = await self._event_queue_subscribers.tap()
        request_id = await self.enqueue_request(request) if request else None

        try:
            if include_initial_task:
                logger.debug(
                    'Subscribe[%s]: Including initial task',
                    self._task_id,
                )
                task = await self.get_task()
                yield task

            while True:
                try:
                    if self._exception:
                        raise self._exception

                    # Wait for next event or task completion
                    try:
                        event = await asyncio.wait_for(
                            tapped_queue.dequeue_event(), timeout=0.1
                        )
                        if self._exception:
                            raise self._exception from None
                        if isinstance(event, _RequestCompleted):
                            if (
                                request_id is not None
                                and event.request_id == request_id
                            ):
                                logger.debug(
                                    'Subscriber[%s]: Request completed',
                                    self._task_id,
                                )
                                return
                            continue
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
                self._reference_count -= 1
            # Evaluate if this was the last subscriber on a finished task.
            await self._maybe_cleanup()

    async def cancel(self, call_context: ServerCallContext) -> Task | Message:
        """Cancels the running active task.

        Concurrency Guarantee:
        Uses `_lock` to ensure we don't attempt to cancel a producer that is
        already winding down or hasn't started. It fires the cancellation signal
        and blocks until the consumer processes the cancellation events.
        """
        logger.debug('Cancel[%s]: Cancelling task', self._task_id)

        # TODO: Conflicts with call_context on the pending request.
        self._task_manager._call_context = call_context

        task = await self.get_task()
        request_context = RequestContext(
            call_context=call_context,
            task_id=self._task_id,
            context_id=task.context_id,
            task=task,
        )

        async with self._lock:
            if not self._is_finished.is_set() and self._producer_task:
                logger.debug(
                    'Cancel[%s]: Cancelling producer task', self._task_id
                )
                self._producer_task.cancel()
                try:
                    await self._agent_executor.cancel(
                        request_context, self._event_queue_agent
                    )
                except Exception as e:
                    logger.exception(
                        'Cancel[%s]: Agent cancel failed', self._task_id
                    )
                    await self._mark_task_as_failed(e)
                    raise
            else:
                logger.debug(
                    'Cancel[%s]: Task already finished [%s] or producer not started [%s], not cancelling',
                    self._task_id,
                    self._is_finished.is_set(),
                    self._producer_task,
                )

        await self._is_finished.wait()
        return await self.get_task()

    async def _maybe_cleanup(self) -> None:
        """Triggers cleanup if task is finished and has no subscribers.

        Concurrency Guarantee:
        Protected by `_lock` to prevent race conditions where a new subscriber
        attaches at the exact moment the task decides to garbage collect itself.
        """
        async with self._lock:
            logger.debug(
                'Cleanup[%s]: Subscribers count: %d is_finished: %s',
                self._task_id,
                self._reference_count,
                self._is_finished.is_set(),
            )

            if (
                self._is_finished.is_set()
                and self._reference_count == 0
                and self._on_cleanup
            ):
                logger.debug('Cleanup[%s]: Triggering cleanup', self._task_id)
                self._on_cleanup(self)

    async def _mark_task_as_failed(self, exception: Exception) -> None:
        if self._exception is None:
            self._exception = exception
        if self._task_created.is_set():
            task = await self._task_manager.get_task()
            if task is not None:
                await self._event_queue_agent.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=task.id,
                        context_id=task.context_id,
                        status=TaskStatus(
                            state=TaskState.TASK_STATE_FAILED,
                        ),
                    )
                )

    async def get_task(self) -> Task:
        """Get task from db."""
        # TODO: THERE IS ZERO CONCURRENCY SAFETY HERE (Except inital task creation).
        await self._task_created.wait()
        task = await self._task_manager.get_task()
        if not task:
            raise RuntimeError('Task should have been created')
        return task
