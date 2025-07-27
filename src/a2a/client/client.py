import json
import logging

from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import httpx

from httpx_sse import SSEError, aconnect_sse
from pydantic import ValidationError

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
    GetTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigResponse,
    GetTaskRequest,
    GetTaskResponse,
    SendMessageRequest,
    SendMessageResponse,
    SendStreamingMessageRequest,
    SendStreamingMessageResponse,
    SetTaskPushNotificationConfigRequest,
    SetTaskPushNotificationConfigResponse,
)
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
)
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


class A2ACardResolver:
    """Agent Card resolver."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        base_url: str,
        agent_card_path: str = AGENT_CARD_WELL_KNOWN_PATH,
    ) -> None:
        """Initializes the A2ACardResolver.

        Args:
            httpx_client: An async HTTP client instance (e.g., httpx.AsyncClient).
            base_url: The base URL of the agent's host.
            agent_card_path: The path to the agent card endpoint, relative to the base URL.
        """
        self.base_url = base_url.rstrip('/')
        self.agent_card_path = agent_card_path.lstrip('/')
        self.httpx_client = httpx_client

    async def get_agent_card(
        self,
        relative_card_path: str | None = None,
        http_kwargs: dict[str, Any] | None = None,
    ) -> AgentCard:
        """Fetches an agent card from a specified path relative to the base_url.

        If relative_card_path is None, it defaults to the resolver's configured
        agent_card_path (for the public agent card).

        Args:
            relative_card_path: Optional path to the agent card endpoint,
                relative to the base URL. If None, uses the default public
                agent card path.
            http_kwargs: Optional dictionary of keyword arguments to pass to the
                underlying httpx.get request.

        Returns:
            An `AgentCard` object representing the agent's capabilities.

        Raises:
            A2AClientHTTPError: If an HTTP error occurs during the request.
            A2AClientJSONError: If the response body cannot be decoded as JSON
                or validated against the AgentCard schema.
        """
        if relative_card_path is None:
            # Use the default public agent card path configured during initialization
            path_segment = self.agent_card_path
        else:
            path_segment = relative_card_path.lstrip('/')

        target_url = f'{self.base_url}/{path_segment}'

        try:
            response = await self.httpx_client.get(
                target_url,
                **(http_kwargs or {}),
            )
            response.raise_for_status()
            agent_card_data = response.json()
            logger.info(
                'Successfully fetched agent card data from %s: %s',
                target_url,
                agent_card_data,
            )
            agent_card = AgentCard.model_validate(agent_card_data)
        except httpx.HTTPStatusError as e:
            raise A2AClientHTTPError(
                e.response.status_code,
                f'Failed to fetch agent card from {target_url}: {e}',
            ) from e
        except json.JSONDecodeError as e:
            raise A2AClientJSONError(
                f'Failed to parse JSON for agent card from {target_url}: {e}'
            ) from e
        except httpx.RequestError as e:
            raise A2AClientHTTPError(
                503,
                f'Network communication error fetching agent card from {target_url}: {e}',
            ) from e
        except ValidationError as e:  # Pydantic validation error
            raise A2AClientJSONError(
                f'Failed to validate agent card structure from {target_url}: {e.json()}'
            ) from e

        return agent_card


@trace_class(kind=SpanKind.CLIENT)
class A2AClient:
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
    ) -> 'A2AClient':
        """Fetches the public AgentCard and initializes an A2A client.

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
        return A2AClient(httpx_client=httpx_client, agent_card=agent_card)

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
