import functools
import logging

from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from typing import Any

from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from a2a.server.apps.jsonrpc import (
    CallContextBuilder,
    DefaultCallContextBuilder,
)
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.request_handlers.rest_handler import RESTHandler
from a2a.types import AgentCard, AuthenticatedExtendedCardNotConfiguredError
from a2a.utils.error_handlers import (
    rest_error_handler,
    rest_stream_error_handler,
)
from a2a.utils.errors import ServerError


logger = logging.getLogger(__name__)


class RESTAdapter:
    """Adapter to make RequestHandler work with RESTful API.

    Defines REST requests processors and the routes to attach them too, as well as
    manages response generation including Server-Sent Events (SSE).
    """

    def __init__(
        self,
        agent_card: AgentCard,
        http_handler: RequestHandler,
        context_builder: CallContextBuilder | None = None,
    ):
        """Initializes the RESTApplication.

        Args:
            agent_card: The AgentCard describing the agent's capabilities.
            http_handler: The handler instance responsible for processing A2A
              requests via http.
            context_builder: The CallContextBuilder used to construct the
              ServerCallContext passed to the http_handler. If None, no
              ServerCallContext is passed.
        """
        self.agent_card = agent_card
        self.handler = RESTHandler(
            agent_card=agent_card, request_handler=http_handler
        )
        self._context_builder = context_builder or DefaultCallContextBuilder()

    @rest_error_handler
    async def _handle_request(
        self,
        method: Callable[[Request, ServerCallContext], Awaitable[Any]],
        request: Request,
    ) -> Response:
        call_context = self._context_builder.build(request)
        response = await method(request, call_context)
        return JSONResponse(content=response)

    @rest_stream_error_handler
    async def _handle_streaming_request(
        self,
        method: Callable[[Request, ServerCallContext], AsyncIterable[Any]],
        request: Request,
    ) -> EventSourceResponse:
        call_context = self._context_builder.build(request)

        async def event_generator(
            stream: AsyncIterable[Any],
        ) -> AsyncIterator[dict[str, dict[str, Any]]]:
            async for item in stream:
                yield {'data': item}

        return EventSourceResponse(
            event_generator(method(request, call_context))
        )

    @rest_error_handler
    async def handle_get_agent_card(self, request: Request) -> JSONResponse:
        """Handles GET requests for the agent card endpoint.

        Args:
            request: The incoming Starlette Request object.

        Returns:
            A JSONResponse containing the agent card data.
        """
        # The public agent card is a direct serialization of the agent_card
        # provided at initialization.
        return JSONResponse(
            self.agent_card.model_dump(mode='json', exclude_none=True)
        )

    @rest_error_handler
    async def handle_authenticated_agent_card(
        self, request: Request
    ) -> JSONResponse:
        """Hook for per credential agent card response.

        If a dynamic card is needed based on the credentials provided in the request
        override this method and return the customized content.

        Args:
            request: The incoming Starlette Request  object.

        Returns:
            A JSONResponse containing the authenticated card.
        """
        if not self.agent_card.supports_authenticated_extended_card:
            raise ServerError(
                error=AuthenticatedExtendedCardNotConfiguredError(
                    message='Authenticated card not supported'
                )
            )
        return JSONResponse(
            self.agent_card.model_dump(mode='json', exclude_none=True)
        )

    def routes(self) -> dict[tuple[str, str], Callable[[Request], Any]]:
        """Constructs a dictionary of API routes and their corresponding handlers.

        This method maps URL paths and HTTP methods to the appropriate handler
        functions from the RESTHandler. It can be used by a web framework
        (like Starlette or FastAPI) to set up the application's endpoints.

        Returns:
            A dictionary where each key is a tuple of (path, http_method) and
            the value is the callable handler for that route.
        """
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
                self.handler.on_resubscribe_to_task,
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
        }
        if self.agent_card.supports_authenticated_extended_card:
            routes[('/v1/card', 'GET')] = self.handle_authenticated_agent_card

        return routes
