import logging

from collections.abc import AsyncGenerator
from typing import Any, NoReturn
from uuid import uuid4

import httpx

from google.protobuf import json_format
from jsonrpc.jsonrpc2 import JSONRPC20Request, JSONRPC20Response

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
    Message,
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
_ERROR_INFO_TYPE = 'type.googleapis.com/google.rpc.ErrorInfo'


def _strip_kind(value: Any) -> Any:
    """Recursively drops "kind" discriminator keys from a decoded JSON value.

    Spec-compliant peers stamp "kind" on Task, Message, and Part
    objects wherever they appear (top level, TaskStatus.message,
    Task.history[], Message.parts[]), but this SDK's protobuf-generated
    types don't declare that field, so json_format.ParseDict rejects it.
    """
    if isinstance(value, dict):
        return {
            key: _strip_kind(val) for key, val in value.items() if key != 'kind'
        }
    if isinstance(value, list):
        return [_strip_kind(item) for item in value]
    return value


@trace_class(kind=SpanKind.CLIENT)
class JsonRpcTransport(ClientTransport):
    """A JSON-RPC transport for the A2A client."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        agent_card: AgentCard,
        url: str,
    ):
        """Initializes the JsonRpcTransport."""
        self.url = url
        self.httpx_client = httpx_client
        self.agent_card = agent_card

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
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)
        result = json_rpc_response.result
        # Servers that still nest the payload under the streaming
        # SendMessageResponse oneof (older SDKs, other language
        # implementations) send {"task": {...}} or {"message": {...}}.
        if isinstance(result, dict) and 'task' in result:
            task = json_format.ParseDict(_strip_kind(result['task']), Task())
            return SendMessageResponse(task=task)
        if isinstance(result, dict) and 'message' in result:
            message = json_format.ParseDict(
                _strip_kind(result['message']), Message()
            )
            return SendMessageResponse(message=message)
        # Otherwise the payload is the Task/Message itself, per spec
        # possibly carrying a "kind" discriminator field on itself and on
        # every nested Message/Part (status.message, history[], parts[]),
        # none of which this SDK's protobuf-generated types declare.
        # Read the top-level kind (or fall back to the same field-presence
        # heuristic the v0.3 compat transport already uses) before
        # stripping it throughout the tree.
        kind = result.get('kind') if isinstance(result, dict) else None
        if not kind and isinstance(result, dict):
            if 'messageId' in result:
                kind = 'message'
            elif 'id' in result:
                kind = 'task'
        payload = _strip_kind(result)
        if kind == 'message':
            message = json_format.ParseDict(payload, Message())
            return SendMessageResponse(message=message)
        if kind == 'task':
            task = json_format.ParseDict(payload, Task())
            return SendMessageResponse(task=task)
        try:
            task = json_format.ParseDict(payload, Task())
        except json_format.ParseError:
            message = json_format.ParseDict(payload, Message())
            return SendMessageResponse(message=message)
        return SendMessageResponse(task=task)

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
    ) -> None:
        """Deletes the push notification configuration for a specific task."""
        rpc_request = JSONRPC20Request(
            method='DeleteTaskPushNotificationConfig',
            params=json_format.MessageToDict(request),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)

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
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)
        # Validate type of the response
        if not isinstance(json_rpc_response.result, dict):
            raise A2AClientError(
                f'Invalid response type: {type(json_rpc_response.result)}'
            )
        response: AgentCard = json_format.ParseDict(
            json_rpc_response.result, AgentCard()
        )

        return response

    async def close(self) -> None:
        """Closes the httpx client."""
        await self.httpx_client.aclose()

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
        return await send_http_request(self.httpx_client, request)

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
            json_rpc_response = JSONRPC20Response.from_json(sse_data)
            if json_rpc_response.error:
                raise self._create_jsonrpc_error(json_rpc_response.error)
            response: StreamResponse = json_format.ParseDict(
                json_rpc_response.result, StreamResponse()
            )
            yield response

    def _handle_sse_error(self, sse_data: str) -> NoReturn:
        """Handles SSE error events by parsing JSON-RPC error payload and raising the appropriate domain error."""
        json_rpc_response = JSONRPC20Response.from_json(sse_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)
        raise A2AClientError(f'SSE stream error: {sse_data}')
