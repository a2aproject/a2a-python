import logging

from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any

from a2a.compat.v0_3.rest_adapter import (
    REST03Adapter as V03RESTAdapter,
)


if TYPE_CHECKING:
    from sse_starlette.sse import EventSourceResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.middleware import Middleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    _package_starlette_installed = True
else:
    try:
        from sse_starlette.sse import EventSourceResponse
        from starlette.exceptions import HTTPException as StarletteHTTPException
        from starlette.middleware import Middleware
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Route

        _package_starlette_installed = True
    except ImportError:
        Middleware = Any
        Route = Any
        EventSourceResponse = Any
        Request = Any
        JSONResponse = Any
        Response = Any
        StarletteHTTPException = Any

        _package_starlette_installed = False

import json

from google.protobuf.json_format import MessageToDict, Parse

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes.jsonrpc_dispatcher import (
    CallContextBuilder,
    DefaultCallContextBuilder,
)
from a2a.types import a2a_pb2
from a2a.types.a2a_pb2 import AgentCard
from a2a.utils import proto_utils
from a2a.utils.error_handlers import (
    rest_error_handler,
    rest_stream_error_handler,
)
from a2a.utils.errors import (
    ExtendedAgentCardNotConfiguredError,
    InvalidRequestError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from a2a.utils.helpers import maybe_await


logger = logging.getLogger(__name__)


class RestRoutes:
    """Provides the Starlette Routes for the A2A protocol REST endpoints.

    Handles incoming JSON-REST requests, routes them to the appropriate
    handler methods, and manages response generation including Server-Sent Events
    (SSE).
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
        enable_v0_3_compat: bool = False,
        rpc_url: str = '',
        middleware: Sequence['Middleware'] | None = None,
    ) -> None:
        """Initializes the RestRoutes.

        Args:
            agent_card: The AgentCard describing the agent's capabilities.
            request_handler: The handler instance responsible for processing A2A
              requests via http.
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
            enable_v0_3_compat: Whether to enable v0.3 backward compatibility on the same endpoint.
            rpc_url: The URL prefix for the RPC endpoints.
            middleware: An optional list of Starlette middleware to apply to the routes.
        """
        if not _package_starlette_installed:
            raise ImportError(
                'The `starlette` package is required to use the `RestRoutes`.'
                ' It can be added as a part of `a2a-sdk` optional dependencies,'
                ' `a2a-sdk[http-server]`.'
            )

        self.agent_card = agent_card
        self.request_handler = request_handler
        self.extended_agent_card = extended_agent_card
        self._context_builder = context_builder or DefaultCallContextBuilder()
        self.card_modifier = card_modifier
        self.extended_card_modifier = extended_card_modifier
        self.enable_v0_3_compat = enable_v0_3_compat

        self._v03_adapter = None
        if enable_v0_3_compat:
            self._v03_adapter = V03RESTAdapter(
                agent_card=agent_card,
                http_handler=request_handler,
                extended_agent_card=extended_agent_card,
                context_builder=context_builder,
            )


        self._setup_routes(rpc_url)

    def _build_call_context(self, request: Request) -> ServerCallContext:
        call_context = self._context_builder.build(request)
        if 'tenant' in request.path_params:
            call_context.tenant = request.path_params['tenant']
        return call_context

    def _setup_routes(
        self,
        rpc_url: str,
    ) -> None:
        """Sets up the Starlette routes."""
        self.routes = []
        if self.enable_v0_3_compat and self._v03_adapter:
            for route, callback in self._v03_adapter.routes().items():
                self.routes.append(
                    Route(
                        path=f'{rpc_url}{route[0]}',
                        endpoint=callback,
                        methods=[route[1]],
                    )
                )

        base_routes: dict[tuple[str, str], Callable[[Request], Any]] = {
            ('/message:send', 'POST'): self._message_send,
            ('/message:stream', 'POST'): self._message_stream,
            ('/tasks/{id}:cancel', 'POST'): self._cancel_task,
            ('/tasks/{id}:subscribe', 'GET'): self._subscribe_task,
            ('/tasks/{id}:subscribe', 'POST'): self._subscribe_task,
            ('/tasks/{id}', 'GET'): self._get_task,
            (
                '/tasks/{id}/pushNotificationConfigs/{push_id}',
                'GET',
            ): self._get_push_notification,
            (
                '/tasks/{id}/pushNotificationConfigs/{push_id}',
                'DELETE',
            ): self._delete_push_notification,
            (
                '/tasks/{id}/pushNotificationConfigs',
                'POST',
            ): self._set_push_notification,
            (
                '/tasks/{id}/pushNotificationConfigs',
                'GET',
            ): self._list_push_notifications,
            ('/tasks', 'GET'): self._list_tasks,
            ('/extendedAgentCard', 'GET'): self._get_extended_agent_card,
        }

        self.routes.extend(
            [
                Route(
                    path=f'{rpc_url}{p}',
                    endpoint=handler,
                    methods=[method],
                )
                for (path, method), handler in base_routes.items()
                for p in (path, f'/{{tenant}}{path}')
            ]
        )

    @rest_error_handler
    async def _message_send(self, request: Request) -> Response:
        body = await request.body()
        params = a2a_pb2.SendMessageRequest()
        Parse(body, params)
        context = self._build_call_context(request)
        task_or_message = await self.request_handler.on_message_send(
            params, context
        )
        if isinstance(task_or_message, a2a_pb2.Task):
            response = a2a_pb2.SendMessageResponse(task=task_or_message)
        else:
            response = a2a_pb2.SendMessageResponse(message=task_or_message)
        return JSONResponse(MessageToDict(response))

    @rest_stream_error_handler
    async def _message_stream(self, request: Request) -> EventSourceResponse:
        try:
            await request.body()
        except (ValueError, RuntimeError, OSError) as e:
            raise InvalidRequestError(
                message=f'Failed to pre-consume request body: {e}'
            ) from e

        if not self.agent_card.capabilities.streaming:
            raise UnsupportedOperationError(
                message='Streaming is not supported by the agent'
            )

        body = await request.body()
        params = a2a_pb2.SendMessageRequest()
        Parse(body, params)
        context = self._build_call_context(request)

        async def event_generator() -> AsyncIterator[str]:
            async for event in self.request_handler.on_message_send_stream(
                params, context
            ):
                yield json.dumps(
                    MessageToDict(proto_utils.to_stream_response(event))
                )

        return EventSourceResponse(event_generator())

    @rest_error_handler
    async def _cancel_task(self, request: Request) -> Response:
        task_id = request.path_params['id']
        params = a2a_pb2.CancelTaskRequest(id=task_id)
        context = self._build_call_context(request)
        task = await self.request_handler.on_cancel_task(params, context)
        if not task:
            raise TaskNotFoundError
        return JSONResponse(MessageToDict(task))

    @rest_stream_error_handler
    async def _subscribe_task(self, request: Request) -> EventSourceResponse:
        try:
            await request.body()
        except (ValueError, RuntimeError, OSError) as e:
            raise InvalidRequestError(
                message=f'Failed to pre-consume request body: {e}'
            ) from e
        task_id = request.path_params['id']
        if not self.agent_card.capabilities.streaming:
            raise UnsupportedOperationError(
                message='Streaming is not supported by the agent'
            )
        params = a2a_pb2.SubscribeToTaskRequest(id=task_id)
        context = self._build_call_context(request)

        async def event_generator() -> AsyncIterator[str]:
            async for event in self.request_handler.on_subscribe_to_task(
                params, context
            ):
                yield json.dumps(
                    MessageToDict(proto_utils.to_stream_response(event))
                )

        return EventSourceResponse(event_generator())

    @rest_error_handler
    async def _get_task(self, request: Request) -> Response:
        params = a2a_pb2.GetTaskRequest()
        proto_utils.parse_params(request.query_params, params)
        params.id = request.path_params['id']
        context = self._build_call_context(request)
        task = await self.request_handler.on_get_task(params, context)
        if not task:
            raise TaskNotFoundError
        return JSONResponse(MessageToDict(task))

    @rest_error_handler
    async def _get_push_notification(self, request: Request) -> Response:
        task_id = request.path_params['id']
        push_id = request.path_params['push_id']
        params = a2a_pb2.GetTaskPushNotificationConfigRequest(
            task_id=task_id, id=push_id
        )
        context = self._build_call_context(request)
        config = (
            await self.request_handler.on_get_task_push_notification_config(
                params, context
            )
        )
        return JSONResponse(MessageToDict(config))

    @rest_error_handler
    async def _delete_push_notification(self, request: Request) -> Response:
        task_id = request.path_params['id']
        push_id = request.path_params['push_id']
        params = a2a_pb2.DeleteTaskPushNotificationConfigRequest(
            task_id=task_id, id=push_id
        )
        context = self._build_call_context(request)
        await self.request_handler.on_delete_task_push_notification_config(
            params, context
        )
        return JSONResponse({})

    @rest_error_handler
    async def _set_push_notification(self, request: Request) -> Response:
        if not self.agent_card.capabilities.push_notifications:
            raise UnsupportedOperationError(
                message='Push notifications are not supported by the agent'
            )
        body = await request.body()
        params = a2a_pb2.TaskPushNotificationConfig()
        Parse(body, params)
        params.task_id = request.path_params['id']
        context = self._build_call_context(request)
        config = (
            await self.request_handler.on_create_task_push_notification_config(
                params, context
            )
        )
        return JSONResponse(MessageToDict(config))

    @rest_error_handler
    async def _list_push_notifications(self, request: Request) -> Response:
        params = a2a_pb2.ListTaskPushNotificationConfigsRequest()
        proto_utils.parse_params(request.query_params, params)
        params.task_id = request.path_params['id']
        context = self._build_call_context(request)
        result = (
            await self.request_handler.on_list_task_push_notification_configs(
                params, context
            )
        )
        return JSONResponse(MessageToDict(result))

    @rest_error_handler
    async def _list_tasks(self, request: Request) -> Response:
        params = a2a_pb2.ListTasksRequest()
        proto_utils.parse_params(request.query_params, params)
        context = self._build_call_context(request)
        result = await self.request_handler.on_list_tasks(params, context)
        return JSONResponse(
            MessageToDict(result, always_print_fields_with_no_presence=True)
        )

    @rest_error_handler
    async def _get_extended_agent_card(self, request: Request) -> Response:
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
            MessageToDict(card_to_serve, preserving_proto_field_name=True)
        )
