import logging

from collections.abc import AsyncGenerator


try:
    import grpc
except ImportError as e:
    raise ImportError(
        'A2AGrpcClient requires grpcio and grpcio-tools to be installed. '
        'Install with: '
        "'pip install a2a-sdk[grpc]'"
    ) from e


from a2a.client.client import ClientConfig
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.client.optionals import Channel
from a2a.client.transports.base import ClientTransport
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.types import a2a_pb2, a2a_pb2_grpc
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    SendMessageRequest,
    SendMessageResponse,
    SetTaskPushNotificationConfigRequest,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


@trace_class(kind=SpanKind.CLIENT)
class GrpcTransport(ClientTransport):
    """A gRPC transport for the A2A client."""

    def __init__(
        self,
        channel: Channel,
        agent_card: AgentCard | None,
        extensions: list[str] | None = None,
    ):
        """Initializes the GrpcTransport."""
        self.agent_card = agent_card
        self.channel = channel
        self.stub = a2a_pb2_grpc.A2AServiceStub(channel)
        self._needs_extended_card = (
            agent_card.supports_authenticated_extended_card
            if agent_card
            else True
        )
        self.extensions = extensions

    def _get_grpc_metadata(
        self,
        extensions: list[str] | None = None,
    ) -> list[tuple[str, str]] | None:
        """Creates gRPC metadata for extensions."""
        if extensions is not None:
            return [(HTTP_EXTENSION_HEADER, ','.join(extensions))]
        if self.extensions is not None:
            return [(HTTP_EXTENSION_HEADER, ','.join(self.extensions))]
        return None

    @classmethod
    def create(
        cls,
        card: AgentCard,
        url: str,
        config: ClientConfig,
        interceptors: list[ClientCallInterceptor],
    ) -> 'GrpcTransport':
        """Creates a gRPC transport for the A2A client."""
        if config.grpc_channel_factory is None:
            raise ValueError('grpc_channel_factory is required when using gRPC')
        return cls(config.grpc_channel_factory(url), card, config.extensions)

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> SendMessageResponse:
        """Sends a non-streaming message request to the agent."""
        return await self.stub.SendMessage(
            request,
            metadata=self._get_grpc_metadata(extensions),
        )

    async def send_message_streaming(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Sends a streaming message request to the agent and yields responses as they arrive."""
        stream = self.stub.SendStreamingMessage(
            request,
            metadata=self._get_grpc_metadata(extensions),
        )
        while True:
            response = await stream.read()
            if response == grpc.aio.EOF:  # pyright: ignore[reportAttributeAccessIssue]
                break
            yield response

    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Reconnects to get task updates."""
        stream = self.stub.SubscribeToTask(
            request,
            metadata=self._get_grpc_metadata(extensions),
        )
        while True:
            response = await stream.read()
            if response == grpc.aio.EOF:  # pyright: ignore[reportAttributeAccessIssue]
                break
            yield response

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""
        return await self.stub.GetTask(
            request,
            metadata=self._get_grpc_metadata(extensions),
        )

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""
        return await self.stub.CancelTask(
            request,
            metadata=self._get_grpc_metadata(extensions),
        )

    async def set_task_callback(
        self,
        request: SetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""
        return await self.stub.SetTaskPushNotificationConfig(
            request,
            metadata=self._get_grpc_metadata(extensions),
        )

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""
        return await self.stub.GetTaskPushNotificationConfig(
            request,
            metadata=self._get_grpc_metadata(extensions),
        )

    async def get_extended_agent_card(
        self,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AgentCard:
        """Retrieves the agent's card."""
        return await self.stub.GetExtendedAgentCard(
            a2a_pb2.GetExtendedAgentCardRequest(),
            metadata=self._get_grpc_metadata(extensions),
        )

    async def close(self) -> None:
        """Closes the gRPC channel."""
        await self.channel.close()
