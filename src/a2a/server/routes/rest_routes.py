import functools
import json
import logging

from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

from google.protobuf.json_format import MessageToDict

from a2a.compat.v0_3.rest_adapter import REST03Adapter
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.request_handlers.rest_handler import RESTHandler
from a2a.server.routes import CallContextBuilder, DefaultCallContextBuilder
from a2a.types.a2a_pb2 import AgentCard
from a2a.utils.error_handlers import (
    rest_error_handler,
    rest_stream_error_handler,
)
from a2a.utils.errors import (
    ExtendedAgentCardNotConfiguredError,
    InvalidRequestError,
)
from a2a.utils.helpers import maybe_await


if TYPE_CHECKING:
    from sse_starlette.sse import EventSourceResponse
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import BaseRoute, Route

    _package_starlette_installed = True
else:
    try:
        from sse_starlette.sse import EventSourceResponse
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        from starlette.routing import BaseRoute, Mount, Route

        _package_starlette_installed = True
    except ImportError:
        EventSourceResponse = Any
        Request = Any
        JSONResponse = Any
        Response = Any
        Route = Any
        Mount = Any
        BaseRoute = Any

        _package_starlette_installed = False

logger = logging.getLogger(__name__)


def create_rest_routes(  # noqa: PLR0913
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
    path_prefix: str = '',
) -> list['BaseRoute']:
    """Creates the Starlette Routes for the A2A protocol REST endpoint.

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
        enable_v0_3_compat: If True, mounts backward-compatible v0.3 protocol
          endpoints using REST03Adapter.
        path_prefix: The URL prefix for the REST endpoints.
    """
    if not _package_starlette_installed:
        raise ImportError(
            'Packages `starlette` and `sse-starlette` are required to use'
            ' the `create_rest_routes`. They can be added as a part of `a2a-sdk` '
            'optional dependencies, `a2a-sdk[http-server]`.'
        )

    v03_routes = {}
    if enable_v0_3_compat:
        v03_adapter = REST03Adapter(
            agent_card=agent_card,
            http_handler=request_handler,
            extended_agent_card=extended_agent_card,
            context_builder=context_builder,
            card_modifier=card_modifier,
            extended_card_modifier=extended_card_modifier,
        )
        v03_routes = v03_adapter.routes()

    routes: list['BaseRoute'] = []
    for (path, method), endpoint in v03_routes.items():
        routes.append(
            Route(
                path=f'{path_prefix}{path}',
                endpoint=endpoint,
                methods=[method],
            )
        )

    handler = RESTHandler(
        agent_card=agent_card, request_handler=request_handler
    )
    _context_builder = context_builder or DefaultCallContextBuilder()

    def _build_call_context(request: 'Request') -> ServerCallContext:
        call_context = _context_builder.build(request)
        if 'tenant' in request.path_params:
            call_context.tenant = request.path_params['tenant']
        return call_context

    @rest_error_handler
    async def _handle_request(
        method: Callable[['Request', ServerCallContext], Awaitable[Any]],
        request: 'Request',
    ) -> 'Response':

        call_context = _build_call_context(request)
        response = await method(request, call_context)
        return JSONResponse(content=response)

    @rest_stream_error_handler
    async def _handle_streaming_request(
        method: Callable[['Request', ServerCallContext], AsyncIterable[Any]],
        request: 'Request',
    ) -> 'EventSourceResponse':
        try:
            await request.body()
        except (ValueError, RuntimeError, OSError) as e:
            raise InvalidRequestError(
                message=f'Failed to pre-consume request body: {e}'
            ) from e

        call_context = _build_call_context(request)

        async def event_generator(
            stream: AsyncIterable[Any],
        ) -> AsyncIterator[str]:
            async for item in stream:
                yield json.dumps(item)

        return EventSourceResponse(
            event_generator(method(request, call_context))
        )

    async def _handle_authenticated_agent_card(
        request: 'Request', call_context: ServerCallContext | None = None
    ) -> dict[str, Any]:
        if not agent_card.capabilities.extended_agent_card:
            raise ExtendedAgentCardNotConfiguredError(
                message='Authenticated card not supported'
            )
        card_to_serve = extended_agent_card or agent_card

        if extended_card_modifier:
            # Re-generate context if none passed to replicate RESTAdapter exact logic
            context = call_context or _build_call_context(request)
            card_to_serve = await maybe_await(
                extended_card_modifier(card_to_serve, context)
            )
        elif card_modifier:
            card_to_serve = await maybe_await(card_modifier(card_to_serve))

        return MessageToDict(card_to_serve, preserving_proto_field_name=True)

    # Dictionary of routes, mapping to bound helper methods
    base_routes: dict[tuple[str, str], Callable[[Request], Any]] = {
        ('/message:send', 'POST'): functools.partial(
            _handle_request, handler.on_message_send
        ),
        ('/message:stream', 'POST'): functools.partial(
            _handle_streaming_request,
            handler.on_message_send_stream,
        ),
        ('/tasks/{id}:cancel', 'POST'): functools.partial(
            _handle_request, handler.on_cancel_task
        ),
        ('/tasks/{id}:subscribe', 'GET'): functools.partial(
            _handle_streaming_request,
            handler.on_subscribe_to_task,
        ),
        ('/tasks/{id}:subscribe', 'POST'): functools.partial(
            _handle_streaming_request,
            handler.on_subscribe_to_task,
        ),
        ('/tasks/{id}', 'GET'): functools.partial(
            _handle_request, handler.on_get_task
        ),
        (
            '/tasks/{id}/pushNotificationConfigs/{push_id}',
            'GET',
        ): functools.partial(_handle_request, handler.get_push_notification),
        (
            '/tasks/{id}/pushNotificationConfigs/{push_id}',
            'DELETE',
        ): functools.partial(_handle_request, handler.delete_push_notification),
        ('/tasks/{id}/pushNotificationConfigs', 'POST'): functools.partial(
            _handle_request, handler.set_push_notification
        ),
        ('/tasks/{id}/pushNotificationConfigs', 'GET'): functools.partial(
            _handle_request, handler.list_push_notifications
        ),
        ('/tasks', 'GET'): functools.partial(
            _handle_request, handler.list_tasks
        ),
        ('/extendedAgentCard', 'GET'): functools.partial(
            _handle_request, _handle_authenticated_agent_card
        ),
    }

    base_route_objects = []
    for (path, method), endpoint in base_routes.items():
        base_route_objects.append(
            Route(
                path=f'{path_prefix}{path}',
                endpoint=endpoint,
                methods=[method],
            )
        )
    routes.extend(base_route_objects)
    routes.append(Mount(path='/{tenant}', routes=base_route_objects))

    return routes
