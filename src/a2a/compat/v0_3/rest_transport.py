import contextlib
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
from a2a.compat.v0_3 import (
    a2a_v0_3_pb2,
    conversions,
    proto_utils,
)
from a2a.compat.v0_3 import (
    types as types_v03,
)
from a2a.compat.v0_3.extension_headers import add_legacy_extension_header
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
from a2a.utils.errors import JSON_RPC_ERROR_CODE_MAP, MethodNotFoundError
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)

_A2A_ERROR_NAME_TO_CLS = {
    error_type.__name__: error_type for error_type in JSON_RPC_ERROR_CODE_MAP
}


@trace_class(kind=SpanKind.CLIENT)
class CompatRestTransport(ClientTransport):
    """A backward compatible REST transport for A2A v0.3."""

    def __init__(
        self,
        httpx_client: httpx.AsyncClient,
        agent_card: AgentCard | None,
        url: str,
        subscribe_method_override: str | None = None,
    ):
        """Initializes the CompatRestTransport."""
        self.url = url.removesuffix('/')
        self.httpx_client = httpx_client
        self.agent_card = agent_card
        self._subscribe_method_override = subscribe_method_override
        self._subscribe_auto_method_override = subscribe_method_override is None

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
        req_proto = a2a_v0_3_pb2.SendMessageRequest(
            request=proto_utils.ToProto.message(req_v03.params.message),
            configuration=proto_utils.ToProto.message_send_configuration(
                req_v03.params.configuration
            ),
            metadata=proto_utils.ToProto.metadata(req_v03.params.metadata),
        )

        response_data = await self._execute_request(
            'POST',
            '/v1/message:send',
            context=context,
            json=MessageToDict(req_proto, preserving_proto_field_name=True),
        )

        resp_proto = ParseDict(
            response_data,
            a2a_v0_3_pb2.SendMessageResponse(),
            ignore_unknown_fields=True,
        )
        which = resp_proto.WhichOneof('payload')
        if which == 'task':
            return SendMessageResponse(
                task=conversions.to_core_task(
                    proto_utils.FromProto.task(resp_proto.task)
                )
            )
        if which == 'msg':
            return SendMessageResponse(
                message=conversions.to_core_message(
                    proto_utils.FromProto.message(resp_proto.msg)
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
        req_proto = a2a_v0_3_pb2.SendMessageRequest(
            request=proto_utils.ToProto.message(req_v03.params.message),
            configuration=proto_utils.ToProto.message_send_configuration(
                req_v03.params.configuration
            ),
            metadata=proto_utils.ToProto.metadata(req_v03.params.metadata),
        )

        async for event in self._send_stream_request(
            'POST',
            '/v1/message:stream',
            context=context,
            json=MessageToDict(req_proto, preserving_proto_field_name=True),
        ):
            yield event

    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""
        params = {}
        if request.HasField('history_length'):
            params['historyLength'] = request.history_length

        response_data = await self._execute_request(
            'GET',
            f'/v1/tasks/{request.id}',
            context=context,
            params=params,
        )
        resp_proto = ParseDict(
            response_data, a2a_v0_3_pb2.Task(), ignore_unknown_fields=True
        )
        return conversions.to_core_task(proto_utils.FromProto.task(resp_proto))

    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTasksResponse:
        """Retrieves tasks for an agent."""
        raise NotImplementedError(
            'ListTasks is not supported in A2A v0.3 REST.'
        )

    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""
        response_data = await self._execute_request(
            'POST',
            f'/v1/tasks/{request.id}:cancel',
            context=context,
        )
        resp_proto = ParseDict(
            response_data, a2a_v0_3_pb2.Task(), ignore_unknown_fields=True
        )
        return conversions.to_core_task(proto_utils.FromProto.task(resp_proto))

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
        req_proto = a2a_v0_3_pb2.CreateTaskPushNotificationConfigRequest(
            parent=f'tasks/{request.task_id}',
            config_id=req_v03.params.push_notification_config.id,
            config=proto_utils.ToProto.task_push_notification_config(
                req_v03.params
            ),
        )
        response_data = await self._execute_request(
            'POST',
            f'/v1/tasks/{request.task_id}/pushNotificationConfigs',
            context=context,
            json=MessageToDict(req_proto, preserving_proto_field_name=True),
        )
        resp_proto = ParseDict(
            response_data,
            a2a_v0_3_pb2.TaskPushNotificationConfig(),
            ignore_unknown_fields=True,
        )
        return conversions.to_core_task_push_notification_config(
            proto_utils.FromProto.task_push_notification_config(resp_proto)
        )

    async def get_task_push_notification_config(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""
        response_data = await self._execute_request(
            'GET',
            f'/v1/tasks/{request.task_id}/pushNotificationConfigs/{request.id}',
            context=context,
        )
        resp_proto = ParseDict(
            response_data,
            a2a_v0_3_pb2.TaskPushNotificationConfig(),
            ignore_unknown_fields=True,
        )
        return conversions.to_core_task_push_notification_config(
            proto_utils.FromProto.task_push_notification_config(resp_proto)
        )

    async def list_task_push_notification_configs(
        self,
        request: ListTaskPushNotificationConfigsRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Lists push notification configurations for a specific task."""
        raise NotImplementedError(
            'list_task_push_notification_configs not supported in v0.3 REST'
        )

    async def delete_task_push_notification_config(
        self,
        request: DeleteTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> None:
        """Deletes the push notification configuration for a specific task."""
        raise NotImplementedError(
            'delete_task_push_notification_config not supported in v0.3 REST'
        )

    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        """Reconnects to get task updates.

        This method implements backward compatibility logic for the subscribe
        endpoint. It first attempts to use POST, which is the official method
        for A2A subscribe endpoint. If the server returns 405 Method Not Allowed,
        it falls back to GET and remembers this preference for future calls
        on this transport instance. If both fail with 405, it will default back
        to POST for next calls but will not retry again.
        """
        subscribe_method = self._subscribe_method_override or 'POST'
        try:
            async for event in self._send_stream_request(
                subscribe_method,
                f'/v1/tasks/{request.id}:subscribe',
                context=context,
            ):
                yield event
        except A2AClientError as e:
            # Check for 405 Method Not Allowed in the cause (httpx.HTTPStatusError)
            cause = e.__cause__
            if (
                isinstance(cause, httpx.HTTPStatusError)
                and cause.response.status_code == httpx.codes.METHOD_NOT_ALLOWED
            ):
                if self._subscribe_method_override:
                    if self._subscribe_auto_method_override:
                        self._subscribe_auto_method_override = False
                        self._subscribe_method_override = 'POST'
                    raise
                else:
                    self._subscribe_method_override = 'GET'
                    async for event in self.subscribe(request, context=context):
                        yield event
            else:
                raise

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

        response_data = await self._execute_request(
            'GET', '/v1/card', context=context
        )
        resp_proto = ParseDict(
            response_data, a2a_v0_3_pb2.AgentCard(), ignore_unknown_fields=True
        )
        card = conversions.to_core_agent_card(
            proto_utils.FromProto.agent_card(resp_proto)
        )
        self.agent_card = card
        return card

    async def close(self) -> None:
        """Closes the httpx client."""
        await self.httpx_client.aclose()

    def _handle_http_error(self, e: httpx.HTTPStatusError) -> NoReturn:
        """Handles HTTP status errors and raises the appropriate A2AError."""
        try:
            with contextlib.suppress(httpx.StreamClosed):
                e.response.read()

            try:
                error_data = e.response.json()
            except (json.JSONDecodeError, ValueError, httpx.ResponseNotRead):
                error_data = {}

            error_type = error_data.get('type')
            message = error_data.get('message', str(e))

            if isinstance(error_type, str):
                exception_cls = _A2A_ERROR_NAME_TO_CLS.get(error_type)
                if exception_cls:
                    raise exception_cls(message) from e
        except (json.JSONDecodeError, ValueError):
            pass

        status_code = e.response.status_code
        if status_code == httpx.codes.NOT_FOUND:
            raise MethodNotFoundError(
                f'Resource not found: {e.request.url}'
            ) from e

        raise A2AClientError(f'HTTP Error {status_code}: {e}') from e

    async def _send_stream_request(
        self,
        method: str,
        path: str,
        context: ClientCallContext | None = None,
        *,
        json: dict[str, Any] | None = None,
    ) -> AsyncGenerator[StreamResponse]:
        http_kwargs = get_http_args(context)
        http_kwargs.setdefault('headers', {})
        http_kwargs['headers'][VERSION_HEADER.lower()] = PROTOCOL_VERSION_0_3
        add_legacy_extension_header(http_kwargs['headers'])

        async for sse_data in send_http_stream_request(
            self.httpx_client,
            method,
            f'{self.url}{path}',
            self._handle_http_error,
            json=json,
            **http_kwargs,
        ):
            event_proto = a2a_v0_3_pb2.StreamResponse()
            Parse(sse_data, event_proto, ignore_unknown_fields=True)
            yield conversions.to_core_stream_response(
                types_v03.SendStreamingMessageSuccessResponse(
                    result=proto_utils.FromProto.stream_response(event_proto)
                )
            )

    async def _send_request(self, request: httpx.Request) -> dict[str, Any]:
        return await send_http_request(
            self.httpx_client, request, self._handle_http_error
        )

    async def _execute_request(
        self,
        method: str,
        path: str,
        context: ClientCallContext | None = None,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        http_kwargs = get_http_args(context)
        http_kwargs.setdefault('headers', {})
        http_kwargs['headers'][VERSION_HEADER.lower()] = PROTOCOL_VERSION_0_3
        add_legacy_extension_header(http_kwargs['headers'])

        request = self.httpx_client.build_request(
            method,
            f'{self.url}{path}',
            json=json,
            params=params,
            **http_kwargs,
        )
        return await self._send_request(request)
