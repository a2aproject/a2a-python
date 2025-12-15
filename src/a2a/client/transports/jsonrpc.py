import json
import logging

from collections.abc import AsyncGenerator, Callable
from typing import Any
from uuid import uuid4

import httpx

from httpx_sse import SSEError, aconnect_sse

from a2a.client.card_resolver import A2ACardResolver
from a2a.client.errors import (
    A2AClientHTTPError,
    A2AClientJSONError,
    A2AClientJSONRPCError,
    A2AClientTimeoutError,
)
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.client.transports.base import ClientTransport
from a2a.extensions.common import update_extension_header
from a2a.types import (
    AgentCard,
    CancelTaskRequest,
    CancelTaskResponse,
    GetAuthenticatedExtendedCardRequest,
    GetAuthenticatedExtendedCardResponse,
    GetTaskPushNotificationConfigParams,
    GetTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigResponse,
    GetTaskRequest,
    GetTaskResponse,
    JSONRPCErrorResponse,
    Message,
    MessageSendParams,
    SendMessageRequest,
    SendMessageResponse,
    SendStreamingMessageRequest,
    SendStreamingMessageResponse,
    SetTaskPushNotificationConfigRequest,
    SetTaskPushNotificationConfigResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskResubscriptionRequest,
    TaskStatusUpdateEvent,
)
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


@trace_class(kind=SpanKind.CLIENT)
class JsonRpcTransport(ClientTransport):
    """A JSON-RPC transport for the A2A client."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        agent_card: AgentCard | None = None,
        url: str | None = None,
        interceptors: list[ClientCallInterceptor] | None = None,
        extensions: list[str] | None = None,
    ):
        """Initializes the JsonRpcTransport."""
        if url:
            self.url = url
        elif agent_card:
            self.url = agent_card.url
        else:
            raise ValueError('Must provide either agent_card or url')

        self.httpx_client = httpx_client
        self.agent_card = agent_card
        self.interceptors = interceptors or []
        self._needs_extended_card = (
            agent_card.supports_authenticated_extended_card
            if agent_card
            else True
        )
        self.extensions = extensions

    async def _apply_interceptors(
        self,
        method_name: str,
        request_payload: dict[str, Any],
        http_kwargs: dict[str, Any] | None,
        context: ClientCallContext | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
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

    def _get_http_args(
        self, context: ClientCallContext | None
    ) -> dict[str, Any] | None:
        return context.state.get('http_kwargs') if context else None

    async def send_message(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task | Message:
        """Sends a non-streaming message request to the agent."""
        rpc_request = SendMessageRequest(params=request, id=str(uuid4()))
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'message/send',
            rpc_request.model_dump(mode='json', exclude_none=True),
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        response = SendMessageResponse.model_validate(response_data)
        if isinstance(response.root, JSONRPCErrorResponse):
            raise A2AClientJSONRPCError(response.root)
        return response.root.result

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
        rpc_request = SendStreamingMessageRequest(
            params=request, id=str(uuid4())
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'message/stream',
            rpc_request.model_dump(mode='json', exclude_none=True),
            modified_kwargs,
            context,
        )
        modified_kwargs.setdefault(
            'timeout', self.httpx_client.timeout.as_dict().get('read', None)
        )
        headers = dict(self.httpx_client.headers.items())
        headers.update(modified_kwargs.get('headers', {}))
        modified_kwargs['headers'] = headers

        async with aconnect_sse(
            self.httpx_client,
            'POST',
            self.url,
            json=payload,
            **modified_kwargs,
        ) as event_source:
            try:
                event_source.response.raise_for_status()
                async for sse in event_source.aiter_sse():
                    response = SendStreamingMessageResponse.model_validate(
                        json.loads(sse.data)
                    )
                    if isinstance(response.root, JSONRPCErrorResponse):
                        raise A2AClientJSONRPCError(response.root)
                    yield response.root.result
            except httpx.HTTPStatusError as e:
                raise A2AClientHTTPError(e.response.status_code, str(e)) from e
            except SSEError as e:
                raise A2AClientHTTPError(
                    400, f'Invalid SSE response or protocol error: {e}'
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
        request: TaskQueryParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""
        rpc_request = GetTaskRequest(params=request, id=str(uuid4()))
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'tasks/get',
            rpc_request.model_dump(mode='json', exclude_none=True),
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        response = GetTaskResponse.model_validate(response_data)
        if isinstance(response.root, JSONRPCErrorResponse):
            raise A2AClientJSONRPCError(response.root)
        return response.root.result

    async def cancel_task(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""
        rpc_request = CancelTaskRequest(params=request, id=str(uuid4()))
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'tasks/cancel',
            rpc_request.model_dump(mode='json', exclude_none=True),
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        response = CancelTaskResponse.model_validate(response_data)
        if isinstance(response.root, JSONRPCErrorResponse):
            raise A2AClientJSONRPCError(response.root)
        return response.root.result

    async def set_task_callback(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""
        rpc_request = SetTaskPushNotificationConfigRequest(
            params=request, id=str(uuid4())
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'tasks/pushNotificationConfig/set',
            rpc_request.model_dump(mode='json', exclude_none=True),
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        response = SetTaskPushNotificationConfigResponse.model_validate(
            response_data
        )
        if isinstance(response.root, JSONRPCErrorResponse):
            raise A2AClientJSONRPCError(response.root)
        return response.root.result

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""
        rpc_request = GetTaskPushNotificationConfigRequest(
            params=request, id=str(uuid4())
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'tasks/pushNotificationConfig/get',
            rpc_request.model_dump(mode='json', exclude_none=True),
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        response = GetTaskPushNotificationConfigResponse.model_validate(
            response_data
        )
        if isinstance(response.root, JSONRPCErrorResponse):
            raise A2AClientJSONRPCError(response.root)
        return response.root.result

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
        rpc_request = TaskResubscriptionRequest(params=request, id=str(uuid4()))
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'tasks/resubscribe',
            rpc_request.model_dump(mode='json', exclude_none=True),
            modified_kwargs,
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
                    response = SendStreamingMessageResponse.model_validate_json(
                        sse.data
                    )
                    if isinstance(response.root, JSONRPCErrorResponse):
                        raise A2AClientJSONRPCError(response.root)
                    yield response.root.result
            except SSEError as e:
                raise A2AClientHTTPError(
                    400, f'Invalid SSE response or protocol error: {e}'
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
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
        signature_verifier: Callable[[AgentCard], None] | None = None,
    ) -> AgentCard:
        """Retrieves the agent's card."""
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        card = self.agent_card

        if not card:
            resolver = A2ACardResolver(self.httpx_client, self.url)
            card = await resolver.get_agent_card(
                http_kwargs=modified_kwargs,
                signature_verifier=signature_verifier,
            )
            self._needs_extended_card = (
                card.supports_authenticated_extended_card
            )
            self.agent_card = card

        if not self._needs_extended_card:
            return card

        request = GetAuthenticatedExtendedCardRequest(id=str(uuid4()))
        payload, modified_kwargs = await self._apply_interceptors(
            request.method,
            request.model_dump(mode='json', exclude_none=True),
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(
            payload,
            modified_kwargs,
        )
        response = GetAuthenticatedExtendedCardResponse.model_validate(
            response_data
        )
        if isinstance(response.root, JSONRPCErrorResponse):
            raise A2AClientJSONRPCError(response.root)
        card = response.root.result
        if signature_verifier:
            signature_verifier(card)

        self.agent_card = card
        self._needs_extended_card = False
        return card

    async def close(self) -> None:
        """Closes the httpx client."""
        await self.httpx_client.aclose()
