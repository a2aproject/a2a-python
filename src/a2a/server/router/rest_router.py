import logging

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI
    from sse_starlette.sse import EventSourceResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response

    _package_fastapi_installed = True
else:
    try:
        from fastapi import APIRouter, FastAPI
        from sse_starlette.sse import EventSourceResponse
        from starlette.exceptions import HTTPException as StarletteHTTPException
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response

        _package_fastapi_installed = True
    except ImportError:
        APIRouter = Any
        FastAPI = Any
        EventSourceResponse = Any
        Request = Any
        JSONResponse = Any
        Response = Any
        StarletteHTTPException = Any

        _package_fastapi_installed = False

import json

from google.protobuf.json_format import MessageToDict, Parse

from a2a.server.apps.jsonrpc import (
    CallContextBuilder,
    DefaultCallContextBuilder,
)
from a2a.server.apps.rest.fastapi_app import _HTTP_TO_GRPC_STATUS_MAP
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.request_handlers.rest_handler_v2 import RESTHandlerV2
from a2a.types import a2a_pb2
from a2a.types.a2a_pb2 import AgentCard
from a2a.utils import proto_utils
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    DEFAULT_RPC_URL,
)
from a2a.utils.errors import (
    ExtendedAgentCardNotConfiguredError,
    InvalidRequestError,
)
from a2a.utils.helpers import maybe_await


logger = logging.getLogger(__name__)


class RestRouter:
    """A FastAPI application implementing the A2A protocol server endpoints.

    Handles incoming JSON-REST requests, routes them to the appropriate
    handler methods, and manages response generation including Server-Sent Events
    (SSE).
    """

    def __init__(  # noqa: PLR0913
        self,
        agent_card: AgentCard,
        http_handler: RequestHandler,
        extended_agent_card: AgentCard | None = None,
        context_builder: CallContextBuilder | None = None,
        card_modifier: Callable[[AgentCard], Awaitable[AgentCard] | AgentCard]
        | None = None,
        extended_card_modifier: Callable[
            [AgentCard, ServerCallContext], Awaitable[AgentCard] | AgentCard
        ]
        | None = None,
        max_content_length: int | None = 10 * 1024 * 1024,  # 10MB
        enable_v0_3_compat: bool = False,
        agent_card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
        rpc_url: str = '',
    ) -> None:
        """Initializes the A2AFastAPIApplication.

        Args:
            agent_card: The AgentCard describing the agent's capabilities.
            http_handler: The handler instance responsible for processing A2A
              requests via http.
            extended_agent_card: An optional, distinct AgentCard to be served
              at the authenticated extended card endpoint.
            context_builder: The CallContextBuilder used to construct the
              ServerCallContext passed to the http_handler. If None, no
              ServerCallContext is passed.
            card_modifier: An optional callback to dynamically modify the public
              agent card before it is served.
            extended_card_modifier: An optional callback to dynamically modify
              the extended agent card before it is served. It receives the
              call context.
            max_content_length: The maximum allowed content length for incoming
              requests. Defaults to 10MB. Set to None for unbounded maximum.
            enable_v0_3_compat: Whether to enable v0.3 backward compatibility on the same endpoint.
        """
        if not _package_fastapi_installed:
            raise ImportError(
                'The `fastapi` package is required to use the `A2AFastAPIApplication`.'
                ' It can be added as a part of `a2a-sdk` optional dependencies,'
                ' `a2a-sdk[http-server]`.'
            )

        self.agent_card = agent_card
        self.http_handler = http_handler
        self.rest_handler = RESTHandlerV2(
            agent_card=agent_card, request_handler=http_handler
        )
        self.extended_agent_card = extended_agent_card
        self._context_builder = context_builder or DefaultCallContextBuilder()
        self.card_modifier = card_modifier
        self.extended_card_modifier = extended_card_modifier
        self.max_content_length = max_content_length
        self.enable_v0_3_compat = enable_v0_3_compat

        self._v03_adapter = None
        if enable_v0_3_compat:
            from a2a.compat.v0_3.rest_adapter import (
                REST03Adapter as V03RESTAdapter,
            )

            self._v03_adapter = V03RESTAdapter(
                agent_card=agent_card,
                http_handler=http_handler,
                extended_agent_card=extended_agent_card,
                context_builder=context_builder,
            )

        self.router = APIRouter()
        self._setup_router(agent_card_url, rpc_url)

    def _build_call_context(self, request: Request) -> ServerCallContext:
        call_context = self._context_builder.build(request)
        if 'tenant' in request.path_params:
            call_context.tenant = request.path_params['tenant']
        return call_context

    def _setup_router(
        self,
        agent_card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
        rpc_url: str = '',
        **kwargs: Any,
    ) -> None:
        """Builds and returns the FastAPI application instance."""

        if self.enable_v0_3_compat and self._v03_adapter:
            v03_router = APIRouter()
            for route, callback in self._v03_adapter.routes().items():
                v03_router.add_api_route(
                    f'{rpc_url}{route[0]}', callback, methods=[route[1]]
                )
            self.router.include_router(v03_router)

        base_routes: dict[tuple[str, str], Callable[[Request], Any]] = {}

        async def message_send(request: Request) -> Response:
            body = await request.body()
            params = a2a_pb2.SendMessageRequest()
            Parse(body, params)
            context = self._build_call_context(request)
            result = await self.rest_handler.on_message_send(
                params, context
            )
            return JSONResponse(result)

        base_routes[('/message:send', 'POST')] = message_send

        async def message_stream(request: Request) -> EventSourceResponse:
            try:
                await request.body()
            except (ValueError, RuntimeError, OSError) as e:
                raise InvalidRequestError(
                    message=f'Failed to pre-consume request body: {e}'
                ) from e

            body = await request.body()
            params = a2a_pb2.SendMessageRequest()
            Parse(body, params)
            context = self._build_call_context(request)

            async def event_generator(
                stream: AsyncIterator[dict[str, Any]],
            ) -> AsyncIterator[str]:
                async for item in stream:
                    yield json.dumps(item)

            return EventSourceResponse(
                event_generator(
                    self.rest_handler.on_message_send_stream(
                        params, context
                    )
                )
            )

        base_routes[('/message:stream', 'POST')] = message_stream

        async def cancel_task(request: Request) -> Response:
            task_id = request.path_params['id']
            params = a2a_pb2.CancelTaskRequest(id=task_id)
            context = self._build_call_context(request)
            result = await self.rest_handler.on_cancel_task(params, context)
            return JSONResponse(result)

        base_routes[('/tasks/{id}:cancel', 'POST')] = cancel_task

        async def subscribe_task(request: Request) -> EventSourceResponse:
            import contextlib  # noqa: PLC0415
            with contextlib.suppress(ValueError, RuntimeError, OSError):
                await request.body()
            task_id = request.path_params['id']
            params = a2a_pb2.SubscribeToTaskRequest(id=task_id)
            context = self._build_call_context(request)

            async def event_generator(
                stream: AsyncIterator[dict[str, Any]],
            ) -> AsyncIterator[str]:
                async for item in stream:
                    yield json.dumps(item)

            return EventSourceResponse(
                event_generator(
                    self.rest_handler.on_subscribe_to_task(params, context)
                )
            )

        base_routes[('/tasks/{id}:subscribe', 'GET')] = subscribe_task
        base_routes[('/tasks/{id}:subscribe', 'POST')] = subscribe_task

        async def get_task(request: Request) -> Response:
            params = a2a_pb2.GetTaskRequest()
            proto_utils.parse_params(request.query_params, params)
            params.id = request.path_params['id']
            context = self._build_call_context(request)
            result = await self.rest_handler.on_get_task(params, context)
            return JSONResponse(result)

        base_routes[('/tasks/{id}', 'GET')] = get_task

        async def get_push_notification(request: Request) -> Response:
            task_id = request.path_params['id']
            push_id = request.path_params['push_id']
            params = a2a_pb2.GetTaskPushNotificationConfigRequest(
                task_id=task_id, id=push_id
            )
            context = self._build_call_context(request)
            result = await self.rest_handler.get_push_notification(
                params, context
            )
            return JSONResponse(result)

        base_routes[('/tasks/{id}/pushNotificationConfigs/{push_id}', 'GET')] = get_push_notification

        async def delete_push_notification(request: Request) -> Response:
            task_id = request.path_params['id']
            push_id = request.path_params['push_id']
            params = a2a_pb2.DeleteTaskPushNotificationConfigRequest(
                task_id=task_id, id=push_id
            )
            context = self._build_call_context(request)
            result = await self.rest_handler.delete_push_notification(
                params, context
            )
            return JSONResponse(result)

        base_routes[('/tasks/{id}/pushNotificationConfigs/{push_id}', 'DELETE')] = delete_push_notification

        async def set_push_notification(request: Request) -> Response:
            body = await request.body()
            params = a2a_pb2.TaskPushNotificationConfig()
            Parse(body, params)
            params.task_id = request.path_params['id']
            context = self._build_call_context(request)
            result = await self.rest_handler.set_push_notification(
                params, context
            )
            return JSONResponse(result)

        base_routes[('/tasks/{id}/pushNotificationConfigs', 'POST')] = set_push_notification

        async def list_push_notifications(request: Request) -> Response:
            params = a2a_pb2.ListTaskPushNotificationConfigsRequest()
            proto_utils.parse_params(request.query_params, params)
            params.task_id = request.path_params['id']
            context = self._build_call_context(request)
            result = await self.rest_handler.list_push_notifications(
                params, context
            )
            return JSONResponse(result)

        base_routes[('/tasks/{id}/pushNotificationConfigs', 'GET')] = list_push_notifications

        async def list_tasks(request: Request) -> Response:
            params = a2a_pb2.ListTasksRequest()
            proto_utils.parse_params(request.query_params, params)
            context = self._build_call_context(request)
            result = await self.rest_handler.list_tasks(params, context)
            return JSONResponse(result)

        base_routes[('/tasks', 'GET')] = list_tasks

        if self.agent_card.capabilities.extended_agent_card:
            async def get_extended_agent_card(request: Request) -> Response:
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
                    card_to_serve = await maybe_await(
                        self.card_modifier(card_to_serve)
                    )

                return JSONResponse(
                    MessageToDict(
                        card_to_serve, preserving_proto_field_name=True
                    )
                )

            base_routes[('/extendedAgentCard', 'GET')] = get_extended_agent_card

        routes: dict[tuple[str, str], Callable[[Request], Any]] = {
            (p, method): handler
            for (path, method), handler in base_routes.items()
            for p in (path, f'/{{tenant}}{path}')
        }

        for (path, method), handler in routes.items():
            self.router.add_api_route(
                f'{rpc_url}{path}', handler, methods=[method]
            )

