import logging

from collections.abc import AsyncIterable, Callable

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.request_handlers.response_helpers import prepare_response_object
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigRequest,
    Message,
    SendMessageRequest,
    SendMessageResponse as SendMessageResponseProto,
    SetTaskPushNotificationConfigRequest,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskStatusUpdateEvent,
)
from a2a.utils import proto_utils
from a2a.types.extras import (
    AuthenticatedExtendedCardNotConfiguredError,
    CancelTaskResponse,
    CancelTaskSuccessResponse,
    DeleteTaskPushNotificationConfigResponse,
    DeleteTaskPushNotificationConfigSuccessResponse,
    GetAuthenticatedExtendedCardResponse,
    GetAuthenticatedExtendedCardSuccessResponse,
    GetTaskPushNotificationConfigResponse,
    GetTaskPushNotificationConfigSuccessResponse,
    GetTaskResponse,
    GetTaskSuccessResponse,
    InternalError,
    JSONRPCErrorResponse,
    ListTaskPushNotificationConfigResponse,
    ListTaskPushNotificationConfigSuccessResponse,
    SendMessageResponse,
    SendMessageSuccessResponse,
    SendStreamingMessageRequest,
    SendStreamingMessageResponse,
    SendStreamingMessageSuccessResponse,
    SetTaskPushNotificationConfigResponse,
    SetTaskPushNotificationConfigSuccessResponse,
    TaskNotFoundError,
    TaskResubscriptionRequest,
)
from a2a.utils.errors import ServerError
from a2a.utils.helpers import validate
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


@trace_class(kind=SpanKind.SERVER)
class JSONRPCHandler:
    """Maps incoming JSON-RPC requests to the appropriate request handler method and formats responses."""

    def __init__(
        self,
        agent_card: AgentCard,
        request_handler: RequestHandler,
        extended_agent_card: AgentCard | None = None,
        extended_card_modifier: Callable[
            [AgentCard, ServerCallContext], AgentCard
        ]
        | None = None,
        card_modifier: Callable[[AgentCard], AgentCard] | None = None,
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

    async def on_message_send(
        self,
        request: SendMessageRequest,
        context: ServerCallContext | None = None,
    ) -> SendMessageResponse:
        """Handles the 'message/send' JSON-RPC method.

        Args:
            request: The incoming `SendMessageRequest` proto message.
            context: Context provided by the server.

        Returns:
            A `SendMessageResponse` object containing the result (Task or Message)
            or a JSON-RPC error response if a `ServerError` is raised by the handler.
        """
        request_id = self._get_request_id(context)
        # TODO: Wrap in error handler to return error states
        try:
            task_or_message = await self.request_handler.on_message_send(
                request, context
            )
            # Wrap the result in SendMessageResponseProto for consistent client parsing
            if isinstance(task_or_message, Task):
                response_proto = SendMessageResponseProto(task=task_or_message)
            else:
                response_proto = SendMessageResponseProto(msg=task_or_message)
            return prepare_response_object(
                request_id,
                response_proto,
                (SendMessageResponseProto,),
                SendMessageSuccessResponse,
                SendMessageResponse,
            )
        except ServerError as e:
            return SendMessageResponse(
                root=JSONRPCErrorResponse(
                    id=request_id, error=e.error if e.error else InternalError()
                )
            )

    @validate(
        lambda self: self.agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def on_message_send_stream(
        self,
        request: SendStreamingMessageRequest,
        context: ServerCallContext | None = None,
    ) -> AsyncIterable[SendStreamingMessageResponse]:
        """Handles the 'message/stream' JSON-RPC method.

        Yields response objects as they are produced by the underlying handler's stream.

        Args:
            request: The incoming `SendStreamingMessageRequest` object.
            context: Context provided by the server.

        Yields:
            `SendStreamingMessageResponse` objects containing streaming events
            (Task, Message, TaskStatusUpdateEvent, TaskArtifactUpdateEvent)
            or JSON-RPC error responses if a `ServerError` is raised.
        """
        try:
            async for event in self.request_handler.on_message_send_stream(
                request, context
            ):
                # Wrap the event in StreamResponse for consistent client parsing
                stream_response = proto_utils.ToProto.stream_response(event)
                yield prepare_response_object(
                    self._get_request_id(context),
                    stream_response,
                    (StreamResponse,),
                    SendStreamingMessageSuccessResponse,
                    SendStreamingMessageResponse,
                )
        except ServerError as e:
            yield SendStreamingMessageResponse(
                root=JSONRPCErrorResponse(
                    id=self._get_request_id(context), error=e.error if e.error else InternalError()
                )
            )

    async def on_cancel_task(
        self,
        request: CancelTaskRequest,
        context: ServerCallContext | None = None,
    ) -> CancelTaskResponse:
        """Handles the 'tasks/cancel' JSON-RPC method.

        Args:
            request: The incoming `CancelTaskRequest` object.
            context: Context provided by the server.

        Returns:
            A `CancelTaskResponse` object containing the updated Task or a JSON-RPC error.
        """
        try:
            task = await self.request_handler.on_cancel_task(
                request, context
            )
        except ServerError as e:
            return CancelTaskResponse(
                root=JSONRPCErrorResponse(
                    id=self._get_request_id(context), error=e.error if e.error else InternalError()
                )
            )

        if task:
            return prepare_response_object(
                self._get_request_id(context),
                task,
                (Task,),
                CancelTaskSuccessResponse,
                CancelTaskResponse,
            )

        return CancelTaskResponse(
            root=JSONRPCErrorResponse(id=self._get_request_id(context), error=TaskNotFoundError())
        )

    async def on_resubscribe_to_task(
        self,
        request: TaskResubscriptionRequest,
        context: ServerCallContext | None = None,
    ) -> AsyncIterable[SendStreamingMessageResponse]:
        """Handles the 'tasks/resubscribe' JSON-RPC method.

        Yields response objects as they are produced by the underlying handler's stream.

        Args:
            request: The incoming `TaskResubscriptionRequest` object.
            context: Context provided by the server.

        Yields:
            `SendStreamingMessageResponse` objects containing streaming events
            or JSON-RPC error responses if a `ServerError` is raised.
        """
        try:
            async for event in self.request_handler.on_resubscribe_to_task(
                request, context
            ):
                # Wrap the event in StreamResponse for consistent client parsing
                stream_response = proto_utils.ToProto.stream_response(event)
                yield prepare_response_object(
                    self._get_request_id(context),
                    stream_response,
                    (StreamResponse,),
                    SendStreamingMessageSuccessResponse,
                    SendStreamingMessageResponse,
                )
        except ServerError as e:
            yield SendStreamingMessageResponse(
                root=JSONRPCErrorResponse(
                    id=self._get_request_id(context), error=e.error if e.error else InternalError()
                )
            )

    async def get_push_notification_config(
        self,
        request: GetTaskPushNotificationConfigRequest,
        context: ServerCallContext | None = None,
    ) -> GetTaskPushNotificationConfigResponse:
        """Handles the 'tasks/pushNotificationConfig/get' JSON-RPC method.

        Args:
            request: The incoming `GetTaskPushNotificationConfigRequest` object.
            context: Context provided by the server.

        Returns:
            A `GetTaskPushNotificationConfigResponse` object containing the config or a JSON-RPC error.
        """
        try:
            config = (
                await self.request_handler.on_get_task_push_notification_config(
                    request, context
                )
            )
            return prepare_response_object(
                self._get_request_id(context),
                config,
                (TaskPushNotificationConfig,),
                GetTaskPushNotificationConfigSuccessResponse,
                GetTaskPushNotificationConfigResponse,
            )
        except ServerError as e:
            return GetTaskPushNotificationConfigResponse(
                root=JSONRPCErrorResponse(
                    id=self._get_request_id(context), error=e.error if e.error else InternalError()
                )
            )

    @validate(
        lambda self: self.agent_card.capabilities.push_notifications,
        'Push notifications are not supported by the agent',
    )
    async def set_push_notification_config(
        self,
        request: SetTaskPushNotificationConfigRequest,
        context: ServerCallContext | None = None,
    ) -> SetTaskPushNotificationConfigResponse:
        """Handles the 'tasks/pushNotificationConfig/set' JSON-RPC method.

        Requires the agent to support push notifications.

        Args:
            request: The incoming `SetTaskPushNotificationConfigRequest` object.
            context: Context provided by the server.

        Returns:
            A `SetTaskPushNotificationConfigResponse` object containing the config or a JSON-RPC error.

        Raises:
            ServerError: If push notifications are not supported by the agent
                (due to the `@validate` decorator).
        """
        try:
            # Extract TaskPushNotificationConfig from the request
            # and set the name from parent if not set
            config = request.config
            if not config.name and request.parent:
                config.name = f'{request.parent}/pushNotificationConfigs/{request.config_id or "default"}'
            result = (
                await self.request_handler.on_set_task_push_notification_config(
                    config, context
                )
            )
            return prepare_response_object(
                self._get_request_id(context),
                result,
                (TaskPushNotificationConfig,),
                SetTaskPushNotificationConfigSuccessResponse,
                SetTaskPushNotificationConfigResponse,
            )
        except ServerError as e:
            return SetTaskPushNotificationConfigResponse(
                root=JSONRPCErrorResponse(
                    id=self._get_request_id(context), error=e.error if e.error else InternalError()
                )
            )

    async def on_get_task(
        self,
        request: GetTaskRequest,
        context: ServerCallContext | None = None,
    ) -> GetTaskResponse:
        """Handles the 'tasks/get' JSON-RPC method.

        Args:
            request: The incoming `GetTaskRequest` object.
            context: Context provided by the server.

        Returns:
            A `GetTaskResponse` object containing the Task or a JSON-RPC error.
        """
        try:
            task = await self.request_handler.on_get_task(
                request, context
            )
        except ServerError as e:
            return GetTaskResponse(
                root=JSONRPCErrorResponse(
                    id=self._get_request_id(context), error=e.error if e.error else InternalError()
                )
            )

        if task:
            return prepare_response_object(
                self._get_request_id(context),
                task,
                (Task,),
                GetTaskSuccessResponse,
                GetTaskResponse,
            )

        return GetTaskResponse(
            root=JSONRPCErrorResponse(id=self._get_request_id(context), error=TaskNotFoundError())
        )

    async def list_push_notification_config(
        self,
        request: ListTaskPushNotificationConfigRequest,
        context: ServerCallContext | None = None,
    ) -> ListTaskPushNotificationConfigResponse:
        """Handles the 'tasks/pushNotificationConfig/list' JSON-RPC method.

        Args:
            request: The incoming `ListTaskPushNotificationConfigRequest` object.
            context: Context provided by the server.

        Returns:
            A `ListTaskPushNotificationConfigResponse` object containing the config or a JSON-RPC error.
        """
        try:
            config = await self.request_handler.on_list_task_push_notification_config(
                request, context
            )
            return prepare_response_object(
                self._get_request_id(context),
                config,
                (list,),
                ListTaskPushNotificationConfigSuccessResponse,
                ListTaskPushNotificationConfigResponse,
            )
        except ServerError as e:
            return ListTaskPushNotificationConfigResponse(
                root=JSONRPCErrorResponse(
                    id=self._get_request_id(context), error=e.error if e.error else InternalError()
                )
            )

    async def delete_push_notification_config(
        self,
        request: DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext | None = None,
    ) -> DeleteTaskPushNotificationConfigResponse:
        """Handles the 'tasks/pushNotificationConfig/list' JSON-RPC method.

        Args:
            request: The incoming `DeleteTaskPushNotificationConfigRequest` object.
            context: Context provided by the server.

        Returns:
            A `DeleteTaskPushNotificationConfigResponse` object containing the config or a JSON-RPC error.
        """
        try:
            (
                await self.request_handler.on_delete_task_push_notification_config(
                    request, context
                )
            )
            return DeleteTaskPushNotificationConfigResponse(
                root=DeleteTaskPushNotificationConfigSuccessResponse(
                    id=self._get_request_id(context), result=None
                )
            )
        except ServerError as e:
            return DeleteTaskPushNotificationConfigResponse(
                root=JSONRPCErrorResponse(
                    id=self._get_request_id(context), error=e.error if e.error else InternalError()
                )
            )

    async def get_authenticated_extended_card(
        self,
        request: GetExtendedAgentCardRequest,
        context: ServerCallContext | None = None,
    ) -> GetAuthenticatedExtendedCardResponse:
        """Handles the 'agent/authenticatedExtendedCard' JSON-RPC method.

        Args:
            request: The incoming `GetExtendedAgentCardRequest` object.
            context: Context provided by the server.

        Returns:
            A `GetAuthenticatedExtendedCardResponse` object containing the config or a JSON-RPC error.
        """
        if not self.agent_card.supports_authenticated_extended_card:
            raise ServerError(
                error=AuthenticatedExtendedCardNotConfiguredError(
                    message='Authenticated card not supported'
                )
            )

        base_card = self.extended_agent_card
        if base_card is None:
            base_card = self.agent_card

        card_to_serve = base_card
        if self.extended_card_modifier and context:
            card_to_serve = self.extended_card_modifier(base_card, context)
        elif self.card_modifier:
            card_to_serve = self.card_modifier(base_card)

        return GetAuthenticatedExtendedCardResponse(
            root=GetAuthenticatedExtendedCardSuccessResponse(
                id=self._get_request_id(context), result=card_to_serve
            )
        )
