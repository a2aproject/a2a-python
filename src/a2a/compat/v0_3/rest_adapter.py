import functools
import json
import logging

from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from sse_starlette.sse import EventSourceResponse
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response

    from a2a.server.context import ServerCallContext
    from a2a.server.request_handlers.request_handler import RequestHandler

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


from a2a.compat.v0_3.context_builders import V03ServerCallContextBuilder
from a2a.compat.v0_3.rest_handler import REST03Handler
from a2a.server.routes.common import (
    DefaultServerCallContextBuilder,
    ServerCallContextBuilder,
)
from a2a.utils.error_handlers import (
    rest_error_handler,
    rest_stream_error_handler,
)
from a2a.utils.errors import (
    InvalidRequestError,
)


logger = logging.getLogger(__name__)


class REST03Adapter:
    """Adapter to make RequestHandler work with v0.3 RESTful API.

    Defines v0.3 REST request processors and their routes, as well as managing response generation including Server-Sent Events (SSE).
    """

    def __init__(
        self,
        http_handler: 'RequestHandler',
        context_builder: 'ServerCallContextBuilder | None' = None,
    ):
        self.handler = REST03Handler(request_handler=http_handler)
        self._context_builder = V03ServerCallContextBuilder(
            context_builder or DefaultServerCallContextBuilder()
        )

    @rest_error_handler
    async def _handle_request(
        self,
        method: 'Callable[[Request, ServerCallContext], Awaitable[Any]]',
        request: Request,
    ) -> Response:
        call_context = self._context_builder.build(request)
        response = await method(request, call_context)
        return JSONResponse(content=response)

    @rest_stream_error_handler
    async def _handle_streaming_request(
        self,
        method: 'Callable[[Request, ServerCallContext], AsyncIterable[Any]]',
        request: Request,
    ) -> EventSourceResponse:
        try:
            await request.body()
        except (ValueError, RuntimeError, OSError) as e:
            raise InvalidRequestError(
                message=f'Failed to pre-consume request body: {e}'
            ) from e

        call_context = self._context_builder.build(request)

        async def event_generator(
            stream: AsyncIterable[Any],
        ) -> AsyncIterator[str]:
            async for item in stream:
                yield json.dumps(item)

        return EventSourceResponse(
            event_generator(method(request, call_context))
        )

    def routes(self) -> dict[tuple[str, str], Callable[[Request], Any]]:
        """Constructs a dictionary of API routes and their corresponding handlers."""
        routes: dict[tuple[str, str], Callable[[Request], Any]] = {
            ('/v1/message:send', 'POST'): functools.partial(
                self._handle_request, self.handler.on_message_send
            ),
            ('/v1/message:stream', 'POST'): functools.partial(
                self._handle_streaming_request,
                self.handler.on_message_send_stream,
            ),
            ('/v1/tasks/{id}:cancel', 'POST'): functools.partial(
                self._handle_request, self.handler.on_cancel_task
            ),
            ('/v1/tasks/{id}:subscribe', 'GET'): functools.partial(
                self._handle_streaming_request,
                self.handler.on_subscribe_to_task,
            ),
            ('/v1/tasks/{id}:subscribe', 'POST'): functools.partial(
                self._handle_streaming_request,
                self.handler.on_subscribe_to_task,
            ),
            ('/v1/tasks/{id}', 'GET'): functools.partial(
                self._handle_request, self.handler.on_get_task
            ),
            (
                '/v1/tasks/{id}/pushNotificationConfigs/{push_id}',
                'GET',
            ): functools.partial(
                self._handle_request, self.handler.get_push_notification
            ),
            (
                '/v1/tasks/{id}/pushNotificationConfigs',
                'POST',
            ): functools.partial(
                self._handle_request, self.handler.set_push_notification
            ),
            (
                '/v1/tasks/{id}/pushNotificationConfigs',
                'GET',
            ): functools.partial(
                self._handle_request, self.handler.list_push_notifications
            ),
            ('/v1/tasks', 'GET'): functools.partial(
                self._handle_request, self.handler.list_tasks
            ),
            ('/v1/card', 'GET'): functools.partial(
                self._handle_request, self.handler.on_get_extended_agent_card
            ),
        }

        return routes
