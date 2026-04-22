import json
import logging

from collections.abc import AsyncGenerator
from typing import Any, NoReturn

import httpx

from google.protobuf.json_format import MessageToDict, Parse, ParseDict

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
from a2a.utils.errors import A2A_REASON_TO_ERROR, MethodNotFoundError
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


def _parse_rest_error(
    error_payload: dict[str, Any],
    fallback_message: str,
) -> Exception | None:
    """Parses a REST error payload and returns the appropriate A2AError.

    Args:
        error_payload: The parsed JSON error payload.
        fallback_message: Message to use if the payload has no ``message``.

    Returns:
        The mapped A2AError if a known reason was found, otherwise ``None``.
    """
    error_data = error_payload.get('error', {})
    message = error_data.get('message', fallback_message)
    details = error_data.get('details', [])
    if not isinstance(details, list):
        return None

    # The `details` array can contain multiple different error objects.
    # We extract the first `ErrorInfo` object because it contains the
    # specific `reason` code needed to map this back to a Python A2AError.
    for d in details:
        if (
            isinstance(d, dict)
            and d.get('@type') == 'type.googleapis.com/google.rpc.ErrorInfo'
        ):
            reason = d.get('reason')
            metadata = d.get('metadata') or {}
            if isinstance(reason, str):
                exception_cls = A2A_REASON_TO_ERROR.get(reason)
                if exception_cls:
                    exc = exception_cls(message)
                    if metadata:
                        exc.data = metadata
                    return exc
            break

    return None


@trace_class(kind=SpanKind.CLIENT)
class RestTransport(ClientTransport):
    """A REST transport for the A2A client."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        agent_card: AgentCard,
        url: str,
    ):
        """Initializes the RestTransport."""
        self.url = url.removesuffix('/')
        self.httpx_client = httpx_client
        self.agent_card = agent_card

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
        if 'tenant' in params:
            del params['tenant']

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
        params = MessageToDict(request)
        if 'tenant' in params:
            del params['tenant']

        response_data = await self._execute_request(
            'GET',
            '/tasks',
            request.tenant,
            context=context,
            params=params,
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
        request: TaskPushNotificationConfig,
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
        if 'taskId' in params:
            del params['taskId']
        if 'tenant' in params:
            del params['tenant']

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
        if 'taskId' in params:
            del params['taskId']
        if 'tenant' in params:
            del params['tenant']

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
        if 'taskId' in params:
            del params['taskId']
        if 'tenant' in params:
            del params['tenant']

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
            'POST',
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
    ) -> AgentCard:
        """Retrieves the Extended AgentCard."""
        card = self.agent_card
        if not card.capabilities.extended_agent_card:
            return card

        response_data = await self._execute_request(
            'GET', '/extendedAgentCard', request.tenant, context=context
        )

        return ParseDict(response_data, AgentCard())

    async def close(self) -> None:
        """Closes the httpx client."""
        await self.httpx_client.aclose()

    def _get_path(self, base_path: str, tenant: str) -> str:
        """Returns the full path, prepending the tenant if provided."""
        return f'/{tenant}{base_path}' if tenant else base_path

    def _handle_http_error(self, e: httpx.HTTPStatusError) -> NoReturn:
        """Handles HTTP status errors and raises the appropriate A2AError."""
        try:
            error_payload = e.response.json()
            mapped = _parse_rest_error(error_payload, str(e))
            if mapped:
                raise mapped from e
        except (json.JSONDecodeError, ValueError):
            pass

        status_code = e.response.status_code
        if status_code == httpx.codes.NOT_FOUND:
            raise MethodNotFoundError(
                f'Resource not found: {e.request.url}'
            ) from e

        raise A2AClientError(f'HTTP Error {status_code}: {e}') from e

    def _handle_sse_error(self, sse_data: str) -> NoReturn:
        """Handles SSE error events by parsing the REST error payload and raising the appropriate A2AError."""
        error_payload = json.loads(sse_data)
        mapped = _parse_rest_error(error_payload, sse_data)
        if mapped:
            raise mapped
        raise A2AClientError(sse_data)

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
            self._handle_sse_error,
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
