from collections.abc import AsyncGenerator, AsyncIterator, Callable
from typing import Any

from a2a.client.client import (
    Client,
    ClientCallContext,
    ClientConfig,
    ClientEvent,
    Consumer,
)
from a2a.client.client_task_manager import ClientTaskManager
from a2a.client.middleware import ClientCallInterceptor
from a2a.client.transports.base import ClientTransport
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    Message,
    SendMessageConfiguration,
    SendMessageRequest,
    SetTaskPushNotificationConfigRequest,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)


class BaseClient(Client):
    """Base implementation of the A2A client, containing transport-independent logic."""

    def __init__(
        self,
        card: AgentCard,
        config: ClientConfig,
        transport: ClientTransport,
        consumers: list[Consumer],
        middleware: list[ClientCallInterceptor],
    ):
        super().__init__(consumers, middleware)
        self._card = card
        self._config = config
        self._transport = transport

    async def send_message(
        self,
        request: Message,
        *,
        configuration: SendMessageConfiguration | None = None,
        context: ClientCallContext | None = None,
        request_metadata: dict[str, Any] | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncIterator[ClientEvent]:
        """Sends a message to the agent.

        This method handles both streaming and non-streaming (polling) interactions
        based on the client configuration and agent capabilities. It will yield
        events as they are received from the agent.

        Args:
            request: The message to send to the agent.
            configuration: Optional per-call overrides for message sending behavior.
            context: The client call context.
            request_metadata: Extensions Metadata attached to the request.
            extensions: List of extensions to be activated.

        Yields:
            An async iterator of `ClientEvent`
        """
        config = SendMessageConfiguration(
            accepted_output_modes=self._config.accepted_output_modes,
            blocking=not self._config.polling,
            push_notification_config=(
                self._config.push_notification_configs[0]
                if self._config.push_notification_configs
                else None
            ),
        )

        if configuration:
            config.MergeFrom(configuration)
            # Proto3 doesn't support HasField for scalars, so MergeFrom won't
            # override with default values (e.g. False). We explicitly set it here
            # assuming configuration is authoritative.
            config.blocking = configuration.blocking

        send_message_request = SendMessageRequest(
            message=request, configuration=config, metadata=request_metadata
        )

        if not self._config.streaming or not self._card.capabilities.streaming:
            response = await self._transport.send_message(
                send_message_request, context=context, extensions=extensions
            )

            # In non-streaming case we convert to a StreamResponse so that the
            # client always sees the same iterator.
            stream_response = StreamResponse()
            client_event: ClientEvent
            if response.HasField('task'):
                stream_response.task.CopyFrom(response.task)
                client_event = (stream_response, response.task)
            elif response.HasField('message'):
                stream_response.message.CopyFrom(response.message)
                client_event = (stream_response, None)
            else:
                # Response must have either task or message
                raise ValueError('Response has neither task nor message')

            await self.consume(client_event, self._card)
            yield client_event
            return

        stream = self._transport.send_message_streaming(
            send_message_request, context=context, extensions=extensions
        )
        async for client_event in self._process_stream(stream):
            yield client_event

    async def _process_stream(
        self, stream: AsyncIterator[StreamResponse]
    ) -> AsyncGenerator[ClientEvent]:
        tracker = ClientTaskManager()
        async for stream_response in stream:
            client_event: ClientEvent
            # When we get a message in the stream then we don't expect any
            # further messages so yield and return
            if stream_response.HasField('message'):
                client_event = (stream_response, None)
                await self.consume(client_event, self._card)
                yield client_event
                return

            # Otherwise track the task / task update then yield to the client
            await tracker.process(stream_response)
            updated_task = tracker.get_task_or_raise()
            client_event = (stream_response, updated_task)
            await self.consume(client_event, self._card)
            yield client_event

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task.

        Args:
            request: The `GetTaskRequest` object specifying the task ID.
            context: The client call context.
            extensions: List of extensions to be activated.

        Returns:
            A `Task` object representing the current state of the task.
        """
        return await self._transport.get_task(
            request, context=context, extensions=extensions
        )

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task.

        Args:
            request: The `CancelTaskRequest` object specifying the task ID.
            context: The client call context.
            extensions: List of extensions to be activated.

        Returns:
            A `Task` object containing the updated task status.
        """
        return await self._transport.cancel_task(
            request, context=context, extensions=extensions
        )

    async def set_task_callback(
        self,
        request: SetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task.

        Args:
            request: The `TaskPushNotificationConfig` object with the new configuration.
            context: The client call context.
            extensions: List of extensions to be activated.

        Returns:
            The created or updated `TaskPushNotificationConfig` object.
        """
        return await self._transport.set_task_callback(
            request, context=context, extensions=extensions
        )

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task.

        Args:
            request: The `GetTaskPushNotificationConfigParams` object specifying the task.
            context: The client call context.
            extensions: List of extensions to be activated.

        Returns:
            A `TaskPushNotificationConfig` object containing the configuration.
        """
        return await self._transport.get_task_callback(
            request, context=context, extensions=extensions
        )

    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncIterator[ClientEvent]:
        """Resubscribes to a task's event stream.

        This is only available if both the client and server support streaming.

        Args:
            request: Parameters to identify the task to resubscribe to.
            context: The client call context.
            extensions: List of extensions to be activated.

        Yields:
            An async iterator of `ClientEvent` objects.

        Raises:
            NotImplementedError: If streaming is not supported by the client or server.
        """
        if not self._config.streaming or not self._card.capabilities.streaming:
            raise NotImplementedError(
                'client and/or server do not support resubscription.'
            )

        # Note: resubscribe can only be called on an existing task. As such,
        # we should never see Message updates, despite the typing of the service
        # definition indicating it may be possible.
        stream = self._transport.subscribe(
            request, context=context, extensions=extensions
        )
        async for client_event in self._process_stream(stream):
            yield client_event

    async def get_extended_agent_card(
        self,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
        signature_verifier: Callable[[AgentCard], None] | None = None,
    ) -> AgentCard:
        """Retrieves the agent's card.

        This will fetch the authenticated card if necessary and update the
        client's internal state with the new card.

        Args:
            context: The client call context.
            extensions: List of extensions to be activated.
            signature_verifier: A callable used to verify the agent card's signatures.

        Returns:
            The `AgentCard` for the agent.
        """
        card = await self._transport.get_extended_agent_card(
            context=context,
            extensions=extensions,
            signature_verifier=signature_verifier,
        )
        self._card = card
        return card

    async def close(self) -> None:
        """Closes the underlying transport."""
        await self._transport.close()
