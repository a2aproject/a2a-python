import functools
import json
import logging
import traceback

from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from typing import Any

from pydantic import ValidationError
from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request
from starlette.responses import JSONResponse

from a2a.server.apps.jsonrpc import (
    CallContextBuilder,
    DefaultCallContextBuilder,
)
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.request_handlers.rest_handler import RESTHandler
from a2a.types import (
    A2AError,
    AgentCard,
    InternalError,
    InvalidRequestError,
    JSONParseError,
    UnsupportedOperationError,
)
from a2a.utils.errors import MethodNotImplementedError


logger = logging.getLogger(__name__)


class RESTApplication:
    """Base class for A2A REST applications.

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

    def _generate_error_response(self, error: A2AError) -> JSONResponse:
        """Creates a JSONResponse for an error.

        Logs the error based on its type.

        Args:
            error: The Error object.

        Returns:
            A `JSONResponse` object formatted as a JSON error response.
        """
        log_level = (
            logging.ERROR
            if isinstance(error, InternalError)
            else logging.WARNING
        )
        logger.log(
            log_level,
            'Request Error: '
            f"Code={error.root.code}, Message='{error.root.message}'"
            f'{", Data=" + str(error.root.data) if error.root.data else ""}',
        )
        return JSONResponse(
            f'{{"message": "{error.root.message}"}}',
            status_code=500,
        )

    def _handle_error(self, error: Exception) -> JSONResponse:
        traceback.print_exc()
        if isinstance(error, MethodNotImplementedError):
            return self._generate_error_response(
                A2AError(UnsupportedOperationError(message=error.message))
            )
        if isinstance(error, json.decoder.JSONDecodeError):
            return self._generate_error_response(
                A2AError(JSONParseError(message=str(error)))
            )
        if isinstance(error, ValidationError):
            return self._generate_error_response(
                A2AError(InvalidRequestError(data=json.loads(error.json()))),
            )
        logger.error(f'Unhandled exception: {error}')
        return self._generate_error_response(
            A2AError(InternalError(message=str(error)))
        )

    async def _handle_request(
        self,
        method: Callable[[Request, ServerCallContext], Awaitable[str]],
        request: Request,
    ) -> JSONResponse:
        try:
            call_context = self._context_builder.build(request)
            response = await method(request, call_context)
            return JSONResponse(content=response)
        except Exception as e:
            return self._handle_error(e)

    async def _handle_streaming_request(
        self,
        method: Callable[[Request, ServerCallContext], AsyncIterator[str]],
        request: Request,
    ) -> EventSourceResponse:
        try:
            call_context = self._context_builder.build(request)

            async def event_generator(
                stream: AsyncGenerator[str],
            ) -> AsyncGenerator[dict[str, str]]:
                async for item in stream:
                    yield {'data': item}

            return EventSourceResponse(
                event_generator(method(request, call_context))
            )
        except Exception as e:
            # Since the stream has started, we can't return a JSONResponse.
            # Instead, we runt the error handling logic (provides logging)
            # and reraise the error and let server framework manage
            self._handle_error(e)
            raise e

    async def _handle_get_agent_card(self, request: Request) -> JSONResponse:
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
            return JSONResponse(
                '{"detail": "Authenticated card not supported"}',
                status_code=404,
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
        routes = {
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
