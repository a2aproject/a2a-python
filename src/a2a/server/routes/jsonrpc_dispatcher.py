"""JSON-RPC application for A2A server."""

import json
import logging
import traceback

from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from google.protobuf.json_format import MessageToDict, ParseDict
from jsonrpc.jsonrpc2 import JSONRPC20Request, JSONRPC20Response

from a2a.auth.user import UnauthenticatedUser
from a2a.auth.user import User as A2AUser
from a2a.compat.v0_3.jsonrpc_adapter import JSONRPC03Adapter
from a2a.extensions.common import (
    HTTP_EXTENSION_HEADER,
    get_requested_extensions,
)
from a2a.server.context import ServerCallContext
from a2a.server.jsonrpc_models import (
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    JSONParseError,
    JSONRPCError,
    MethodNotFoundError,
)
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.request_handlers.response_helpers import (
    build_error_response,
)
from a2a.types import A2ARequest
from a2a.types.a2a_pb2 import (
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTasksRequest,
    SendMessageRequest,
    SendMessageResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)
from a2a.utils import constants, proto_utils
from a2a.utils.errors import (
    A2AError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from a2a.utils.helpers import validate_version
from a2a.utils.telemetry import SpanKind, trace_class


INTERNAL_ERROR_CODE = -32603

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sse_starlette.sse import EventSourceResponse
    from starlette.applications import Starlette
    from starlette.authentication import BaseUser
    from starlette.exceptions import HTTPException
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response

    try:
        # Starlette v0.48.0
        from starlette.status import HTTP_413_CONTENT_TOO_LARGE
    except ImportError:
        from starlette.status import (  # type: ignore[no-redef]
            HTTP_413_REQUEST_ENTITY_TOO_LARGE as HTTP_413_CONTENT_TOO_LARGE,
        )

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

        try:
            # Starlette v0.48.0
            from starlette.status import HTTP_413_CONTENT_TOO_LARGE
        except ImportError:
            from starlette.status import (
                HTTP_413_REQUEST_ENTITY_TOO_LARGE as HTTP_413_CONTENT_TOO_LARGE,
            )

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
        HTTP_413_CONTENT_TOO_LARGE = Any


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
        if 'user' in request.scope:
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


@trace_class(kind=SpanKind.SERVER)
class JsonRpcDispatcher:
    """Base class for A2A JSONRPC applications.

    Handles incoming JSON-RPC requests, routes them to the appropriate
    handler methods, and manages response generation including Server-Sent Events
    (SSE).
    """

    # Method-to-model mapping for centralized routing
    # Proto types don't have model_fields, so we define the mapping explicitly
    # Method names match gRPC service method names
    METHOD_TO_MODEL: dict[str, type] = {
        'SendMessage': SendMessageRequest,
        'SendStreamingMessage': SendMessageRequest,  # Same proto type as SendMessage
        'GetTask': GetTaskRequest,
        'ListTasks': ListTasksRequest,
        'CancelTask': CancelTaskRequest,
        'CreateTaskPushNotificationConfig': TaskPushNotificationConfig,
        'GetTaskPushNotificationConfig': GetTaskPushNotificationConfigRequest,
        'ListTaskPushNotificationConfigs': ListTaskPushNotificationConfigsRequest,
        'DeleteTaskPushNotificationConfig': DeleteTaskPushNotificationConfigRequest,
        'SubscribeToTask': SubscribeToTaskRequest,
        'GetExtendedAgentCard': GetExtendedAgentCardRequest,
    }

    def __init__(
        self,
        request_handler: RequestHandler,
        context_builder: CallContextBuilder | None = None,
        enable_v0_3_compat: bool = False,
    ) -> None:
        """Initializes the JsonRpcDispatcher.

        Args:
            request_handler: The handler instance responsible for processing A2A
              requests via http.
            context_builder: The CallContextBuilder used to construct the
              ServerCallContext passed to the request_handler. If None the
              DefaultCallContextBuilder is used.
            enable_v0_3_compat: Whether to enable v0.3 backward compatibility on the same endpoint.
        """
        if not _package_starlette_installed:
            raise ImportError(
                'Packages `starlette` and `sse-starlette` are required to use the'
                ' `JsonRpcDispatcher`. They can be added as a part of `a2a-sdk`'
                ' optional dependencies, `a2a-sdk[http-server]`.'
            )

        self.request_handler = request_handler
        self._context_builder = context_builder or DefaultCallContextBuilder()
        self.enable_v0_3_compat = enable_v0_3_compat
        self._v03_adapter: JSONRPC03Adapter | None = None

        if self.enable_v0_3_compat:
            self._v03_adapter = JSONRPC03Adapter(
                agent_card=request_handler.agent_card,
                http_handler=request_handler,
                extended_agent_card=getattr(
                    request_handler, '_extended_agent_card', None
                ),
                context_builder=self._context_builder,
                card_modifier=getattr(request_handler, '_card_modifier', None),
                extended_card_modifier=getattr(
                    request_handler, '_extended_card_modifier', None
                ),
            )

    def _generate_error_response(
        self,
        request_id: str | int | None,
        error: Exception | JSONRPCError | A2AError,
    ) -> JSONResponse:
        """Creates a Starlette JSONResponse for a JSON-RPC error.

        Logs the error based on its type.

        Args:
            request_id: The ID of the request that caused the error.
            error: The error object (one of the JSONRPCError types).

        Returns:
            A `JSONResponse` object formatted as a JSON-RPC error response.
        """
        if not isinstance(error, A2AError | JSONRPCError):
            error = InternalError(message=str(error))

        response_data = build_error_response(request_id, error)
        error_info = response_data.get('error', {})
        code = error_info.get('code')
        message = error_info.get('message')
        data = error_info.get('data')

        log_level = logging.WARNING
        if code == INTERNAL_ERROR_CODE:
            log_level = logging.ERROR

        logger.log(
            log_level,
            "Request Error (ID: %s): Code=%s, Message='%s'%s",
            request_id,
            code,
            message,
            f', Data={data}' if data else '',
        )
        return JSONResponse(
            response_data,
            status_code=200,
        )

    async def handle_requests(self, request: Request) -> Response:  # noqa: PLR0911, PLR0912
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
            logger.debug('Request body: %s', body)
            # 1) Validate base JSON-RPC structure only (-32600 on failure)
            try:
                base_request = JSONRPC20Request.from_data(body)
                if not isinstance(base_request, JSONRPC20Request):
                    # Batch requests are not supported
                    return self._generate_error_response(
                        request_id,
                        InvalidRequestError(
                            message='Batch requests are not supported'
                        ),
                    )
                if body.get('jsonrpc') != '2.0':
                    return self._generate_error_response(
                        request_id,
                        InvalidRequestError(
                            message="Invalid request: 'jsonrpc' must be exactly '2.0'"
                        ),
                    )
            except Exception as e:
                logger.exception('Failed to validate base JSON-RPC request')
                return self._generate_error_response(
                    request_id,
                    InvalidRequestError(data=str(e)),
                )

            # 2) Route by method name; unknown -> -32601, known -> validate params (-32602 on failure)
            method: str | None = base_request.method
            request_id = base_request._id  # noqa: SLF001

            if not method:
                return self._generate_error_response(
                    request_id,
                    InvalidRequestError(message='Method is required'),
                )

            if (
                self.enable_v0_3_compat
                and self._v03_adapter
                and self._v03_adapter.supports_method(method)
            ):
                return await self._v03_adapter.handle_request(
                    request_id=request_id,
                    method=method,
                    body=body,
                    request=request,
                )

            model_class = self.METHOD_TO_MODEL.get(method)
            if not model_class:
                return self._generate_error_response(
                    request_id, MethodNotFoundError()
                )
            try:
                # Parse the params field into the proto message type
                params = body.get('params', {})
                specific_request = ParseDict(params, model_class())
            except Exception as e:
                logger.exception('Failed to parse request params')
                return self._generate_error_response(
                    request_id,
                    InvalidParamsError(data=str(e)),
                )

            # 3) Build call context and wrap the request for downstream handling
            call_context = self._context_builder.build(request)
            call_context.tenant = getattr(specific_request, 'tenant', '')
            call_context.state['method'] = method
            call_context.state['request_id'] = request_id

            handler_result: (
                AsyncGenerator[dict[str, Any], None] | dict[str, Any]
            )

            # Route streaming requests by method name
            if method in ('SendStreamingMessage', 'SubscribeToTask'):
                handler_result = await self._process_streaming_request(
                    request_id, specific_request, call_context
                )
            else:
                try:
                    raw_result = await self._process_non_streaming_request(
                        request_id, specific_request, call_context
                    )
                    handler_result = JSONRPC20Response(
                        result=raw_result, _id=request_id
                    ).data
                except A2AError as e:
                    handler_result = build_error_response(request_id, e)
            return self._create_response(call_context, handler_result)
        except json.decoder.JSONDecodeError as e:
            traceback.print_exc()
            return self._generate_error_response(
                None, JSONParseError(message=str(e))
            )
        except HTTPException as e:
            if e.status_code == HTTP_413_CONTENT_TOO_LARGE:
                return self._generate_error_response(
                    request_id,
                    InvalidRequestError(message='Payload too large'),
                )
            raise e
        except A2AError as e:
            return self._generate_error_response(request_id, e)
        except Exception as e:
            logger.exception('Unhandled exception')
            return self._generate_error_response(
                request_id, InternalError(message=str(e))
            )

    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def _process_streaming_request(
        self,
        request_id: str | int | None,
        request_obj: A2ARequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Processes streaming requests (SendStreamingMessage or SubscribeToTask).

        Args:
            request_id: The ID of the request.
            request_obj: The proto request message.
            context: The ServerCallContext for the request.

        Returns:
            An `AsyncGenerator` object to stream results to the client.
        """
        stream: AsyncGenerator | None = None
        if isinstance(request_obj, SendMessageRequest):
            stream = self.request_handler.on_message_send_stream(
                request_obj, context
            )
        elif isinstance(request_obj, SubscribeToTaskRequest):
            stream = self.request_handler.on_subscribe_to_task(
                request_obj, context
            )

        if stream is None:
            raise UnsupportedOperationError(message='Stream not supported')

        async def _wrap_stream(
            st: AsyncGenerator,
        ) -> AsyncGenerator[dict[str, Any], None]:
            try:
                async for event in st:
                    stream_response = proto_utils.to_stream_response(event)
                    result = MessageToDict(
                        stream_response, preserving_proto_field_name=False
                    )
                    yield JSONRPC20Response(result=result, _id=request_id).data
            except A2AError as e:
                yield build_error_response(request_id, e)

        return _wrap_stream(stream)

    async def _handle_send_message(
        self, request_obj: SendMessageRequest, context: ServerCallContext
    ) -> dict[str, Any]:
        task_or_message = await self.request_handler.on_message_send(
            request_obj, context
        )
        if isinstance(task_or_message, Task):
            return MessageToDict(SendMessageResponse(task=task_or_message))
        return MessageToDict(SendMessageResponse(message=task_or_message))

    async def _handle_cancel_task(
        self, request_obj: CancelTaskRequest, context: ServerCallContext
    ) -> dict[str, Any]:
        task = await self.request_handler.on_cancel_task(request_obj, context)
        if task:
            return MessageToDict(task, preserving_proto_field_name=False)
        raise TaskNotFoundError

    async def _handle_get_task(
        self, request_obj: GetTaskRequest, context: ServerCallContext
    ) -> dict[str, Any]:
        task = await self.request_handler.on_get_task(request_obj, context)
        if task:
            return MessageToDict(task, preserving_proto_field_name=False)
        raise TaskNotFoundError

    async def _handle_list_tasks(
        self, request_obj: ListTasksRequest, context: ServerCallContext
    ) -> dict[str, Any]:
        tasks_response = await self.request_handler.on_list_tasks(
            request_obj, context
        )
        return MessageToDict(
            tasks_response,
            preserving_proto_field_name=False,
            always_print_fields_with_no_presence=True,
        )

    async def _handle_create_task_push_notification_config(
        self,
        request_obj: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        result_config = (
            await self.request_handler.on_create_task_push_notification_config(
                request_obj, context
            )
        )
        return MessageToDict(result_config, preserving_proto_field_name=False)

    async def _handle_get_task_push_notification_config(
        self,
        request_obj: GetTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        config = (
            await self.request_handler.on_get_task_push_notification_config(
                request_obj, context
            )
        )
        return MessageToDict(config, preserving_proto_field_name=False)

    async def _handle_list_task_push_notification_configs(
        self,
        request_obj: ListTaskPushNotificationConfigsRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        configs_response = (
            await self.request_handler.on_list_task_push_notification_configs(
                request_obj, context
            )
        )
        return MessageToDict(
            configs_response, preserving_proto_field_name=False
        )

    async def _handle_delete_task_push_notification_config(
        self,
        request_obj: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> None:
        await self.request_handler.on_delete_task_push_notification_config(
            request_obj, context
        )

    async def _handle_get_extended_agent_card(
        self,
        request_obj: GetExtendedAgentCardRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        card = await self.request_handler.on_get_extended_agent_card(
            request_obj, context
        )
        return MessageToDict(card, preserving_proto_field_name=False)

    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def _process_non_streaming_request(  # noqa: PLR0911
        self,
        request_id: str | int | None,
        request_obj: A2ARequest,
        context: ServerCallContext,
    ) -> dict[str, Any] | None:
        """Processes non-streaming requests (message/send, tasks/get, tasks/cancel, tasks/pushNotificationConfig/*).

        Args:
            request_id: The ID of the request.
            request_obj: The proto request message.
            context: The ServerCallContext for the request.

        Returns:
            A dict containing the result or error.
        """
        match request_obj:
            case SendMessageRequest():
                return await self._handle_send_message(request_obj, context)
            case CancelTaskRequest():
                return await self._handle_cancel_task(request_obj, context)
            case GetTaskRequest():
                return await self._handle_get_task(request_obj, context)
            case ListTasksRequest():
                return await self._handle_list_tasks(request_obj, context)
            case TaskPushNotificationConfig():
                return await self._handle_create_task_push_notification_config(
                    request_obj, context
                )
            case GetTaskPushNotificationConfigRequest():
                return await self._handle_get_task_push_notification_config(
                    request_obj, context
                )
            case ListTaskPushNotificationConfigsRequest():
                return await self._handle_list_task_push_notification_configs(
                    request_obj, context
                )
            case DeleteTaskPushNotificationConfigRequest():
                return await self._handle_delete_task_push_notification_config(
                    request_obj, context
                )
            case GetExtendedAgentCardRequest():
                return await self._handle_get_extended_agent_card(
                    request_obj, context
                )
            case _:
                logger.error(
                    'Unhandled validated request type: %s', type(request_obj)
                )
                raise UnsupportedOperationError(
                    message=f'Request type {type(request_obj).__name__} is unknown.'
                )

    def _create_response(
        self,
        context: ServerCallContext,
        handler_result: AsyncGenerator[dict[str, Any]] | dict[str, Any],
    ) -> Response:
        """Creates a Starlette Response based on the result from the request handler.

        Handles:
        - AsyncGenerator for Server-Sent Events (SSE).
        - Dict responses from handlers.

        Args:
            context: The ServerCallContext provided to the request handler.
            handler_result: The result from a request handler method. Can be an
                async generator for streaming or a dict for non-streaming.

        Returns:
            A Starlette JSONResponse or EventSourceResponse.
        """
        headers = {}
        if exts := context.activated_extensions:
            headers[HTTP_EXTENSION_HEADER] = ', '.join(sorted(exts))
        if isinstance(handler_result, AsyncGenerator):
            # Result is a stream of dict objects
            async def event_generator(
                stream: AsyncGenerator[dict[str, Any]],
            ) -> AsyncGenerator[dict[str, str]]:
                async for item in stream:
                    yield {'data': json.dumps(item)}

            return EventSourceResponse(
                event_generator(handler_result), headers=headers
            )

        # handler_result is a dict (JSON-RPC response)
        return JSONResponse(handler_result, headers=headers)
