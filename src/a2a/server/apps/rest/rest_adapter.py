import functools
import json
import logging

from abc import ABC, abstractmethod
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

from google.protobuf.json_format import MessageToDict

from a2a.utils.helpers import maybe_await


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

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.request_handlers.response_helpers import (
    agent_card_to_dict,
)
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


logger = logging.getLogger(__name__)


class RESTAdapterInterface(ABC):
    """Interface for RESTAdapter."""

    @abstractmethod
    async def handle_get_agent_card(
        self, request: 'Request', call_context: ServerCallContext | None = None
    ) -> dict[str, Any]:
        """Handles GET requests for the agent card endpoint."""

    @abstractmethod
    def routes(self) -> dict[tuple[str, str], Callable[['Request'], Any]]:
        """Constructs a dictionary of API routes and their corresponding handlers."""


class RESTAdapter(RESTAdapterInterface):
    """Adapter to make RequestHandler work with RESTful API.

    Defines REST requests processors and the routes to attach them too, as well as
    manages response generation including Server-Sent Events (SSE).
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
    ):
        """Initializes the RESTApplication.

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
        """
        if not _package_starlette_installed:
            raise ImportError(
                'Packages `starlette` and `sse-starlette` are required to use'
                ' the `RESTAdapter`. They can be added as a part of `a2a-sdk`'
                ' optional dependencies, `a2a-sdk[http-server]`.'
            )
        self.agent_card = agent_card
        self.extended_agent_card = extended_agent_card
        self.card_modifier = card_modifier
        self.extended_card_modifier = extended_card_modifier
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
        call_context = self._build_call_context(request)

        response = await method(request, call_context)
        return JSONResponse(content=response)

    @rest_stream_error_handler
    async def _handle_streaming_request(
        self,
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

        call_context = self._build_call_context(request)

        async def event_generator(
            stream: AsyncIterable[Any],
        ) -> AsyncIterator[str]:
            async for item in stream:
                yield json.dumps(item)

        return EventSourceResponse(
            event_generator(method(request, call_context))
        )

    async def handle_get_agent_card(
        self, request: Request, call_context: ServerCallContext | None = None
    ) -> dict[str, Any]:
        """Handles GET requests for the agent card endpoint.

        Args:
            request: The incoming Starlette Request object.
            call_context: ServerCallContext

        Returns:
            A JSONResponse containing the agent card data.
        """
        card_to_serve = self.agent_card
        if self.card_modifier:
            card_to_serve = await maybe_await(self.card_modifier(card_to_serve))

        return agent_card_to_dict(card_to_serve)

    async def _handle_authenticated_agent_card(
        self, request: Request, call_context: ServerCallContext | None = None
    ) -> dict[str, Any]:
        """Hook for per credential agent card response.

        If a dynamic card is needed based on the credentials provided in the request
        override this method and return the customized content.

        Args:
            request: The incoming Starlette Request  object.
            call_context: ServerCallContext

        Returns:
            A JSONResponse containing the authenticated card.
        """
        if not self.agent_card.capabilities.extended_agent_card:
            raise ExtendedAgentCardNotConfiguredError(
                message='Authenticated card not supported'
            )
        card_to_serve = self.extended_agent_card

        if not card_to_serve:
            card_to_serve = self.agent_card

        if self.extended_card_modifier:
            context = self._build_call_context(request)
            card_to_serve = await maybe_await(
                self.extended_card_modifier(card_to_serve, context)
            )
        elif self.card_modifier:
            card_to_serve = await maybe_await(self.card_modifier(card_to_serve))

        return MessageToDict(card_to_serve, preserving_proto_field_name=True)

    def routes(self) -> dict[tuple[str, str], Callable[[Request], Any]]:
        """Constructs a dictionary of API routes and their corresponding handlers.

        This method maps URL paths and HTTP methods to the appropriate handler
        functions from the RESTHandler. It can be used by a web framework
        (like Starlette or FastAPI) to set up the application's endpoints.

        Returns:
            A dictionary where each key is a tuple of (path, http_method) and
            the value is the callable handler for that route.
        """
        base_routes: dict[tuple[str, str], Callable[[Request], Any]] = {
            ('/message:send', 'POST'): functools.partial(
                self._handle_request, self.handler.on_message_send
            ),
            ('/message:stream', 'POST'): functools.partial(
                self._handle_streaming_request,
                self.handler.on_message_send_stream,
            ),
            ('/tasks/{id}:cancel', 'POST'): functools.partial(
                self._handle_request, self.handler.on_cancel_task
            ),
            ('/tasks/{id}:subscribe', 'GET'): functools.partial(
                self._handle_streaming_request,
                self.handler.on_subscribe_to_task,
            ),
            ('/tasks/{id}:subscribe', 'POST'): functools.partial(
                self._handle_streaming_request,
                self.handler.on_subscribe_to_task,
            ),
            ('/tasks/{id}', 'GET'): functools.partial(
                self._handle_request, self.handler.on_get_task
            ),
            (
                '/tasks/{id}/pushNotificationConfigs/{push_id}',
                'GET',
            ): functools.partial(
                self._handle_request, self.handler.get_push_notification
            ),
            (
                '/tasks/{id}/pushNotificationConfigs/{push_id}',
                'DELETE',
            ): functools.partial(
                self._handle_request, self.handler.delete_push_notification
            ),
            (
                '/tasks/{id}/pushNotificationConfigs',
                'POST',
            ): functools.partial(
                self._handle_request, self.handler.set_push_notification
            ),
            (
                '/tasks/{id}/pushNotificationConfigs',
                'GET',
            ): functools.partial(
                self._handle_request, self.handler.list_push_notifications
            ),
            ('/tasks', 'GET'): functools.partial(
                self._handle_request, self.handler.list_tasks
            ),
        }

        if self.agent_card.capabilities.extended_agent_card:
            base_routes[('/extendedAgentCard', 'GET')] = functools.partial(
                self._handle_request, self._handle_authenticated_agent_card
            )

        routes: dict[tuple[str, str], Callable[[Request], Any]] = {
            (p, method): handler
            for (path, method), handler in base_routes.items()
            for p in (path, f'/{{tenant}}{path}')
        }

        return routes

    def _build_call_context(self, request: Request) -> ServerCallContext:
        call_context = self._context_builder.build(request)
        if 'tenant' in request.path_params:
            call_context.tenant = request.path_params['tenant']
        return call_context
