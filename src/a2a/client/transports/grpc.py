import logging

from collections.abc import AsyncGenerator, Callable
from functools import wraps
from typing import Any, NoReturn, cast

from a2a.client.client import ClientCallContext
from a2a.client.errors import A2AClientError, A2AClientTimeoutError


try:
    import grpc  # type: ignore[reportMissingModuleSource]

    from grpc_status import rpc_status
except ImportError as e:
    raise ImportError(
        'A2AGrpcClient requires grpcio, grpcio-tools, and grpcio-status to be installed. '
        'Install with: '
        "'pip install a2a-sdk[grpc]'"
    ) from e


from google.rpc import (  # type: ignore[reportMissingModuleSource]
    error_details_pb2,
)

from a2a.client.client import ClientConfig
from a2a.client.optionals import Channel
from a2a.client.transports.base import ClientTransport
from a2a.types import a2a_pb2_grpc
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)
from a2a.utils.constants import PROTOCOL_VERSION_CURRENT, VERSION_HEADER
from a2a.utils.errors import A2A_REASON_TO_ERROR, A2AError
from a2a.utils.proto_utils import bad_request_to_validation_errors
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


def _map_grpc_error(e: grpc.aio.AioRpcError) -> NoReturn:

    if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
        raise A2AClientTimeoutError('Client Request timed out') from e

    # Use grpc_status to cleanly extract the rich Status from the call
    status = rpc_status.from_call(cast('grpc.Call', e))
    data = None

    if status is not None:
        exception_cls: type[A2AError] | None = None
        for detail in status.details:
            if detail.Is(error_details_pb2.ErrorInfo.DESCRIPTOR):
                error_info = error_details_pb2.ErrorInfo()
                detail.Unpack(error_info)
                if error_info.domain == 'a2a-protocol.org':
                    exception_cls = A2A_REASON_TO_ERROR.get(error_info.reason)
            elif detail.Is(error_details_pb2.BadRequest.DESCRIPTOR):
                bad_request = error_details_pb2.BadRequest()
                detail.Unpack(bad_request)
                data = {'errors': bad_request_to_validation_errors(bad_request)}

        if exception_cls:
            raise exception_cls(status.message, data=data) from e

    raise A2AClientError(f'gRPC Error {e.code().name}: {e.details()}') from e


def _handle_grpc_exception(func: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except grpc.aio.AioRpcError as e:
            _map_grpc_error(e)

    return wrapper


def _handle_grpc_stream_exception(
    func: Callable[..., Any],
) -> Callable[..., Any]:
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            async for item in func(*args, **kwargs):
                yield item
        except grpc.aio.AioRpcError as e:
            _map_grpc_error(e)

    return wrapper


@trace_class(kind=SpanKind.CLIENT)
class GrpcTransport(ClientTransport):
    """A gRPC transport for the A2A client."""

    def __init__(
        self,
        channel: Channel,
        agent_card: AgentCard | None,
    ):
        """Initializes the GrpcTransport."""
        self.agent_card = agent_card
        self.channel = channel
        self.stub = a2a_pb2_grpc.A2AServiceStub(channel)

    @classmethod
    def create(
        cls,
        card: AgentCard,
        url: str,
        config: ClientConfig,
    ) -> 'GrpcTransport':
        """Creates a gRPC transport for the A2A client."""
        if config.grpc_channel_factory is None:
            raise ValueError('grpc_channel_factory is required when using gRPC')
        return cls(config.grpc_channel_factory(url), card)

    @_handle_grpc_exception
    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> SendMessageResponse:
        """Sends a non-streaming message request to the agent."""
        return await self._call_grpc(
            self.stub.SendMessage,
            request,
            context,
        )

    @_handle_grpc_stream_exception
    async def send_message_streaming(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Sends a streaming message request to the agent and yields responses as they arrive."""
        async for response in self._call_grpc_stream(
            self.stub.SendStreamingMessage,
            request,
            context,
        ):
            yield response

    @_handle_grpc_stream_exception
    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Reconnects to get task updates."""
        async for response in self._call_grpc_stream(
            self.stub.SubscribeToTask,
            request,
            context,
        ):
            yield response

    @_handle_grpc_exception
    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""
        return await self._call_grpc(
            self.stub.GetTask,
            request,
            context,
        )

    @_handle_grpc_exception
    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTasksResponse:
        """Retrieves tasks for an agent."""
        return await self._call_grpc(
            self.stub.ListTasks,
            request,
            context,
        )

    @_handle_grpc_exception
    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""
        return await self._call_grpc(
            self.stub.CancelTask,
            request,
            context,
        )

    @_handle_grpc_exception
    async def create_task_push_notification_config(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""
        return await self._call_grpc(
            self.stub.CreateTaskPushNotificationConfig,
            request,
            context,
        )

    @_handle_grpc_exception
    async def get_task_push_notification_config(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""
        return await self._call_grpc(
            self.stub.GetTaskPushNotificationConfig,
            request,
            context,
        )

    @_handle_grpc_exception
    async def list_task_push_notification_configs(
        self,
        request: ListTaskPushNotificationConfigsRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Lists push notification configurations for a specific task."""
        return await self._call_grpc(
            self.stub.ListTaskPushNotificationConfigs,
            request,
            context,
        )

    @_handle_grpc_exception
    async def delete_task_push_notification_config(
        self,
        request: DeleteTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> None:
        """Deletes the push notification configuration for a specific task."""
        await self._call_grpc(
            self.stub.DeleteTaskPushNotificationConfig,
            request,
            context,
        )

    @_handle_grpc_exception
    async def get_extended_agent_card(
        self,
        request: GetExtendedAgentCardRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AgentCard:
        """Retrieves the agent's card."""
        card = self.agent_card
        if card and not card.capabilities.extended_agent_card:
            return card

        return await self._call_grpc(
            self.stub.GetExtendedAgentCard,
            request,
            context,
        )

    async def close(self) -> None:
        """Closes the gRPC channel."""
        await self.channel.close()

    def _get_grpc_metadata(
        self, context: ClientCallContext | None
    ) -> list[tuple[str, str]]:
        metadata = [(VERSION_HEADER.lower(), PROTOCOL_VERSION_CURRENT)]
        if context and context.service_parameters:
            for key, value in context.service_parameters.items():
                metadata.append((key.lower(), value))
        return metadata

    def _get_grpc_timeout(
        self, context: ClientCallContext | None
    ) -> float | None:
        return context.timeout if context else None

    async def _call_grpc(
        self,
        method: Callable[..., Any],
        request: Any,
        context: ClientCallContext | None,
        **kwargs: Any,
    ) -> Any:

        return await method(
            request,
            metadata=self._get_grpc_metadata(context),
            timeout=self._get_grpc_timeout(context),
            **kwargs,
        )

    async def _call_grpc_stream(
        self,
        method: Callable[..., Any],
        request: Any,
        context: ClientCallContext | None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamResponse]:

        stream = method(
            request,
            metadata=self._get_grpc_metadata(context),
            timeout=self._get_grpc_timeout(context),
            **kwargs,
        )
        while True:
            response = await stream.read()
            if response == grpc.aio.EOF:  # pyright: ignore[reportAttributeAccessIssue]
                break
            yield response
