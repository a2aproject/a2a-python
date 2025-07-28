import json
import logging

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any
from uuid import uuid4

import httpx

from httpx_sse import SSEError, aconnect_sse

from a2a.client.card_resolver import A2ACardResolver
from a2a.client.client import (
    Client,
    ClientConfig,
    Consumer,
)
from a2a.client.client_task_manager import ClientTaskManager
from a2a.client.errors import (
    A2AClientHTTPError,
    A2AClientJSONError,
    A2AClientTimeoutError,
)
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.types import (
    AgentCard,
    CancelTaskRequest,
    CancelTaskResponse,
    GetTaskPushNotificationConfigParams,
    GetTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigResponse,
    GetTaskRequest,
    GetTaskResponse,
    JSONRPCErrorResponse,
    Message,
    MessageSendConfiguration,
    MessageSendParams,
    SendMessageRequest,
    SendMessageResponse,
    SendStreamingMessageRequest,
    SendStreamingMessageResponse,
    SetTaskPushNotificationConfigRequest,
    SetTaskPushNotificationConfigResponse,
    Task,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskResubscriptionRequest,
)
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
)
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


@trace_class(kind=SpanKind.CLIENT)
class JsonRpcTransportClient:
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
        method_name: str,
        request_payload: dict[str, Any],
        http_kwargs: dict[str, Any] | None,
        context: ClientCallContext | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Applies all registered interceptors to the request."""
        final_http_kwargs = http_kwargs or {}
        final_request_payload = request_payload

        for interceptor in self.interceptors:
            (
                final_request_payload,
                final_http_kwargs,
            ) = await interceptor.intercept(
                method_name,
                final_request_payload,
                final_http_kwargs,
                self.agent_card,
                context,
            )
        return final_request_payload, final_http_kwargs

    @staticmethod
    async def get_client_from_agent_card_url(
        httpx_client: httpx.AsyncClient,
        base_url: str,
        agent_card_path: str = AGENT_CARD_WELL_KNOWN_PATH,
        http_kwargs: dict[str, Any] | None = None,
    ) -> 'JsonRpcTransportClient':
        """[deprecated] Fetches the public AgentCard and initializes an A2A client.

        This method will always fetch the public agent card. If an authenticated
        or extended agent card is required, the A2ACardResolver should be used
        directly to fetch the specific card, and then the A2AClient should be
        instantiated with it.

        Args:
            httpx_client: An async HTTP client instance (e.g., httpx.AsyncClient).
            base_url: The base URL of the agent's host.
            agent_card_path: The path to the agent card endpoint, relative to the base URL.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.get request when fetching the agent card.

        Returns:
            An initialized `A2AClient` instance.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs fetching the agent card.
            A2AClientJSONError: If the agent card response is invalid.
        """
        agent_card: AgentCard = await A2ACardResolver(
            httpx_client, base_url=base_url, agent_card_path=agent_card_path
        ).get_agent_card(
            http_kwargs=http_kwargs
        )  # Fetches public card by default
        return JsonRpcTransportClient(
            httpx_client=httpx_client, agent_card=agent_card
        )

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> SendMessageResponse:
        """Sends a non-streaming message request to the agent.

        Args:
            request: The `SendMessageRequest` object containing the message and configuration.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `SendMessageResponse` object containing the agent's response (Task or Message) or an error.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        if not request.id:
            request.id = str(uuid4())

        # Apply interceptors before sending
        payload, modified_kwargs = await self._apply_interceptors(
            'message/send',
            request.model_dump(mode='json', exclude_none=True),
            http_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        return SendMessageResponse.model_validate(response_data)

    async def send_message_streaming(
        self,
        request: SendStreamingMessageRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[SendStreamingMessageResponse]:
        """Sends a streaming message request to the agent and yields responses as they arrive.

        This method uses Server-Sent Events (SSE) to receive a stream of updates from the agent.

        Args:
            request: The `SendStreamingMessageRequest` object containing the message and configuration.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request. A default `timeout=None` is set but can be overridden.
            context: The client call context.

        Yields:
            `SendStreamingMessageResponse` objects as they are received in the SSE stream.
            These can be Task, Message, TaskStatusUpdateEvent, or TaskArtifactUpdateEvent.

        Raises:
            A2AClientHTTPError: If an HTTP or SSE protocol error occurs during the request.
            A2AClientJSONError: If an SSE event data cannot be decoded as JSON or validated.
        """
        if not request.id:
            request.id = str(uuid4())

        # Apply interceptors before sending
        payload, modified_kwargs = await self._apply_interceptors(
            'message/stream',
            request.model_dump(mode='json', exclude_none=True),
            http_kwargs,
            context,
        )

        modified_kwargs.setdefault('timeout', None)

        async with aconnect_sse(
            self.httpx_client,
            'POST',
            self.url,
            json=payload,
            **modified_kwargs,
        ) as event_source:
            try:
                async for sse in event_source.aiter_sse():
                    yield SendStreamingMessageResponse.model_validate(
                        json.loads(sse.data)
                    )
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

    async def _send_request(
        self,
        rpc_request_payload: dict[str, Any],
        http_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Sends a non-streaming JSON-RPC request to the agent.

        Args:
            rpc_request_payload: JSON RPC payload for sending the request.
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
                self.url, json=rpc_request_payload, **(http_kwargs or {})
            )
            response.raise_for_status()
            return response.json()
        except httpx.ReadTimeout as e:
            raise A2AClientTimeoutError('Client Request timed out') from e
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
        request: GetTaskRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> GetTaskResponse:
        """Retrieves the current state and history of a specific task.

        Args:
            request: The `GetTaskRequest` object specifying the task ID and history length.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `GetTaskResponse` object containing the Task or an error.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        if not request.id:
            request.id = str(uuid4())

        # Apply interceptors before sending
        payload, modified_kwargs = await self._apply_interceptors(
            'tasks/get',
            request.model_dump(mode='json', exclude_none=True),
            http_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        return GetTaskResponse.model_validate(response_data)

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> CancelTaskResponse:
        """Requests the agent to cancel a specific task.

        Args:
            request: The `CancelTaskRequest` object specifying the task ID.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `CancelTaskResponse` object containing the updated Task with canceled status or an error.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        if not request.id:
            request.id = str(uuid4())

        # Apply interceptors before sending
        payload, modified_kwargs = await self._apply_interceptors(
            'tasks/cancel',
            request.model_dump(mode='json', exclude_none=True),
            http_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        return CancelTaskResponse.model_validate(response_data)

    async def set_task_callback(
        self,
        request: SetTaskPushNotificationConfigRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> SetTaskPushNotificationConfigResponse:
        """Sets or updates the push notification configuration for a specific task.

        Args:
            request: The `SetTaskPushNotificationConfigRequest` object specifying the task ID and configuration.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `SetTaskPushNotificationConfigResponse` object containing the confirmation or an error.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        if not request.id:
            request.id = str(uuid4())

        # Apply interceptors before sending
        payload, modified_kwargs = await self._apply_interceptors(
            'tasks/pushNotificationConfig/set',
            request.model_dump(mode='json', exclude_none=True),
            http_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        return SetTaskPushNotificationConfigResponse.model_validate(
            response_data
        )

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> GetTaskPushNotificationConfigResponse:
        """Retrieves the push notification configuration for a specific task.

        Args:
            request: The `GetTaskPushNotificationConfigRequest` object specifying the task ID.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request.
            context: The client call context.

        Returns:
            A `GetTaskPushNotificationConfigResponse` object containing the configuration or an error.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON or validated.
        """
        if not request.id:
            request.id = str(uuid4())

        # Apply interceptors before sending
        payload, modified_kwargs = await self._apply_interceptors(
            'tasks/pushNotificationConfig/get',
            request.model_dump(mode='json', exclude_none=True),
            http_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        return GetTaskPushNotificationConfigResponse.model_validate(
            response_data
        )

    async def resubscribe(
        self,
        request: TaskResubscriptionRequest,
        *,
        http_kwargs: dict[str, Any] | None = None,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[SendStreamingMessageResponse]:
        """Reconnects to get task updates.

        This method uses Server-Sent Events (SSE) to receive a stream of updates from the agent.

        Args:
            request: The `TaskResubscriptionRequest` object containing the task information to reconnect to.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.post request. A default `timeout=None` is set but can be overridden.
            context: The client call context.

        Yields:
            `SendStreamingMessageResponse` objects as they are received in the SSE stream.
            These can be Task, Message, TaskStatusUpdateEvent, or TaskArtifactUpdateEvent.

        Raises:
            A2AClientHTTPError: If an HTTP or SSE protocol error occurs during the request.
            A2AClientJSONError: If an SSE event data cannot be decoded as JSON or validated.
        """
        # Apply interceptors before sending
        payload, modified_kwargs = await self._apply_interceptors(
            'tasks/resubscribe',
            request.model_dump(mode='json', exclude_none=True),
            http_kwargs,
            context,
        )

        modified_kwargs.setdefault('timeout', None)

        async with aconnect_sse(
            self.httpx_client,
            'POST',
            self.url,
            json=payload,
            **modified_kwargs,
        ) as event_source:
            try:
                async for sse in event_source.aiter_sse():
                    yield SendStreamingMessageResponse.model_validate(
                        json.loads(sse.data)
                    )
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
            'card/getAuthenticated',
            {},
            http_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        card = AgentCard.model_validate(response_data)
        self.agent_card = card
        self._needs_extended_card = False
        return card


@trace_class(kind=SpanKind.CLIENT)
class JsonRpcClient(Client):
    """JsonRpcClient is the implementation of the JSONRPC A2A client.

    This client proxies requests to the JsonRpcTransportClient implementation
    and manages the JSONRPC specific details. If passing additional arguments
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
        self._transport_client = JsonRpcTransportClient(
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
            An async iterator of `ClientEvent` or a final `Message` response.

        Raises:
            JSONRPCError: If the agent returns a JSON-RPC error in the response.
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
                SendMessageRequest(
                    params=MessageSendParams(
                        message=request,
                        configuration=config,
                    ),
                    id=str(uuid4()),
                ),
                http_kwargs=self.get_http_args(context),
                context=context,
            )
            if isinstance(response.root, JSONRPCErrorResponse):
                raise response.root.error
            result = response.root.result
            result = result if isinstance(result, Message) else (result, None)
            await self.consume(result, self._card)
            yield result
            return
        tracker = ClientTaskManager()
        async for event in self._transport_client.send_message_streaming(
            SendStreamingMessageRequest(
                params=MessageSendParams(
                    message=request,
                    configuration=config,
                ),
                id=str(uuid4()),
            ),
            http_kwargs=self.get_http_args(context),
            context=context,
        ):
            if isinstance(event.root, JSONRPCErrorResponse):
                raise event.root.error
            result = event.root.result
            # Update task, check for errors, etc.
            if isinstance(result, Message):
                yield result
                return
            await tracker.process(result)
            result = (
                tracker.get_task(),
                None if isinstance(result, Task) else result,
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
        response = await self._transport_client.get_task(
            GetTaskRequest(
                params=request,
                id=str(uuid4()),
            ),
            http_kwargs=self.get_http_args(context),
            context=context,
        )
        return response.result

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
        response = await self._transport_client.cancel_task(
            CancelTaskRequest(
                params=request,
                id=str(uuid4()),
            ),
            http_kwargs=self.get_http_args(context),
            context=context,
        )
        return response.result

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
        response = await self._transport_client.set_task_callback(
            SetTaskPushNotificationConfigRequest(
                params=request,
                id=str(uuid4()),
            ),
            http_kwargs=self.get_http_args(context),
            context=context,
        )
        return response.result

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
        response = await self._transport_client.get_task_callback(
            GetTaskPushNotificationConfigRequest(
                params=request,
                id=str(uuid4()),
            ),
            http_kwargs=self.get_http_args(context),
            context=context,
        )
        return response.result

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
            TaskResubscriptionRequest(
                params=request,
                id=str(uuid4()),
            ),
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


def NewJsonRpcClient(  # noqa: N802
    card: AgentCard,
    config: ClientConfig,
    consumers: list[Consumer],
    middleware: list[ClientCallInterceptor],
) -> Client:
    """Factory function for the `JsonRpcClient` implementation."""
    return JsonRpcClient(card, config, consumers, middleware)


# For backward compatability define this alias. This will be deprecated in
# a future release.
A2AClient = JsonRpcTransportClient
