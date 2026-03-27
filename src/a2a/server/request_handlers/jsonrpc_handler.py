"""JSON-RPC handler for A2A server requests."""

import logging

from collections.abc import AsyncIterable, Awaitable, Callable
from typing import Any

from google.protobuf.json_format import MessageToDict
from jsonrpc.jsonrpc2 import JSONRPC20Response

from a2a.server.context import ServerCallContext
from a2a.server.jsonrpc_models import (
    InternalError as JSONRPCInternalError,
)
from a2a.server.jsonrpc_models import (
    JSONRPCError,
)
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import (
    AgentCard,
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
    JSON_RPC_ERROR_CODE_MAP,
    A2AError,
    ContentTypeNotSupportedError,
    ExtendedAgentCardNotConfiguredError,
    ExtensionSupportRequiredError,
    InternalError,
    InvalidAgentResponseError,
    InvalidParamsError,
    InvalidRequestError,
    MethodNotFoundError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
    VersionNotSupportedError,
)
from a2a.utils.helpers import (
    maybe_await,
    validate,
    validate_version,
)
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


EXCEPTION_MAP: dict[type[A2AError], type[JSONRPCError]] = {
    TaskNotFoundError: JSONRPCError,
    TaskNotCancelableError: JSONRPCError,
    PushNotificationNotSupportedError: JSONRPCError,
    UnsupportedOperationError: JSONRPCError,
    ContentTypeNotSupportedError: JSONRPCError,
    InvalidAgentResponseError: JSONRPCError,
    ExtendedAgentCardNotConfiguredError: JSONRPCError,
    InternalError: JSONRPCInternalError,
    InvalidParamsError: JSONRPCError,
    InvalidRequestError: JSONRPCError,
    MethodNotFoundError: JSONRPCError,
    ExtensionSupportRequiredError: JSONRPCError,
    VersionNotSupportedError: JSONRPCError,
}


def _build_success_response(
    request_id: str | int | None, result: Any
) -> dict[str, Any]:
    """Build a JSON-RPC success response dict."""
    return JSONRPC20Response(result=result, _id=request_id).data


def _build_error_response(
    request_id: str | int | None, error: Exception
) -> dict[str, Any]:
    """Build a JSON-RPC error response dict."""
    logger.debug('JSONRPCHandler: Building error response for exception: %s', error)
    jsonrpc_error: JSONRPCError
    if isinstance(error, A2AError):
        error_type = type(error)
        model_class = EXCEPTION_MAP.get(error_type, JSONRPCInternalError)
        code = JSON_RPC_ERROR_CODE_MAP.get(error_type, -32603)
        jsonrpc_error = model_class(
            code=code,
            message=str(error),
        )
    else:
        jsonrpc_error = JSONRPCInternalError(message=str(error))

    error_dict = jsonrpc_error.model_dump(exclude_none=True)
    return JSONRPC20Response(error=error_dict, _id=request_id).data


@trace_class(kind=SpanKind.SERVER)
class JSONRPCHandler:
    """Maps incoming JSON-RPC requests to the appropriate request handler method and formats responses."""

    def __init__(
        self,
        agent_card: AgentCard,
        request_handler: RequestHandler,
        extended_agent_card: AgentCard | None = None,
        extended_card_modifier: Callable[
            [AgentCard, ServerCallContext], Awaitable[AgentCard] | AgentCard
        ]
        | None = None,
        card_modifier: Callable[[AgentCard], Awaitable[AgentCard] | AgentCard]
        | None = None,
    ):
        """Initializes the JSONRPCHandler.

        Args:
            agent_card: The AgentCard describing the agent's capabilities.
            request_handler: The underlying `RequestHandler` instance to delegate requests to.
            extended_agent_card: An optional, distinct Extended AgentCard to be served
            extended_card_modifier: An optional callback to dynamically modify
              the extended agent card before it is served. It receives the
              call context.
            card_modifier: An optional callback to dynamically modify the public
              agent card before it is served.
        """
        self.agent_card = agent_card
        self.request_handler = request_handler
        self.extended_agent_card = extended_agent_card
        self.extended_card_modifier = extended_card_modifier
        self.card_modifier = card_modifier

    def _get_request_id(
        self, context: ServerCallContext | None
    ) -> str | int | None:
        """Get the JSON-RPC request ID from the context."""
        if context is None:
            return None
        return context.state.get('request_id')

    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def on_message_send(
        self,
        request: SendMessageRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'message/send' JSON-RPC method.

        Args:
            request: The incoming `SendMessageRequest` proto message.
            context: Context provided by the server.

        Returns:
            A dict representing the JSON-RPC response.
        """
        request_id = self._get_request_id(context)
        try:
            task_or_message = await self.request_handler.on_message_send(
                request, context
            )
            if isinstance(task_or_message, Task):
                response = SendMessageResponse(task=task_or_message)
            else:
                response = SendMessageResponse(message=task_or_message)

            result = MessageToDict(response)
            return _build_success_response(request_id, result)
        except A2AError as e:
            return _build_error_response(request_id, e)

    @validate_version(constants.PROTOCOL_VERSION_1_0)
    @validate(
        lambda self: self.agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def on_message_send_stream(
        self,
        request: SendMessageRequest,
        context: ServerCallContext,
    ) -> AsyncIterable[dict[str, Any]]:
        """Handles the 'message/stream' JSON-RPC method.

        Yields response objects as they are produced by the underlying handler's stream.

        Args:
            request: The incoming `SendMessageRequest` object (for streaming).
            context: Context provided by the server.

        Yields:
            Dict representations of JSON-RPC responses containing streaming events.
        """
        try:
            async for event in self.request_handler.on_message_send_stream(
                request, context
            ):
                # Wrap the event in StreamResponse for consistent client parsing
                stream_response = proto_utils.to_stream_response(event)
                result = MessageToDict(
                    stream_response, preserving_proto_field_name=False
                )
                yield _build_success_response(
                    self._get_request_id(context), result
                )
        except A2AError as e:
            yield _build_error_response(
                self._get_request_id(context),
                e,
            )

    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def on_cancel_task(
        self,
        request: CancelTaskRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'tasks/cancel' JSON-RPC method.

        Args:
            request: The incoming `CancelTaskRequest` object.
            context: Context provided by the server.

        Returns:
            A dict representing the JSON-RPC response.
        """
        request_id = self._get_request_id(context)
        try:
            task = await self.request_handler.on_cancel_task(request, context)
        except A2AError as e:
            return _build_error_response(request_id, e)

        if task:
            result = MessageToDict(task, preserving_proto_field_name=False)
            return _build_success_response(request_id, result)

        return _build_error_response(request_id, TaskNotFoundError())

    @validate_version(constants.PROTOCOL_VERSION_1_0)
    @validate(
        lambda self: self.agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def on_subscribe_to_task(
        self,
        request: SubscribeToTaskRequest,
        context: ServerCallContext,
    ) -> AsyncIterable[dict[str, Any]]:
        """Handles the 'SubscribeToTask' JSON-RPC method.

        Yields response objects as they are produced by the underlying handler's stream.

        Args:
            request: The incoming `SubscribeToTaskRequest` object.
            context: Context provided by the server.

        Yields:
            Dict representations of JSON-RPC responses containing streaming events.
        """
        try:
            async for event in self.request_handler.on_subscribe_to_task(
                request, context
            ):
                # Wrap the event in StreamResponse for consistent client parsing
                stream_response = proto_utils.to_stream_response(event)
                result = MessageToDict(
                    stream_response, preserving_proto_field_name=False
                )
                yield _build_success_response(
                    self._get_request_id(context), result
                )
        except A2AError as e:
            yield _build_error_response(
                self._get_request_id(context),
                e,
            )

    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def get_push_notification_config(
        self,
        request: GetTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'tasks/pushNotificationConfig/get' JSON-RPC method.

        Args:
            request: The incoming `GetTaskPushNotificationConfigRequest` object.
            context: Context provided by the server.

        Returns:
            A dict representing the JSON-RPC response.
        """
        request_id = self._get_request_id(context)
        try:
            config = (
                await self.request_handler.on_get_task_push_notification_config(
                    request, context
                )
            )
            result = MessageToDict(config, preserving_proto_field_name=False)
            return _build_success_response(request_id, result)
        except A2AError as e:
            return _build_error_response(request_id, e)

    @validate_version(constants.PROTOCOL_VERSION_1_0)
    @validate(
        lambda self: self.agent_card.capabilities.push_notifications,
        'Push notifications are not supported by the agent',
    )
    async def set_push_notification_config(
        self,
        request: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'tasks/pushNotificationConfig/set' JSON-RPC method.

        Requires the agent to support push notifications.

        Args:
            request: The incoming `TaskPushNotificationConfig` object.
            context: Context provided by the server.

        Returns:
            A dict representing the JSON-RPC response.

        Raises:
            UnsupportedOperationError: If push notifications are not supported by the agent
                (due to the `@validate` decorator).
        """
        request_id = self._get_request_id(context)
        try:
            # Pass the full request to the handler
            result_config = await self.request_handler.on_create_task_push_notification_config(
                request, context
            )
            result = MessageToDict(
                result_config, preserving_proto_field_name=False
            )
            return _build_success_response(request_id, result)
        except A2AError as e:
            return _build_error_response(request_id, e)

    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def on_get_task(
        self,
        request: GetTaskRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'tasks/get' JSON-RPC method.

        Args:
            request: The incoming `GetTaskRequest` object.
            context: Context provided by the server.

        Returns:
            A dict representing the JSON-RPC response.
        """
        request_id = self._get_request_id(context)
        try:
            task = await self.request_handler.on_get_task(request, context)
        except A2AError as e:
            return _build_error_response(request_id, e)

        if task:
            result = MessageToDict(task, preserving_proto_field_name=False)
            return _build_success_response(request_id, result)

        return _build_error_response(request_id, TaskNotFoundError())

    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def list_tasks(
        self,
        request: ListTasksRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'tasks/list' JSON-RPC method.

        Args:
            request: The incoming `ListTasksRequest` object.
            context: Context provided by the server.

        Returns:
            A dict representing the JSON-RPC response.
        """
        request_id = self._get_request_id(context)
        try:
            response = await self.request_handler.on_list_tasks(
                request, context
            )
            result = MessageToDict(
                response,
                preserving_proto_field_name=False,
                always_print_fields_with_no_presence=True,
            )
            return _build_success_response(request_id, result)
        except A2AError as e:
            return _build_error_response(request_id, e)

    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def list_push_notification_configs(
        self,
        request: ListTaskPushNotificationConfigsRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'ListTaskPushNotificationConfigs' JSON-RPC method.

        Args:
            request: The incoming `ListTaskPushNotificationConfigsRequest` object.
            context: Context provided by the server.

        Returns:
            A dict representing the JSON-RPC response.
        """
        request_id = self._get_request_id(context)
        try:
            response = await self.request_handler.on_list_task_push_notification_configs(
                request, context
            )
            # response is a ListTaskPushNotificationConfigsResponse proto
            result = MessageToDict(response, preserving_proto_field_name=False)
            return _build_success_response(request_id, result)
        except A2AError as e:
            return _build_error_response(request_id, e)

    @validate_version(constants.PROTOCOL_VERSION_1_0)
    async def delete_push_notification_config(
        self,
        request: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'tasks/pushNotificationConfig/delete' JSON-RPC method.

        Args:
            request: The incoming `DeleteTaskPushNotificationConfigRequest` object.
            context: Context provided by the server.

        Returns:
            A dict representing the JSON-RPC response.
        """
        request_id = self._get_request_id(context)
        try:
            await self.request_handler.on_delete_task_push_notification_config(
                request, context
            )
            return _build_success_response(request_id, None)
        except A2AError as e:
            return _build_error_response(request_id, e)

    async def get_authenticated_extended_card(
        self,
        request: GetExtendedAgentCardRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'agent/authenticatedExtendedCard' JSON-RPC method.

        Args:
            request: The incoming `GetExtendedAgentCardRequest` object.
            context: Context provided by the server.

        Returns:
            A dict representing the JSON-RPC response.
        """
        request_id = self._get_request_id(context)
        if not self.agent_card.capabilities.extended_agent_card:
            raise ExtendedAgentCardNotConfiguredError(
                message='The agent does not have an extended agent card configured'
            )

        base_card = self.extended_agent_card
        if base_card is None:
            base_card = self.agent_card

        card_to_serve = base_card
        if self.extended_card_modifier and context:
            card_to_serve = await maybe_await(
                self.extended_card_modifier(base_card, context)
            )
        elif self.card_modifier:
            card_to_serve = await maybe_await(self.card_modifier(base_card))

        result = MessageToDict(card_to_serve, preserving_proto_field_name=False)
        return _build_success_response(request_id, result)
