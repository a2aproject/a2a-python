import asyncio
import logging

from collections.abc import AsyncGenerator, Awaitable, Callable
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
    EventQueueLegacy,
    InMemoryQueueManager,
    QueueManager,
)
from a2a.server.request_handlers.request_handler import (
    RequestHandler,
    validate,
    validate_request_params,
)
from a2a.server.tasks import (
    PushNotificationConfigStore,
    PushNotificationEvent,
    PushNotificationSender,
    ResultAggregator,
    TaskManager,
    TaskStore,
)
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
)
from a2a.utils.errors import (
    ExtendedAgentCardNotConfiguredError,
    InternalError,
    InvalidParamsError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from a2a.utils.task import (
    apply_history_length,
    validate_history_length,
    validate_page_size,
)
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)

TERMINAL_TASK_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_REJECTED,
}


@trace_class(kind=SpanKind.SERVER)
class LegacyRequestHandler(RequestHandler):
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
        agent_card: AgentCard,
        queue_manager: QueueManager | None = None,
        push_config_store: PushNotificationConfigStore | None = None,
        push_sender: PushNotificationSender | None = None,
        request_context_builder: RequestContextBuilder | None = None,
        extended_agent_card: AgentCard | None = None,
        extended_card_modifier: Callable[
            [AgentCard, ServerCallContext], Awaitable[AgentCard]
        ]
        | None = None,
    ) -> None:
        """Initializes the DefaultRequestHandler.

        Args:
            agent_executor: The `AgentExecutor` instance to run agent logic.
            task_store: The `TaskStore` instance to manage task persistence.
            agent_card: The `AgentCard` describing the agent's capabilities.
            queue_manager: The `QueueManager` instance to manage event queues. Defaults to `InMemoryQueueManager`.
            push_config_store: The `PushNotificationConfigStore` instance for managing push notification configurations. Defaults to None.
            push_sender: The `PushNotificationSender` instance for sending push notifications. Defaults to None.
            request_context_builder: The `RequestContextBuilder` instance used
              to build request contexts. Defaults to `SimpleRequestContextBuilder`.
            extended_agent_card: An optional, distinct `AgentCard` to be served at the extended card endpoint.
            extended_card_modifier: An optional callback to dynamically modify the extended `AgentCard` before it is served.
        """
        self.agent_executor = agent_executor
        self.task_store = task_store
        self._agent_card = agent_card
        self._queue_manager = queue_manager or InMemoryQueueManager()
        self._push_config_store = push_config_store
        self._push_sender = push_sender
        self.extended_agent_card = extended_agent_card
        self.extended_card_modifier = extended_card_modifier
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

    @validate_request_params
    async def on_get_task(
        self,
        params: GetTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        """Default handler for 'tasks/get'."""
        validate_history_length(params)

        task_id = params.id
        task: Task | None = await self.task_store.get(task_id, context)
        if not task:
            raise TaskNotFoundError

        return apply_history_length(task, params)

    @validate_request_params
    async def on_list_tasks(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        """Default handler for 'tasks/list'."""
        validate_history_length(params)
        if params.HasField('page_size'):
            validate_page_size(params.page_size)

        page = await self.task_store.list(params, context)
        for task in page.tasks:
            if not params.include_artifacts:
                task.ClearField('artifacts')

            updated_task = apply_history_length(task, params)
            if updated_task is not task:
                task.CopyFrom(updated_task)

        return page

    @validate_request_params
    async def on_cancel_task(
        self,
        params: CancelTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        """Default handler for 'tasks/cancel'.

        Attempts to cancel the task managed by the `AgentExecutor`.
        """
        task_id = params.id
        task: Task | None = await self.task_store.get(task_id, context)
        if not task:
            raise TaskNotFoundError

        # Check if task is in a non-cancelable state (completed, canceled, failed, rejected)
        if task.status.state in TERMINAL_TASK_STATES:
            raise TaskNotCancelableError(
                message=f'Task cannot be canceled - current state: {task.status.state}'
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
            queue = EventQueueLegacy()

        await self.agent_executor.cancel(
            RequestContext(
                call_context=context,
                request=None,
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
            raise InternalError(
                message='Agent did not return valid response for cancel'
            )

        if result.status.state != TaskState.TASK_STATE_CANCELED:
            raise TaskNotCancelableError(
                message=f'Task cannot be canceled - current state: {result.status.state}'
            )

        return result

    async def _run_event_stream(
        self, request: RequestContext, queue: EventQueueLegacy
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
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> tuple[
        TaskManager, str, EventQueueLegacy, ResultAggregator, asyncio.Task
    ]:
        """Common setup logic for both streaming and non-streaming message handling.

        Returns:
            A tuple of (task_manager, task_id, queue, result_aggregator, producer_task)
        """
        # Create task manager and validate existing task
        # Proto empty strings should be treated as None
        task_id = params.message.task_id or None
        context_id = params.message.context_id or None
        task_manager = TaskManager(
            task_id=task_id,
            context_id=context_id,
            task_store=self.task_store,
            initial_message=params.message,
            context=context,
        )
        task: Task | None = await task_manager.get_task()

        if task:
            if task.status.state in TERMINAL_TASK_STATES:
                raise InvalidParamsError(
                    message=f'Task {task.id} is in terminal state: {task.status.state}'
                )

            task = task_manager.update_with_message(params.message, task)
        elif params.message.task_id:
            raise TaskNotFoundError(
                message=f'Task {params.message.task_id} was specified but does not exist'
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
            and params.configuration.task_push_notification_config
        ):
            await self._push_config_store.set_info(
                task_id,
                params.configuration.task_push_notification_config,
                context,
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
            raise InternalError(message='Task ID mismatch in agent response')

    async def _send_push_notification_if_needed(
        self, task_id: str, event: Event
    ) -> None:
        """Sends push notification if configured."""
        if (
            self._push_sender
            and task_id
            and isinstance(event, PushNotificationEvent)
        ):
            await self._push_sender.send_notification(task_id, event)

    @validate_request_params
    async def on_message_send(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> Message | Task:
        """Default handler for 'message/send' interface (non-streaming).

        Starts the agent execution for the message and waits for the final
        result (Task or Message).
        """
        validate_history_length(params.configuration)

        (
            _task_manager,
            task_id,
            queue,
            result_aggregator,
            producer_task,
        ) = await self._setup_message_execution(params, context)

        consumer = EventConsumer(queue)
        producer_task.add_done_callback(consumer.agent_task_callback)

        blocking = not params.configuration.return_immediately

        interrupted_or_non_blocking = False
        try:
            # Create async callback for push notifications
            async def push_notification_callback(event: Event) -> None:
                await self._send_push_notification_if_needed(task_id, event)

            (
                result,
                interrupted_or_non_blocking,
                bg_consume_task,
            ) = await result_aggregator.consume_and_break_on_interrupt(
                consumer,
                blocking=blocking,
                event_callback=push_notification_callback,
            )

            if bg_consume_task is not None:
                bg_consume_task.set_name(f'continue_consuming:{task_id}')
                self._track_background_task(bg_consume_task)

        except Exception:
            logger.exception('Agent execution failed')
            producer_task.cancel()
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
            raise InternalError

        if isinstance(result, Task):
            self._validate_task_id_match(task_id, result.id)
            if params.configuration:
                result = apply_history_length(result, params.configuration)

        return result

    @validate_request_params
    @validate(
        lambda self: self._agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def on_message_send_stream(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
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

                await self._send_push_notification_if_needed(task_id, event)
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
        try:
            await producer_task
        except asyncio.CancelledError:
            logger.debug(
                'Producer task %s was cancelled during cleanup', task_id
            )
        await self._queue_manager.close(task_id)
        async with self._running_agents_lock:
            self._running_agents.pop(task_id, None)

    @validate_request_params
    @validate(
        lambda self: self._agent_card.capabilities.push_notifications,
        error_message='Push notifications are not supported by the agent',
        error_type=PushNotificationNotSupportedError,
    )
    async def on_create_task_push_notification_config(
        self,
        params: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        """Default handler for 'tasks/pushNotificationConfig/create'.

        Requires a `PushNotifier` to be configured.
        """
        if not self._push_config_store:
            raise PushNotificationNotSupportedError

        task_id = params.task_id
        task: Task | None = await self.task_store.get(task_id, context)
        if not task:
            raise TaskNotFoundError

        await self._push_config_store.set_info(
            task_id,
            params,
            context,
        )

        return params

    @validate_request_params
    @validate(
        lambda self: self._agent_card.capabilities.push_notifications,
        error_message='Push notifications are not supported by the agent',
        error_type=PushNotificationNotSupportedError,
    )
    async def on_get_task_push_notification_config(
        self,
        params: GetTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        """Default handler for 'tasks/pushNotificationConfig/get'.

        Requires a `PushConfigStore` to be configured.
        """
        if not self._push_config_store:
            raise PushNotificationNotSupportedError

        task_id = params.task_id
        config_id = params.id
        task: Task | None = await self.task_store.get(task_id, context)
        if not task:
            raise TaskNotFoundError

        push_notification_configs: list[TaskPushNotificationConfig] = (
            await self._push_config_store.get_info(task_id, context) or []
        )

        for config in push_notification_configs:
            if config.id == config_id:
                return config

        raise TaskNotFoundError

    @validate_request_params
    @validate(
        lambda self: self._agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def on_subscribe_to_task(
        self,
        params: SubscribeToTaskRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event, None]:
        """Default handler for 'SubscribeToTask'.

        Allows a client to re-attach to a running streaming task's event stream.
        Requires the task and its queue to still be active.
        """
        task_id = params.id
        task: Task | None = await self.task_store.get(task_id, context)
        if not task:
            raise TaskNotFoundError

        if task.status.state in TERMINAL_TASK_STATES:
            raise UnsupportedOperationError(
                message=f'Task {task.id} is in terminal state: {task.status.state}'
            )

        # The operation MUST return a Task object as the first event in the stream
        # https://a2a-protocol.org/latest/specification/#316-subscribe-to-task
        yield task

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
            raise TaskNotFoundError

        consumer = EventConsumer(queue)
        async for event in result_aggregator.consume_and_emit(consumer):
            yield event

    @validate_request_params
    @validate(
        lambda self: self._agent_card.capabilities.push_notifications,
        error_message='Push notifications are not supported by the agent',
        error_type=PushNotificationNotSupportedError,
    )
    async def on_list_task_push_notification_configs(
        self,
        params: ListTaskPushNotificationConfigsRequest,
        context: ServerCallContext,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Default handler for 'ListTaskPushNotificationConfigs'.

        Requires a `PushConfigStore` to be configured.
        """
        if not self._push_config_store:
            raise PushNotificationNotSupportedError

        task_id = params.task_id
        task: Task | None = await self.task_store.get(task_id, context)
        if not task:
            raise TaskNotFoundError

        push_notification_config_list = await self._push_config_store.get_info(
            task_id, context
        )

        return ListTaskPushNotificationConfigsResponse(
            configs=push_notification_config_list
        )

    @validate_request_params
    @validate(
        lambda self: self._agent_card.capabilities.push_notifications,
        error_message='Push notifications are not supported by the agent',
        error_type=PushNotificationNotSupportedError,
    )
    async def on_delete_task_push_notification_config(
        self,
        params: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> None:
        """Default handler for 'tasks/pushNotificationConfig/delete'.

        Requires a `PushConfigStore` to be configured.
        """
        if not self._push_config_store:
            raise PushNotificationNotSupportedError

        task_id = params.task_id
        config_id = params.id
        task: Task | None = await self.task_store.get(task_id, context)
        if not task:
            raise TaskNotFoundError

        await self._push_config_store.delete_info(task_id, context, config_id)

    @validate_request_params
    @validate(
        lambda self: self._agent_card.capabilities.extended_agent_card,
        error_message='The agent does not support authenticated extended cards',
    )
    async def on_get_extended_agent_card(
        self,
        params: GetExtendedAgentCardRequest,
        context: ServerCallContext,
    ) -> AgentCard:
        """Default handler for 'GetExtendedAgentCard'.

        Requires `capabilities.extended_agent_card` to be true.
        """
        extended_card = self.extended_agent_card
        if not extended_card:
            raise ExtendedAgentCardNotConfiguredError

        if self.extended_card_modifier:
            extended_card = await self.extended_card_modifier(
                extended_card, context
            )

        return extended_card
