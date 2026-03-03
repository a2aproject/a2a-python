import json
import logging

from collections.abc import AsyncGenerator, Callable
from typing import Any, NoReturn

import httpx

from google.protobuf.json_format import MessageToDict, Parse, ParseDict
from google.protobuf.message import Message
from httpx_sse import SSEError, aconnect_sse

import a2a.utils.errors

from a2a.client.errors import A2AClientError
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.client.transports.base import ClientTransport
from a2a.extensions.common import update_extension_header
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    CreateTaskPushNotificationConfigRequest,
    DeleteTaskPushNotificationConfigRequest,
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
from a2a.utils.errors import MethodNotFoundError
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


@trace_class(kind=SpanKind.CLIENT)
class RestTransport(ClientTransport):
    """A REST transport for the A2A client."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        agent_card: AgentCard,
        url: str,
        interceptors: list[ClientCallInterceptor] | None = None,
        extensions: list[str] | None = None,
    ):
        """Initializes the RestTransport."""
        self.url = url.removesuffix('/')
        self.httpx_client = httpx_client
        self.agent_card = agent_card
        self.interceptors = interceptors or []
        self._needs_extended_card = agent_card.capabilities.extended_agent_card
        self.extensions = extensions

    async def _apply_interceptors(
        self,
        request_payload: dict[str, Any],
        http_kwargs: dict[str, Any] | None,
        context: ClientCallContext | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        final_http_kwargs = http_kwargs or {}
        final_request_payload = request_payload
        # TODO: Implement interceptors for other transports
        return final_request_payload, final_http_kwargs

    def _get_http_args(
        self, context: ClientCallContext | None
    ) -> dict[str, Any] | None:
        return context.state.get('http_kwargs') if context else None

    async def _prepare_send_message(
        self,
        request: SendMessageRequest,
        context: ClientCallContext | None,
        extensions: list[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = MessageToDict(request)
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            payload,
            modified_kwargs,
            context,
        )
        return payload, modified_kwargs

    def _handle_http_error(self, e: httpx.HTTPStatusError) -> NoReturn:
        """Handles HTTP status errors and raises the appropriate A2AError."""
        try:
            error_data = e.response.json()
            error_type = error_data.get('type')
            message = error_data.get('message', str(e))

            if isinstance(error_type, str):
                exception_cls = getattr(a2a.utils.errors, error_type, None)
                if (
                    exception_cls
                    and isinstance(exception_cls, type)
                    and issubclass(exception_cls, a2a.utils.errors.A2AError)
                ):
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

    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> SendMessageResponse:
        """Sends a non-streaming message request to the agent."""
        payload, modified_kwargs = await self._prepare_send_message(
            request, context, extensions
        )
        response_data = await self._send_post_request(
            '/v1/message:send', payload, modified_kwargs
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
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Sends a streaming message request to the agent and yields responses as they arrive."""
        payload, modified_kwargs = await self._prepare_send_message(
            request, context, extensions
        )

        modified_kwargs.setdefault('timeout', None)

        try:
            async with aconnect_sse(
                self.httpx_client,
                'POST',
                f'{self.url}/v1/message:stream',
                json=payload,
                **modified_kwargs,
            ) as event_source:
                try:
                    event_source.response.raise_for_status()
                    async for sse in event_source.aiter_sse():
                        event: StreamResponse = Parse(
                            sse.data, StreamResponse()
                        )
                        yield event
                except httpx.HTTPStatusError as e:
                    self._handle_http_error(e)
                except SSEError as e:
                    raise A2AClientError(
                        f'Invalid SSE response or protocol error: {e}'
                    ) from e
        except httpx.TimeoutException as e:
            raise A2AClientError('Client Request timed out') from e
        except httpx.RequestError as e:
            raise A2AClientError(f'Network communication error: {e}') from e
        except json.JSONDecodeError as e:
            raise A2AClientError(f'JSON Decode Error: {e}') from e

    async def _send_request(self, request: httpx.Request) -> dict[str, Any]:
        try:
            response = await self.httpx_client.send(request)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as e:
            raise A2AClientError('Client Request timed out') from e
        except httpx.HTTPStatusError as e:
            self._handle_http_error(e)
        except json.JSONDecodeError as e:
            raise A2AClientError(f'JSON Decode Error: {e}') from e
        except httpx.RequestError as e:
            raise A2AClientError(f'Network communication error: {e}') from e

    async def _send_post_request(
        self,
        target: str,
        rpc_request_payload: dict[str, Any],
        http_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._send_request(
            self.httpx_client.build_request(
                'POST',
                f'{self.url}{target}',
                json=rpc_request_payload,
                **(http_kwargs or {}),
            )
        )

    async def _send_get_request(
        self,
        target: str,
        query_params: dict[str, str],
        http_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._send_request(
            self.httpx_client.build_request(
                'GET',
                f'{self.url}{target}',
                params=query_params,
                **(http_kwargs or {}),
            )
        )

    async def _send_delete_request(
        self,
        target: str,
        query_params: dict[str, Any],
        http_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._send_request(
            self.httpx_client.build_request(
                'DELETE',
                f'{self.url}{target}',
                params=query_params,
                **(http_kwargs or {}),
            )
        )

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""
        params = MessageToDict(request)
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        _payload, modified_kwargs = await self._apply_interceptors(
            params,
            modified_kwargs,
            context,
        )

        if 'id' in params:
            del params['id']  # id is part of the URL path, not query params

        response_data = await self._send_get_request(
            f'/v1/tasks/{request.id}',
            params,
            modified_kwargs,
        )
        response: Task = ParseDict(response_data, Task())
        return response

    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> ListTasksResponse:
        """Retrieves tasks for an agent."""
        _, modified_kwargs = await self._apply_interceptors(
            MessageToDict(request, preserving_proto_field_name=True),
            self._get_http_args(context),
            context,
        )
        modified_kwargs = update_extension_header(
            modified_kwargs,
            extensions if extensions is not None else self.extensions,
        )
        response_data = await self._send_get_request(
            '/v1/tasks',
            _model_to_query_params(request),
            modified_kwargs,
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
        extensions: list[str] | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""
        payload = MessageToDict(request)
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions not in (None, []) else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            payload,
            modified_kwargs,
            context,
        )
        response_data = await self._send_post_request(
            f'/v1/tasks/{request.id}:cancel', payload, modified_kwargs
        )
        response: Task = ParseDict(response_data, Task())
        return response

    async def create_task_push_notification_config(
        self,
        request: CreateTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""
        payload = MessageToDict(request)
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            payload, modified_kwargs, context
        )
        response_data = await self._send_post_request(
            f'/v1/tasks/{request.task_id}/pushNotificationConfigs',
            payload,
            modified_kwargs,
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
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""
        params = MessageToDict(request)
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        params, modified_kwargs = await self._apply_interceptors(
            params,
            modified_kwargs,
            context,
        )
        if 'id' in params:
            del params['id']
        if 'task_id' in params:
            del params['task_id']
        response_data = await self._send_get_request(
            f'/v1/tasks/{request.task_id}/pushNotificationConfigs/{request.id}',
            params,
            modified_kwargs,
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
        extensions: list[str] | None = None,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Lists push notification configurations for a specific task."""
        params = MessageToDict(request)
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        params, modified_kwargs = await self._apply_interceptors(
            params,
            modified_kwargs,
            context,
        )
        if 'task_id' in params:
            del params['task_id']
        response_data = await self._send_get_request(
            f'/v1/tasks/{request.task_id}/pushNotificationConfigs',
            params,
            modified_kwargs,
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
        extensions: list[str] | None = None,
    ) -> None:
        """Deletes the push notification configuration for a specific task."""
        params = MessageToDict(request)
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        params, modified_kwargs = await self._apply_interceptors(
            params,
            modified_kwargs,
            context,
        )
        if 'id' in params:
            del params['id']
        if 'task_id' in params:
            del params['task_id']
        await self._send_delete_request(
            f'/v1/tasks/{request.task_id}/pushNotificationConfigs/{request.id}',
            params,
            modified_kwargs,
        )

    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Reconnects to get task updates."""
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        modified_kwargs.setdefault('timeout', None)

        try:
            async with aconnect_sse(
                self.httpx_client,
                'GET',
                f'{self.url}/v1/tasks/{request.id}:subscribe',
                **modified_kwargs,
            ) as event_source:
                try:
                    async for sse in event_source.aiter_sse():
                        if not sse.data:
                            continue
                        event: StreamResponse = Parse(
                            sse.data, StreamResponse()
                        )
                        yield event
                except httpx.HTTPStatusError as e:
                    self._handle_http_error(e)
                except SSEError as e:
                    raise A2AClientError(
                        f'Invalid SSE response or protocol error: {e}'
                    ) from e
        except httpx.TimeoutException as e:
            raise A2AClientError('Client Request timed out') from e
        except httpx.RequestError as e:
            raise A2AClientError(f'Network communication error: {e}') from e
        except json.JSONDecodeError as e:
            raise A2AClientError(f'JSON Decode Error: {e}') from e

    async def get_extended_agent_card(
        self,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
        signature_verifier: Callable[[AgentCard], None] | None = None,
    ) -> AgentCard:
        """Retrieves the Extended AgentCard."""
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )

        card = self.agent_card

        if not card.capabilities.extended_agent_card:
            return card
        _, modified_kwargs = await self._apply_interceptors(
            {},
            modified_kwargs,
            context,
        )
        response_data = await self._send_get_request(
            '/v1/card', {}, modified_kwargs
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
