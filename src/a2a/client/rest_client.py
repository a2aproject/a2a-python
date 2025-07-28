import json
import logging

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import httpx

from google.protobuf.json_format import MessageToDict, Parse
from httpx_sse import SSEError, aconnect_sse

from a2a.client.client import A2ACardResolver, Client, ClientConfig, Consumer
from a2a.client.client_task_manager import ClientTaskManager
from a2a.client.errors import A2AClientHTTPError, A2AClientJSONError
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.grpc import a2a_pb2
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
class RestTransportClient:
    """A2A Client for interacting with an A2A agent."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        agent_card: AgentCard | None = None,
        url: str | None = None,
        interceptors: list[ClientCallInterceptor] | None = None,
    ):
        """Initializes the A2AClient.

        Requires either an `AgentCard` or a direct `url` to the agent's RPC endpoint.

        Args:
            httpx_client: An async HTTP client instance (e.g., httpx.AsyncClient).
            agent_card: The agent card object. If provided, `url` is taken from `agent_card.url`.
            url: The direct URL to the agent's A2A RPC endpoint. Required if `agent_card` is None.
            interceptors: An optional list of client call interceptors to apply to requests.

        Raises:
            ValueError: If neither `agent_card` nor `url` is provided.
        """
        if agent_card:
            self.url = agent_card.url
        elif url:
            self.url = url
        else:
            raise ValueError('Must provide either agent_card or url')
        # If the url ends in / remove it as this is added by the routes
        if self.url.endswith('/'):
            self.url = self.url[:-1]
        self.httpx_client = httpx_client
        self.agent_card = agent_card
        self.interceptors = interceptors or []
        # Indicate if we have captured an extended card details so we can update
        # on first call if needed. It is done this way so the caller can setup
        # their auth credentials based on the public card and get the updated
        # card.
        self._needs_extended_card = (
            not agent_card.supports_authenticated_extended_card
            if agent_card
            else True
        )

    async def _apply_interceptors(
        self,
        request_payload: dict[str, Any],
        http_kwargs: dict[str, Any] | None,
        context: ClientCallContext | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Applies all registered interceptors to the request."""
        final_http_kwargs = http_kwargs or {}
        final_request_payload = request_payload
        # TODO: Implement interceptors for other transports
        return final_request_payload, final_http_kwargs

    async def send_message(
        self,
        request: MessageSendParams,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> Task | Message:
        """Sends a non-streaming message request to the agent.

        Args:
            request: The `MessageSendParams` object containing the message and configuration.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `Task` or `Message` object containing the agent's response.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        pb = a2a_pb2.SendMessageRequest(
            request=proto_utils.ToProto.message(request.message),
            configuration=proto_utils.ToProto.send_message_config(
                request.config
            ),
            metadata=(
                proto_utils.ToProto.metadata(request.metadata)
                if request.metadata
                else None
            ),
        )
        payload = MessageToDict(pb)
        # Apply interceptors before sending
        payload, modified_kwargs = await self._apply_interceptors(
            payload,
            http_kwargs,
            context,
        )
        response_data = await self._send_post_request(
            '/v1/message:send', payload, modified_kwargs
        )
        response_pb = a2a_pb2.SendMessageResponse()
        Parse(response_data, response_pb)
        return proto_utils.FromProto.task_or_message(response_pb)

    async def send_message_streaming(
        self,
        request: MessageSendParams,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[
        Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent | Message
    ]:
        """Sends a streaming message request to the agent and yields responses as they arrive.

        This method uses Server-Sent Events (SSE) to receive a stream of updates from the agent.

        Args:
            request: The `MessageSendParams` object containing the message and configuration.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request. A default `timeout=None` is set but can be overridden.
            context: The client call context.

        Yields:
            Objects as they are received in the SSE stream.
            These can be Task, Message, TaskStatusUpdateEvent, or TaskArtifactUpdateEvent.

        Raises:
            A2AClientHTTPError: If an HTTP or SSE protocol error occurs during the request.
            A2AClientJSONError: If an SSE event data cannot be decoded as JSON or validated.
        """
        pb = a2a_pb2.SendMessageRequest(
            request=proto_utils.ToProto.message(request.message),
            configuration=proto_utils.ToProto.send_message_config(
                request.configuration
            ),
            metadata=(
                proto_utils.ToProto.metadata(request.metadata)
                if request.metadata
                else None
            ),
        )
        payload = MessageToDict(pb)
        # Apply interceptors before sending
        payload, modified_kwargs = await self._apply_interceptors(
            payload,
            http_kwargs,
            context,
        )

        modified_kwargs.setdefault('timeout', None)

        async with aconnect_sse(
            self.httpx_client,
            'POST',
            f'{self.url}/v1/message:stream',
            json=payload,
            **modified_kwargs,
        ) as event_source:
            try:
                async for sse in event_source.aiter_sse():
                    event = a2a_pb2.StreamResponse()
                    Parse(sse.data, event)
                    yield proto_utils.FromProto.stream_response(event)
            except SSEError as e:
                raise A2AClientHTTPError(
                    400,
                    f'Invalid SSE response or protocol error: {e}',
                ) from e
            except json.JSONDecodeError as e:
                raise A2AClientJSONError(str(e)) from e
            except httpx.RequestError as e:
                raise A2AClientHTTPError(
                    503, f'Network communication error: {e}'
                ) from e

    async def _send_post_request(
        self,
        target: str,
        rpc_request_payload: dict[str, Any],
        http_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Sends a non-streaming JSON-RPC request to the agent.

        Args:
            target: url path
            rpc_request_payload: JSON payload for sending the request.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.

        Returns:
            The JSON response payload as a dictionary.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON.
        """
        try:
            response = await self.httpx_client.post(
                f'{self.url}{target}',
                json=rpc_request_payload,
                **(http_kwargs or {}),
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise A2AClientHTTPError(e.response.status_code, str(e)) from e
        except json.JSONDecodeError as e:
            raise A2AClientJSONError(str(e)) from e
        except httpx.RequestError as e:
            raise A2AClientHTTPError(
                503, f'Network communication error: {e}'
            ) from e

    async def _send_get_request(
        self,
        target: str,
        query_params: dict[str, str],
        http_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Sends a non-streaming JSON-RPC request to the agent.

        Args:
            target: url path
            query_params: HTTP query params for the request.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.

        Returns:
            The JSON response payload as a dictionary.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON.
        """
        try:
            response = await self.httpx_client.get(
                f'{self.url}{target}',
                params=query_params,
                **(http_kwargs or {}),
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise A2AClientHTTPError(e.response.status_code, str(e)) from e
        except json.JSONDecodeError as e:
            raise A2AClientJSONError(str(e)) from e
        except httpx.RequestError as e:
            raise A2AClientHTTPError(
                503, f'Network communication error: {e}'
            ) from e

    async def get_task(
        self,
        request: TaskQueryParams,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task.

        Args:
            request: The `TaskQueryParams` object specifying the task ID and history length.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `Task` object containing the Task.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        # Apply interceptors before sending - only for the http kwargs
        payload, modified_kwargs = await self._apply_interceptors(
            request.model_dump(mode='json', exclude_none=True),
            http_kwargs,
            context,
        )
        response_data = await self._send_get_request(
            f'/v1/tasks/{request.taskId}',
            {'historyLength': request.history_length}
            if request.history_length
            else {},
            modified_kwargs,
        )
        task = a2a_pb2.Task()
        Parse(response_data, task)
        return proto_utils.FromProto.task(task)

    async def cancel_task(
        self,
        request: TaskIdParams,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task.

        Args:
            request: The `TaskIdParams` object specifying the task ID.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `Task` object containing the updated Task with canceled status

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        pb = a2a_pb2.CancelTaskRequest(name=f'tasks/{request.id}')
        payload = MessageToDict(pb)
        # Apply interceptors before sending
        payload, modified_kwargs = await self._apply_interceptors(
            payload,
            http_kwargs,
            context,
        )
        response_data = await self._send_post_request(
            f'/v1/tasks/{request.id}:cancel', payload, modified_kwargs
        )
        task = a2a_pb2.Task()
        Parse(response_data, task)
        return proto_utils.FromProto.task(task)

    async def set_task_callback(
        self,
        request: TaskPushNotificationConfig,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task.

        Args:
            request: The `TaskPushNotificationConfig` object specifying the task ID and configuration.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `TaskPushNotificationConfig` object containing the confirmation.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        pb = a2a_pb2.CreateTaskPushNotificationConfigRequest(
            parent=f'tasks/{request.taskId}',
            config_id=request.pushNotificationConfig.id,
            config=proto_utils.ToProto.push_notification_config(
                request.pushNotificationConfig
            ),
        )
        payload = MessageToDict(pb)
        # Apply interceptors before sending
        payload, modified_kwargs = await self._apply_interceptors(
            payload, http_kwargs, context
        )
        response_data = await self._send_post_request(
            f'/v1/tasks/{request.taskId}/pushNotificationConfigs/',
            payload,
            modified_kwargs,
        )
        config = a2a_pb2.TaskPushNotificationConfig()
        Parse(response_data, config)
        return proto_utils.FromProto.task_push_notification_config(config)

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigParams,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task.

        Args:
            request: The `GetTaskPushNotificationConfigParams` object specifying the task ID.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `TaskPushNotificationConfig` object containing the configuration.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        pb = a2a_pb2.GetTaskPushNotificationConfigRequest(
            name=f'tasks/{request.id}/pushNotificationConfigs/{request.push_notification_config_id}',
        )
        payload = MessageToDict(pb)
        # Apply interceptors before sending
        payload, modified_kwargs = await self._apply_interceptors(
            payload,
            http_kwargs,
            context,
        )
        response_data = await self._send_get_request(
            f'/v1/tasks/{request.id}/pushNotificationConfigs/{request.push_notification_config_id}',
            {},
            modified_kwargs,
        )
        config = a2a_pb2.TaskPushNotificationConfig()
        Parse(response_data, config)
        return proto_utils.FromProto.task_push_notification_config(config)

    async def resubscribe(
        self,
        request: TaskIdParams,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[
        Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent | Message
    ]:
        """Reconnects to get task updates.

        This method uses Server-Sent Events (SSE) to receive a stream of updates from the agent.

        Args:
            request: The `TaskIdParams` object containing the task information to reconnect to.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request. A default `timeout=None` is set but can be overridden.
            context: The client call context.

        Yields:
            Objects as they are received in the SSE stream.
            These can be Task, Message, TaskStatusUpdateEvent, or TaskArtifactUpdateEvent.

        Raises:
            A2AClientHTTPError: If an HTTP or SSE protocol error occurs during the request.
            A2AClientJSONError: If an SSE event data cannot be decoded as JSON or validated.
        """
        pb = a2a_pb2.TaskSubscriptionRequest(
            name=f'tasks/{request.id}',
        )
        payload = MessageToDict(pb)
        # Apply interceptors before sending
        payload, modified_kwargs = await self._apply_interceptors(
            payload,
            http_kwargs,
            context,
        )

        modified_kwargs.setdefault('timeout', None)

        async with aconnect_sse(
            self.httpx_client,
            'POST',
            f'{self.url}/v1/tasks/{request.id}:subscribe',
            json=payload,
            **modified_kwargs,
        ) as event_source:
            try:
                async for sse in event_source.aiter_sse():
                    event = a2a_pb2.StreamResponse()
                    Parse(sse.data, event)
                    yield proto_utils.FromProto.stream_response(event)
            except SSEError as e:
                raise A2AClientHTTPError(
                    400,
                    f'Invalid SSE response or protocol error: {e}',
                ) from e
            except json.JSONDecodeError as e:
                raise A2AClientJSONError(str(e)) from e
            except httpx.RequestError as e:
                raise A2AClientHTTPError(
                    503, f'Network communication error: {e}'
                ) from e

    async def get_card(
        self,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> AgentCard:
        """Retrieves the authenticated card (if necessary) or the public one.

        Args:
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `AgentCard` object containing the card or an error.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        # If we don't have the public card, try to get that first.
        card = self.agent_card
        if not card:
            resolver = A2ACardResolver(self.httpx_client, self.url)
            card = await resolver.get_agent_card(http_kwargs=http_kwargs)
            self._needs_extended_card = (
                card.supports_authenticated_extended_card
            )
            self.agent_card = card

        if not self._needs_extended_card:
            return card

        # Apply interceptors before sending
        payload, modified_kwargs = await self._apply_interceptors(
            '',
            http_kwargs,
            context,
        )
        response_data = await self._send_get_request(
            '/v1/card/get', {}, modified_kwargs
        )
        card = AgentCard.model_validate(response_data)
        self.agent_card = card
        self._needs_extended_card = False
        return card


@trace_class(kind=SpanKind.CLIENT)
class RestClient(Client):
    """RestClient is the implementation of the RESTful A2A client.

    This client proxies requests to the RestTransportClient implementation
    and manages the REST specific details. If passing additional arguments
    in the http.post command, these should be attached to the ClientCallContext
    under the dictionary key 'http_kwargs'.
    """

    def __init__(
        self,
        card: AgentCard,
        config: ClientConfig,
        consumers: list[Consumer],
        middleware: list[ClientCallInterceptor],
    ):
        super().__init__(consumers, middleware)
        if not config.httpx_client:
            raise Exception('JsonRpc client requires httpx client.')
        self._card = card
        url = card.url
        self._config = config
        self._transport_client = RestTransportClient(
            config.httpx_client, self._card, url, middleware
        )

    def get_http_args(
        self, context: ClientCallContext
    ) -> dict[str, Any] | None:
        """Extract HTTP-specific keyword arguments from the client call context.

        Args:
            context: The client call context.

        Returns:
            A dictionary of HTTP arguments, or None.
        """
        return context.state.get('http_kwargs', None) if context else None

    async def send_message(
        self,
        request: Message,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncIterator[Task | Message]:
        """Send a message to the agent and consumes the response(s).

        This method handles both blocking (non-streaming) and streaming responses
        based on the client configuration and agent capabilities.

        Args:
            request: The message to send.
            context: The client call context.

        Yields:
            The final message or task result from the agent.
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
                http_kwargs=self.get_http_args(context),
                context=context,
            )
            result = (
                response if isinstance(response, Message) else (response, None)
            )
            await self.consume(result, self._card)
            yield result
            return
        tracker = ClientTaskManager()
        async for event in self._transport_client.send_message_streaming(
            MessageSendParams(
                message=request,
                configuration=config,
            ),
            http_kwargs=self.get_http_args(context),
            context=context,
        ):
            # Update task, check for errors, etc.
            if isinstance(event, Message):
                yield event
                return
            await tracker.process(event)
            result = (
                tracker.get_task(),
                None if isinstance(event, Task) else event,
            )
            await self.consume(result, self._card)
            yield result

    async def get_task(
        self,
        request: TaskQueryParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Retrieve a task from the agent.

        Args:
            request: Parameters to identify the task.
            context: The client call context.

        Returns:
            The requested task.
        """
        return await self._transport_client.get_task(
            request,
            http_kwargs=self.get_http_args(context),
            context=context,
        )

    async def cancel_task(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Cancel an ongoing task on the agent.

        Args:
            request: Parameters to identify the task to cancel.
            context: The client call context.

        Returns:
            The task after the cancellation request.
        """
        return await self._transport_client.cancel_task(
            request,
            http_kwargs=self.get_http_args(context),
            context=context,
        )

    async def set_task_callback(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Set a push notification callback for a task.

        Args:
            request: The push notification configuration to set.
            context: The client call context.

        Returns:
            The configured task push notification configuration.
        """
        return await self._transport_client.set_task_callback(
            request,
            http_kwargs=self.get_http_args(context),
            context=context,
        )

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigParams,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieve the push notification callback configuration for a task.

        Args:
            request: Parameters to identify the task and configuration.
            context: The client call context.

        Returns:
            The requested task push notification configuration.
        """
        return await self._transport_client.get_task_callback(
            request,
            http_kwargs=self.get_http_args(context),
            context=context,
        )

    async def resubscribe(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncIterator[Task | Message]:
        """Resubscribe to a task's event stream.

        This is only available if both the client and server support streaming.

        Args:
            request: Parameters to identify the task to resubscribe to.
            context: The client call context.

        Yields:
            Task events from the agent.

        Raises:
            Exception: If streaming is not supported.
        """
        if not self._config.streaming or not self._card.capabilities.streaming:
            raise Exception(
                'client and/or server do not support resubscription.'
            )
        async for event in self._transport_client.resubscribe(
            request,
            http_kwargs=self.get_http_args(context),
            context=context,
        ):
            # Update task, check for errors, etc.
            yield event

    async def get_card(
        self,
        *,
        context: ClientCallContext | None = None,
    ) -> AgentCard:
        """Retrieve the agent's card.

        This may involve fetching the public card first if not already available,
        and then fetching the authenticated extended card if supported and required.

        Args:
            context: The client call context.

        Returns:
            The agent's card.
        """
        return await self._transport_client.get_card(
            http_kwargs=self.get_http_args(context),
            context=context,
        )


def NewRestfulClient(  # noqa: N802
    card: AgentCard,
    config: ClientConfig,
    consumers: list[Consumer],
    middleware: list[ClientCallInterceptor],
) -> Client:
    """Factory function for the `RestClient` implementation."""
    return RestClient(card, config, consumers, middleware)
