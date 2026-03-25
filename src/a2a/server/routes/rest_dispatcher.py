import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

from google.protobuf.json_format import MessageToDict, Parse

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes import CallContextBuilder, DefaultCallContextBuilder
from a2a.types import a2a_pb2
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    GetTaskPushNotificationConfigRequest,
    SubscribeToTaskRequest,
)
from a2a.utils import constants, proto_utils
from a2a.utils.error_handlers import (
    rest_error_handler,
    rest_stream_error_handler,
)
from a2a.utils.errors import (
    ExtendedAgentCardNotConfiguredError,
    InvalidRequestError,
    TaskNotFoundError,
)
from a2a.utils.helpers import maybe_await, validate, validate_version
from a2a.utils.telemetry import SpanKind, trace_class


if TYPE_CHECKING:
    from sse_starlette.sse import EventSourceResponse
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response

    _package_starlette_installed = True
else:
    try:
        from sse_starlette.sse import EventSourceResponse
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response

        _package_starlette_installed = True
    except ImportError:
        EventSourceResponse = Any
        Request = Any
        JSONResponse = Any
        Response = Any

        _package_starlette_installed = False

logger = logging.getLogger(__name__)

@trace_class(kind=SpanKind.SERVER)
class RestDispatcher:
    """Dispatches incoming REST requests to the appropriate handler methods.

    Handles context building, routing to RequestHandler directly, and response formatting (JSON/SSE).
    """

    def __init__(  # noqa: PLR0913
        self,
        agent_card: AgentCard,
        request_handler: RequestHandler,
        extended_agent_card: AgentCard | None = None,
        context_builder: CallContextBuilder | None = None,
        card_modifier: Callable[[AgentCard], Awaitable[AgentCard] | AgentCard]
        | None = None,
        extended_card_modifier: Callable[
            [AgentCard, ServerCallContext], Awaitable[AgentCard] | AgentCard
        ]
        | None = None,
    ) -> None:
        """Initializes the RestDispatcher.

        Args:
            agent_card: The AgentCard describing the agent's capabilities.
            request_handler: The underlying `RequestHandler` instance to delegate requests to.
            extended_agent_card: An optional, distinct AgentCard to be served
              at the authenticated extended card endpoint.
            context_builder: The CallContextBuilder used to construct the
              ServerCallContext passed to the request_handler. If None, no
              ServerCallContext is passed.
            card_modifier: An optional callback to dynamically modify the public
              agent card before it is served.
            extended_card_modifier: An optional callback to dynamically modify
              the extended agent card before it is served. It receives the
              call context.
        """
        if not _package_starlette_installed:
            raise ImportError(
                'Packages `starlette` and `sse-starlette` are required to use the'
                ' `RestDispatcher`. They can be added as a part of `a2a-sdk` '
                'optional dependencies, `a2a-sdk[http-server]`.'
            )

        self.agent_card = agent_card
        self.extended_agent_card = extended_agent_card
        self.card_modifier = card_modifier
        self.extended_card_modifier = extended_card_modifier
        self._context_builder = context_builder or DefaultCallContextBuilder()
        self.request_handler = request_handler

    def _build_call_context(self, request: Request) -> ServerCallContext:
        call_context = self._context_builder.build(request)
        if 'tenant' in request.path_params:
            call_context.tenant = request.path_params['tenant']
        return call_context

    @rest_error_handler
    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def on_message_send(self, request: Request) -> Response:
        """Handles the 'message/send' REST method."""
        context = self._build_call_context(request)
        body = await request.body()
        params = a2a_pb2.SendMessageRequest()
        Parse(body, params)
        task_or_message = await self.request_handler.on_message_send(params, context)
        if isinstance(task_or_message, a2a_pb2.Task):
            response = a2a_pb2.SendMessageResponse(task=task_or_message)
        else:
            response = a2a_pb2.SendMessageResponse(message=task_or_message)
        return JSONResponse(content=MessageToDict(response))

    @rest_stream_error_handler
    @validate_version(constants.PROTOCOL_VERSION_1_0)
    @validate(
        lambda self: self.agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def on_message_send_stream(self, request: Request) -> EventSourceResponse:
        """Handles the 'message/stream' REST method."""
        try:
            await request.body()
        except (ValueError, RuntimeError, OSError) as e:
            raise InvalidRequestError(
                message=f'Failed to pre-consume request body: {e}'
            ) from e

        context = self._build_call_context(request)
        body = await request.body()
        params = a2a_pb2.SendMessageRequest()
        Parse(body, params)

        stream = aiter(self.request_handler.on_message_send_stream(params, context))
        try:
            first_event = await anext(stream)
        except StopAsyncIteration:
            return EventSourceResponse(iter([]))

        async def event_generator() -> AsyncIterator[str]:
            yield json.dumps(MessageToDict(proto_utils.to_stream_response(first_event)))
            async for event in stream:
                yield json.dumps(MessageToDict(proto_utils.to_stream_response(event)))

        return EventSourceResponse(event_generator())

    @rest_error_handler
    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def on_cancel_task(self, request: Request) -> Response:
        """Handles the 'tasks/cancel' REST method."""
        context = self._build_call_context(request)
        task_id = request.path_params['id']
        task = await self.request_handler.on_cancel_task(CancelTaskRequest(id=task_id), context)
        if task:
            return JSONResponse(content=MessageToDict(task))
        raise TaskNotFoundError

    @rest_stream_error_handler
    @validate_version(constants.PROTOCOL_VERSION_1_0)
    @validate(
        lambda self: self.agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def on_subscribe_to_task(self, request: Request) -> EventSourceResponse:
        """Handles the 'SubscribeToTask' REST method."""
        try:
            await request.body()
        except (ValueError, RuntimeError, OSError) as e:
            raise InvalidRequestError(
                message=f'Failed to pre-consume request body: {e}'
            ) from e

        context = self._build_call_context(request)
        task_id = request.path_params['id']
        
        stream = aiter(self.request_handler.on_subscribe_to_task(SubscribeToTaskRequest(id=task_id), context))
        try:
            first_event = await anext(stream)
        except StopAsyncIteration:
            return EventSourceResponse(iter([]))

        async def event_generator() -> AsyncIterator[str]:
            yield json.dumps(MessageToDict(proto_utils.to_stream_response(first_event)))
            async for event in stream:
                yield json.dumps(MessageToDict(proto_utils.to_stream_response(event)))

        return EventSourceResponse(event_generator())

    @rest_error_handler
    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def on_get_task(self, request: Request) -> Response:
        """Handles the 'tasks/{id}' REST method."""
        context = self._build_call_context(request)
        params = a2a_pb2.GetTaskRequest()
        proto_utils.parse_params(request.query_params, params)
        params.id = request.path_params['id']
        task = await self.request_handler.on_get_task(params, context)
        if task:
            return JSONResponse(content=MessageToDict(task))
        raise TaskNotFoundError

    @rest_error_handler
    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def get_push_notification(self, request: Request) -> Response:
        """Handles the 'tasks/pushNotificationConfig/get' REST method."""
        context = self._build_call_context(request)
        task_id = request.path_params['id']
        push_id = request.path_params['push_id']
        params = GetTaskPushNotificationConfigRequest(task_id=task_id, id=push_id)
        config = await self.request_handler.on_get_task_push_notification_config(params, context)
        return JSONResponse(content=MessageToDict(config))

    @rest_error_handler
    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def delete_push_notification(self, request: Request) -> Response:
        """Handles the 'tasks/pushNotificationConfig/delete' REST method."""
        context = self._build_call_context(request)
        task_id = request.path_params['id']
        push_id = request.path_params['push_id']
        params = a2a_pb2.DeleteTaskPushNotificationConfigRequest(task_id=task_id, id=push_id)
        await self.request_handler.on_delete_task_push_notification_config(params, context)
        return JSONResponse(content={})

    @rest_error_handler
    @validate_version(constants.PROTOCOL_VERSION_1_0)
    @validate(
        lambda self: self.agent_card.capabilities.push_notifications,
        'Push notifications are not supported by the agent',
    )
    async def set_push_notification(self, request: Request) -> Response:
        """Handles the 'tasks/pushNotificationConfig/set' REST method."""
        context = self._build_call_context(request)
        body = await request.body()
        params = a2a_pb2.TaskPushNotificationConfig()
        Parse(body, params)
        params.task_id = request.path_params['id']
        config = await self.request_handler.on_create_task_push_notification_config(params, context)
        return JSONResponse(content=MessageToDict(config))

    @rest_error_handler
    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def list_push_notifications(self, request: Request) -> Response:
        """Handles the 'tasks/pushNotificationConfig/list' REST method."""
        context = self._build_call_context(request)
        params = a2a_pb2.ListTaskPushNotificationConfigsRequest()
        proto_utils.parse_params(request.query_params, params)
        params.task_id = request.path_params['id']
        result = await self.request_handler.on_list_task_push_notification_configs(params, context)
        return JSONResponse(content=MessageToDict(result))

    @rest_error_handler
    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def list_tasks(self, request: Request) -> Response:
        """Handles the 'tasks/list' REST method."""
        context = self._build_call_context(request)
        params = a2a_pb2.ListTasksRequest()
        proto_utils.parse_params(request.query_params, params)
        result = await self.request_handler.on_list_tasks(params, context)
        return JSONResponse(content=MessageToDict(result, always_print_fields_with_no_presence=True))

    @rest_error_handler
    async def handle_authenticated_agent_card(self, request: Request) -> Response:
        """Handles the 'extendedAgentCard' REST method."""
        if not self.agent_card.capabilities.extended_agent_card:
            raise ExtendedAgentCardNotConfiguredError(
                message='Authenticated card not supported'
            )
        card_to_serve = self.extended_agent_card or self.agent_card

        if self.extended_card_modifier:
            context = self._build_call_context(request)
            card_to_serve = await maybe_await(
                self.extended_card_modifier(card_to_serve, context)
            )
        elif self.card_modifier:
            card_to_serve = await maybe_await(self.card_modifier(card_to_serve))

        return JSONResponse(
            content=MessageToDict(card_to_serve, preserving_proto_field_name=True)
        )
