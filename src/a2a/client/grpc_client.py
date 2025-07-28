import logging

from collections.abc import AsyncGenerator, AsyncIterator


try:
    import grpc
except ImportError as e:
    raise ImportError(
        'A2AGrpcClient requires grpcio and grpcio-tools to be installed. '
        'Install with: '
        "'pip install a2a-sdk[grpc]'"
    ) from e


from a2a.client.client import (
    Client,
    ClientCallContext,
    ClientConfig,
    ClientEvent,
    Consumer,
)
from a2a.client.client_task_manager import ClientTaskManager
from a2a.client.errors import A2AClientInvalidStateError
from a2a.client.middleware import ClientCallInterceptor
from a2a.grpc import a2a_pb2, a2a_pb2_grpc
from a2a.types import (
    AgentCard,
    GetTaskPushNotificationConfigParams,
    Message,
    MessageSendConfiguration,
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
class GrpcTransportClient:
    """Transport specific details for interacting with an A2A agent via gRPC."""

    def __init__(
        self,
        grpc_stub: a2a_pb2_grpc.A2AServiceStub,
        agent_card: AgentCard | None,
    ):
        """Initializes the GrpcTransportClient.

        Requires an `AgentCard` and a grpc `A2AServiceStub`.

        Args:
            grpc_stub: A grpc client stub.
            agent_card: The agent card object.
        """
        self.agent_card = agent_card
        self.stub = grpc_stub
        # If they don't provide an agent card, but do have a stub, lookup the
        # card from the stub.
        self._needs_extended_card = (
            agent_card.supports_authenticated_extended_card
            if agent_card
            else True
        )

    async def send_message(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task | Message:
        """Sends a non-streaming message request to the agent.

        Args:
            request: The `MessageSendParams` object containing the message and configuration.
            context: The client call context.

        Returns:
            A `Task` or `Message` object containing the agent's response.
        """
        response = await self.stub.SendMessage(
            a2a_pb2.SendMessageRequest(
                request=proto_utils.ToProto.message(request.message),
                configuration=proto_utils.ToProto.message_send_configuration(
                    request.configuration
                ),
                metadata=proto_utils.ToProto.metadata(request.metadata),
            )
        )
        if response.task:
            return proto_utils.FromProto.task(response.task)
        return proto_utils.FromProto.message(response.msg)

    async def send_message_streaming(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[
        Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
    ]:
        """Sends a streaming message request to the agent and yields responses as they arrive.

        This method uses gRPC streams to receive a stream of updates from the
        agent.

        Args:
            request: The `MessageSendParams` object containing the message and configuration.
            context: The client call context.

        Yields:
            `Message` or `Task` or `TaskStatusUpdateEvent` or
            `TaskArtifactUpdateEvent` objects as they are received in the
            stream.
        """
        stream = self.stub.SendStreamingMessage(
            a2a_pb2.SendMessageRequest(
                request=proto_utils.ToProto.message(request.message),
                configuration=proto_utils.ToProto.message_send_configuration(
                    request.configuration
                ),
                metadata=proto_utils.ToProto.metadata(request.metadata),
            )
        )
        while True:
            response = await stream.read()
            if response == grpc.aio.EOF:  # pyright: ignore [reportAttributeAccessIssue]
                break
            yield proto_utils.FromProto.stream_response(response)

    async def resubscribe(
        self, request: TaskIdParams, *, context: ClientCallContext | None = None
    ) -> AsyncGenerator[
        Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
    ]:
        """Reconnects to get task updates.

        This method uses a unary server-side stream to receive updates.

        Args:
            request: The `TaskIdParams` object containing the task information to reconnect to.
            context: The client call context.

        Yields:
            Task update events, which can be either a Task, Message,
            TaskStatusUpdateEvent, or TaskArtifactUpdateEvent.

        Raises:
            A2AClientInvalidStateError: If the server returns an invalid response.
        """
        stream = self.stub.TaskSubscription(
            a2a_pb2.TaskSubscriptionRequest(name=f'tasks/{request.id}')
        )
        while True:
            response = await stream.read()
            if response == grpc.aio.EOF:  # pyright: ignore [reportAttributeAccessIssue]
                break
            yield proto_utils.FromProto.stream_response(response)

    async def get_task(
        self,
        request: TaskQueryParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task.

        Args:
            request: The `TaskQueryParams` object specifying the task ID
            context: The client call context.

        Returns:
            A `Task` object containing the Task.
        """
        task = await self.stub.GetTask(
            a2a_pb2.GetTaskRequest(name=f'tasks/{request.id}')
        )
        return proto_utils.FromProto.task(task)

    async def cancel_task(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task.

        Args:
            request: The `TaskIdParams` object specifying the task ID.
            context: The client call context.

        Returns:
            A `Task` object containing the updated Task
        """
        task = await self.stub.CancelTask(
            a2a_pb2.CancelTaskRequest(name=f'tasks/{request.id}')
        )
        return proto_utils.FromProto.task(task)

    async def set_task_callback(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task.

        Args:
            request: The `TaskPushNotificationConfig` object specifying the task ID and configuration.
            context: The client call context.

        Returns:
            A `TaskPushNotificationConfig` object containing the config.
        """
        config = await self.stub.CreateTaskPushNotificationConfig(
            a2a_pb2.CreateTaskPushNotificationConfigRequest(
                parent='',
                config_id='',
                config=proto_utils.ToProto.task_push_notification_config(
                    request
                ),
            )
        )
        return proto_utils.FromProto.task_push_notification_config_request(
            config
        )

    async def get_task_callback(
        self,
        request: TaskIdParams,  # TODO: Update to a push id params
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task.

        Args:
            request: The `TaskIdParams` object specifying the task ID.
            context: The client call context.

        Returns:
            A `TaskPushNotificationConfig` object containing the configuration.
        """
        config = await self.stub.GetTaskPushNotificationConfig(
            a2a_pb2.GetTaskPushNotificationConfigRequest(
                name=f'tasks/{request.id}/pushNotification/undefined',
            )
        )
        return proto_utils.FromProto.task_push_notification_config_request(
            config
        )

    async def get_card(
        self,
        *,
        context: ClientCallContext | None = None,
    ) -> AgentCard:
        """Retrieves the authenticated card (if necessary) or the public one.

        Args:
            context: The client call context.

        Returns:
            A `AgentCard` object containing the card.

        Raises:
            grpc.RpcError: If a gRPC error occurs during the request.
        """
        # If we don't have the public card, try to get that first.
        card = self.agent_card
        if card is None and not self._needs_extended_card:
            raise ValueError('Agent card is not available.')

        if not self._needs_extended_card:
            return card

        card_pb = await self.stub.GetAgentCard(
            a2a_pb2.GetAgentCardRequest(),
        )
        card = proto_utils.FromProto.agent_card(card_pb)
        self.agent_card = card
        self._needs_extended_card = False
        return card


class GrpcClient(Client):
    """GrpcClient provides the Client interface for the gRPC transport."""

    def __init__(
        self,
        card: AgentCard,
        config: ClientConfig,
        consumers: list[Consumer],
        middleware: list[ClientCallInterceptor],
    ):
        super().__init__(consumers, middleware)
        if not config.grpc_channel_factory:
            raise ValueError('GRPC client requires channel factory.')
        self._card = card
        self._config = config
        channel = config.grpc_channel_factory(self._card.url)
        stub = a2a_pb2_grpc.A2AServiceStub(channel)
        self._transport_client = GrpcTransportClient(stub, self._card)

    async def send_message(
        self,
        request: Message,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncIterator[ClientEvent | Message]:
        """Sends a message to the agent.

        This method handles both streaming and non-streaming (polling) interactions
        based on the client configuration and agent capabilities. It will yield
        events as they are received from the agent.

        Args:
            request: The message to send to the agent.
            context: The client call context.

        Yields:
            An async iterator of `ClientEvent` or a final `Message` response.
        """
        config = MessageSendConfiguration(
            accepted_output_modes=self._config.accepted_output_modes,
            blocking=not self._config.polling,
            push_notification_config=(
                self._config.push_notification_configs[0]
                if self._config.push_notification_configs
                else None
            ),
        )
        if not self._config.streaming or not self._card.capabilities.streaming:
            response = await self._transport_client.send_message(
                MessageSendParams(
                    message=request,
                    configuration=config,
                ),
                context=context,
            )
            result = (
                (response, None) if isinstance(response, Task) else response
            )
            await self.consume(result, self._card)
            yield result
            return
        tracker = ClientTaskManager()
        stream = self._transport_client.send_message_streaming(
            MessageSendParams(
                message=request,
                configuration=config,
            ),
            context=context,
        )
        # Only the first event may be a Message. All others must be Task
        # or TaskStatusUpdates. Separate this one out, which allows our core
        # event processing logic to ignore that case.
        # TODO(mikeas1): Reconcile with other transport logic.
        first_event = await anext(stream)
        if isinstance(first_event, Message):
            yield first_event
            return
        yield await self._process_response(tracker, first_event)
        async for result in stream:
            yield await self._process_response(tracker, result)

    async def _process_response(
        self,
        tracker: ClientTaskManager,
        event: Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    ) -> ClientEvent:
        result = event.root.result
        # Update task, check for errors, etc.
        if isinstance(result, Message):
            raise A2AClientInvalidStateError(
                'received a streamed Message from server after first response; this'
                ' is not supported'
            )
        await tracker.process(result)
        result = (
            tracker.get_task_or_raise(),
            None if isinstance(result, Task) else result,
        )
        await self.consume(result, self._card)
        return result

    async def get_task(
        self,
        request: TaskQueryParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task.

        Args:
            request: The `TaskQueryParams` object specifying the task ID.
            context: The client call context.

        Returns:
            A `Task` object representing the current state of the task.
        """
        return await self._transport_client.get_task(
            request,
            context=context,
        )

    async def cancel_task(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task.

        Args:
            request: The `TaskIdParams` object specifying the task ID.
            context: The client call context.

        Returns:
            A `Task` object containing the updated task status.
        """
        return await self._transport_client.cancel_task(
            request,
            context=context,
        )

    async def set_task_callback(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task.

        Args:
            request: The `TaskPushNotificationConfig` object with the new configuration.
            context: The client call context.

        Returns:
            The created or updated `TaskPushNotificationConfig` object.
        """
        return await self._transport_client.set_task_callback(
            request,
            context=context,
        )

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigParams,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task.

        Args:
            request: The `GetTaskPushNotificationConfigParams` object specifying the task.
            context: The client call context.

        Returns:
            A `TaskPushNotificationConfig` object containing the configuration.
        """
        return await self._transport_client.get_task_callback(
            request,
            context=context,
        )

    async def resubscribe(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncIterator[ClientEvent]:
        """Resubscribes to a task's event stream.

        This is only available if both the client and server support streaming.

        Args:
            request: The `TaskIdParams` object specifying the task ID to resubscribe to.
            context: The client call context.

        Yields:
            An async iterator of `Task` or `Message` events.

        Raises:
            Exception: If streaming is not supported by the client or server.
        """
        if not self._config.streaming or not self._card.capabilities.streaming:
            raise NotImplementedError(
                'client and/or server do not support resubscription.'
            )
        if not self._transport_client:
            raise ValueError('Transport client is not initialized.')
        if not hasattr(self._transport_client, 'resubscribe'):
            # This can happen if the proto definitions are out of date or the method is missing
            raise NotImplementedError(
                'Resubscribe is not implemented on the gRPC transport client.'
            )
        # Note: works correctly for resubscription where the first event is the
        # current Task state.
        tracker = ClientTaskManager()
        async for result in self._transport_client.resubscribe(
            request,
            context=context,
        ):
            yield await self._process_response(tracker, result)

    async def get_card(
        self,
        *,
        context: ClientCallContext | None = None,
    ) -> AgentCard:
        """Retrieves the agent's card.

        This will fetch the authenticated card if necessary and update the
        client's internal state with the new card.

        Args:
            context: The client call context.

        Returns:
            The `AgentCard` for the agent.
        """
        card = await self._transport_client.get_card(
            context=context,
        )
        self._card = card
        return card


def NewGrpcClient(  # noqa: N802
    card: AgentCard,
    config: ClientConfig,
    consumers: list[Consumer],
    middleware: list[ClientCallInterceptor],
) -> Client:
    """Generator for the `GrpcClient` implementation."""
    return GrpcClient(card, config, consumers, middleware)
