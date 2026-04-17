import json
import logging

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any, TypeVar

from google.protobuf.json_format import MessageToDict, Parse

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes.common import (
    DefaultServerCallContextBuilder,
    ServerCallContextBuilder,
)
from a2a.types import a2a_pb2
from a2a.types.a2a_pb2 import (
    CancelTaskRequest,
    GetTaskPushNotificationConfigRequest,
    SubscribeToTaskRequest,
)
from a2a.utils import constants, proto_utils
from a2a.utils.error_handlers import (
    build_rest_error_payload,
    rest_error_handler,
    rest_stream_error_handler,
)
from a2a.utils.errors import (
    InvalidRequestError,
    TaskNotFoundError,
)
from a2a.utils.telemetry import SpanKind, trace_class
from a2a.utils.version_validator import validate_version


if TYPE_CHECKING:
    from sse_starlette.event import ServerSentEvent
    from sse_starlette.sse import EventSourceResponse
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response

    _package_starlette_installed = True
else:
    try:
        from sse_starlette.event import ServerSentEvent
        from sse_starlette.sse import EventSourceResponse
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response

        _package_starlette_installed = True
    except ImportError:
        EventSourceResponse = Any
        ServerSentEvent = Any
        Request = Any
        JSONResponse = Any
        Response = Any

        _package_starlette_installed = False

logger = logging.getLogger(__name__)

TResponse = TypeVar('TResponse')


@trace_class(kind=SpanKind.SERVER)
class RestDispatcher:
    """Dispatches incoming REST requests to the appropriate handler methods.

    Handles context building, routing to RequestHandler directly, and response formatting (JSON/SSE).
    """

    def __init__(
        self,
        request_handler: RequestHandler,
        context_builder: ServerCallContextBuilder | None = None,
    ) -> None:
        """Initializes the RestDispatcher.

        Args:
            request_handler: The underlying `RequestHandler` instance to delegate requests to.
            context_builder: The ServerCallContextBuilder used to construct the
              ServerCallContext passed to the request_handler. If None the
              DefaultServerCallContextBuilder is used.
        """
        if not _package_starlette_installed:
            raise ImportError(
                'Packages `starlette` and `sse-starlette` are required to use the'
                ' `RestDispatcher`. They can be added as a part of `a2a-sdk` '
                'optional dependencies, `a2a-sdk[http-server]`.'
            )

        self._context_builder = (
            context_builder or DefaultServerCallContextBuilder()
        )
        self.request_handler = request_handler

    def _build_call_context(self, request: Request) -> ServerCallContext:
        call_context = self._context_builder.build(request)
        if 'tenant' in request.path_params:
            call_context.tenant = request.path_params['tenant']
        return call_context

    async def _handle_non_streaming(
        self,
        request: Request,
        handler_func: Callable[[ServerCallContext], Awaitable[TResponse]],
    ) -> TResponse:
        """Centralized error handling and context management for unary calls."""
        context = self._build_call_context(request)
        return await handler_func(context)

    async def _handle_streaming(
        self,
        request: Request,
        handler_func: Callable[[ServerCallContext], AsyncIterator[Any]],
    ) -> EventSourceResponse:
        """Centralized error handling and context management for streaming calls."""
        # Pre-consume and cache the request body to prevent deadlock in streaming context
        # This is required because Starlette's request.body() can only be consumed once,
        # and attempting to consume it after EventSourceResponse starts causes deadlock
        try:
            await request.body()
        except (ValueError, RuntimeError, OSError) as e:
            raise InvalidRequestError(
                message=f'Failed to pre-consume request body: {e}'
            ) from e

        context = self._build_call_context(request)

        # Eagerly fetch the first item from the stream so that errors raised
        # before any event is yielded (e.g. validation, parsing, or handler
        # failures) propagate here and are caught by
        # @rest_stream_error_handler, which returns a JSONResponse with
        # the correct HTTP status code instead of starting an SSE stream.
        # Without this, the error would be raised after SSE headers are
        # already sent, and the client would see a broken stream instead
        stream = aiter(handler_func(context))
        try:
            first_item = await anext(stream)
        except StopAsyncIteration:
            return EventSourceResponse(iter([]))

        async def event_generator() -> AsyncIterator[ServerSentEvent]:
            yield ServerSentEvent(data=json.dumps(first_item))
            try:
                async for item in stream:
                    yield ServerSentEvent(data=json.dumps(item))
            except Exception as e:
                logger.exception('Error during REST SSE stream')
                yield ServerSentEvent(
                    data=json.dumps(build_rest_error_payload(e)),
                    event='error',
                )

        return EventSourceResponse(event_generator())

    @rest_error_handler
    async def on_message_send(self, request: Request) -> Response:
        """Handles the 'message/send' REST method."""

        @validate_version(constants.PROTOCOL_VERSION_1_0)
        async def _handler(
            context: ServerCallContext,
        ) -> a2a_pb2.SendMessageResponse:
            body = await request.body()
            params = a2a_pb2.SendMessageRequest()
            Parse(body, params)
            task_or_message = await self.request_handler.on_message_send(
                params, context
            )
            if isinstance(task_or_message, a2a_pb2.Task):
                return a2a_pb2.SendMessageResponse(task=task_or_message)
            return a2a_pb2.SendMessageResponse(message=task_or_message)

        response = await self._handle_non_streaming(request, _handler)
        return JSONResponse(content=MessageToDict(response))

    @rest_stream_error_handler
    async def on_message_send_stream(
        self, request: Request
    ) -> EventSourceResponse:
        """Handles the 'message/stream' REST method."""

        @validate_version(constants.PROTOCOL_VERSION_1_0)
        async def _handler(
            context: ServerCallContext,
        ) -> AsyncIterator[dict[str, Any]]:
            body = await request.body()
            params = a2a_pb2.SendMessageRequest()
            Parse(body, params)
            async for event in self.request_handler.on_message_send_stream(
                params, context
            ):
                response = proto_utils.to_stream_response(event)
                yield MessageToDict(response)

        return await self._handle_streaming(request, _handler)

    @rest_error_handler
    async def on_cancel_task(self, request: Request) -> Response:
        """Handles the 'tasks/cancel' REST method."""

        @validate_version(constants.PROTOCOL_VERSION_1_0)
        async def _handler(context: ServerCallContext) -> a2a_pb2.Task:
            task_id = request.path_params['id']
            task = await self.request_handler.on_cancel_task(
                CancelTaskRequest(id=task_id), context
            )
            if task:
                return task
            raise TaskNotFoundError

        response = await self._handle_non_streaming(request, _handler)
        return JSONResponse(content=MessageToDict(response))

    @rest_stream_error_handler
    async def on_subscribe_to_task(
        self, request: Request
    ) -> EventSourceResponse:
        """Handles the 'SubscribeToTask' REST method."""
        task_id = request.path_params['id']

        @validate_version(constants.PROTOCOL_VERSION_1_0)
        async def _handler(
            context: ServerCallContext,
        ) -> AsyncIterator[dict[str, Any]]:
            async for event in self.request_handler.on_subscribe_to_task(
                SubscribeToTaskRequest(id=task_id), context
            ):
                response = proto_utils.to_stream_response(event)
                yield MessageToDict(response)

        return await self._handle_streaming(request, _handler)

    @rest_error_handler
    async def on_get_task(self, request: Request) -> Response:
        """Handles the 'tasks/{id}' REST method."""

        @validate_version(constants.PROTOCOL_VERSION_1_0)
        async def _handler(context: ServerCallContext) -> a2a_pb2.Task:
            params = a2a_pb2.GetTaskRequest()
            proto_utils.parse_params(request.query_params, params)
            params.id = request.path_params['id']
            task = await self.request_handler.on_get_task(params, context)
            if task:
                return task
            raise TaskNotFoundError

        response = await self._handle_non_streaming(request, _handler)
        return JSONResponse(content=MessageToDict(response))

    @rest_error_handler
    async def get_push_notification(self, request: Request) -> Response:
        """Handles the 'tasks/pushNotificationConfig/get' REST method."""

        @validate_version(constants.PROTOCOL_VERSION_1_0)
        async def _handler(
            context: ServerCallContext,
        ) -> a2a_pb2.TaskPushNotificationConfig:
            task_id = request.path_params['id']
            push_id = request.path_params['push_id']
            params = GetTaskPushNotificationConfigRequest(
                task_id=task_id, id=push_id
            )
            return (
                await self.request_handler.on_get_task_push_notification_config(
                    params, context
                )
            )

        response = await self._handle_non_streaming(request, _handler)
        return JSONResponse(content=MessageToDict(response))

    @rest_error_handler
    async def delete_push_notification(self, request: Request) -> Response:
        """Handles the 'tasks/pushNotificationConfig/delete' REST method."""

        @validate_version(constants.PROTOCOL_VERSION_1_0)
        async def _handler(context: ServerCallContext) -> None:
            task_id = request.path_params['id']
            push_id = request.path_params['push_id']
            params = a2a_pb2.DeleteTaskPushNotificationConfigRequest(
                task_id=task_id, id=push_id
            )
            await self.request_handler.on_delete_task_push_notification_config(
                params, context
            )

        await self._handle_non_streaming(request, _handler)
        return JSONResponse(content={})

    @rest_error_handler
    async def set_push_notification(self, request: Request) -> Response:
        """Handles the 'tasks/pushNotificationConfig/set' REST method."""

        @validate_version(constants.PROTOCOL_VERSION_1_0)
        async def _handler(
            context: ServerCallContext,
        ) -> a2a_pb2.TaskPushNotificationConfig:
            body = await request.body()
            params = a2a_pb2.TaskPushNotificationConfig()
            Parse(body, params)
            params.task_id = request.path_params['id']
            return await self.request_handler.on_create_task_push_notification_config(
                params, context
            )

        response = await self._handle_non_streaming(request, _handler)
        return JSONResponse(content=MessageToDict(response))

    @rest_error_handler
    async def list_push_notifications(self, request: Request) -> Response:
        """Handles the 'tasks/pushNotificationConfig/list' REST method."""

        @validate_version(constants.PROTOCOL_VERSION_1_0)
        async def _handler(
            context: ServerCallContext,
        ) -> a2a_pb2.ListTaskPushNotificationConfigsResponse:
            params = a2a_pb2.ListTaskPushNotificationConfigsRequest()
            proto_utils.parse_params(request.query_params, params)
            params.task_id = request.path_params['id']
            return await self.request_handler.on_list_task_push_notification_configs(
                params, context
            )

        response = await self._handle_non_streaming(request, _handler)
        return JSONResponse(content=MessageToDict(response))

    @rest_error_handler
    async def list_tasks(self, request: Request) -> Response:
        """Handles the 'tasks/list' REST method."""

        @validate_version(constants.PROTOCOL_VERSION_1_0)
        async def _handler(
            context: ServerCallContext,
        ) -> a2a_pb2.ListTasksResponse:
            params = a2a_pb2.ListTasksRequest()
            proto_utils.parse_params(request.query_params, params)
            return await self.request_handler.on_list_tasks(params, context)

        response = await self._handle_non_streaming(request, _handler)
        return JSONResponse(
            content=MessageToDict(
                response, always_print_fields_with_no_presence=True
            )
        )

    @rest_error_handler
    async def handle_authenticated_agent_card(
        self, request: Request
    ) -> Response:
        """Handles the 'agentCard' REST method."""

        @validate_version(constants.PROTOCOL_VERSION_1_0)
        async def _handler(
            context: ServerCallContext,
        ) -> a2a_pb2.AgentCard:
            params = a2a_pb2.GetExtendedAgentCardRequest()
            return await self.request_handler.on_get_extended_agent_card(
                params, context
            )

        response = await self._handle_non_streaming(request, _handler)
        return JSONResponse(content=MessageToDict(response))
