import json
import logging

from collections.abc import AsyncGenerator
from typing import Any
from uuid import uuid4

import httpx

from google.protobuf import json_format
from httpx_sse import SSEError, aconnect_sse
from jsonrpc.jsonrpc2 import JSONRPC20Request, JSONRPC20Response

from a2a.client.errors import (
    A2AClientHTTPError,
    A2AClientJSONError,
    A2AClientJSONRPCError,
    A2AClientTimeoutError,
)
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.client.transports.base import ClientTransport
from a2a.extensions.common import update_extension_header
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    SendMessageRequest,
    SendMessageResponse,
    SetTaskPushNotificationConfigRequest,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
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
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> SendMessageResponse:
        """Sends a non-streaming message request to the agent."""
        rpc_request = JSONRPC20Request(
            method='message/send',
            params=json_format.MessageToDict(request),
            _id=str(uuid4())
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'message/send',
            rpc_request.data,
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise A2AClientJSONRPCError(json_rpc_response.error)
        response: SendMessageResponse = json_format.ParseDict(
            json_rpc_response.result, SendMessageResponse()
        )
        return response

    async def send_message_streaming(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Sends a streaming message request to the agent and yields responses as they arrive."""
        rpc_request = JSONRPC20Request(
            method='message/stream',
            params=json_format.MessageToDict(request),
            _id=str(uuid4())
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'message/stream',
            rpc_request.data,
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
                async for sse in event_source.aiter_sse():
                    json_rpc_response = JSONRPC20Response.from_json(sse.data)
                    if json_rpc_response.error:
                        raise A2AClientJSONRPCError(json_rpc_response.error)
                    response: StreamResponse = json_format.ParseDict(
                        json_rpc_response.result, StreamResponse()
                    )
                    yield response
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
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""
        rpc_request = JSONRPC20Request(
            method='tasks/get',
            params=json_format.MessageToDict(request),
            _id=str(uuid4())
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'tasks/get',
            rpc_request.data,
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise A2AClientJSONRPCError(json_rpc_response.error)
        response: Task = json_format.ParseDict(json_rpc_response.result, Task())
        return response

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""
        rpc_request = JSONRPC20Request(
            method='tasks/cancel',
            params=json_format.MessageToDict(request),
            _id=str(uuid4())
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'tasks/cancel',
            rpc_request.data,
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise A2AClientJSONRPCError(json_rpc_response.error)
        response: Task = json_format.ParseDict(json_rpc_response.result, Task())
        return response

    async def set_task_callback(
        self,
        request: SetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""
        rpc_request = JSONRPC20Request(
            method='tasks/pushNotificationConfig/set',
            params=json_format.MessageToDict(request),
            _id=str(uuid4())
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'tasks/pushNotificationConfig/set',
            rpc_request.data,
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise A2AClientJSONRPCError(json_rpc_response.error)
        response: TaskPushNotificationConfig = json_format.ParseDict(
            json_rpc_response.result, TaskPushNotificationConfig()
        )
        return response

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""
        rpc_request = JSONRPC20Request(
            method='tasks/pushNotificationConfig/get',
            params=json_format.MessageToDict(request),
            _id=str(uuid4())
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'tasks/pushNotificationConfig/get',
            rpc_request.data,
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise A2AClientJSONRPCError(json_rpc_response.error)
        response: TaskPushNotificationConfig = json_format.ParseDict(
            json_rpc_response.result, TaskPushNotificationConfig()
        )
        return response

    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Reconnects to get task updates."""
        rpc_request = JSONRPC20Request(
            method='tasks/resubscribe',
            params=json_format.MessageToDict(request),
            _id=str(uuid4())
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'tasks/resubscribe',
            rpc_request.data,
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
                    json_rpc_response = JSONRPC20Response.from_json(sse.data)
                    if json_rpc_response.error:
                        raise A2AClientJSONRPCError(json_rpc_response.error)
                    response: StreamResponse = json_format.ParseDict(
                        json_rpc_response.result, StreamResponse()
                    )
                    yield response
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

    async def get_extended_agent_card(
        self,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AgentCard:
        """Retrieves the agent's card."""
        request = GetExtendedAgentCardRequest()
        rpc_request = JSONRPC20Request(
            method='agent/authenticatedExtendedCard',
            params=json_format.MessageToDict(request),
            _id=str(uuid4())
        )

        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'GetExtendedAgentCard',
            rpc_request.data,
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(
            payload,
            modified_kwargs,
        )
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise A2AClientJSONRPCError(json_rpc_response.error)
        response: AgentCard = json_format.ParseDict(
            json_rpc_response.result, AgentCard()
        )
        return response

    async def close(self) -> None:
        """Closes the httpx client."""
        await self.httpx_client.aclose()
