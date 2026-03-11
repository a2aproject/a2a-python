from collections.abc import AsyncGenerator, AsyncIterator, Callable

from a2a.client.client import (
    Client,
    ClientConfig,
    ClientEvent,
    Consumer,
)
from a2a.client.client_task_manager import ClientTaskManager
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.client.transports.base import ClientTransport
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
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncIterator[ClientEvent]:
        """Sends a message to the agent.

        This method handles both streaming and non-streaming (polling) interactions
        based on the client configuration and agent capabilities. It will yield
        events as they are received from the agent.

        Args:
            request: The message to send to the agent.
            context: Optional client call context.

        Yields:
            An async iterator of `ClientEvent`
        """
        self._apply_client_config(request)
        if not self._config.streaming or not self._card.capabilities.streaming:
            response = await self._transport.send_message(
                request, context=context
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
            request, context=context
        )
        async for client_event in self._process_stream(stream):
            yield client_event

    def _apply_client_config(self, request: SendMessageRequest) -> None:
        request.configuration.return_immediately |= self._config.polling
        if (
            not request.configuration.HasField('task_push_notification_config')
            and self._config.push_notification_configs
        ):
            request.configuration.task_push_notification_config.CopyFrom(
                self._config.push_notification_configs[0]
            )
        if (
            not request.configuration.accepted_output_modes
            and self._config.accepted_output_modes
        ):
            request.configuration.accepted_output_modes.extend(
                self._config.accepted_output_modes
            )

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
    ) -> Task:
        """Retrieves the current state and history of a specific task.

        Args:
            request: The `GetTaskRequest` object specifying the task ID.
            context: Optional client call context.

        Returns:
            A `Task` object representing the current state of the task.
        """
        return await self._transport.get_task(request, context=context)

    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTasksResponse:
        """Retrieves tasks for an agent."""
        return await self._transport.list_tasks(request, context=context)

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task.

        Args:
            request: The `CancelTaskRequest` object specifying the task ID.
            context: Optional client call context.

        Returns:
            A `Task` object containing the updated task status.
        """
        return await self._transport.cancel_task(request, context=context)

    async def create_task_push_notification_config(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task.

        Args:
            request: The `TaskPushNotificationConfig` object with the new configuration.
            context: Optional client call context.

        Returns:
            The created or updated `TaskPushNotificationConfig` object.
        """
        return await self._transport.create_task_push_notification_config(
            request, context=context
        )

    async def get_task_push_notification_config(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task.

        Args:
            request: The `GetTaskPushNotificationConfigParams` object specifying the task.
            context: Optional client call context.

        Returns:
            A `TaskPushNotificationConfig` object containing the configuration.
        """
        return await self._transport.get_task_push_notification_config(
            request, context=context
        )

    async def list_task_push_notification_configs(
        self,
        request: ListTaskPushNotificationConfigsRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Lists push notification configurations for a specific task.

        Args:
            request: The `ListTaskPushNotificationConfigsRequest` object specifying the request.
            context: Optional client call context.

        Returns:
            A `ListTaskPushNotificationConfigsResponse` object.
        """
        return await self._transport.list_task_push_notification_configs(
            request, context=context
        )

    async def delete_task_push_notification_config(
        self,
        request: DeleteTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> None:
        """Deletes the push notification configuration for a specific task.

        Args:
            request: The `DeleteTaskPushNotificationConfigRequest` object specifying the request.
            context: Optional client call context.
        """
        await self._transport.delete_task_push_notification_config(
            request, context=context
        )

    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncIterator[ClientEvent]:
        """Resubscribes to a task's event stream.

        This is only available if both the client and server support streaming.

        Args:
            request: Parameters to identify the task to resubscribe to.
            context: Optional client call context.

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
        stream = self._transport.subscribe(request, context=context)
        async for client_event in self._process_stream(stream):
            yield client_event

    async def get_extended_agent_card(
        self,
        request: GetExtendedAgentCardRequest,
        *,
        context: ClientCallContext | None = None,
        signature_verifier: Callable[[AgentCard], None] | None = None,
    ) -> AgentCard:
        """Retrieves the agent's card.

        This will fetch the authenticated card if necessary and update the
        client's internal state with the new card.

        Args:
            request: The `GetExtendedAgentCardRequest` object specifying the request.
            context: Optional client call context.
            signature_verifier: A callable used to verify the agent card's signatures.

        Returns:
            The `AgentCard` for the agent.
        """
        card = await self._transport.get_extended_agent_card(
            request,
            context=context,
        )
        if signature_verifier:
            signature_verifier(card)

        self._card = card
        return card

    async def close(self) -> None:
        """Closes the underlying transport."""
        await self._transport.close()
