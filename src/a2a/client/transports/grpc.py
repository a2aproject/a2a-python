import logging

from collections.abc import AsyncGenerator, Callable


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
from a2a.grpc import a2a_pb2, a2a_pb2_grpc
from a2a.types import (
    AgentCard,
    GetTaskPushNotificationConfigParams,
    Message,
    MessageSendParams,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskStatusUpdateEvent,
)
from a2a.utils import proto_utils
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
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task | Message:
        """Sends a non-streaming message request to the agent."""
        response = await self.stub.SendMessage(
            a2a_pb2.SendMessageRequest(
                request=proto_utils.ToProto.message(request.message),
                configuration=proto_utils.ToProto.message_send_configuration(
                    request.configuration
                ),
                metadata=proto_utils.ToProto.metadata(request.metadata),
            ),
            metadata=self._get_grpc_metadata(extensions),
        )
        if response.HasField('task'):
            return proto_utils.FromProto.task(response.task)
        return proto_utils.FromProto.message(response.msg)

    async def send_message_streaming(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[
        Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
    ]:
        """Sends a streaming message request to the agent and yields responses as they arrive."""
        stream = self.stub.SendStreamingMessage(
            a2a_pb2.SendMessageRequest(
                request=proto_utils.ToProto.message(request.message),
                configuration=proto_utils.ToProto.message_send_configuration(
                    request.configuration
                ),
                metadata=proto_utils.ToProto.metadata(request.metadata),
            ),
            metadata=self._get_grpc_metadata(extensions),
        )
        while True:
            response = await stream.read()
            if response == grpc.aio.EOF:  # pyright: ignore[reportAttributeAccessIssue]
                break
            yield proto_utils.FromProto.stream_response(response)

    async def resubscribe(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[
        Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
    ]:
        """Reconnects to get task updates."""
        stream = self.stub.TaskSubscription(
            a2a_pb2.TaskSubscriptionRequest(name=f'tasks/{request.id}'),
            metadata=self._get_grpc_metadata(extensions),
        )
        while True:
            response = await stream.read()
            if response == grpc.aio.EOF:  # pyright: ignore[reportAttributeAccessIssue]
                break
            yield proto_utils.FromProto.stream_response(response)

    async def get_task(
        self,
        request: TaskQueryParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""
        task = await self.stub.GetTask(
            a2a_pb2.GetTaskRequest(
                name=f'tasks/{request.id}',
                history_length=request.history_length,
            ),
            metadata=self._get_grpc_metadata(extensions),
        )
        return proto_utils.FromProto.task(task)

    async def cancel_task(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""
        task = await self.stub.CancelTask(
            a2a_pb2.CancelTaskRequest(name=f'tasks/{request.id}'),
            metadata=self._get_grpc_metadata(extensions),
        )
        return proto_utils.FromProto.task(task)

    async def set_task_callback(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""
        config = await self.stub.CreateTaskPushNotificationConfig(
            a2a_pb2.CreateTaskPushNotificationConfigRequest(
                parent=f'tasks/{request.task_id}',
                config_id=request.push_notification_config.id,
                config=proto_utils.ToProto.task_push_notification_config(
                    request
                ),
            ),
            metadata=self._get_grpc_metadata(extensions),
        )
        return proto_utils.FromProto.task_push_notification_config(config)

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""
        config = await self.stub.GetTaskPushNotificationConfig(
            a2a_pb2.GetTaskPushNotificationConfigRequest(
                name=f'tasks/{request.id}/pushNotificationConfigs/{request.push_notification_config_id}',
            ),
            metadata=self._get_grpc_metadata(extensions),
        )
        return proto_utils.FromProto.task_push_notification_config(config)

    async def get_card(
        self,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
        signature_verifier: Callable[[AgentCard], None] | None = None,
    ) -> AgentCard:
        """Retrieves the agent's card."""
        card = self.agent_card
        if card and not self._needs_extended_card:
            return card
        if card is None and not self._needs_extended_card:
            raise ValueError('Agent card is not available.')

        card_pb = await self.stub.GetAgentCard(
            a2a_pb2.GetAgentCardRequest(),
            metadata=self._get_grpc_metadata(extensions),
        )
        card = proto_utils.FromProto.agent_card(card_pb)
        if signature_verifier:
            signature_verifier(card)

        self.agent_card = card
        self._needs_extended_card = False
        return card

    async def close(self) -> None:
        """Closes the gRPC channel."""
        await self.channel.close()
