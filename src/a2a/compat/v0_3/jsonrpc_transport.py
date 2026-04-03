import json
import logging

from collections.abc import AsyncGenerator
from typing import Any, NoReturn
from uuid import uuid4

import httpx

from jsonrpc.jsonrpc2 import JSONRPC20Request, JSONRPC20Response

from a2a.client.client import ClientCallContext
from a2a.client.errors import A2AClientError
from a2a.client.transports.base import ClientTransport
from a2a.client.transports.http_helpers import (
    get_http_args,
    send_http_request,
    send_http_stream_request,
)
from a2a.compat.v0_3 import conversions
from a2a.compat.v0_3 import types as types_v03
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
from a2a.utils.constants import PROTOCOL_VERSION_0_3, VERSION_HEADER
from a2a.utils.errors import JSON_RPC_ERROR_CODE_MAP
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)

_JSON_RPC_ERROR_CODE_TO_A2A_ERROR = {
    code: error_type for error_type, code in JSON_RPC_ERROR_CODE_MAP.items()
}


@trace_class(kind=SpanKind.CLIENT)
class CompatJsonRpcTransport(ClientTransport):
    """A backward compatible JSON-RPC transport for A2A v0.3."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        agent_card: AgentCard | None,
        url: str,
    ):
        """Initializes the CompatJsonRpcTransport."""
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
        req_v03 = conversions.to_compat_send_message_request(
            request, request_id=0
        )

        rpc_request = JSONRPC20Request(
            method='message/send',
            params=req_v03.params.model_dump(
                by_alias=True, exclude_none=True, mode='json'
            ),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)

        result_dict = json_rpc_response.result
        if not isinstance(result_dict, dict):
            return SendMessageResponse()

        kind = result_dict.get('kind')

        # Fallback for old servers that might omit kind
        if not kind:
            if 'messageId' in result_dict:
                kind = 'message'
            elif 'id' in result_dict:
                kind = 'task'

        if kind == 'task':
            return SendMessageResponse(
                task=conversions.to_core_task(
                    types_v03.Task.model_validate(result_dict)
                )
            )
        if kind == 'message':
            return SendMessageResponse(
                message=conversions.to_core_message(
                    types_v03.Message.model_validate(result_dict)
                )
            )

        return SendMessageResponse()

    async def send_message_streaming(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Sends a streaming message request to the agent and yields responses as they arrive."""
        req_v03 = conversions.to_compat_send_message_request(
            request, request_id=0
        )

        rpc_request = JSONRPC20Request(
            method='message/stream',
            params=req_v03.params.model_dump(
                by_alias=True, exclude_none=True, mode='json'
            ),
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
        req_v03 = conversions.to_compat_get_task_request(request, request_id=0)

        rpc_request = JSONRPC20Request(
            method='tasks/get',
            params=req_v03.params.model_dump(
                by_alias=True, exclude_none=True, mode='json'
            ),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)
        return conversions.to_core_task(
            types_v03.Task.model_validate(json_rpc_response.result)
        )

    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTasksResponse:
        """Retrieves tasks for an agent."""
        raise NotImplementedError(
            'ListTasks is not supported in A2A v0.3 JSONRPC.'
        )

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""
        req_v03 = conversions.to_compat_cancel_task_request(
            request, request_id=0
        )

        rpc_request = JSONRPC20Request(
            method='tasks/cancel',
            params=req_v03.params.model_dump(
                by_alias=True, exclude_none=True, mode='json'
            ),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)

        return conversions.to_core_task(
            types_v03.Task.model_validate(json_rpc_response.result)
        )

    async def create_task_push_notification_config(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""
        req_v03 = (
            conversions.to_compat_create_task_push_notification_config_request(
                request, request_id=0
            )
        )
        rpc_request = JSONRPC20Request(
            method='tasks/pushNotificationConfig/set',
            params=req_v03.params.model_dump(
                by_alias=True, exclude_none=True, mode='json'
            ),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)

        return conversions.to_core_task_push_notification_config(
            types_v03.TaskPushNotificationConfig.model_validate(
                json_rpc_response.result
            )
        )

    async def get_task_push_notification_config(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""
        req_v03 = (
            conversions.to_compat_get_task_push_notification_config_request(
                request, request_id=0
            )
        )
        rpc_request = JSONRPC20Request(
            method='tasks/pushNotificationConfig/get',
            params=req_v03.params.model_dump(
                by_alias=True, exclude_none=True, mode='json'
            ),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)

        return conversions.to_core_task_push_notification_config(
            types_v03.TaskPushNotificationConfig.model_validate(
                json_rpc_response.result
            )
        )

    async def list_task_push_notification_configs(
        self,
        request: ListTaskPushNotificationConfigsRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Lists push notification configurations for a specific task."""
        req_v03 = (
            conversions.to_compat_list_task_push_notification_config_request(
                request, request_id=0
            )
        )
        rpc_request = JSONRPC20Request(
            method='tasks/pushNotificationConfig/list',
            params=req_v03.params.model_dump(
                by_alias=True, exclude_none=True, mode='json'
            ),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)

        configs_data = json_rpc_response.result
        if not isinstance(configs_data, list):
            return ListTaskPushNotificationConfigsResponse()

        response = ListTaskPushNotificationConfigsResponse()
        for config_data in configs_data:
            response.configs.append(
                conversions.to_core_task_push_notification_config(
                    types_v03.TaskPushNotificationConfig.model_validate(
                        config_data
                    )
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
        req_v03 = (
            conversions.to_compat_delete_task_push_notification_config_request(
                request, request_id=0
            )
        )
        rpc_request = JSONRPC20Request(
            method='tasks/pushNotificationConfig/delete',
            params=req_v03.params.model_dump(
                by_alias=True, exclude_none=True, mode='json'
            ),
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        if 'result' not in response_data and 'error' not in response_data:
            response_data['result'] = None

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
        req_v03 = conversions.to_compat_subscribe_to_task_request(
            request, request_id=0
        )
        rpc_request = JSONRPC20Request(
            method='tasks/resubscribe',
            params=req_v03.params.model_dump(
                by_alias=True, exclude_none=True, mode='json'
            ),
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
        """Retrieves the Extended AgentCard."""
        card = self.agent_card
        if card and not card.capabilities.extended_agent_card:
            return card

        rpc_request = JSONRPC20Request(
            method='agent/getAuthenticatedExtendedCard',
            params={},
            _id=str(uuid4()),
        )
        response_data = await self._send_request(
            dict(rpc_request.data), context
        )
        json_rpc_response = JSONRPC20Response(**response_data)
        if json_rpc_response.error:
            raise self._create_jsonrpc_error(json_rpc_response.error)

        card = conversions.to_core_agent_card(
            types_v03.AgentCard.model_validate(json_rpc_response.result)
        )
        self.agent_card = card
        return card

    async def close(self) -> None:
        """Closes the httpx client."""
        await self.httpx_client.aclose()

    def _create_jsonrpc_error(
        self, error_dict: dict[str, Any]
    ) -> A2AClientError:
        """Raises a specific error based on jsonrpc error code."""
        code = error_dict.get('code')
        message = error_dict.get('message', 'Unknown Error')

        if isinstance(code, int):
            error_class = _JSON_RPC_ERROR_CODE_TO_A2A_ERROR.get(code)
            if error_class:
                return error_class(message)  # type: ignore[return-value]

        return A2AClientError(message)

    def _handle_http_error(self, e: httpx.HTTPStatusError) -> NoReturn:
        """Handles HTTP errors for standard requests."""
        raise A2AClientError(f'HTTP Error: {e.response.status_code}') from e

    async def _send_stream_request(
        self,
        json_data: dict[str, Any],
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Sends an HTTP stream request."""
        http_kwargs = get_http_args(context)
        http_kwargs.setdefault('headers', {})
        http_kwargs['headers'][VERSION_HEADER.lower()] = PROTOCOL_VERSION_0_3

        async for sse_data in send_http_stream_request(
            self.httpx_client,
            'POST',
            self.url,
            self._handle_http_error,
            json=json_data,
            **http_kwargs,
        ):
            data = json.loads(sse_data)
            if 'error' in data:
                raise self._create_jsonrpc_error(data['error'])

            result_dict = data.get('result', {})
            if not isinstance(result_dict, dict):
                continue

            kind = result_dict.get('kind')

            if not kind:
                if 'taskId' in result_dict and 'final' in result_dict:
                    kind = 'status-update'
                elif 'messageId' in result_dict:
                    kind = 'message'
                elif 'id' in result_dict:
                    kind = 'task'

            result: (
                types_v03.Task
                | types_v03.Message
                | types_v03.TaskStatusUpdateEvent
                | types_v03.TaskArtifactUpdateEvent
            )
            if kind == 'task':
                result = types_v03.Task.model_validate(result_dict)
            elif kind == 'message':
                result = types_v03.Message.model_validate(result_dict)
            elif kind == 'status-update':
                result = types_v03.TaskStatusUpdateEvent.model_validate(
                    result_dict
                )
            elif kind == 'artifact-update':
                result = types_v03.TaskArtifactUpdateEvent.model_validate(
                    result_dict
                )
            else:
                continue

            yield conversions.to_core_stream_response(
                types_v03.SendStreamingMessageSuccessResponse(result=result)
            )

    async def _send_request(
        self,
        json_data: dict[str, Any],
        context: ClientCallContext | None = None,
    ) -> dict[str, Any]:
        """Sends an HTTP request."""
        http_kwargs = get_http_args(context)
        http_kwargs.setdefault('headers', {})
        http_kwargs['headers'][VERSION_HEADER.lower()] = PROTOCOL_VERSION_0_3

        request = self.httpx_client.build_request(
            'POST',
            self.url,
            json=json_data,
            **http_kwargs,
        )
        return await send_http_request(
            self.httpx_client, request, self._handle_http_error
        )
