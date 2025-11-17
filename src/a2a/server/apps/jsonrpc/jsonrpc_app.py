import contextlib
import json
import logging
import traceback

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator, Callable
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from a2a.auth.user import UnauthenticatedUser
from a2a.auth.user import User as A2AUser
from a2a.extensions.common import (
    HTTP_EXTENSION_HEADER,
    get_requested_extensions,
)
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.jsonrpc_handler import JSONRPCHandler
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import (
    A2AError,
    A2ARequest,
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetAuthenticatedExtendedCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    JSONParseError,
    JSONRPCError,
    JSONRPCErrorResponse,
    JSONRPCRequest,
    JSONRPCResponse,
    ListTaskPushNotificationConfigRequest,
    MethodNotFoundError,
    SendMessageRequest,
    SendStreamingMessageRequest,
    SendStreamingMessageResponse,
    SetTaskPushNotificationConfigRequest,
    TaskResubscriptionRequest,
    UnsupportedOperationError,
)
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    DEFAULT_RPC_URL,
    EXTENDED_AGENT_CARD_PATH,
    PREV_AGENT_CARD_WELL_KNOWN_PATH,
)
from a2a.utils.errors import MethodNotImplementedError


logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sse_starlette.sse import EventSourceResponse
    from starlette.applications import Starlette
    from starlette.authentication import BaseUser
    from starlette.exceptions import HTTPException
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.status import HTTP_413_REQUEST_ENTITY_TOO_LARGE

    _package_starlette_installed = True
else:
    FastAPI = Any
    try:
        from sse_starlette.sse import EventSourceResponse
        from starlette.applications import Starlette
        from starlette.authentication import BaseUser
        from starlette.exceptions import HTTPException
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        from starlette.status import HTTP_413_REQUEST_ENTITY_TOO_LARGE

        _package_starlette_installed = True
    except ImportError:
        _package_starlette_installed = False
        # Provide placeholder types for runtime type hinting when dependencies are not installed.
        # These will not be used if the code path that needs them is guarded by _http_server_installed.
        EventSourceResponse = Any
        Starlette = Any
        BaseUser = Any
        HTTPException = Any
        Request = Any
        JSONResponse = Any
        Response = Any
        HTTP_413_REQUEST_ENTITY_TOO_LARGE = Any


class StarletteUserProxy(A2AUser):
    """Adapts the Starlette User class to the A2A user representation."""

    def __init__(self, user: BaseUser):
        self._user = user

    @property
    def is_authenticated(self) -> bool:
        """Returns whether the current user is authenticated."""
        return self._user.is_authenticated

    @property
    def user_name(self) -> str:
        """Returns the user name of the current user."""
        return self._user.display_name


class CallContextBuilder(ABC):
    """A class for building ServerCallContexts using the Starlette Request."""

    @abstractmethod
    def build(self, request: Request) -> ServerCallContext:
        """Builds a ServerCallContext from a Starlette Request."""


class DefaultCallContextBuilder(CallContextBuilder):
    """A default implementation of CallContextBuilder."""

    def build(self, request: Request) -> ServerCallContext:
        """Builds a ServerCallContext from a Starlette Request.

        Args:
            request: The incoming Starlette Request object.

        Returns:
            A ServerCallContext instance populated with user and state
            information from the request.
        """
        user: A2AUser = UnauthenticatedUser()
        state = {}
        with contextlib.suppress(Exception):
            user = StarletteUserProxy(request.user)
            state['auth'] = request.auth
        state['headers'] = dict(request.headers)
        return ServerCallContext(
            user=user,
            state=state,
            requested_extensions=get_requested_extensions(
                request.headers.getlist(HTTP_EXTENSION_HEADER)
            ),
        )


class JSONRPCApplication(ABC):
    """Base class for A2A JSONRPC applications.

    Handles incoming JSON-RPC requests, routes them to the appropriate
    handler methods, and manages response generation including Server-Sent Events
    (SSE).
    """

    # Method-to-model mapping for centralized routing
    A2ARequestModel = (
        SendMessageRequest
        | SendStreamingMessageRequest
        | GetTaskRequest
        | CancelTaskRequest
        | SetTaskPushNotificationConfigRequest
        | GetTaskPushNotificationConfigRequest
        | ListTaskPushNotificationConfigRequest
        | DeleteTaskPushNotificationConfigRequest
        | TaskResubscriptionRequest
        | GetAuthenticatedExtendedCardRequest
    )

    METHOD_TO_MODEL: dict[str, type[A2ARequestModel]] = {
        model.model_fields['method'].default: model
        for model in A2ARequestModel.__args__
    }

    def __init__(  # noqa: PLR0913
        self,
        agent_card: AgentCard,
        http_handler: RequestHandler,
        extended_agent_card: AgentCard | None = None,
        context_builder: CallContextBuilder | None = None,
        card_modifier: Callable[[AgentCard], AgentCard] | None = None,
        extended_card_modifier: Callable[
            [AgentCard, ServerCallContext], AgentCard
        ]
        | None = None,
        max_content_length: int | None = 10 * 1024 * 1024,  # 10MB
    ) -> None:
        """Initializes the JSONRPCApplication.

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
        """
        if not _package_starlette_installed:
            raise ImportError(
                'Packages `starlette` and `sse-starlette` are required to use the'
                ' `JSONRPCApplication`. They can be added as a part of `a2a-sdk`'
                ' optional dependencies, `a2a-sdk[http-server]`.'
            )
        self.agent_card = agent_card
        self.extended_agent_card = extended_agent_card
        self.card_modifier = card_modifier
        self.extended_card_modifier = extended_card_modifier
        self.handler = JSONRPCHandler(
            agent_card=agent_card,
            request_handler=http_handler,
            extended_agent_card=extended_agent_card,
            extended_card_modifier=extended_card_modifier,
        )
        self._context_builder = context_builder or DefaultCallContextBuilder()
        self._max_content_length = max_content_length

    def _generate_error_response(
        self, request_id: str | int | None, error: JSONRPCError | A2AError
    ) -> JSONResponse:
        """Creates a Starlette JSONResponse for a JSON-RPC error.

        Logs the error based on its type.

        Args:
            request_id: The ID of the request that caused the error.
            error: The `JSONRPCError` or `A2AError` object.

        Returns:
            A `JSONResponse` object formatted as a JSON-RPC error response.
        """
        error_resp = JSONRPCErrorResponse(
            id=request_id,
            error=error if isinstance(error, JSONRPCError) else error.root,
        )

        log_level = (
            logging.ERROR
            if not isinstance(error, A2AError)
            or isinstance(error.root, InternalError)
            else logging.WARNING
        )
        logger.log(
            log_level,
            "Request Error (ID: %s): Code=%s, Message='%s'%s",
            request_id,
            error_resp.error.code,
            error_resp.error.message,
            ', Data=' + str(error_resp.error.data)
            if error_resp.error.data
            else '',
        )
        return JSONResponse(
            error_resp.model_dump(mode='json', exclude_none=True),
            status_code=200,
        )

    def _allowed_content_length(self, request: Request) -> bool:
        """Checks if the request content length is within the allowed maximum.

        Args:
            request: The incoming Starlette Request object.

        Returns:
            False if the content length is larger than the allowed maximum, True otherwise.
        """
        if self._max_content_length is not None:
            with contextlib.suppress(ValueError):
                content_length = int(request.headers.get('content-length', '0'))
                if content_length and content_length > self._max_content_length:
                    return False
        return True

    async def _handle_requests(self, request: Request) -> Response:  # noqa: PLR0911
        """Handles incoming POST requests to the main A2A endpoint.

        Parses the request body as JSON, validates it against A2A request types,
        dispatches it to the appropriate handler method, and returns the response.
        Handles JSON parsing errors, validation errors, and other exceptions,
        returning appropriate JSON-RPC error responses.

        Args:
            request: The incoming Starlette Request object.

        Returns:
            A Starlette Response object (JSONResponse or EventSourceResponse).

        Raises:
            (Implicitly handled): Various exceptions are caught and converted
            into JSON-RPC error responses by this method.
        """
        request_id = None
        body = None

        try:
            body = await request.json()
            if isinstance(body, dict):
                request_id = body.get('id')
                # Ensure request_id is valid for JSON-RPC response (str/int/None only)
                if request_id is not None and not isinstance(
                    request_id, str | int
                ):
                    request_id = None
            # Treat payloads lager than allowed as invalid request (-32600) before routing
            if not self._allowed_content_length(request):
                return self._generate_error_response(
                    request_id,
                    A2AError(
                        root=InvalidRequestError(message='Payload too large')
                    ),
                )
            logger.debug('Request body: %s', body)
            # 1) Validate base JSON-RPC structure only (-32600 on failure)
            try:
                base_request = JSONRPCRequest.model_validate(body)
            except ValidationError as e:
                logger.exception('Failed to validate base JSON-RPC request')
                return self._generate_error_response(
                    request_id,
                    A2AError(
                        root=InvalidRequestError(data=json.loads(e.json()))
                    ),
                )

            # 2) Route by method name; unknown -> -32601, known -> validate params (-32602 on failure)
            method = base_request.method

            model_class = self.METHOD_TO_MODEL.get(method)
            if not model_class:
                return self._generate_error_response(
                    request_id, A2AError(root=MethodNotFoundError())
                )
            try:
                specific_request = model_class.model_validate(body)
            except ValidationError as e:
                logger.exception('Failed to validate base JSON-RPC request')
                return self._generate_error_response(
                    request_id,
                    A2AError(
                        root=InvalidParamsError(data=json.loads(e.json()))
                    ),
                )

            # 3) Build call context and wrap the request for downstream handling
            call_context = self._context_builder.build(request)
            call_context.state['method'] = method

            request_id = specific_request.id
            a2a_request = A2ARequest(root=specific_request)
            request_obj = a2a_request.root

            if isinstance(
                request_obj,
                TaskResubscriptionRequest | SendStreamingMessageRequest,
            ):
                return await self._process_streaming_request(
                    request_id, a2a_request, call_context
                )

            return await self._process_non_streaming_request(
                request_id, a2a_request, call_context
            )
        except MethodNotImplementedError:
            traceback.print_exc()
            return self._generate_error_response(
                request_id, A2AError(root=UnsupportedOperationError())
            )
        except json.decoder.JSONDecodeError as e:
            traceback.print_exc()
            return self._generate_error_response(
                None, A2AError(root=JSONParseError(message=str(e)))
            )
        except HTTPException as e:
            if e.status_code == HTTP_413_REQUEST_ENTITY_TOO_LARGE:
                return self._generate_error_response(
                    request_id,
                    A2AError(
                        root=InvalidRequestError(message='Payload too large')
                    ),
                )
            raise e
        except Exception as e:
            logger.exception('Unhandled exception')
            return self._generate_error_response(
                request_id, A2AError(root=InternalError(message=str(e)))
            )

    async def _process_streaming_request(
        self,
        request_id: str | int | None,
        a2a_request: A2ARequest,
        context: ServerCallContext,
    ) -> Response:
        """Processes streaming requests (message/stream or tasks/resubscribe).

        Args:
            request_id: The ID of the request.
            a2a_request: The validated A2ARequest object.
            context: The ServerCallContext for the request.

        Returns:
            An `EventSourceResponse` object to stream results to the client.
        """
        request_obj = a2a_request.root
        handler_result: Any = None
        if isinstance(
            request_obj,
            SendStreamingMessageRequest,
        ):
            handler_result = self.handler.on_message_send_stream(
                request_obj, context
            )
        elif isinstance(request_obj, TaskResubscriptionRequest):
            handler_result = self.handler.on_resubscribe_to_task(
                request_obj, context
            )

        return self._create_response(context, handler_result)

    async def _process_non_streaming_request(
        self,
        request_id: str | int | None,
        a2a_request: A2ARequest,
        context: ServerCallContext,
    ) -> Response:
        """Processes non-streaming requests (message/send, tasks/get, tasks/cancel, tasks/pushNotificationConfig/*).

        Args:
            request_id: The ID of the request.
            a2a_request: The validated A2ARequest object.
            context: The ServerCallContext for the request.

        Returns:
            A `JSONResponse` object containing the result or error.
        """
        request_obj = a2a_request.root
        handler_result: Any = None
        match request_obj:
            case SendMessageRequest():
                handler_result = await self.handler.on_message_send(
                    request_obj, context
                )
            case CancelTaskRequest():
                handler_result = await self.handler.on_cancel_task(
                    request_obj, context
                )
            case GetTaskRequest():
                handler_result = await self.handler.on_get_task(
                    request_obj, context
                )
            case SetTaskPushNotificationConfigRequest():
                handler_result = (
                    await self.handler.set_push_notification_config(
                        request_obj,
                        context,
                    )
                )
            case GetTaskPushNotificationConfigRequest():
                handler_result = (
                    await self.handler.get_push_notification_config(
                        request_obj,
                        context,
                    )
                )
            case ListTaskPushNotificationConfigRequest():
                handler_result = (
                    await self.handler.list_push_notification_config(
                        request_obj,
                        context,
                    )
                )
            case DeleteTaskPushNotificationConfigRequest():
                handler_result = (
                    await self.handler.delete_push_notification_config(
                        request_obj,
                        context,
                    )
                )
            case GetAuthenticatedExtendedCardRequest():
                handler_result = (
                    await self.handler.get_authenticated_extended_card(
                        request_obj,
                        context,
                    )
                )
            case _:
                logger.error(
                    'Unhandled validated request type: %s', type(request_obj)
                )
                error = UnsupportedOperationError(
                    message=f'Request type {type(request_obj).__name__} is unknown.'
                )
                handler_result = JSONRPCErrorResponse(
                    id=request_id, error=error
                )

        return self._create_response(context, handler_result)

    def _create_response(
        self,
        context: ServerCallContext,
        handler_result: (
            AsyncGenerator[SendStreamingMessageResponse]
            | JSONRPCErrorResponse
            | JSONRPCResponse
        ),
    ) -> Response:
        """Creates a Starlette Response based on the result from the request handler.

        Handles:
        - AsyncGenerator for Server-Sent Events (SSE).
        - JSONRPCErrorResponse for explicit errors returned by handlers.
        - Pydantic RootModels (like GetTaskResponse) containing success or error
        payloads.

        Args:
            context: The ServerCallContext provided to the request handler.
            handler_result: The result from a request handler method. Can be an
                async generator for streaming or a Pydantic model for non-streaming.

        Returns:
            A Starlette JSONResponse or EventSourceResponse.
        """
        headers = {}
        if exts := context.activated_extensions:
            headers[HTTP_EXTENSION_HEADER] = ', '.join(sorted(exts))
        if isinstance(handler_result, AsyncGenerator):
            # Result is a stream of SendStreamingMessageResponse objects
            async def event_generator(
                stream: AsyncGenerator[SendStreamingMessageResponse],
            ) -> AsyncGenerator[dict[str, str]]:
                async for item in stream:
                    yield {'data': item.root.model_dump_json(exclude_none=True)}

            return EventSourceResponse(
                event_generator(handler_result), headers=headers
            )
        if isinstance(handler_result, JSONRPCErrorResponse):
            return JSONResponse(
                handler_result.model_dump(
                    mode='json',
                    exclude_none=True,
                ),
                headers=headers,
            )

        return JSONResponse(
            handler_result.root.model_dump(mode='json', exclude_none=True),
            headers=headers,
        )

    async def _handle_get_agent_card(self, request: Request) -> JSONResponse:
        """Handles GET requests for the agent card endpoint.

        Args:
            request: The incoming Starlette Request object.

        Returns:
            A JSONResponse containing the agent card data.
        """
        if request.url.path == PREV_AGENT_CARD_WELL_KNOWN_PATH:
            logger.warning(
                "Deprecated agent card endpoint '%s' accessed. "
                "Please use '%s' instead. This endpoint will be removed in a future version.",
                PREV_AGENT_CARD_WELL_KNOWN_PATH,
                AGENT_CARD_WELL_KNOWN_PATH,
            )

        card_to_serve = self.agent_card
        if self.card_modifier:
            card_to_serve = self.card_modifier(card_to_serve)

        return JSONResponse(
            card_to_serve.model_dump(
                exclude_none=True,
                by_alias=True,
            )
        )

    async def _handle_get_authenticated_extended_agent_card(
        self, request: Request
    ) -> JSONResponse:
        """Handles GET requests for the authenticated extended agent card."""
        logger.warning(
            'HTTP GET for authenticated extended card has been called by a client. '
            'This endpoint is deprecated in favor of agent/authenticatedExtendedCard JSON-RPC method and will be removed in a future release.'
        )
        if not self.agent_card.supports_authenticated_extended_card:
            return JSONResponse(
                {'error': 'Extended agent card not supported or not enabled.'},
                status_code=404,
            )

        card_to_serve = self.extended_agent_card

        if self.extended_card_modifier:
            context = self._context_builder.build(request)
            # If no base extended card is provided, pass the public card to the modifier
            base_card = card_to_serve if card_to_serve else self.agent_card
            card_to_serve = self.extended_card_modifier(base_card, context)

        if card_to_serve:
            return JSONResponse(
                card_to_serve.model_dump(
                    exclude_none=True,
                    by_alias=True,
                )
            )
        # If supports_authenticated_extended_card is true, but no
        # extended_agent_card was provided, and no modifier produced a card,
        # return a 404.
        return JSONResponse(
            {
                'error': 'Authenticated extended agent card is supported but not configured on the server.'
            },
            status_code=404,
        )

    @abstractmethod
    def build(
        self,
        agent_card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
        rpc_url: str = DEFAULT_RPC_URL,
        extended_agent_card_url: str = EXTENDED_AGENT_CARD_PATH,
        **kwargs: Any,
    ) -> FastAPI | Starlette:
        """Builds and returns the JSONRPC application instance.

        Args:
            agent_card_url: The URL for the agent card endpoint.
            rpc_url: The URL for the A2A JSON-RPC endpoint.
            extended_agent_card_url: The URL for the authenticated extended
              agent card endpoint.
            **kwargs: Additional keyword arguments to pass to the FastAPI constructor.

        Returns:
            A configured JSONRPC application instance.
        """
        raise NotImplementedError(
            'Subclasses must implement the build method to create the application instance.'
        )
