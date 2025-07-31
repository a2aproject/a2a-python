# ruff: noqa: N802
import contextlib
import logging

from abc import ABC, abstractmethod
from collections.abc import AsyncIterable, Sequence


try:
    import grpc
    import grpc.aio

    from grpc.aio import Metadata
except ImportError as e:
    raise ImportError(
        'GrpcHandler requires grpcio and grpcio-tools to be installed. '
        'Install with: '
        "'pip install a2a-sdk[grpc]'"
    ) from e

from collections.abc import Callable

import a2a.grpc.a2a_pb2_grpc as a2a_grpc

from a2a import types
from a2a.auth.user import UnauthenticatedUser
from a2a.extensions.common import (
    HTTP_EXTENSION_HEADER,
    get_requested_extensions,
)
from a2a.grpc import a2a_pb2
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import AgentCard, TaskNotFoundError
from a2a.utils import proto_utils
from a2a.utils.errors import ServerError
from a2a.utils.helpers import validate, validate_async_generator


logger = logging.getLogger(__name__)

# For now we use a trivial wrapper on the grpc context object


class CallContextBuilder(ABC):
    """A class for building ServerCallContexts using the Starlette Request."""

    @abstractmethod
    def build(self, context: grpc.aio.ServicerContext) -> ServerCallContext:
        """Builds a ServerCallContext from a gRPC Request."""


def _get_metadata_value(
    context: grpc.aio.ServicerContext, key: str
) -> list[str]:
    md = context.invocation_metadata
    raw_values: list[str | bytes] = []
    if isinstance(md, Metadata):
        raw_values = md.get_all(key)
    elif isinstance(md, Sequence):
        lower_key = key.lower()
        raw_values = [e for (k, e) in md if k.lower() == lower_key]
    return [e if isinstance(e, str) else e.decode('utf-8') for e in raw_values]


class DefaultCallContextBuilder(CallContextBuilder):
    """A default implementation of CallContextBuilder."""

    def build(self, context: grpc.aio.ServicerContext) -> ServerCallContext:
        """Builds the ServerCallContext."""
        user = UnauthenticatedUser()
        state = {}
        with contextlib.suppress(Exception):
            state['grpc_context'] = context
        return ServerCallContext(
            user=user,
            state=state,
            requested_extensions=get_requested_extensions(
                _get_metadata_value(context, HTTP_EXTENSION_HEADER)
            ),
        )


class GrpcHandler(a2a_grpc.A2AServiceServicer):
    """Maps incoming gRPC requests to the appropriate request handler method."""

    def __init__(
        self,
        agent_card: AgentCard,
        request_handler: RequestHandler,
        context_builder: CallContextBuilder | None = None,
        card_modifier: Callable[[AgentCard], AgentCard] | None = None,
    ):
        """Initializes the GrpcHandler.

        Args:
            agent_card: The AgentCard describing the agent's capabilities.
            request_handler: The underlying `RequestHandler` instance to
                             delegate requests to.
            context_builder: The CallContextBuilder object. If none the
                             DefaultCallContextBuilder is used.
            card_modifier: An optional callback to dynamically modify the public
              agent card before it is served.
        """
        self.agent_card = agent_card
        self.request_handler = request_handler
        self.context_builder = context_builder or DefaultCallContextBuilder()
        self.card_modifier = card_modifier

    async def SendMessage(
        self,
        request: a2a_pb2.SendMessageRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_pb2.SendMessageResponse:
        """Handles the 'SendMessage' gRPC method.

        Args:
            request: The incoming `SendMessageRequest` object.
            context: Context provided by the server.

        Returns:
            A `SendMessageResponse` object containing the result (Task or
            Message) or throws an error response if a `ServerError` is raised
            by the handler.
        """
        try:
            # Construct the server context object
            server_context = self.context_builder.build(context)
            # Transform the proto object to the python internal objects
            a2a_request = proto_utils.FromProto.message_send_params(
                request,
            )
            task_or_message = await self.request_handler.on_message_send(
                a2a_request, server_context
            )
            self._set_extension_metadata(context, server_context)
            return proto_utils.ToProto.task_or_message(task_or_message)
        except ServerError as e:
            await self.abort_context(e, context)
        return a2a_pb2.SendMessageResponse()

    @validate_async_generator(
        lambda self: self.agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def SendStreamingMessage(
        self,
        request: a2a_pb2.SendMessageRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterable[a2a_pb2.StreamResponse]:
        """Handles the 'StreamMessage' gRPC method.

        Yields response objects as they are produced by the underlying handler's
        stream.

        Args:
            request: The incoming `SendMessageRequest` object.
            context: Context provided by the server.

        Yields:
            `StreamResponse` objects containing streaming events
            (Task, Message, TaskStatusUpdateEvent, TaskArtifactUpdateEvent)
            or gRPC error responses if a `ServerError` is raised.
        """
        server_context = self.context_builder.build(context)
        # Transform the proto object to the python internal objects
        a2a_request = proto_utils.FromProto.message_send_params(
            request,
        )
        try:
            async for event in self.request_handler.on_message_send_stream(
                a2a_request, server_context
            ):
                yield proto_utils.ToProto.stream_response(event)
            self._set_extension_metadata(context, server_context)
        except ServerError as e:
            await self.abort_context(e, context)
        return

    async def CancelTask(
        self,
        request: a2a_pb2.CancelTaskRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_pb2.Task:
        """Handles the 'CancelTask' gRPC method.

        Args:
            request: The incoming `CancelTaskRequest` object.
            context: Context provided by the server.

        Returns:
            A `Task` object containing the updated Task or a gRPC error.
        """
        try:
            server_context = self.context_builder.build(context)
            task_id_params = proto_utils.FromProto.task_id_params(request)
            task = await self.request_handler.on_cancel_task(
                task_id_params, server_context
            )
            if task:
                return proto_utils.ToProto.task(task)
            await self.abort_context(
                ServerError(error=TaskNotFoundError()), context
            )
        except ServerError as e:
            await self.abort_context(e, context)
        return a2a_pb2.Task()

    @validate_async_generator(
        lambda self: self.agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def TaskSubscription(
        self,
        request: a2a_pb2.TaskSubscriptionRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterable[a2a_pb2.StreamResponse]:
        """Handles the 'TaskSubscription' gRPC method.

        Yields response objects as they are produced by the underlying handler's
        stream.

        Args:
            request: The incoming `TaskSubscriptionRequest` object.
            context: Context provided by the server.

        Yields:
            `StreamResponse` objects containing streaming events
        """
        try:
            server_context = self.context_builder.build(context)
            async for event in self.request_handler.on_resubscribe_to_task(
                proto_utils.FromProto.task_id_params(request),
                server_context,
            ):
                yield proto_utils.ToProto.stream_response(event)
        except ServerError as e:
            await self.abort_context(e, context)

    async def GetTaskPushNotificationConfig(
        self,
        request: a2a_pb2.GetTaskPushNotificationConfigRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_pb2.TaskPushNotificationConfig:
        """Handles the 'GetTaskPushNotificationConfig' gRPC method.

        Args:
            request: The incoming `GetTaskPushNotificationConfigRequest` object.
            context: Context provided by the server.

        Returns:
            A `TaskPushNotificationConfig` object containing the config.
        """
        try:
            server_context = self.context_builder.build(context)
            config = (
                await self.request_handler.on_get_task_push_notification_config(
                    proto_utils.FromProto.task_id_params(request),
                    server_context,
                )
            )
            return proto_utils.ToProto.task_push_notification_config(config)
        except ServerError as e:
            await self.abort_context(e, context)
        return a2a_pb2.TaskPushNotificationConfig()

    @validate(
        lambda self: self.agent_card.capabilities.push_notifications,
        'Push notifications are not supported by the agent',
    )
    async def CreateTaskPushNotificationConfig(
        self,
        request: a2a_pb2.CreateTaskPushNotificationConfigRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_pb2.TaskPushNotificationConfig:
        """Handles the 'CreateTaskPushNotificationConfig' gRPC method.

        Requires the agent to support push notifications.

        Args:
            request: The incoming `CreateTaskPushNotificationConfigRequest` object.
            context: Context provided by the server.

        Returns:
            A `TaskPushNotificationConfig` object

        Raises:
            ServerError: If push notifications are not supported by the agent
                (due to the `@validate` decorator).
        """
        try:
            server_context = self.context_builder.build(context)
            config = (
                await self.request_handler.on_set_task_push_notification_config(
                    proto_utils.FromProto.task_push_notification_config_request(
                        request,
                    ),
                    server_context,
                )
            )
            return proto_utils.ToProto.task_push_notification_config(config)
        except ServerError as e:
            await self.abort_context(e, context)
        return a2a_pb2.TaskPushNotificationConfig()

    async def GetTask(
        self,
        request: a2a_pb2.GetTaskRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_pb2.Task:
        """Handles the 'GetTask' gRPC method.

        Args:
            request: The incoming `GetTaskRequest` object.
            context: Context provided by the server.

        Returns:
            A `Task` object.
        """
        try:
            server_context = self.context_builder.build(context)
            task = await self.request_handler.on_get_task(
                proto_utils.FromProto.task_query_params(request), server_context
            )
            if task:
                return proto_utils.ToProto.task(task)
            await self.abort_context(
                ServerError(error=TaskNotFoundError()), context
            )
        except ServerError as e:
            await self.abort_context(e, context)
        return a2a_pb2.Task()

    async def GetAgentCard(
        self,
        request: a2a_pb2.GetAgentCardRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_pb2.AgentCard:
        """Get the agent card for the agent served."""
        card_to_serve = self.agent_card
        if self.card_modifier:
            card_to_serve = self.card_modifier(card_to_serve)
        return proto_utils.ToProto.agent_card(card_to_serve)

    async def abort_context(
        self, error: ServerError, context: grpc.aio.ServicerContext
    ) -> None:
        """Sets the grpc errors appropriately in the context."""
        match error.error:
            case types.JSONParseError():
                await context.abort(
                    grpc.StatusCode.INTERNAL,
                    f'JSONParseError: {error.error.message}',
                )
            case types.InvalidRequestError():
                await context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    f'InvalidRequestError: {error.error.message}',
                )
            case types.MethodNotFoundError():
                await context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f'MethodNotFoundError: {error.error.message}',
                )
            case types.InvalidParamsError():
                await context.abort(
                    grpc.StatusCode.INVALID_ARGUMENT,
                    f'InvalidParamsError: {error.error.message}',
                )
            case types.InternalError():
                await context.abort(
                    grpc.StatusCode.INTERNAL,
                    f'InternalError: {error.error.message}',
                )
            case types.TaskNotFoundError():
                await context.abort(
                    grpc.StatusCode.NOT_FOUND,
                    f'TaskNotFoundError: {error.error.message}',
                )
            case types.TaskNotCancelableError():
                await context.abort(
                    grpc.StatusCode.UNIMPLEMENTED,
                    f'TaskNotCancelableError: {error.error.message}',
                )
            case types.PushNotificationNotSupportedError():
                await context.abort(
                    grpc.StatusCode.UNIMPLEMENTED,
                    f'PushNotificationNotSupportedError: {error.error.message}',
                )
            case types.UnsupportedOperationError():
                await context.abort(
                    grpc.StatusCode.UNIMPLEMENTED,
                    f'UnsupportedOperationError: {error.error.message}',
                )
            case types.ContentTypeNotSupportedError():
                await context.abort(
                    grpc.StatusCode.UNIMPLEMENTED,
                    f'ContentTypeNotSupportedError: {error.error.message}',
                )
            case types.InvalidAgentResponseError():
                await context.abort(
                    grpc.StatusCode.INTERNAL,
                    f'InvalidAgentResponseError: {error.error.message}',
                )
            case _:
                await context.abort(
                    grpc.StatusCode.UNKNOWN,
                    f'Unknown error type: {error.error}',
                )

    def _set_extension_metadata(
        self,
        context: grpc.aio.ServicerContext,
        server_context: ServerCallContext,
    ) -> None:
        if server_context.activated_extensions:
            context.set_trailing_metadata(
                [
                    (HTTP_EXTENSION_HEADER, e)
                    for e in sorted(server_context.activated_extensions)
                ]
            )
