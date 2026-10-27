import json
import logging

from collections.abc import AsyncGenerator
from typing import Any, NoReturn
from uuid import uuid4

import httpx

from google.protobuf import json_format
from jsonrpc.jsonrpc2 import JSONRPC20Request

from a2a.client.client import ClientCallContext
from a2a.client.errors import A2AClientError
from a2a.client.transports.base import ClientTransport
from a2a.client.transports.http_helpers import (
    get_http_args,
    send_http_request,
    send_http_stream_request,
)
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
from a2a.utils.proto_utils import parse_lenient
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)

_JSON_RPC_ERROR_CODE_TO_A2A_ERROR = {
    code: error_type for error_type, code in JSON_RPC_ERROR_CODE_MAP.items()
}
_ERROR_INFO_TYPE = 'type.googleapis.com/google.rpc.ErrorInfo'


@trace_class(kind=SpanKind.CLIENT)
class JsonRpcTransport(ClientTransport):
    """A JSON-RPC transport for the A2A client."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        agent_card: AgentCard,
        url: str,
        is_shared_httpx: bool = False,
    ):
        """Initializes the JsonRpcTransport."""
        self.url = url
        self.httpx_client = httpx_client
        self.agent_card = agent_card
        self.is_shared_httpx = is_shared_httpx

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> SendMessageResponse:
        """Sends a non-streaming message request to the agent."""
        rpc_request = JSONRPC20Request(
            method='SendMessage',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        response: SendMessageResponse = parse_lenient(
            response_data['result'], SendMessageResponse()
        )
        return response

    async def send_message_streaming(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Sends a streaming message request to the agent and yields responses as they arrive."""
        rpc_request = JSONRPC20Request(
            method='SendStreamingMessage',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        async for event in self._send_stream_request(
            dict(rpc_request.data),
            context,
        ):
            yield event

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""
        rpc_request = JSONRPC20Request(
            method='GetTask',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        response: Task = parse_lenient(response_data['result'], Task())
        return response

    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTasksResponse:
        """Retrieves tasks for an agent."""
        rpc_request = JSONRPC20Request(
            method='ListTasks',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        response: ListTasksResponse = parse_lenient(
            response_data['result'], ListTasksResponse()
        )
        return response

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""
        rpc_request = JSONRPC20Request(
            method='CancelTask',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        response: Task = parse_lenient(response_data['result'], Task())
        return response

    async def create_task_push_notification_config(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""
        rpc_request = JSONRPC20Request(
            method='CreateTaskPushNotificationConfig',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        response: TaskPushNotificationConfig = parse_lenient(
            response_data['result'], TaskPushNotificationConfig()
        )
        return response

    async def get_task_push_notification_config(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""
        rpc_request = JSONRPC20Request(
            method='GetTaskPushNotificationConfig',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        response: TaskPushNotificationConfig = parse_lenient(
            response_data['result'], TaskPushNotificationConfig()
        )
        return response

    async def list_task_push_notification_configs(
        self,
        request: ListTaskPushNotificationConfigsRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Lists push notification configurations for a specific task."""
        rpc_request = JSONRPC20Request(
            method='ListTaskPushNotificationConfigs',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        response: ListTaskPushNotificationConfigsResponse = parse_lenient(
            response_data['result'], ListTaskPushNotificationConfigsResponse()
        )
        return response

    async def delete_task_push_notification_config(
        self,
        request: DeleteTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> None:
        """Deletes the push notification configuration for a specific task."""
        rpc_request = JSONRPC20Request(
            method='DeleteTaskPushNotificationConfig',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        await self._send_request(dict(rpc_request.data), context)

    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Reconnects to get task updates."""
        rpc_request = JSONRPC20Request(
            method='SubscribeToTask',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        async for event in self._send_stream_request(
            dict(rpc_request.data),
            context,
        ):
            yield event

    async def get_extended_agent_card(
        self,
        request: GetExtendedAgentCardRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AgentCard:
        """Retrieves the agent's card."""
        card = self.agent_card
        if not card.capabilities.extended_agent_card:
            return card

        rpc_request = JSONRPC20Request(
            method='GetExtendedAgentCard',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data),
            context,
        )
        result = response_data['result']
        # Validate type of the response
        if not isinstance(result, dict):
            raise A2AClientError(f'Invalid response type: {type(result)}')
        response: AgentCard = parse_lenient(result, AgentCard())

        return response

    async def close(self) -> None:
        """Closes the httpx client unless it is shared."""
        if self.is_shared_httpx:
            logger.debug(
                'Cannot close a shared httpx client; only the provider '
                'that supplied it via ClientConfig.httpx_client can close it.'
            )
            return
        if self.httpx_client.is_closed:
            raise A2AClientError('HTTP client is already closed')
        try:
            await self.httpx_client.aclose()
        except Exception as e:
            raise A2AClientError(f'Failed to close HTTP client: {e}') from e

    def _create_jsonrpc_error(self, error_dict: dict[str, Any]) -> Exception:
        """Creates the appropriate A2AError from a JSON-RPC error dictionary."""
        code = error_dict.get('code')
        message = error_dict.get('message', str(error_dict))
        raw_data = error_dict.get('data')

        a2a_data: dict[str, Any] | None = None
        if isinstance(raw_data, list):
            for d in raw_data:
                if isinstance(d, dict) and d.get('@type') == _ERROR_INFO_TYPE:
                    a2a_data = d.get('metadata') or None
                    break

        if isinstance(code, int) and code in _JSON_RPC_ERROR_CODE_TO_A2A_ERROR:
            return _JSON_RPC_ERROR_CODE_TO_A2A_ERROR[code](
                message, data=a2a_data
            )

        # Fallback to general A2AClientError
        return A2AClientError(f'JSON-RPC Error {code}: {message}')

    async def _send_request(
        self,
        payload: dict[str, Any],
        context: ClientCallContext | None = None,
    ) -> dict[str, Any]:
        http_kwargs = get_http_args(context)

        request = self.httpx_client.build_request(
            'POST', self.url, json=payload, **(http_kwargs or {})
        )
        response_data = await send_http_request(self.httpx_client, request)
        return self._check_jsonrpc_response(response_data)

    def _check_jsonrpc_response(self, response_data: Any) -> dict[str, Any]:
        """Returns a JSON-RPC success response, raising for anything else.

        An error response raises the A2A error mapped from its code, even when
        the error object lacks a ``message`` or carries extra members. A body
        that is not a JSON-RPC 2.0 response object (a list, a string, an object
        with neither ``result`` nor ``error``, or an ``error`` that is not an
        object) raises ``A2AClientError`` instead of leaking a ``TypeError`` or
        ``ValueError`` from the ``json-rpc`` package.
        """
        if not isinstance(response_data, dict):
            raise A2AClientError(
                'Invalid JSON-RPC response: expected an object, got '
                f'{type(response_data).__name__}'
            )
        error = response_data.get('error')
        if error:
            if not isinstance(error, dict):
                raise A2AClientError(
                    f'Invalid JSON-RPC response: error is not an object: '
                    f'{error!r}'
                )
            raise self._create_jsonrpc_error(error)
        if 'result' not in response_data:
            raise A2AClientError(
                "Invalid JSON-RPC response: it has neither 'result' nor 'error'"
            )
        return response_data

    async def _send_stream_request(
        self,
        rpc_request_payload: dict[str, Any],
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        http_kwargs = get_http_args(context)

        async for sse_data in send_http_stream_request(
            self.httpx_client,
            'POST',
            self.url,
            None,
            self._handle_sse_error,
            json=rpc_request_payload,
            **http_kwargs,
        ):
            try:
                response_data = json.loads(sse_data)
            except json.JSONDecodeError as e:
                raise A2AClientError(
                    f'Invalid JSON-RPC response in SSE stream: {e}'
                ) from e
            response_data = self._check_jsonrpc_response(response_data)
            response: StreamResponse = parse_lenient(
                response_data['result'], StreamResponse()
            )
            yield response

    def _handle_sse_error(self, sse_data: str) -> NoReturn:
        """Handles SSE error events by parsing JSON-RPC error payload and raising the appropriate domain error."""
        try:
            response_data = json.loads(sse_data)
        except json.JSONDecodeError:
            response_data = None
        if isinstance(response_data, dict) and isinstance(
            response_data.get('error'), dict
        ):
            raise self._create_jsonrpc_error(response_data['error'])
        raise A2AClientError(f'SSE stream error: {sse_data}')
