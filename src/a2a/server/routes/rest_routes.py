import functools
import json
import logging

from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

from a2a.compat.v0_3.rest_adapter import REST03Adapter
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.request_handlers.rest_handler import RESTHandler
from a2a.server.routes import CallContextBuilder, DefaultCallContextBuilder
from a2a.utils.error_handlers import (
    rest_error_handler,
    rest_stream_error_handler,
)
from a2a.utils.errors import (
    InvalidRequestError,
)


if TYPE_CHECKING:
    from sse_starlette.sse import EventSourceResponse
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import BaseRoute, Mount, Route

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


def create_rest_routes(
    request_handler: RequestHandler,
    context_builder: CallContextBuilder | None = None,
    enable_v0_3_compat: bool = False,
    path_prefix: str = '',
) -> list['BaseRoute']:
    """Creates the Starlette Routes for the A2A protocol REST endpoint.

    Args:
        request_handler: The handler instance responsible for processing A2A
          requests via http.
        context_builder: The CallContextBuilder used to construct the
          ServerCallContext passed to the request_handler. If None the
          DefaultCallContextBuilder is used.
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
            agent_card=request_handler.agent_card,
            http_handler=request_handler,
            extended_agent_card=getattr(
                request_handler, 'extended_agent_card', None
            ),
            context_builder=context_builder,
            card_modifier=getattr(request_handler, 'card_modifier', None),
            extended_card_modifier=getattr(
                request_handler, 'extended_card_modifier', None
            ),
        )
        v03_routes = v03_adapter.routes()

    routes: list[BaseRoute] = []
    for (path, method), endpoint in v03_routes.items():
        routes.append(
            Route(
                path=f'{path_prefix}{path}',
                endpoint=endpoint,
                methods=[method],
            )
        )

    handler = RESTHandler(request_handler=request_handler)
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
        method: Callable[[Request, ServerCallContext], AsyncIterable[Any]],
        request: Request,
    ) -> EventSourceResponse:
        # Pre-consume and cache the request body to prevent deadlock in streaming context
        # This is required because Starlette's request.body() can only be consumed once,
        # and attempting to consume it after EventSourceResponse starts causes deadlock
        try:
            await request.body()
        except (ValueError, RuntimeError, OSError) as e:
            raise InvalidRequestError(
                message=f'Failed to pre-consume request body: {e}'
            ) from e

        call_context = _build_call_context(request)

        # Eagerly fetch the first item from the stream so that errors raised
        # before any event is yielded (e.g. validation, parsing, or handler
        # failures) propagate here and are caught by
        # @rest_stream_error_handler, which returns a JSONResponse with
        # the correct HTTP status code instead of starting an SSE stream.
        # Without this, the error would be raised after SSE headers are
        # already sent, and the client would see a broken stream instead
        # of a proper error response.
        stream = aiter(method(request, call_context))
        try:
            first_item = await anext(stream)
        except StopAsyncIteration:
            return EventSourceResponse(iter([]))

        async def event_generator() -> AsyncIterator[str]:
            yield json.dumps(first_item)
            async for item in stream:
                yield json.dumps(item)

        return EventSourceResponse(event_generator())

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
            _handle_request, handler.get_extended_agent_card
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
