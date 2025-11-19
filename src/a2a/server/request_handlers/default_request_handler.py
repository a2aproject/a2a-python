import asyncio
import logging

from collections.abc import AsyncGenerator
from typing import cast

from a2a.server.agent_execution import (
    AgentExecutor,
    RequestContext,
    RequestContextBuilder,
    SimpleRequestContextBuilder,
)
from a2a.server.context import ServerCallContext
from a2a.server.events import (
    Event,
    EventConsumer,
    EventQueue,
    InMemoryQueueManager,
    QueueManager,
)
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.tasks import (
    PushNotificationConfigStore,
    PushNotificationSender,
    ResultAggregator,
    TaskManager,
    TaskStore,
)
from a2a.types import (
    DeleteTaskPushNotificationConfigParams,
    GetTaskPushNotificationConfigParams,
    InternalError,
    InvalidParamsError,
    ListTaskPushNotificationConfigParams,
    Message,
    MessageSendParams,
    Task,
    TaskIdParams,
    TaskNotCancelableError,
    TaskNotFoundError,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskState,
    UnsupportedOperationError,
)
from a2a.utils.errors import ServerError
from a2a.utils.task import apply_history_length
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)

TERMINAL_TASK_STATES = {
    TaskState.completed,
    TaskState.canceled,
    TaskState.failed,
    TaskState.rejected,
}


@trace_class(kind=SpanKind.SERVER)
class DefaultRequestHandler(RequestHandler):
    """Default request handler for all incoming requests.

    This handler provides default implementations for all A2A JSON-RPC methods,
    coordinating between the `AgentExecutor`, `TaskStore`, `QueueManager`,
    and optional `PushNotifier`.
    """

    _running_agents: dict[str, asyncio.Task]
    _background_tasks: set[asyncio.Task]

    def __init__(  # noqa: PLR0913
        self,
        agent_executor: AgentExecutor,
        task_store: TaskStore,
        queue_manager: QueueManager | None = None,
        push_config_store: PushNotificationConfigStore | None = None,
        push_sender: PushNotificationSender | None = None,
        request_context_builder: RequestContextBuilder | None = None,
    ) -> None:
        """Initializes the DefaultRequestHandler.

        Args:
            agent_executor: The `AgentExecutor` instance to run agent logic.
            task_store: The `TaskStore` instance to manage task persistence.
            queue_manager: The `QueueManager` instance to manage event queues. Defaults to `InMemoryQueueManager`.
            push_config_store: The `PushNotificationConfigStore` instance for managing push notification configurations. Defaults to None.
            push_sender: The `PushNotificationSender` instance for sending push notifications. Defaults to None.
            request_context_builder: The `RequestContextBuilder` instance used
              to build request contexts. Defaults to `SimpleRequestContextBuilder`.
        """
        self.agent_executor = agent_executor
        self.task_store = task_store
        self._queue_manager = queue_manager or InMemoryQueueManager()
        self._push_config_store = push_config_store
        self._push_sender = push_sender
        self._request_context_builder = (
            request_context_builder
            or SimpleRequestContextBuilder(
                should_populate_referred_tasks=False, task_store=self.task_store
            )
        )
        # TODO: Likely want an interface for managing this, like AgentExecutionManager.
        self._running_agents = {}
        self._running_agents_lock = asyncio.Lock()
        # Tracks background tasks (e.g., deferred cleanups) to avoid orphaning
        # asyncio tasks and to surface unexpected exceptions.
        self._background_tasks = set()

    async def on_get_task(
        self,
        params: TaskQueryParams,
        context: ServerCallContext | None = None,
    ) -> Task | None:
        """Default handler for 'tasks/get'."""
        task: Task | None = await self.task_store.get(params.id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())

        # Apply historyLength parameter if specified
        return apply_history_length(task, params.history_length)

    async def on_cancel_task(
        self, params: TaskIdParams, context: ServerCallContext | None = None
    ) -> Task | None:
        """Default handler for 'tasks/cancel'.

        Attempts to cancel the task managed by the `AgentExecutor`.
        """
        task: Task | None = await self.task_store.get(params.id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())

        # Check if task is in a non-cancelable state (completed, canceled, failed, rejected)
        if task.status.state in TERMINAL_TASK_STATES:
            raise ServerError(
                error=TaskNotCancelableError(
                    message=f'Task cannot be canceled - current state: {task.status.state}'
                )
            )

        task_manager = TaskManager(
            task_id=task.id,
            context_id=task.context_id,
            task_store=self.task_store,
            initial_message=None,
            context=context,
        )
        result_aggregator = ResultAggregator(task_manager)

        queue = await self._queue_manager.tap(task.id)
        if not queue:
            queue = EventQueue()

        await self.agent_executor.cancel(
            RequestContext(
                None,
                task_id=task.id,
                context_id=task.context_id,
                task=task,
            ),
            queue,
        )
        # Cancel the ongoing task, if one exists.
        if producer_task := self._running_agents.get(task.id):
            producer_task.cancel()

        consumer = EventConsumer(queue)
        result = await result_aggregator.consume_all(consumer)
        if not isinstance(result, Task):
            raise ServerError(
                error=InternalError(
                    message='Agent did not return valid response for cancel'
                )
            )

        if result.status.state != TaskState.canceled:
            raise ServerError(
                error=TaskNotCancelableError(
                    message=f'Task cannot be canceled - current state: {result.status.state}'
                )
            )

        return result

    async def _run_event_stream(
        self, request: RequestContext, queue: EventQueue
    ) -> None:
        """Runs the agent's `execute` method and closes the queue afterwards.

        Args:
            request: The request context for the agent.
            queue: The event queue for the agent to publish to.
        """
        await self.agent_executor.execute(request, queue)
        await queue.close()

    async def _setup_message_execution(
        self,
        params: MessageSendParams,
        context: ServerCallContext | None = None,
    ) -> tuple[TaskManager, str, EventQueue, ResultAggregator, asyncio.Task]:
        """Common setup logic for both streaming and non-streaming message handling.

        Returns:
            A tuple of (task_manager, task_id, queue, result_aggregator, producer_task)
        """
        # Create task manager and validate existing task
        task_manager = TaskManager(
            task_id=params.message.task_id,
            context_id=params.message.context_id,
            task_store=self.task_store,
            initial_message=params.message,
            context=context,
        )
        task: Task | None = await task_manager.get_task()

        if task:
            if task.status.state in TERMINAL_TASK_STATES:
                raise ServerError(
                    error=InvalidParamsError(
                        message=f'Task {task.id} is in terminal state: {task.status.state.value}'
                    )
                )

            task = task_manager.update_with_message(params.message, task)
        elif params.message.task_id:
            raise ServerError(
                error=TaskNotFoundError(
                    message=f'Task {params.message.task_id} was specified but does not exist'
                )
            )

        # Build request context
        request_context = await self._request_context_builder.build(
            params=params,
            task_id=task.id if task else None,
            context_id=params.message.context_id,
            task=task,
            context=context,
        )

        task_id = cast('str', request_context.task_id)
        # Always assign a task ID. We may not actually upgrade to a task, but
        # dictating the task ID at this layer is useful for tracking running
        # agents.

        if (
            self._push_config_store
            and params.configuration
            and params.configuration.push_notification_config
        ):
            await self._push_config_store.set_info(
                task_id, params.configuration.push_notification_config
            )

        queue = await self._queue_manager.create_or_tap(task_id)
        result_aggregator = ResultAggregator(task_manager)
        # TODO: to manage the non-blocking flows.
        producer_task = asyncio.create_task(
            self._run_event_stream(request_context, queue)
        )
        await self._register_producer(task_id, producer_task)

        return task_manager, task_id, queue, result_aggregator, producer_task

    def _validate_task_id_match(self, task_id: str, event_task_id: str) -> None:
        """Validates that agent-generated task ID matches the expected task ID."""
        if task_id != event_task_id:
            logger.error(
                'Agent generated task_id=%s does not match the RequestContext task_id=%s.',
                event_task_id,
                task_id,
            )
            raise ServerError(
                InternalError(message='Task ID mismatch in agent response')
            )

    async def _send_push_notification_if_needed(
        self, task_id: str, result_aggregator: ResultAggregator
    ) -> None:
        """Sends push notification if configured and task is available."""
        if self._push_sender and task_id:
            latest_task = await result_aggregator.current_result
            if isinstance(latest_task, Task):
                await self._push_sender.send_notification(latest_task)

    async def on_message_send(
        self,
        params: MessageSendParams,
        context: ServerCallContext | None = None,
    ) -> Message | Task:
        """Default handler for 'message/send' interface (non-streaming).

        Starts the agent execution for the message and waits for the final
        result (Task or Message).
        """
        (
            _task_manager,
            task_id,
            queue,
            result_aggregator,
            producer_task,
        ) = await self._setup_message_execution(params, context)

        consumer = EventConsumer(queue)
        producer_task.add_done_callback(consumer.agent_task_callback)

        blocking = True  # Default to blocking behavior
        if params.configuration and params.configuration.blocking is False:
            blocking = False

        interrupted_or_non_blocking = False
        try:
            # Create async callback for push notifications
            async def push_notification_callback() -> None:
                await self._send_push_notification_if_needed(
                    task_id, result_aggregator
                )

            (
                result,
                interrupted_or_non_blocking,
            ) = await result_aggregator.consume_and_break_on_interrupt(
                consumer,
                blocking=blocking,
                event_callback=push_notification_callback,
            )

        except Exception:
            logger.exception('Agent execution failed')
            raise
        finally:
            if interrupted_or_non_blocking:
                cleanup_task = asyncio.create_task(
                    self._cleanup_producer(producer_task, task_id)
                )
                cleanup_task.set_name(f'cleanup_producer:{task_id}')
                self._track_background_task(cleanup_task)
            else:
                await self._cleanup_producer(producer_task, task_id)

        if not result:
            raise ServerError(error=InternalError())

        if isinstance(result, Task):
            self._validate_task_id_match(task_id, result.id)
            if params.configuration:
                result = apply_history_length(
                    result, params.configuration.history_length
                )

        await self._send_push_notification_if_needed(task_id, result_aggregator)

        return result

    async def on_message_send_stream(
        self,
        params: MessageSendParams,
        context: ServerCallContext | None = None,
    ) -> AsyncGenerator[Event]:
        """Default handler for 'message/stream' (streaming).

        Starts the agent execution and yields events as they are produced
        by the agent.
        """
        (
            _task_manager,
            task_id,
            queue,
            result_aggregator,
            producer_task,
        ) = await self._setup_message_execution(params, context)
        consumer = EventConsumer(queue)
        producer_task.add_done_callback(consumer.agent_task_callback)

        try:
            async for event in result_aggregator.consume_and_emit(consumer):
                if isinstance(event, Task):
                    self._validate_task_id_match(task_id, event.id)

                await self._send_push_notification_if_needed(
                    task_id, result_aggregator
                )
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            # Client disconnected: continue consuming and persisting events in the background
            bg_task = asyncio.create_task(
                result_aggregator.consume_all(consumer)
            )
            bg_task.set_name(f'background_consume:{task_id}')
            self._track_background_task(bg_task)
            raise
        finally:
            cleanup_task = asyncio.create_task(
                self._cleanup_producer(producer_task, task_id)
            )
            cleanup_task.set_name(f'cleanup_producer:{task_id}')
            self._track_background_task(cleanup_task)

    async def _register_producer(
        self, task_id: str, producer_task: asyncio.Task
    ) -> None:
        """Registers the agent execution task with the handler."""
        async with self._running_agents_lock:
            self._running_agents[task_id] = producer_task

    def _track_background_task(self, task: asyncio.Task) -> None:
        """Tracks a background task and logs exceptions on completion.

        This avoids unreferenced tasks (and associated lint warnings) while
        ensuring any exceptions are surfaced in logs.
        """
        self._background_tasks.add(task)

        def _on_done(completed: asyncio.Task) -> None:
            try:
                # Retrieve result to raise exceptions, if any
                completed.result()
            except asyncio.CancelledError:
                name = completed.get_name()
                logger.debug('Background task %s cancelled', name)
            except Exception:
                name = completed.get_name()
                logger.exception('Background task %s failed', name)
            finally:
                self._background_tasks.discard(completed)

        task.add_done_callback(_on_done)

    async def _cleanup_producer(
        self,
        producer_task: asyncio.Task,
        task_id: str,
    ) -> None:
        """Cleans up the agent execution task and queue manager entry."""
        await producer_task
        await self._queue_manager.close(task_id)
        async with self._running_agents_lock:
            self._running_agents.pop(task_id, None)

    async def on_set_task_push_notification_config(
        self,
        params: TaskPushNotificationConfig,
        context: ServerCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Default handler for 'tasks/pushNotificationConfig/set'.

        Requires a `PushNotifier` to be configured.
        """
        if not self._push_config_store:
            raise ServerError(error=UnsupportedOperationError())

        task: Task | None = await self.task_store.get(params.task_id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())

        await self._push_config_store.set_info(
            params.task_id,
            params.push_notification_config,
        )

        return params

    async def on_get_task_push_notification_config(
        self,
        params: TaskIdParams | GetTaskPushNotificationConfigParams,
        context: ServerCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Default handler for 'tasks/pushNotificationConfig/get'.

        Requires a `PushConfigStore` to be configured.
        """
        if not self._push_config_store:
            raise ServerError(error=UnsupportedOperationError())

        task: Task | None = await self.task_store.get(params.id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())

        push_notification_config = await self._push_config_store.get_info(
            params.id
        )
        if not push_notification_config or not push_notification_config[0]:
            raise ServerError(
                error=InternalError(
                    message='Push notification config not found'
                )
            )

        return TaskPushNotificationConfig(
            task_id=params.id,
            push_notification_config=push_notification_config[0],
        )

    async def on_resubscribe_to_task(
        self,
        params: TaskIdParams,
        context: ServerCallContext | None = None,
    ) -> AsyncGenerator[Event]:
        """Default handler for 'tasks/resubscribe'.

        Allows a client to re-attach to a running streaming task's event stream.
        Requires the task and its queue to still be active.
        """
        task: Task | None = await self.task_store.get(params.id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())

        if task.status.state in TERMINAL_TASK_STATES:
            raise ServerError(
                error=InvalidParamsError(
                    message=f'Task {task.id} is in terminal state: {task.status.state.value}'
                )
            )

        task_manager = TaskManager(
            task_id=task.id,
            context_id=task.context_id,
            task_store=self.task_store,
            initial_message=None,
            context=context,
        )

        result_aggregator = ResultAggregator(task_manager)

        queue = await self._queue_manager.tap(task.id)
        if not queue:
            raise ServerError(error=TaskNotFoundError())

        consumer = EventConsumer(queue)
        async for event in result_aggregator.consume_and_emit(consumer):
            yield event

    async def on_list_task_push_notification_config(
        self,
        params: ListTaskPushNotificationConfigParams,
        context: ServerCallContext | None = None,
    ) -> list[TaskPushNotificationConfig]:
        """Default handler for 'tasks/pushNotificationConfig/list'.

        Requires a `PushConfigStore` to be configured.
        """
        if not self._push_config_store:
            raise ServerError(error=UnsupportedOperationError())

        task: Task | None = await self.task_store.get(params.id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())

        push_notification_config_list = await self._push_config_store.get_info(
            params.id
        )

        return [
            TaskPushNotificationConfig(
                task_id=params.id, push_notification_config=config
            )
            for config in push_notification_config_list
        ]

    async def on_delete_task_push_notification_config(
        self,
        params: DeleteTaskPushNotificationConfigParams,
        context: ServerCallContext | None = None,
    ) -> None:
        """Default handler for 'tasks/pushNotificationConfig/delete'.

        Requires a `PushConfigStore` to be configured.
        """
        if not self._push_config_store:
            raise ServerError(error=UnsupportedOperationError())

        task: Task | None = await self.task_store.get(params.id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())

        await self._push_config_store.delete_info(
            params.id, params.push_notification_config_id
        )
