import json
import logging

from collections.abc import AsyncGenerator, Callable
from typing import Any

import httpx

from google.protobuf.json_format import MessageToDict, Parse, ParseDict
from httpx_sse import SSEError, aconnect_sse

from a2a.client.card_resolver import A2ACardResolver
from a2a.client.errors import A2AClientHTTPError, A2AClientJSONError
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.client.transports.base import ClientTransport
from a2a.extensions.common import update_extension_header
from a2a.grpc import a2a_pb2
from a2a.types import (
    AgentCard,
    GetTaskPushNotificationConfigParams,
    Message,
    MessageSendParams,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskStatusUpdateEvent,
)
from a2a.utils import proto_utils
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


@trace_class(kind=SpanKind.CLIENT)
class RestTransport(ClientTransport):
    """A REST transport for the A2A client."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        agent_card: AgentCard | None = None,
        url: str | None = None,
        interceptors: list[ClientCallInterceptor] | None = None,
        extensions: list[str] | None = None,
    ):
        """Initializes the RestTransport."""
        if url:
            self.url = url
        elif agent_card:
            self.url = agent_card.url
        else:
            raise ValueError('Must provide either agent_card or url')
        if self.url.endswith('/'):
            self.url = self.url[:-1]
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
        request: MessageSendParams,
        context: ClientCallContext | None,
        extensions: list[str] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        pb = a2a_pb2.SendMessageRequest(
            request=proto_utils.ToProto.message(request.message),
            configuration=proto_utils.ToProto.message_send_configuration(
                request.configuration
            ),
            metadata=(
                proto_utils.ToProto.metadata(request.metadata)
                if request.metadata
                else None
            ),
        )
        payload = MessageToDict(pb)
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

    async def send_message(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task | Message:
        """Sends a non-streaming message request to the agent."""
        payload, modified_kwargs = await self._prepare_send_message(
            request, context, extensions
        )
        response_data = await self._send_post_request(
            '/v1/message:send', payload, modified_kwargs
        )
        response_pb = a2a_pb2.SendMessageResponse()
        ParseDict(response_data, response_pb)
        return proto_utils.FromProto.task_or_message(response_pb)

    async def send_message_streaming(
        self,
        request: MessageSendParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[
        Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent | Message
    ]:
        """Sends a streaming message request to the agent and yields responses as they arrive."""
        payload, modified_kwargs = await self._prepare_send_message(
            request, context, extensions
        )

        modified_kwargs.setdefault('timeout', None)

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
                    event = a2a_pb2.StreamResponse()
                    Parse(sse.data, event)
                    yield proto_utils.FromProto.stream_response(event)
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

    async def _send_request(self, request: httpx.Request) -> dict[str, Any]:
        try:
            response = await self.httpx_client.send(request)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            raise A2AClientHTTPError(e.response.status_code, str(e)) from e
        except json.JSONDecodeError as e:
            raise A2AClientJSONError(str(e)) from e
        except httpx.RequestError as e:
            raise A2AClientHTTPError(
                503, f'Network communication error: {e}'
            ) from e

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

    async def get_task(
        self,
        request: TaskQueryParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        _payload, modified_kwargs = await self._apply_interceptors(
            request.model_dump(mode='json', exclude_none=True),
            modified_kwargs,
            context,
        )
        response_data = await self._send_get_request(
            f'/v1/tasks/{request.id}',
            {'historyLength': str(request.history_length)}
            if request.history_length is not None
            else {},
            modified_kwargs,
        )
        task = a2a_pb2.Task()
        ParseDict(response_data, task)
        return proto_utils.FromProto.task(task)

    async def cancel_task(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""
        pb = a2a_pb2.CancelTaskRequest(name=f'tasks/{request.id}')
        payload = MessageToDict(pb)
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            payload,
            modified_kwargs,
            context,
        )
        response_data = await self._send_post_request(
            f'/v1/tasks/{request.id}:cancel', payload, modified_kwargs
        )
        task = a2a_pb2.Task()
        ParseDict(response_data, task)
        return proto_utils.FromProto.task(task)

    async def set_task_callback(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""
        pb = a2a_pb2.CreateTaskPushNotificationConfigRequest(
            parent=f'tasks/{request.task_id}',
            config_id=request.push_notification_config.id,
            config=proto_utils.ToProto.task_push_notification_config(request),
        )
        payload = MessageToDict(pb)
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
        config = a2a_pb2.TaskPushNotificationConfig()
        ParseDict(response_data, config)
        return proto_utils.FromProto.task_push_notification_config(config)

    async def get_task_callback(
        self,
        request: GetTaskPushNotificationConfigParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""
        pb = a2a_pb2.GetTaskPushNotificationConfigRequest(
            name=f'tasks/{request.id}/pushNotificationConfigs/{request.push_notification_config_id}',
        )
        payload = MessageToDict(pb)
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        payload, modified_kwargs = await self._apply_interceptors(
            payload,
            modified_kwargs,
            context,
        )
        response_data = await self._send_get_request(
            f'/v1/tasks/{request.id}/pushNotificationConfigs/{request.push_notification_config_id}',
            {},
            modified_kwargs,
        )
        config = a2a_pb2.TaskPushNotificationConfig()
        ParseDict(response_data, config)
        return proto_utils.FromProto.task_push_notification_config(config)

    async def resubscribe(
        self,
        request: TaskIdParams,
        *,
        context: ClientCallContext | None = None,
        extensions: list[str] | None = None,
    ) -> AsyncGenerator[
        Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent | Message
    ]:
        """Reconnects to get task updates."""
        modified_kwargs = update_extension_header(
            self._get_http_args(context),
            extensions if extensions is not None else self.extensions,
        )
        modified_kwargs.setdefault('timeout', None)

        async with aconnect_sse(
            self.httpx_client,
            'GET',
            f'{self.url}/v1/tasks/{request.id}:subscribe',
            **modified_kwargs,
        ) as event_source:
            try:
                async for sse in event_source.aiter_sse():
                    event = a2a_pb2.StreamResponse()
                    Parse(sse.data, event)
                    yield proto_utils.FromProto.stream_response(event)
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

        _, modified_kwargs = await self._apply_interceptors(
            {},
            modified_kwargs,
            context,
        )
        response_data = await self._send_get_request(
            '/v1/card', {}, modified_kwargs
        )
        card = AgentCard.model_validate(response_data)
        if signature_verifier:
            signature_verifier(card)

        self.agent_card = card
        self._needs_extended_card = False
        return card

    async def close(self) -> None:
        """Closes the httpx client."""
        await self.httpx_client.aclose()
