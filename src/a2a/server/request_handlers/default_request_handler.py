"""Patched version of a2a/server/request_handlers/default_request_handler.py

Fix for A2A-INJ-01: context-level ownership tracking prevents unauthorized
callers from injecting messages into another user's context.

Root cause of vulnerability:
  _setup_message_execution() uses params.message.context_id directly without
  any ownership check. An attacker who knows a victim's contextId can send a
  new task under that context — task_manager.get_task() returns None for the
  new task_id, so the original task-level check is never reached.

Fix design:
  DefaultRequestHandler maintains a _context_owners dict (context_id → owner)
  in memory. When a get_caller_id extractor is configured:
    1. On first message for a context_id: record caller as owner.
    2. On subsequent messages for same context_id: verify caller matches owner.
  If get_caller_id is None (default): no ownership tracking — backward compatible.

Target file: src/a2a/server/request_handlers/default_request_handler.py
"""

import asyncio
import logging

from collections.abc import AsyncGenerator, Callable
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

# ---- NEW: caller identity extractor type (fix for A2A-INJ-01) ----
# CallerIdExtractor extracts a stable identity string from ServerCallContext.
# Returns None if caller identity cannot be determined (unauthenticated).
CallerIdExtractor = Callable[['ServerCallContext | None'], str | None]
# ------------------------------------------------------------------


@trace_class(kind=SpanKind.SERVER)
class DefaultRequestHandler(RequestHandler):
    """Default request handler for all incoming requests."""

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
        # ---- NEW PARAMETER (fix for A2A-INJ-01) ----
        get_caller_id: CallerIdExtractor | None = None,
        # --------------------------------------------
    ) -> None:
        """Initializes the DefaultRequestHandler.

        Args:
            agent_executor: The AgentExecutor instance to run agent logic.
            task_store: The TaskStore instance to manage task persistence.
            queue_manager: The QueueManager instance. Defaults to InMemoryQueueManager.
            push_config_store: The PushNotificationConfigStore instance.
            push_sender: The PushNotificationSender instance.
            request_context_builder: The RequestContextBuilder instance.
            get_caller_id: Optional callable that extracts a stable identity
                string from a ServerCallContext (e.g. JWT sub, API key, mTLS
                fingerprint). When provided, the handler tracks which caller
                created each contextId and rejects messages from different
                callers attempting to join that context (A2A-INJ-01 fix).
                If None (default), no ownership tracking is performed —
                backward compatible with existing deployments.

                Example::

                    def get_caller_id(ctx: ServerCallContext | None) -> str | None:
                        if ctx is None or not ctx.user.is_authenticated:
                            return None
                        return ctx.user.user_name

                    handler = DefaultRequestHandler(
                        agent_executor=executor,
                        task_store=task_store,
                        get_caller_id=get_caller_id,
                    )
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
        # ---- NEW (fix for A2A-INJ-01) ----
        self._get_caller_id: CallerIdExtractor | None = get_caller_id
        # Maps context_id → owner identity; populated on first message per context.
        self._context_owners: dict[str, str] = {}
        # ----------------------------------
        self._running_agents = {}
        self._running_agents_lock = asyncio.Lock()
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
        return apply_history_length(task, params.history_length)

    async def on_cancel_task(
        self, params: TaskIdParams, context: ServerCallContext | None = None
    ) -> Task | None:
        """Default handler for 'tasks/cancel'."""
        task: Task | None = await self.task_store.get(params.id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())

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
        await self.agent_executor.execute(request, queue)
        await queue.close()

    def _check_context_ownership(
        self,
        context_id: str,
        context: ServerCallContext | None,
    ) -> None:
        """Enforce context ownership when get_caller_id is configured.

        Called before any message is processed for an existing context_id.
        Raises ServerError(InvalidParamsError) if the caller does not own
        the context.
        """
        if self._get_caller_id is None:
            # Ownership tracking not configured — log warning and allow.
            # Operators should configure get_caller_id in production.
            logger.warning(
                'Context ownership not enforced for context_id=%s: '
                'no get_caller_id configured on DefaultRequestHandler. '
                'This allows cross-user context injection (A2A-INJ-01 / CWE-639). '
                'Provide a get_caller_id extractor to enable ownership checks.',
                context_id,
            )
            return

        caller = self._get_caller_id(context)
        owner = self._context_owners.get(context_id)

        if owner is None:
            # Context exists in the store but ownership was not recorded
            # (e.g. created before this patch was deployed). Skip check.
            logger.debug(
                'context_id=%s has no recorded owner; skipping ownership check.',
                context_id,
            )
            return

        if caller is None:
            raise ServerError(
                error=InvalidParamsError(
                    message=(
                        f'Access denied: cannot send to context_id={context_id!r} '
                        'because caller identity could not be determined.'
                    )
                )
            )

        if caller != owner:
            logger.warning(
                'Context injection attempt blocked: caller=%r tried to send to '
                'context_id=%s owned by %r.',
                caller, context_id, owner,
            )
            raise ServerError(
                error=InvalidParamsError(
                    message=(
                        f'Access denied: context_id={context_id!r} was created '
                        'by a different caller.'
                    )
                )
            )

    def _record_context_owner(
        self,
        context_id: str,
        context: ServerCallContext | None,
    ) -> None:
        """Record caller as owner of context_id on first use."""
        if self._get_caller_id is None or context_id in self._context_owners:
            return
        caller = self._get_caller_id(context)
        if caller:
            self._context_owners[context_id] = caller
            logger.debug('Recorded owner %r for context_id=%s', caller, context_id)

    async def _setup_message_execution(
        self,
        params: MessageSendParams,
        context: ServerCallContext | None = None,
    ) -> tuple[TaskManager, str, EventQueue, ResultAggregator, asyncio.Task]:
        context_id = params.message.context_id

        # ---- FIX: A2A-INJ-01 — enforce context ownership BEFORE task lookup ----
        # The check must happen at context_id level, not task level. An attacker
        # who sends a new task_id under an existing context_id would otherwise
        # bypass a task-level check (get_task() returns None → check never runs).
        if context_id and context_id in self._context_owners:
            self._check_context_ownership(context_id, context)
        # -----------------------------------------------------------------------

        task_manager = TaskManager(
            task_id=params.message.task_id,
            context_id=context_id,
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

        request_context = await self._request_context_builder.build(
            params=params,
            task_id=task.id if task else None,
            context_id=context_id,
            task=task,
            context=context,
        )
        task_id = cast('str', request_context.task_id)

        # Record ownership for new contexts after successful validation
        new_context_id = request_context.context_id or context_id
        if new_context_id:
            self._record_context_owner(new_context_id, context)

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
        producer_task = asyncio.create_task(
            self._run_event_stream(request_context, queue)
        )
        await self._register_producer(task_id, producer_task)

        return task_manager, task_id, queue, result_aggregator, producer_task

    def _validate_task_id_match(self, task_id: str, event_task_id: str) -> None:
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
        if self._push_sender and task_id:
            latest_task = await result_aggregator.current_result
            if isinstance(latest_task, Task):
                await self._push_sender.send_notification(latest_task)

    async def on_message_send(
        self,
        params: MessageSendParams,
        context: ServerCallContext | None = None,
    ) -> Message | Task:
        """Default handler for 'message/send' (non-streaming)."""
        (
            _task_manager,
            task_id,
            queue,
            result_aggregator,
            producer_task,
        ) = await self._setup_message_execution(params, context)

        consumer = EventConsumer(queue)
        producer_task.add_done_callback(consumer.agent_task_callback)

        blocking = True
        if params.configuration and params.configuration.blocking is False:
            blocking = False

        interrupted_or_non_blocking = False
        try:
            async def push_notification_callback() -> None:
                await self._send_push_notification_if_needed(task_id, result_aggregator)

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
            raise ServerError(error=InternalError())

        if isinstance(result, Task):
            self._validate_task_id_match(task_id, result.id)
            if params.configuration:
                result = apply_history_length(result, params.configuration.history_length)

        await self._send_push_notification_if_needed(task_id, result_aggregator)
        return result

    async def on_message_send_stream(
        self,
        params: MessageSendParams,
        context: ServerCallContext | None = None,
    ) -> AsyncGenerator[Event]:
        """Default handler for 'message/stream' (streaming)."""
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
                await self._send_push_notification_if_needed(task_id, result_aggregator)
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            bg_task = asyncio.create_task(result_aggregator.consume_all(consumer))
            bg_task.set_name(f'background_consume:{task_id}')
            self._track_background_task(bg_task)
            raise
        finally:
            cleanup_task = asyncio.create_task(self._cleanup_producer(producer_task, task_id))
            cleanup_task.set_name(f'cleanup_producer:{task_id}')
            self._track_background_task(cleanup_task)

    async def _register_producer(self, task_id: str, producer_task: asyncio.Task) -> None:
        async with self._running_agents_lock:
            self._running_agents[task_id] = producer_task

    def _track_background_task(self, task: asyncio.Task) -> None:
        self._background_tasks.add(task)

        def _on_done(completed: asyncio.Task) -> None:
            try:
                completed.result()
            except asyncio.CancelledError:
                logger.debug('Background task %s cancelled', completed.get_name())
            except Exception:
                logger.exception('Background task %s failed', completed.get_name())
            finally:
                self._background_tasks.discard(completed)

        task.add_done_callback(_on_done)

    async def _cleanup_producer(self, producer_task: asyncio.Task, task_id: str) -> None:
        try:
            await producer_task
        except asyncio.CancelledError:
            logger.debug('Producer task %s was cancelled during cleanup', task_id)
        await self._queue_manager.close(task_id)
        async with self._running_agents_lock:
            self._running_agents.pop(task_id, None)

    async def on_set_task_push_notification_config(
        self,
        params: TaskPushNotificationConfig,
        context: ServerCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        if not self._push_config_store:
            raise ServerError(error=UnsupportedOperationError())
        task: Task | None = await self.task_store.get(params.task_id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())
        await self._push_config_store.set_info(params.task_id, params.push_notification_config)
        return params

    async def on_get_task_push_notification_config(
        self,
        params: TaskIdParams | GetTaskPushNotificationConfigParams,
        context: ServerCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        if not self._push_config_store:
            raise ServerError(error=UnsupportedOperationError())
        task: Task | None = await self.task_store.get(params.id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())
        push_notification_config = await self._push_config_store.get_info(params.id)
        if not push_notification_config or not push_notification_config[0]:
            raise ServerError(error=InternalError(message='Push notification config not found'))
        return TaskPushNotificationConfig(
            task_id=params.id,
            push_notification_config=push_notification_config[0],
        )

    async def on_resubscribe_to_task(
        self,
        params: TaskIdParams,
        context: ServerCallContext | None = None,
    ) -> AsyncGenerator[Event]:
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
        if not self._push_config_store:
            raise ServerError(error=UnsupportedOperationError())
        task: Task | None = await self.task_store.get(params.id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())
        push_notification_config_list = await self._push_config_store.get_info(params.id)
        return [
            TaskPushNotificationConfig(task_id=params.id, push_notification_config=cfg)
            for cfg in push_notification_config_list
        ]

    async def on_delete_task_push_notification_config(
        self,
        params: DeleteTaskPushNotificationConfigParams,
        context: ServerCallContext | None = None,
    ) -> None:
        if not self._push_config_store:
            raise ServerError(error=UnsupportedOperationError())
        task: Task | None = await self.task_store.get(params.id, context)
        if not task:
            raise ServerError(error=TaskNotFoundError())
        await self._push_config_store.delete_info(params.id, params.push_notification_config_id)
