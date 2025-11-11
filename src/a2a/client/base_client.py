from collections.abc import AsyncIterator
from typing import Any

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
from a2a.client.transports.base import ClientTransport
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
        context: ClientCallContext | None = None,
        request_metadata: dict[str, Any] | None = None,
    ) -> AsyncIterator[ClientEvent | Message]:
        """Sends a message to the agent.

        This method handles both streaming and non-streaming (polling) interactions
        based on the client configuration and agent capabilities. It will yield
        events as they are received from the agent.

        Args:
            request: The message to send to the agent.
            context: The client call context.
            request_metadata: Extensions Metadata attached to the request.

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
        params = MessageSendParams(
            message=request, configuration=config, metadata=request_metadata
        )

        if not self._config.streaming or not self._card.capabilities.streaming:
            response = await self._transport.send_message(
                params, context=context
            )
            result = (
                (response, None) if isinstance(response, Task) else response
            )
            await self.consume(result, self._card)
            yield result
            return

        tracker = ClientTaskManager()
        stream = self._transport.send_message_streaming(params, context=context)

        first_event = await anext(stream)
        # The response from a server may be either exactly one Message or a
        # series of Task updates. Separate out the first message for special
        # case handling, which allows us to simplify further stream processing.
        if isinstance(first_event, Message):
            await self.consume(first_event, self._card)
            yield first_event
            return

        yield await self._process_response(tracker, first_event)

        async for event in stream:
            yield await self._process_response(tracker, event)

    async def _process_response(
        self,
        tracker: ClientTaskManager,
        event: Task | Message | TaskStatusUpdateEvent | TaskArtifactUpdateEvent,
    ) -> ClientEvent:
        if isinstance(event, Message):
            raise A2AClientInvalidStateError(
                'received a streamed Message from server after first response; this is not supported'
            )
        await tracker.process(event)
        task = tracker.get_task_or_raise()
        update = None if isinstance(event, Task) else event
        client_event = (task, update)
        await self.consume(client_event, self._card)
        return client_event

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
        return await self._transport.get_task(request, context=context)

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
        return await self._transport.cancel_task(request, context=context)

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
        return await self._transport.set_task_callback(request, context=context)

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
        return await self._transport.get_task_callback(request, context=context)

    async def resubscribe(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncIterator[ClientEvent]:
        """Resubscribes to a task's event stream.

        This is only available if both the client and server support streaming.

        Args:
            request: Parameters to identify the task to resubscribe to.
            context: The client call context.

        Yields:
            An async iterator of `ClientEvent` objects.

        Raises:
            NotImplementedError: If streaming is not supported by the client or server.
        """
        if not self._config.streaming or not self._card.capabilities.streaming:
            raise NotImplementedError(
                'client and/or server do not support resubscription.'
            )

        tracker = ClientTaskManager()
        # Note: resubscribe can only be called on an existing task. As such,
        # we should never see Message updates, despite the typing of the service
        # definition indicating it may be possible.
        async for event in self._transport.resubscribe(
            request, context=context
        ):
            yield await self._process_response(tracker, event)

    async def get_card(
        self, *, context: ClientCallContext | None = None
    ) -> AgentCard:
        """Retrieves the agent's card.

        This will fetch the authenticated card if necessary and update the
        client's internal state with the new card.

        Args:
            context: The client call context.

        Returns:
            The `AgentCard` for the agent.
        """
        card = await self._transport.get_card(context=context)
        self._card = card
        return card

    async def close(self) -> None:
        """Closes the underlying transport."""
        await self._transport.close()
