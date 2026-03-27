from __future__ import annotations

import asyncio
import logging

from typing import TYPE_CHECKING, Any, cast

from a2a.server.agent_execution import (
    AgentExecutor,
    RequestContext,
    RequestContextBuilder,
    SimpleRequestContextBuilder,
)
from a2a.server.agent_execution.active_task_registry import ActiveTaskRegistry
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.tasks import (
    PushNotificationConfigStore,
    PushNotificationSender,
    TaskManager,
    TaskStore,
)
from a2a.types.a2a_pb2 import (
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
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
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import (
    InternalError,
    InvalidParamsError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from a2a.utils.task import (
    apply_history_length,
    validate_history_length,
    validate_page_size,
)
from a2a.utils.telemetry import SpanKind, trace_class


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from a2a.server.agent_execution.active_task import ActiveTask
    from a2a.server.events import Event


logger = logging.getLogger(__name__)

TERMINAL_TASK_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_REJECTED,
}

#TODO cleanup context_id management

@trace_class(kind=SpanKind.SERVER)
class DefaultRequestHandler(RequestHandler):
    """Default request handler for all incoming requests."""

    _background_tasks: set[asyncio.Task]

    def __init__(  # noqa: PLR0913
        self,
        agent_executor: AgentExecutor,
        task_store: TaskStore,
        queue_manager: Any
        | None = None,  # Kept for backward compat in signature
        push_config_store: PushNotificationConfigStore | None = None,
        push_sender: PushNotificationSender | None = None,
        request_context_builder: RequestContextBuilder | None = None,
    ) -> None:
        self.agent_executor = agent_executor
        self.task_store = task_store
        self._push_config_store = push_config_store
        self._push_sender = push_sender
        self._request_context_builder = (
            request_context_builder
            or SimpleRequestContextBuilder(
                should_populate_referred_tasks=False, task_store=self.task_store
            )
        )
        self._active_task_registry = ActiveTaskRegistry(
            agent_executor=self.agent_executor,
            task_store=self.task_store,
            push_sender=self._push_sender,
        )
        self._background_tasks = set()

    async def on_get_task(  # noqa: D102
        self,
        params: GetTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        validate_history_length(params)

        task_id = params.id
        task: Task | None = await self.task_store.get(task_id, context)
        if not task:
            raise TaskNotFoundError

        return apply_history_length(task, params)

    async def on_list_tasks(  # noqa: D102
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
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

    async def on_cancel_task(  # noqa: D102
        self,
        params: CancelTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        task_id = params.id
        task: Task | None = await self.task_store.get(task_id, context)
        if not task:
            raise TaskNotFoundError

        if task.status.state in TERMINAL_TASK_STATES:
            logger.warning(
                'Task %s is in terminal state: %s, returning as is',
                task.id,
                task.status.state,
            )
            # raise TaskNotCancelableError(
            #     message=f'Task {task.id} is in terminal state: {task.status.state}'
            # )
            return task

        active_task = await self._active_task_registry.get_or_create(task.id)

        request_context = RequestContext(
            None,
            task_id=task.id,
            context_id=task.context_id,
            task=task,
            call_context=context,
        )

        await active_task.cancel(request_context)

        result = await active_task.wait()
        if not isinstance(result, Task):
            raise InternalError(
                message='Agent did not return valid response for cancel'
            )

        if result.status.state != TaskState.TASK_STATE_CANCELED:
            logger.warning(
                'Task %s is in terminal state: %s, returning as is',
                task.id,
                result.status.state,
            )

        return result

    def _validate_task_id_match(self, task_id: str, event_task_id: str) -> None:
        if task_id != event_task_id:
            logger.error(
                'Agent generated task_id=%s does not match the RequestContext task_id=%s.',
                event_task_id,
                task_id,
            )
            raise InternalError(message='Task ID mismatch in agent response')

    async def _setup_active_task(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> tuple[ActiveTask, RequestContext]:
        validate_history_length(params.configuration)

        original_task_id = params.message.task_id or None
        original_context_id = params.message.context_id or None

        # Build preliminary context to resolve or generate missing IDs
        request_context = await self._request_context_builder.build(
            params=params,
            task_id=original_task_id,
            context_id=original_context_id,
            task=None,
            context=context,
        )

        task_id = cast('str', request_context.task_id)

        if (
            self._push_config_store
            and params.configuration
            and params.configuration.task_push_notification_config
        ):
            await self._push_config_store.set_info(
                task_id,
                params.configuration.task_push_notification_config,
                context or ServerCallContext(),
            )

        active_task = await self._active_task_registry.get_or_create(
            task_id, context=context, initial_message=params.message
        )

        async def setup_db() -> None:
            temp_task_manager = TaskManager(
                task_id=task_id,
                context_id=original_context_id,
                task_store=self.task_store,
                # Do not store initial message during task creation. It will be done before AgentExecutor.execute().
                initial_message=None,
                context=context,
            )
            task: Task | None = await temp_task_manager.get_task()

            if task:
                if task.status.state in TERMINAL_TASK_STATES:
                    raise InvalidParamsError(
                        message=f'Task {task.id} is in terminal state: {task.status.state}'
                    )
                task = temp_task_manager.update_with_message(params.message, task)
                await temp_task_manager.save_task_event(task)
            elif original_task_id:
                raise TaskNotFoundError(
                    message=f'Task {original_task_id} was specified but does not exist'
                )
            else:
                # NEW task. Create and save it so it's not "missing" if queried immediately
                # (especially important for return_immediately=True)
                # TODO integrate as part of ActiveTask
                task = temp_task_manager._init_task_obj(
                    task_id, cast('str', request_context.context_id)
                )
                await temp_task_manager.save_task_event(task)
                if self._push_sender:
                    await self._push_sender.send_notification(task_id, task)

            # request_context.current_task = task

        await active_task.start(setup_callback=setup_db)
        return active_task, request_context

    async def on_message_send(  # noqa: D102
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> Message | Task:
        active_task, request_context = await self._setup_active_task(
            params, context
        )

        if params.configuration and params.configuration.return_immediately:
            await active_task.enqueue_request(request_context)

            task = await active_task.get_task()
            if params.configuration:
                task = apply_history_length(task, params.configuration)
            return task

        try:
            RESULT_STATES = {
                TaskState.TASK_STATE_COMPLETED,
                TaskState.TASK_STATE_FAILED,
                TaskState.TASK_STATE_CANCELED,
                TaskState.TASK_STATE_REJECTED,
                TaskState.TASK_STATE_INPUT_REQUIRED,
                TaskState.TASK_STATE_AUTH_REQUIRED,
            }

            result = None
            async for event in active_task.subscribe(request=request_context):
                logger.info('Processing[%s] event [%s] %s', request_context.task_id, type(event).__name__, event)
                if isinstance(event, Message):
                    result = event
                    break
                elif isinstance(event, Task) and event.status.state in RESULT_STATES:
                    result = event
                    break
                elif isinstance(event, TaskStatusUpdateEvent) and event.status.state in RESULT_STATES:
                    result = await self.task_store.get(event.task_id, context)
                    break
            
            logger.info('Processing[%s] result: %s', request_context.task_id, result)

        except Exception:
            logger.exception('Agent execution failed')
            raise

        if isinstance(result, Task):
            self._validate_task_id_match(
                cast('str', request_context.task_id), result.id
            )
            if params.configuration:
                result = apply_history_length(result, params.configuration)

        return result

    async def on_message_send_stream(  # noqa: D102
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event, None]:
        active_task, request_context = await self._setup_active_task(
            params, context
        )

        task_id = cast('str', request_context.task_id)

        async for event in active_task.subscribe(request=request_context):
            if isinstance(event, Task):
                self._validate_task_id_match(task_id, event.id)
            yield event

    async def on_create_task_push_notification_config(  # noqa: D102
        self,
        params: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        if not self._push_config_store:
            raise UnsupportedOperationError

        task_id = params.task_id
        task: Task | None = await self.task_store.get(task_id, context)
        if not task:
            raise TaskNotFoundError

        await self._push_config_store.set_info(
            task_id,
            params,
            context or ServerCallContext(),
        )

        return params

    async def on_get_task_push_notification_config(  # noqa: D102
        self,
        params: GetTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
        if not self._push_config_store:
            raise UnsupportedOperationError

        task_id = params.task_id
        config_id = params.id
        task: Task | None = await self.task_store.get(task_id, context)
        if not task:
            raise TaskNotFoundError

        push_notification_configs: list[TaskPushNotificationConfig] = (
            await self._push_config_store.get_info(
                task_id, context or ServerCallContext()
            )
            or []
        )

        for config in push_notification_configs:
            if config.id == config_id:
                return config

        raise InternalError(message='Push notification config not found')

    async def on_subscribe_to_task(  # noqa: D102
        self,
        params: SubscribeToTaskRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event, None]:
        task_id = params.id
        task: Task | None = await self.task_store.get(task_id, context)
        if not task:
            raise TaskNotFoundError

        # TODO: Move to  ActiveTask
        if task.status.state in TERMINAL_TASK_STATES:
            raise UnsupportedOperationError(
                message=f'Task {task.id} is in terminal state: {task.status.state}'
            )

        yield task

        active_task = await self._active_task_registry.get(task_id)
        if not active_task:
            raise TaskNotFoundError

        async for event in active_task.subscribe():
            yield event

    async def on_list_task_push_notification_configs(  # noqa: D102
        self,
        params: ListTaskPushNotificationConfigsRequest,
        context: ServerCallContext,
    ) -> ListTaskPushNotificationConfigsResponse:
        if not self._push_config_store:
            raise UnsupportedOperationError

        task_id = params.task_id
        task: Task | None = await self.task_store.get(task_id, context)
        if not task:
            raise TaskNotFoundError

        push_notification_config_list = await self._push_config_store.get_info(
            task_id, context or ServerCallContext()
        )

        return ListTaskPushNotificationConfigsResponse(
            configs=push_notification_config_list
        )

    async def on_delete_task_push_notification_config(  # noqa: D102
        self,
        params: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> None:
        if not self._push_config_store:
            raise UnsupportedOperationError

        task_id = params.task_id
        config_id = params.id
        task: Task | None = await self.task_store.get(task_id, context)
        if not task:
            raise TaskNotFoundError

        await self._push_config_store.delete_info(
            task_id, context or ServerCallContext(), config_id
        )
