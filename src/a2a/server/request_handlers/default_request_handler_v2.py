from __future__ import annotations

import asyncio  # noqa: TC003
import logging

from typing import TYPE_CHECKING, Any, cast

from a2a.server.agent_execution import (
    AgentExecutor,
    RequestContext,
    RequestContextBuilder,
    SimpleRequestContextBuilder,
)
from a2a.server.agent_execution.active_task import (
    INTERRUPTED_TASK_STATES,
    TERMINAL_TASK_STATES,
)
from a2a.server.agent_execution.active_task_registry import ActiveTaskRegistry
from a2a.server.request_handlers.request_handler import (
    RequestHandler,
    validate,
    validate_request_params,
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
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import (
    ExtendedAgentCardNotConfiguredError,
    InternalError,
    InvalidParamsError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
)
from a2a.utils.helpers import maybe_await
from a2a.utils.task import (
    apply_history_length,
    validate_history_length,
    validate_page_size,
)
from a2a.utils.telemetry import SpanKind, trace_class


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Awaitable, Callable

    from a2a.server.agent_execution.active_task import ActiveTask
    from a2a.server.context import ServerCallContext
    from a2a.server.events import Event
    from a2a.server.tasks import (
        PushNotificationConfigStore,
        PushNotificationSender,
        TaskStore,
    )


logger = logging.getLogger(__name__)


# TODO: cleanup context_id management


@trace_class(kind=SpanKind.SERVER)
class DefaultRequestHandlerV2(RequestHandler):
    """Default request handler for all incoming requests."""

    _background_tasks: set[asyncio.Task]

    def __init__(  # noqa: PLR0913
        self,
        agent_executor: AgentExecutor,
        task_store: TaskStore,
        agent_card: AgentCard,
        queue_manager: Any
        | None = None,  # Kept for backward compat in signature
        push_config_store: PushNotificationConfigStore | None = None,
        push_sender: PushNotificationSender | None = None,
        request_context_builder: RequestContextBuilder | None = None,
        extended_agent_card: AgentCard | None = None,
        extended_card_modifier: Callable[
            [AgentCard, ServerCallContext], Awaitable[AgentCard] | AgentCard
        ]
        | None = None,
    ) -> None:
        self.agent_executor = agent_executor
        self.task_store = task_store
        self._agent_card = agent_card
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
        self._active_task_registry = ActiveTaskRegistry(
            agent_executor=self.agent_executor,
            task_store=self.task_store,
            push_sender=self._push_sender,
        )
        self._background_tasks = set()

    @validate_request_params
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

    @validate_request_params
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

    @validate_request_params
    async def on_cancel_task(  # noqa: D102
        self,
        params: CancelTaskRequest,
        context: ServerCallContext,
    ) -> Task | None:
        task_id = params.id

        try:
            active_task = await self._active_task_registry.get_or_create(
                task_id, call_context=context, create_task_if_missing=False
            )
            result = await active_task.cancel(context)
        except InvalidParamsError as e:
            raise TaskNotCancelableError from e

        if isinstance(result, Message):
            raise InternalError(
                message='Cancellation returned a message instead of a task.'
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
        call_context: ServerCallContext,
    ) -> tuple[ActiveTask, RequestContext]:
        validate_history_length(params.configuration)

        original_task_id = params.message.task_id or None
        original_context_id = params.message.context_id or None

        if original_task_id:
            task = await self.task_store.get(original_task_id, call_context)
            if not task:
                raise TaskNotFoundError(f'Task {original_task_id} not found')

        # Build context to resolve or generate missing IDs
        request_context = await self._request_context_builder.build(
            params=params,
            task_id=original_task_id,
            context_id=original_context_id,
            # We will get the task when we have to process the request to avoid concurrent read/write issues.
            task=None,
            context=call_context,
        )

        task_id = cast('str', request_context.task_id)
        context_id = cast('str', request_context.context_id)

        if (
            self._push_config_store
            and params.configuration
            and params.configuration.task_push_notification_config
        ):
            await self._push_config_store.set_info(
                task_id,
                params.configuration.task_push_notification_config,
                call_context,
            )

        active_task = await self._active_task_registry.get_or_create(
            task_id,
            context_id=context_id,
            call_context=call_context,
            create_task_if_missing=True,
        )

        return active_task, request_context

    @validate_request_params
    async def on_message_send(  # noqa: D102
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> Message | Task:
        active_task, request_context = await self._setup_active_task(
            params, context
        )
        task_id = cast('str', request_context.task_id)

        result: Message | Task | None = None

        async for raw_event in active_task.subscribe(
            request=request_context,
            include_initial_task=False,
            replace_status_update_with_task=True,
        ):
            event = raw_event
            logger.debug(
                'Processing[%s] event [%s] %s',
                params.message.task_id,
                type(event).__name__,
                event,
            )
            if isinstance(event, TaskStatusUpdateEvent):
                self._validate_task_id_match(task_id, event.task_id)
                event = await active_task.get_task()
                logger.debug(
                    'Replaced TaskStatusUpdateEvent with Task: %s', event
                )

            if isinstance(event, Task) and (
                params.configuration.return_immediately
                or event.status.state
                in (TERMINAL_TASK_STATES | INTERRUPTED_TASK_STATES)
            ):
                self._validate_task_id_match(task_id, event.id)
                result = event
                break

            if isinstance(event, Message):
                result = event
                break

        if result is None:
            logger.debug('Missing result for task %s', request_context.task_id)
            result = await active_task.get_task()

        if isinstance(result, Task):
            result = apply_history_length(result, params.configuration)

        logger.debug(
            'Returning result for task %s: %s',
            request_context.task_id,
            result,
        )
        return result

    @validate_request_params
    @validate(
        lambda self: self._agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def on_message_send_stream(  # noqa: D102
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event, None]:
        active_task, request_context = await self._setup_active_task(
            params, context
        )

        task_id = cast('str', request_context.task_id)

        async for event in active_task.subscribe(
            request=request_context,
            include_initial_task=False,
        ):
            if isinstance(event, Task):
                self._validate_task_id_match(task_id, event.id)
                yield apply_history_length(event, params.configuration)
            else:
                yield event

            if isinstance(event, Message):
                break

    @validate_request_params
    @validate(
        lambda self: self._agent_card.capabilities.push_notifications,
        error_message='Push notifications are not supported by the agent',
        error_type=PushNotificationNotSupportedError,
    )
    async def on_create_task_push_notification_config(  # noqa: D102
        self,
        params: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
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
    async def on_get_task_push_notification_config(  # noqa: D102
        self,
        params: GetTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> TaskPushNotificationConfig:
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
    async def on_subscribe_to_task(  # noqa: D102
        self,
        params: SubscribeToTaskRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event, None]:
        task_id = params.id

        active_task = await self._active_task_registry.get_or_create(
            task_id,
            call_context=context,
            create_task_if_missing=False,
        )

        async for event in active_task.subscribe(include_initial_task=True):
            yield event

    @validate_request_params
    @validate(
        lambda self: self._agent_card.capabilities.push_notifications,
        error_message='Push notifications are not supported by the agent',
        error_type=PushNotificationNotSupportedError,
    )
    async def on_list_task_push_notification_configs(  # noqa: D102
        self,
        params: ListTaskPushNotificationConfigsRequest,
        context: ServerCallContext,
    ) -> ListTaskPushNotificationConfigsResponse:
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
    async def on_delete_task_push_notification_config(  # noqa: D102
        self,
        params: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> None:
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
            return await maybe_await(
                self.extended_card_modifier(extended_card, context)
            )

        return extended_card
