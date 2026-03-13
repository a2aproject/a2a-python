from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from typing import Any

from a2a.client.client import (
    Client,
    ClientCallContext,
    ClientConfig,
    ClientEvent,
    Consumer,
)
from a2a.client.client_task_manager import ClientTaskManager
from a2a.client.interceptors import (
    AfterArgs,
    BeforeArgs,
    ClientCallInput,
    ClientCallInterceptor,
    ClientCallResult,
)
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
        interceptors: list[ClientCallInterceptor],
    ):
        super().__init__(consumers, interceptors)
        self._card = card
        self._config = config
        self._transport = transport
        self._interceptors = interceptors

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
            response = await self._execute_with_interceptors(
                input_data=ClientCallInput(
                    method='send_message', value=request
                ),
                context=context,
                transport_call=lambda req, ctx: self._transport.send_message(
                    req, context=ctx
                ),
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

        async for event in self._execute_stream_with_interceptors(
            input_data=ClientCallInput(
                method='send_message_streaming', value=request
            ),
            context=context,
            transport_call=lambda req, ctx: (
                self._transport.send_message_streaming(req, context=ctx)
            ),
        ):
            yield event

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
        self,
        stream: AsyncIterator[StreamResponse],
        before_args: BeforeArgs,
    ) -> AsyncGenerator[ClientEvent]:
        tracker = ClientTaskManager()
        async for stream_response in stream:
            after_args = AfterArgs(
                result=ClientCallResult(
                    method=before_args.input.method, value=stream_response
                ),
                agent_card=self._card,
                context=before_args.context,
            )
            await self._intercept_after(after_args)
            intercepted_response = after_args.result.value
            client_event = await self._format_stream_event(
                intercepted_response, tracker
            )
            yield client_event
            if intercepted_response.HasField('message'):
                return

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
        return await self._execute_with_interceptors(
            input_data=ClientCallInput(method='get_task', value=request),
            context=context,
            transport_call=lambda req, ctx: self._transport.get_task(
                req, context=ctx
            ),
        )

    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTasksResponse:
        """Retrieves tasks for an agent."""
        return await self._execute_with_interceptors(
            input_data=ClientCallInput(method='list_tasks', value=request),
            context=context,
            transport_call=lambda req, ctx: self._transport.list_tasks(
                req, context=ctx
            ),
        )

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
        return await self._execute_with_interceptors(
            input_data=ClientCallInput(method='cancel_task', value=request),
            context=context,
            transport_call=lambda req, ctx: self._transport.cancel_task(
                req, context=ctx
            ),
        )

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
        return await self._execute_with_interceptors(
            input_data=ClientCallInput(
                method='create_task_push_notification_config', value=request
            ),
            context=context,
            transport_call=lambda req, ctx: (
                self._transport.create_task_push_notification_config(
                    req, context=ctx
                )
            ),
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
        return await self._execute_with_interceptors(
            input_data=ClientCallInput(
                method='get_task_push_notification_config', value=request
            ),
            context=context,
            transport_call=lambda req, ctx: (
                self._transport.get_task_push_notification_config(
                    req, context=ctx
                )
            ),
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
        return await self._execute_with_interceptors(
            input_data=ClientCallInput(
                method='list_task_push_notification_configs', value=request
            ),
            context=context,
            transport_call=lambda req, ctx: (
                self._transport.list_task_push_notification_configs(
                    req, context=ctx
                )
            ),
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
        return await self._execute_with_interceptors(
            input_data=ClientCallInput(
                method='delete_task_push_notification_config', value=request
            ),
            context=context,
            transport_call=lambda req, ctx: (
                self._transport.delete_task_push_notification_config(
                    req, context=ctx
                )
            ),
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

        async for event in self._execute_stream_with_interceptors(
            input_data=ClientCallInput(method='subscribe', value=request),
            context=context,
            transport_call=lambda req, ctx: self._transport.subscribe(
                req, context=ctx
            ),
        ):
            yield event

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
        card = await self._execute_with_interceptors(
            input_data=ClientCallInput(
                method='get_extended_agent_card', value=request
            ),
            context=context,
            transport_call=lambda req, ctx: (
                self._transport.get_extended_agent_card(req, context=ctx)
            ),
        )
        if signature_verifier:
            signature_verifier(card)

        self._card = card
        return card

    async def close(self) -> None:
        """Closes the underlying transport."""
        await self._transport.close()

    async def _execute_with_interceptors(
        self,
        input_data: ClientCallInput,
        context: ClientCallContext | None,
        transport_call: Callable[
            [Any, ClientCallContext | None], Awaitable[Any]
        ],
    ) -> Any:
        before_args = BeforeArgs(
            input=input_data,
            agent_card=self._card,
            context=context,
        )
        before_result = await self._intercept_before(before_args)

        if before_result is not None:
            early_after_args = AfterArgs(
                result=ClientCallResult(
                    method=input_data.method,
                    value=before_result['early_return'].value,
                ),
                agent_card=self._card,
                context=before_args.context,
            )
            await self._intercept_after(
                early_after_args,
                before_result['executed'],
            )
            return early_after_args.result.value

        result = await transport_call(
            before_args.input.value, before_args.context
        )

        after_args = AfterArgs(
            result=ClientCallResult(method=input_data.method, value=result),
            agent_card=self._card,
            context=before_args.context,
        )
        await self._intercept_after(after_args)

        return after_args.result.value

    async def _execute_stream_with_interceptors(
        self,
        input_data: ClientCallInput,
        context: ClientCallContext | None,
        transport_call: Callable[
            [Any, ClientCallContext | None], AsyncIterator[StreamResponse]
        ],
    ) -> AsyncIterator[ClientEvent]:

        before_args = BeforeArgs(
            input=input_data,
            agent_card=self._card,
            context=context,
        )
        before_result = await self._intercept_before(before_args)

        if before_result:
            after_args = AfterArgs(
                result=before_result['early_return'],
                agent_card=self._card,
                context=before_args.context,
            )
            await self._intercept_after(after_args, before_result['executed'])

            tracker = ClientTaskManager()
            yield await self._format_stream_event(
                after_args.result.value, tracker
            )
            return

        stream = transport_call(before_args.input.value, before_args.context)

        async for client_event in self._process_stream(stream, before_args):
            yield client_event

    async def _intercept_before(
        self,
        args: BeforeArgs,
    ) -> dict[str, Any] | None:
        if not self._interceptors:
            return None
        executed: list[ClientCallInterceptor] = []
        for interceptor in self._interceptors:
            await interceptor.before(args)
            executed.append(interceptor)
            if args.early_return:
                return {
                    'early_return': args.early_return,
                    'executed': executed,
                }
        return None

    async def _intercept_after(
        self,
        args: AfterArgs,
        interceptors: list[ClientCallInterceptor] | None = None,
    ) -> None:
        interceptors_to_use = (
            interceptors if interceptors is not None else self._interceptors
        )

        reversed_interceptors = list(reversed(interceptors_to_use))
        for interceptor in reversed_interceptors:
            await interceptor.after(args)
            if args.early_return:
                return

    async def _format_stream_event(
        self, stream_response: StreamResponse, tracker: ClientTaskManager
    ) -> ClientEvent:
        client_event: ClientEvent
        if stream_response.HasField('message'):
            client_event = (stream_response, None)
            await self.consume(client_event, self._card)
            return client_event

        await tracker.process(stream_response)
        updated_task = tracker.get_task_or_raise()
        client_event = (stream_response, updated_task)

        await self.consume(client_event, self._card)
        return client_event
