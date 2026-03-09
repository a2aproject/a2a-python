import logging

from collections.abc import AsyncGenerator, Callable
from typing import Any, cast
from uuid import uuid4

import httpx

from google.protobuf import json_format
from google.protobuf.json_format import ParseDict
from jsonrpc.jsonrpc2 import JSONRPC20Request, JSONRPC20Response

from a2a.client.errors import A2AClientError
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.client.transports.base import ClientTransport
from a2a.client.transports.http_helpers import (
    send_http_request,
    send_http_stream_request,
)
from a2a.extensions.common import update_extension_header
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
    SendMessageResponse,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)
from a2a.utils.errors import JSON_RPC_ERROR_CODE_MAP
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)

_JSON_RPC_ERROR_CODE_TO_A2A_ERROR = {
    code: error_type for error_type, code in JSON_RPC_ERROR_CODE_MAP.items()
}


@trace_class(kind=SpanKind.CLIENT)
class JsonRpcTransport(ClientTransport):
    """A JSON-RPC transport for the A2A client."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        agent_card: AgentCard,
        url: str,
        interceptors: list[ClientCallInterceptor] | None = None,
        extensions: list[str] | None = None,
    ):
        """Initializes the JsonRpcTransport."""
        self.url = url
        self.httpx_client = httpx_client
        self.agent_card = agent_card
        self.interceptors = interceptors or []
        self.extensions = extensions
        self._needs_extended_card = agent_card.capabilities.extended_agent_card

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> SendMessageResponse:
        """Sends a non-streaming message request to the agent."""
        rpc_request = JSONRPC20Request(
            method='SendMessage',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'SendMessage',
            cast('dict[str, Any]', rpc_request.data),
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)
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
            method='SendStreamingMessage',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'SendStreamingMessage',
            cast('dict[str, Any]', rpc_request.data),
            modified_kwargs,
            context,
        )
        async for event in self._send_stream_request(
            payload,
            http_kwargs=modified_kwargs,
        ):
            yield event

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""
        rpc_request = JSONRPC20Request(
            method='GetTask',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'GetTask',
            cast('dict[str, Any]', rpc_request.data),
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)
        response: Task = json_format.ParseDict(json_rpc_response.result, Task())
        return response

    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> ListTasksResponse:
        """Retrieves tasks for an agent."""
        rpc_request = JSONRPC20Request(
            method='ListTasks',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'ListTasks',
            cast('dict[str, Any]', rpc_request.data),
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)
        response: ListTasksResponse = json_format.ParseDict(
            json_rpc_response.result, ListTasksResponse()
        )
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
            method='CancelTask',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'CancelTask',
            cast('dict[str, Any]', rpc_request.data),
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)
        response: Task = json_format.ParseDict(json_rpc_response.result, Task())
        return response

    async def create_task_push_notification_config(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""
        rpc_request = JSONRPC20Request(
            method='CreateTaskPushNotificationConfig',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'CreateTaskPushNotificationConfig',
            cast('dict[str, Any]', rpc_request.data),
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)
        response: TaskPushNotificationConfig = json_format.ParseDict(
            json_rpc_response.result, TaskPushNotificationConfig()
        )
        return response

    async def get_task_push_notification_config(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""
        rpc_request = JSONRPC20Request(
            method='GetTaskPushNotificationConfig',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'GetTaskPushNotificationConfig',
            cast('dict[str, Any]', rpc_request.data),
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)
        response: TaskPushNotificationConfig = json_format.ParseDict(
            json_rpc_response.result, TaskPushNotificationConfig()
        )
        return response

    async def list_task_push_notification_configs(
        self,
        request: ListTaskPushNotificationConfigsRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Lists push notification configurations for a specific task."""
        rpc_request = JSONRPC20Request(
            method='ListTaskPushNotificationConfigs',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'ListTaskPushNotificationConfigs',
            cast('dict[str, Any]', rpc_request.data),
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)
        response: ListTaskPushNotificationConfigsResponse = (
            json_format.ParseDict(
                json_rpc_response.result,
                ListTaskPushNotificationConfigsResponse(),
            )
        )
        return response

    async def delete_task_push_notification_config(
        self,
        request: DeleteTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> None:
        """Deletes the push notification configuration for a specific task."""
        rpc_request = JSONRPC20Request(
            method='DeleteTaskPushNotificationConfig',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'DeleteTaskPushNotificationConfig',
            cast('dict[str, Any]', rpc_request.data),
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(payload, modified_kwargs)
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)

    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Reconnects to get task updates."""
        rpc_request = JSONRPC20Request(
            method='SubscribeToTask',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'SubscribeToTask',
            cast('dict[str, Any]', rpc_request.data),
            modified_kwargs,
            context,
        )
        async for event in self._send_stream_request(
            payload,
            http_kwargs=modified_kwargs,
        ):
            yield event

    async def get_extended_agent_card(
        self,
        request: GetExtendedAgentCardRequest,
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

        if not card.capabilities.extended_agent_card:
            return card

        rpc_request = JSONRPC20Request(
            method='GetExtendedAgentCard',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        payload, modified_kwargs = await self._apply_interceptors(
            'GetExtendedAgentCard',
            cast('dict[str, Any]', rpc_request.data),
            modified_kwargs,
            context,
        )
        response_data = await self._send_request(
            payload,
            modified_kwargs,
        )
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)
        # Validate type of the response
        if not isinstance(json_rpc_response.result, dict):
            raise A2AClientError(
                f'Invalid response type: {type(json_rpc_response.result)}'
            )
        response: AgentCard = ParseDict(
            cast('dict[str, Any]', json_rpc_response.result), AgentCard()
        )
        if signature_verifier:
            signature_verifier(response)

        self.agent_card = response
        self._needs_extended_card = False
        return response

    async def close(self) -> None:
        """Closes the httpx client."""
        await self.httpx_client.aclose()

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
    ) -> dict[str, Any]:
        http_kwargs: dict[str, Any] = {}
        if context and context.timeout is not None:
            http_kwargs['timeout'] = httpx.Timeout(context.timeout)
        return http_kwargs

    def _create_jsonrpc_error(self, error_dict: dict[str, Any]) -> Exception:
        """Creates the appropriate A2AError from a JSON-RPC error dictionary."""
        code = error_dict.get('code')
        message = error_dict.get('message', str(error_dict))

        if isinstance(code, int) and code in _JSON_RPC_ERROR_CODE_TO_A2A_ERROR:
            return _JSON_RPC_ERROR_CODE_TO_A2A_ERROR[code](message)

        # Fallback to general A2AClientError
        return A2AClientError(f'JSON-RPC Error {code}: {message}')

    async def _send_request(
        self,
        rpc_request_payload: dict[str, Any],
        http_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        request = self.httpx_client.build_request(
            'POST', self.url, json=rpc_request_payload, **(http_kwargs or {})
        )
        return await send_http_request(self.httpx_client, request)

    async def _send_stream_request(
        self,
        rpc_request_payload: dict[str, Any],
        http_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[StreamResponse]:
        final_kwargs = dict(http_kwargs or {})
        final_kwargs.update(kwargs)
        headers = dict(self.httpx_client.headers.items())
        headers.update(final_kwargs.get('headers', {}))
        final_kwargs['headers'] = headers

        async for sse_data in send_http_stream_request(
            self.httpx_client,
            'POST',
            self.url,
            None,
            json=rpc_request_payload,
            **final_kwargs,
        ):
            json_rpc_response = JSONRPC20Response.from_json(sse_data)
            if json_rpc_response.error:
                raise self._create_jsonrpc_error(json_rpc_response.error)
            response: StreamResponse = json_format.ParseDict(
                json_rpc_response.result, StreamResponse()
            )
            yield response
