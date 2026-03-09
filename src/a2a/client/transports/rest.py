import json
import logging

from collections.abc import AsyncGenerator, Callable
from typing import Any, NoReturn

import httpx

from google.protobuf.json_format import MessageToDict, Parse, ParseDict
from google.protobuf.message import Message

from a2a.client.errors import A2AClientError
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.client.transports.base import ClientTransport
from a2a.client.transports.http_helpers import (
    get_http_args,
    send_http_request,
    send_http_stream_request,
)
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    CreateTaskPushNotificationConfigRequest,
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
from a2a.utils.errors import JSON_RPC_ERROR_CODE_MAP, MethodNotFoundError
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)

_A2A_ERROR_NAME_TO_CLS = {
    error_type.__name__: error_type for error_type in JSON_RPC_ERROR_CODE_MAP
}


@trace_class(kind=SpanKind.CLIENT)
class RestTransport(ClientTransport):
    """A REST transport for the A2A client."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        agent_card: AgentCard,
        url: str,
        interceptors: list[ClientCallInterceptor] | None = None,
    ):
        """Initializes the RestTransport."""
        self.url = url.removesuffix('/')
        self.httpx_client = httpx_client
        self.agent_card = agent_card
        self.interceptors = interceptors or []
        self._needs_extended_card = agent_card.capabilities.extended_agent_card

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> SendMessageResponse:
        """Sends a non-streaming message request to the agent."""
        response_data = await self._execute_request(
            'POST',
            '/message:send',
            request.tenant,
            context=context,
            json=MessageToDict(request),
        )
        response: SendMessageResponse = ParseDict(
            response_data, SendMessageResponse()
        )
        return response

    async def send_message_streaming(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Sends a streaming message request to the agent and yields responses as they arrive."""
        payload = MessageToDict(request)

        async for event in self._send_stream_request(
            'POST',
            '/message:stream',
            request.tenant,
            context=context,
            json=payload,
        ):
            yield event

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""
        params = MessageToDict(request)
        if 'id' in params:
            del params['id']  # id is part of the URL path

        response_data = await self._execute_request(
            'GET',
            f'/tasks/{request.id}',
            request.tenant,
            context=context,
            params=params,
        )
        response: Task = ParseDict(response_data, Task())
        return response

    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTasksResponse:
        """Retrieves tasks for an agent."""
        response_data = await self._execute_request(
            'GET',
            '/tasks',
            request.tenant,
            context=context,
            params=MessageToDict(request),
        )
        response: ListTasksResponse = ParseDict(
            response_data, ListTasksResponse()
        )
        return response

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""
        response_data = await self._execute_request(
            'POST',
            f'/tasks/{request.id}:cancel',
            request.tenant,
            context=context,
            json=MessageToDict(request),
        )
        response: Task = ParseDict(response_data, Task())
        return response

    async def create_task_push_notification_config(
        self,
        request: CreateTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""
        response_data = await self._execute_request(
            'POST',
            f'/tasks/{request.task_id}/pushNotificationConfigs',
            request.tenant,
            context=context,
            json=MessageToDict(request),
        )
        response: TaskPushNotificationConfig = ParseDict(
            response_data, TaskPushNotificationConfig()
        )
        return response

    async def get_task_push_notification_config(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""
        params = MessageToDict(request)
        if 'id' in params:
            del params['id']
        if 'task_id' in params:
            del params['task_id']

        response_data = await self._execute_request(
            'GET',
            f'/tasks/{request.task_id}/pushNotificationConfigs/{request.id}',
            request.tenant,
            context=context,
            params=params,
        )
        response: TaskPushNotificationConfig = ParseDict(
            response_data, TaskPushNotificationConfig()
        )
        return response

    async def list_task_push_notification_configs(
        self,
        request: ListTaskPushNotificationConfigsRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Lists push notification configurations for a specific task."""
        params = MessageToDict(request)
        if 'task_id' in params:
            del params['task_id']

        response_data = await self._execute_request(
            'GET',
            f'/tasks/{request.task_id}/pushNotificationConfigs',
            request.tenant,
            context=context,
            params=params,
        )
        response: ListTaskPushNotificationConfigsResponse = ParseDict(
            response_data, ListTaskPushNotificationConfigsResponse()
        )
        return response

    async def delete_task_push_notification_config(
        self,
        request: DeleteTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> None:
        """Deletes the push notification configuration for a specific task."""
        params = MessageToDict(request)
        if 'id' in params:
            del params['id']
        if 'task_id' in params:
            del params['task_id']

        await self._execute_request(
            'DELETE',
            f'/tasks/{request.task_id}/pushNotificationConfigs/{request.id}',
            request.tenant,
            context=context,
            params=params,
        )

    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Reconnects to get task updates."""
        async for event in self._send_stream_request(
            'GET',
            f'/tasks/{request.id}:subscribe',
            request.tenant,
            context=context,
        ):
            yield event

    async def get_extended_agent_card(
        self,
        request: GetExtendedAgentCardRequest,
        *,
        context: ClientCallContext | None = None,
        signature_verifier: Callable[[AgentCard], None] | None = None,
    ) -> AgentCard:
        """Retrieves the Extended AgentCard."""
        card = self.agent_card

        if not card.capabilities.extended_agent_card:
            return card

        response_data = await self._execute_request(
            'GET', '/extendedAgentCard', request.tenant, context=context
        )
        response: AgentCard = ParseDict(response_data, AgentCard())

        if signature_verifier:
            signature_verifier(response)

        # Update the transport's agent_card
        self.agent_card = response
        self._needs_extended_card = False
        return response

    async def close(self) -> None:
        """Closes the httpx client."""
        await self.httpx_client.aclose()

    def _get_path(self, base_path: str, tenant: str) -> str:
        """Returns the full path, prepending the tenant if provided."""
        return f'/{tenant}{base_path}' if tenant else base_path

    def _handle_http_error(self, e: httpx.HTTPStatusError) -> NoReturn:
        """Handles HTTP status errors and raises the appropriate A2AError."""
        try:
            error_data = e.response.json()
            error_type = error_data.get('type')
            message = error_data.get('message', str(e))

            if isinstance(error_type, str):
                # TODO(#723): Resolving imports by name is temporary until proper error handling structure is added in #723.
                exception_cls = _A2A_ERROR_NAME_TO_CLS.get(error_type)
                if exception_cls:
                    raise exception_cls(message) from e
        except (json.JSONDecodeError, ValueError):
            pass

        # Fallback mappings for status codes if 'type' is missing or unknown
        status_code = e.response.status_code
        if status_code == httpx.codes.NOT_FOUND:
            raise MethodNotFoundError(
                f'Resource not found: {e.request.url}'
            ) from e

        raise A2AClientError(f'HTTP Error {status_code}: {e}') from e

    async def _send_stream_request(
        self,
        method: str,
        target: str,
        tenant: str,
        context: ClientCallContext | None = None,
        *,
        json: dict[str, Any] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        path = self._get_path(target, tenant)
        http_kwargs = get_http_args(context)

        async for sse_data in send_http_stream_request(
            self.httpx_client,
            method,
            f'{self.url}{path}',
            self._handle_http_error,
            json=json,
            **http_kwargs,
        ):
            event: StreamResponse = Parse(sse_data, StreamResponse())
            yield event

    async def _send_request(self, request: httpx.Request) -> dict[str, Any]:
        return await send_http_request(
            self.httpx_client, request, self._handle_http_error
        )

    async def _execute_request(  # noqa: PLR0913
        self,
        method: str,
        target: str,
        tenant: str,
        context: ClientCallContext | None = None,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        path = self._get_path(target, tenant)
        http_kwargs = get_http_args(context)

        request = self.httpx_client.build_request(
            method,
            f'{self.url}{path}',
            json=json,
            params=params,
            **http_kwargs,
        )
        return await self._send_request(request)


def _model_to_query_params(instance: Message) -> dict[str, str]:
    data = MessageToDict(instance, preserving_proto_field_name=True)
    return _json_to_query_params(data)


def _json_to_query_params(data: dict[str, Any]) -> dict[str, str]:
    query_dict = {}
    for key, value in data.items():
        if isinstance(value, list):
            query_dict[key] = ','.join(map(str, value))
        elif isinstance(value, bool):
            query_dict[key] = str(value).lower()
        else:
            query_dict[key] = str(value)

    return query_dict
