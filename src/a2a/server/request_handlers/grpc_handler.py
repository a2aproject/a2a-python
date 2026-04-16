# ruff: noqa: N802
import logging

from abc import ABC, abstractmethod
from collections.abc import AsyncIterable, Awaitable, Callable
from typing import TypeVar


try:
    import grpc  # type: ignore[reportMissingModuleSource]
    import grpc.aio  # type: ignore[reportMissingModuleSource]

    from grpc_status import rpc_status
except ImportError as e:
    raise ImportError(
        'GrpcHandler requires grpcio, grpcio-tools, and grpcio-status to be installed. '
        'Install with: '
        "'pip install a2a-sdk[grpc]'"
    ) from e

from google.protobuf import any_pb2, empty_pb2, message
from google.rpc import error_details_pb2, status_pb2

import a2a.types.a2a_pb2_grpc as a2a_grpc

from a2a import types
from a2a.auth.user import UnauthenticatedUser, User
from a2a.extensions.common import (
    HTTP_EXTENSION_HEADER,
    get_requested_extensions,
)
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import a2a_pb2
from a2a.utils import proto_utils
from a2a.utils.errors import A2A_ERROR_REASONS, A2AError, TaskNotFoundError
from a2a.utils.proto_utils import validation_errors_to_bad_request


logger = logging.getLogger(__name__)


class GrpcServerCallContextBuilder(ABC):
    """Interface for building ServerCallContext from gRPC context."""

    @abstractmethod
    def build(self, context: grpc.aio.ServicerContext) -> ServerCallContext:
        """Builds a ServerCallContext from a gRPC ServicerContext."""


class DefaultGrpcServerCallContextBuilder(GrpcServerCallContextBuilder):
    """Default implementation of GrpcServerCallContextBuilder."""

    def build(self, context: grpc.aio.ServicerContext) -> ServerCallContext:
        """Builds a ServerCallContext from a gRPC ServicerContext."""
        state = {'grpc_context': context}
        return ServerCallContext(
            user=self.build_user(context),
            state=state,
            requested_extensions=get_requested_extensions(
                _get_metadata_value(context, HTTP_EXTENSION_HEADER)
            ),
        )

    def build_user(self, context: grpc.aio.ServicerContext) -> User:
        """Builds a User from a gRPC ServicerContext."""
        return UnauthenticatedUser()


def _get_metadata_value(
    context: grpc.aio.ServicerContext, key: str
) -> list[str]:
    md = context.invocation_metadata()
    if md is None:
        return []

    lower_key = key.lower()
    return [
        e if isinstance(e, str) else e.decode('utf-8')
        for k, e in md
        if k.lower() == lower_key
    ]


_ERROR_CODE_MAP = {
    types.InvalidRequestError: grpc.StatusCode.INVALID_ARGUMENT,
    types.MethodNotFoundError: grpc.StatusCode.NOT_FOUND,
    types.InvalidParamsError: grpc.StatusCode.INVALID_ARGUMENT,
    types.InternalError: grpc.StatusCode.INTERNAL,
    types.TaskNotFoundError: grpc.StatusCode.NOT_FOUND,
    types.TaskNotCancelableError: grpc.StatusCode.FAILED_PRECONDITION,
    types.PushNotificationNotSupportedError: grpc.StatusCode.UNIMPLEMENTED,
    types.UnsupportedOperationError: grpc.StatusCode.UNIMPLEMENTED,
    types.ContentTypeNotSupportedError: grpc.StatusCode.INVALID_ARGUMENT,
    types.InvalidAgentResponseError: grpc.StatusCode.INTERNAL,
    types.ExtendedAgentCardNotConfiguredError: grpc.StatusCode.FAILED_PRECONDITION,
    types.ExtensionSupportRequiredError: grpc.StatusCode.FAILED_PRECONDITION,
    types.VersionNotSupportedError: grpc.StatusCode.UNIMPLEMENTED,
}


TResponse = TypeVar('TResponse')


class GrpcHandler(a2a_grpc.A2AServiceServicer):
    """Maps incoming gRPC requests to the appropriate request handler method."""

    def __init__(
        self,
        request_handler: RequestHandler,
        context_builder: GrpcServerCallContextBuilder | None = None,
    ):
        """Initializes the GrpcHandler.

        Args:
            request_handler: The underlying `RequestHandler` instance to
                             delegate requests to.
            context_builder: The GrpcContextBuilder used to construct the
              ServerCallContext passed to the request_handler. If None the
              DefaultGrpcContextBuilder is used.
        """
        self.request_handler = request_handler
        self._context_builder = (
            context_builder or DefaultGrpcServerCallContextBuilder()
        )

    async def _handle_unary(
        self,
        request: message.Message,
        context: grpc.aio.ServicerContext,
        handler_func: Callable[[ServerCallContext], Awaitable[TResponse]],
        default_response: TResponse,
    ) -> TResponse:
        """Centralized error handling and context management for unary calls."""
        try:
            server_context = self._build_call_context(context, request)
            result = await handler_func(server_context)
            self._set_extension_metadata(context, server_context)
        except A2AError as e:
            await self.abort_context(e, context)
        else:
            return result
        return default_response

    async def _handle_stream(
        self,
        request: message.Message,
        context: grpc.aio.ServicerContext,
        handler_func: Callable[[ServerCallContext], AsyncIterable[TResponse]],
    ) -> AsyncIterable[TResponse]:
        """Centralized error handling and context management for streaming calls."""
        try:
            server_context = self._build_call_context(context, request)
            async for item in handler_func(server_context):
                yield item
            self._set_extension_metadata(context, server_context)
        except A2AError as e:
            await self.abort_context(e, context)

    async def SendMessage(
        self,
        request: a2a_pb2.SendMessageRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_pb2.SendMessageResponse:
        """Handles the 'SendMessage' gRPC method."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> a2a_pb2.SendMessageResponse:
            task_or_message = await self.request_handler.on_message_send(
                request, server_context
            )
            if isinstance(task_or_message, a2a_pb2.Task):
                return a2a_pb2.SendMessageResponse(task=task_or_message)
            return a2a_pb2.SendMessageResponse(message=task_or_message)

        return await self._handle_unary(
            request, context, _handler, a2a_pb2.SendMessageResponse()
        )

    async def SendStreamingMessage(
        self,
        request: a2a_pb2.SendMessageRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterable[a2a_pb2.StreamResponse]:
        """Handles the 'StreamMessage' gRPC method."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> AsyncIterable[a2a_pb2.StreamResponse]:
            async for event in self.request_handler.on_message_send_stream(
                request, server_context
            ):
                yield proto_utils.to_stream_response(event)

        async for item in self._handle_stream(request, context, _handler):
            yield item

    async def CancelTask(
        self,
        request: a2a_pb2.CancelTaskRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_pb2.Task:
        """Handles the 'CancelTask' gRPC method."""

        async def _handler(server_context: ServerCallContext) -> a2a_pb2.Task:
            task = await self.request_handler.on_cancel_task(
                request, server_context
            )
            if task:
                return task
            raise TaskNotFoundError

        return await self._handle_unary(
            request, context, _handler, a2a_pb2.Task()
        )

    async def SubscribeToTask(
        self,
        request: a2a_pb2.SubscribeToTaskRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterable[a2a_pb2.StreamResponse]:
        """Handles the 'SubscribeToTask' gRPC method."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> AsyncIterable[a2a_pb2.StreamResponse]:
            async for event in self.request_handler.on_subscribe_to_task(
                request, server_context
            ):
                yield proto_utils.to_stream_response(event)

        async for item in self._handle_stream(request, context, _handler):
            yield item

    async def GetTaskPushNotificationConfig(
        self,
        request: a2a_pb2.GetTaskPushNotificationConfigRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_pb2.TaskPushNotificationConfig:
        """Handles the 'GetTaskPushNotificationConfig' gRPC method."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> a2a_pb2.TaskPushNotificationConfig:
            return (
                await self.request_handler.on_get_task_push_notification_config(
                    request, server_context
                )
            )

        return await self._handle_unary(
            request, context, _handler, a2a_pb2.TaskPushNotificationConfig()
        )

    async def CreateTaskPushNotificationConfig(
        self,
        request: a2a_pb2.TaskPushNotificationConfig,
        context: grpc.aio.ServicerContext,
    ) -> a2a_pb2.TaskPushNotificationConfig:
        """Handles the 'CreateTaskPushNotificationConfig' gRPC method."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> a2a_pb2.TaskPushNotificationConfig:
            return await self.request_handler.on_create_task_push_notification_config(
                request, server_context
            )

        return await self._handle_unary(
            request, context, _handler, a2a_pb2.TaskPushNotificationConfig()
        )

    async def ListTaskPushNotificationConfigs(
        self,
        request: a2a_pb2.ListTaskPushNotificationConfigsRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_pb2.ListTaskPushNotificationConfigsResponse:
        """Handles the 'ListTaskPushNotificationConfig' gRPC method."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> a2a_pb2.ListTaskPushNotificationConfigsResponse:
            return await self.request_handler.on_list_task_push_notification_configs(
                request, server_context
            )

        return await self._handle_unary(
            request,
            context,
            _handler,
            a2a_pb2.ListTaskPushNotificationConfigsResponse(),
        )

    async def DeleteTaskPushNotificationConfig(
        self,
        request: a2a_pb2.DeleteTaskPushNotificationConfigRequest,
        context: grpc.aio.ServicerContext,
    ) -> empty_pb2.Empty:
        """Handles the 'DeleteTaskPushNotificationConfig' gRPC method."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> empty_pb2.Empty:
            await self.request_handler.on_delete_task_push_notification_config(
                request, server_context
            )
            return empty_pb2.Empty()

        return await self._handle_unary(
            request, context, _handler, empty_pb2.Empty()
        )

    async def GetTask(
        self,
        request: a2a_pb2.GetTaskRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_pb2.Task:
        """Handles the 'GetTask' gRPC method."""

        async def _handler(server_context: ServerCallContext) -> a2a_pb2.Task:
            task = await self.request_handler.on_get_task(
                request, server_context
            )
            if task:
                return task
            raise TaskNotFoundError

        return await self._handle_unary(
            request, context, _handler, a2a_pb2.Task()
        )

    async def ListTasks(
        self,
        request: a2a_pb2.ListTasksRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_pb2.ListTasksResponse:
        """Handles the 'ListTasks' gRPC method."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> a2a_pb2.ListTasksResponse:
            return await self.request_handler.on_list_tasks(
                request, server_context
            )

        return await self._handle_unary(
            request, context, _handler, a2a_pb2.ListTasksResponse()
        )

    async def GetExtendedAgentCard(
        self,
        request: a2a_pb2.GetExtendedAgentCardRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_pb2.AgentCard:
        """Get the extended agent card for the agent served."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> a2a_pb2.AgentCard:
            return await self.request_handler.on_get_extended_agent_card(
                request, server_context
            )

        return await self._handle_unary(
            request, context, _handler, a2a_pb2.AgentCard()
        )

    async def abort_context(
        self, error: A2AError, context: grpc.aio.ServicerContext
    ) -> None:
        """Sets the grpc errors appropriately in the context."""
        code = _ERROR_CODE_MAP.get(type(error))

        if code:
            reason = A2A_ERROR_REASONS.get(type(error), 'UNKNOWN_ERROR')
            error_info = error_details_pb2.ErrorInfo(
                reason=reason,
                domain='a2a-protocol.org',
            )

            status_code = code.value[0]
            error_msg = (
                error.message if hasattr(error, 'message') else str(error)
            )

            # Create standard Status with ErrorInfo for all A2A errors
            status = status_pb2.Status(code=status_code, message=error_msg)
            error_info_detail = any_pb2.Any()
            error_info_detail.Pack(error_info)
            status.details.append(error_info_detail)

            # Append structured field violations for validation errors
            if (
                isinstance(error, types.InvalidParamsError)
                and error.data
                and error.data.get('errors')
            ):
                bad_request_detail = any_pb2.Any()
                bad_request_detail.Pack(
                    validation_errors_to_bad_request(error.data['errors'])
                )
                status.details.append(bad_request_detail)

            # Use grpc_status to safely generate standard trailing metadata
            rich_status = rpc_status.to_status(status)

            new_metadata: list[tuple[str, str | bytes]] = []
            trailing = context.trailing_metadata()
            if trailing:
                for k, v in trailing:
                    new_metadata.append((str(k), v))

            for k, v in rich_status.trailing_metadata:
                new_metadata.append((str(k), v))

            context.set_trailing_metadata(tuple(new_metadata))
            await context.abort(rich_status.code, rich_status.details)
        else:
            await context.abort(
                grpc.StatusCode.UNKNOWN,
                f'Unknown error type: {error}',
            )

    def _set_extension_metadata(
        self,
        context: grpc.aio.ServicerContext,
        server_context: ServerCallContext,
    ) -> None:
        if server_context.activated_extensions:
            context.set_trailing_metadata(
                [
                    (HTTP_EXTENSION_HEADER.lower(), e)
                    for e in sorted(server_context.activated_extensions)
                ]
            )

    def _build_call_context(
        self,
        context: grpc.aio.ServicerContext,
        request: message.Message,
    ) -> ServerCallContext:
        server_context = self._context_builder.build(context)
        server_context.tenant = getattr(request, 'tenant', '')
        return server_context
