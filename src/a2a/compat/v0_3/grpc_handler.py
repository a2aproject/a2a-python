# ruff: noqa: N802
import logging

from collections.abc import AsyncIterable, Awaitable, Callable
from typing import TypeVar

import grpc
import grpc.aio

from google.protobuf import empty_pb2

from a2a.compat.v0_3 import (
    a2a_v0_3_pb2,
    a2a_v0_3_pb2_grpc,
    conversions,
    proto_utils,
)
from a2a.compat.v0_3 import (
    types as types_v03,
)
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.grpc_handler import (
    _ERROR_CODE_MAP,
    CallContextBuilder,
    DefaultCallContextBuilder,
)
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import a2a_pb2
from a2a.types.a2a_pb2 import AgentCard
from a2a.utils.errors import A2AError, InvalidParamsError, TaskNotFoundError
from a2a.utils.helpers import maybe_await


logger = logging.getLogger(__name__)

TResponse = TypeVar('TResponse')


class CompatGrpcHandler(a2a_v0_3_pb2_grpc.A2AServiceServicer):
    """Backward compatible gRPC handler for A2A v0.3."""

    def __init__(
        self,
        agent_card: AgentCard,
        request_handler: RequestHandler,
        context_builder: CallContextBuilder | None = None,
        card_modifier: Callable[[AgentCard], Awaitable[AgentCard] | AgentCard]
        | None = None,
    ):
        """Initializes the CompatGrpcHandler.

        Args:
            agent_card: The AgentCard describing the agent's capabilities (v1.0).
            request_handler: The underlying `RequestHandler` instance to
                             delegate requests to.
            context_builder: The CallContextBuilder object. If none the
                             DefaultCallContextBuilder is used.
            card_modifier: An optional callback to dynamically modify the public
              agent card before it is served.
        """
        self.agent_card = agent_card
        self.request_handler = request_handler
        self.context_builder = context_builder or DefaultCallContextBuilder()
        self.card_modifier = card_modifier

    async def _handle_unary(
        self,
        context: grpc.aio.ServicerContext,
        handler_func: Callable[[ServerCallContext], Awaitable[TResponse]],
        default_response: TResponse,
    ) -> TResponse:
        """Centralized error handling and context management for unary calls."""
        try:
            server_context = self.context_builder.build(context)
            result = await handler_func(server_context)
            self._set_extension_metadata(context, server_context)
        except A2AError as e:
            await self.abort_context(e, context)
        else:
            return result
        return default_response

    async def _handle_stream(
        self,
        context: grpc.aio.ServicerContext,
        handler_func: Callable[[ServerCallContext], AsyncIterable[TResponse]],
    ) -> AsyncIterable[TResponse]:
        """Centralized error handling and context management for streaming calls."""
        try:
            server_context = self.context_builder.build(context)
            async for item in handler_func(server_context):
                yield item
            self._set_extension_metadata(context, server_context)
        except A2AError as e:
            await self.abort_context(e, context)

    def _extract_task_id(self, resource_name: str) -> str:
        """Extracts task_id from resource name."""
        m = proto_utils.TASK_NAME_MATCH.match(resource_name)
        if not m:
            raise InvalidParamsError(message=f'No task for {resource_name}')
        return m.group(1)

    def _extract_task_and_config_id(
        self, resource_name: str
    ) -> tuple[str, str]:
        """Extracts task_id and config_id from resource name."""
        m = proto_utils.TASK_PUSH_CONFIG_NAME_MATCH.match(resource_name)
        if not m:
            raise InvalidParamsError(
                message=f'Bad resource name {resource_name}'
            )
        return m.group(1), m.group(2)

    def _event_to_v03_stream_response(
        self,
        event: a2a_pb2.Message
        | a2a_pb2.Task
        | a2a_pb2.TaskStatusUpdateEvent
        | a2a_pb2.TaskArtifactUpdateEvent,
    ) -> a2a_v0_3_pb2.StreamResponse:
        """Maps a core streaming event directly to a v0.3 StreamResponse."""
        if isinstance(event, a2a_pb2.Task):
            return a2a_v0_3_pb2.StreamResponse(
                task=proto_utils.ToProto.task(conversions.to_compat_task(event))
            )
        if isinstance(event, a2a_pb2.Message):
            return a2a_v0_3_pb2.StreamResponse(
                msg=proto_utils.ToProto.message(
                    conversions.to_compat_message(event)
                )
            )
        if isinstance(event, a2a_pb2.TaskStatusUpdateEvent):
            return a2a_v0_3_pb2.StreamResponse(
                status_update=proto_utils.ToProto.task_status_update_event(
                    conversions.to_compat_task_status_update_event(event)
                )
            )
        if isinstance(event, a2a_pb2.TaskArtifactUpdateEvent):
            return a2a_v0_3_pb2.StreamResponse(
                artifact_update=proto_utils.ToProto.task_artifact_update_event(
                    conversions.to_compat_task_artifact_update_event(event)
                )
            )
        raise ValueError(f'Unknown event type: {type(event)}')

    async def abort_context(
        self, error: A2AError, context: grpc.aio.ServicerContext
    ) -> None:
        """Sets the grpc errors appropriately in the context."""
        code = _ERROR_CODE_MAP.get(type(error))
        if code:
            await context.abort(
                code,
                f'{type(error).__name__}: {error.message}',
            )
        else:
            await context.abort(
                grpc.StatusCode.UNKNOWN,
                f'Unknown error type: {error}',
            )

    def _set_extension_metadata(
        self,
        context: grpc.aio.ServicerContext,
        server_context: ServerCallContext,
    ) -> None:
        if server_context.activated_extensions:
            context.set_trailing_metadata(
                [
                    (HTTP_EXTENSION_HEADER.lower(), e)
                    for e in sorted(server_context.activated_extensions)
                ]
            )

    async def SendMessage(
        self,
        request: a2a_v0_3_pb2.SendMessageRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_v0_3_pb2.SendMessageResponse:
        """Handles the 'SendMessage' gRPC method (v0.3)."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> a2a_v0_3_pb2.SendMessageResponse:
            req_v03 = types_v03.SendMessageRequest(
                id=0, params=proto_utils.FromProto.message_send_params(request)
            )
            req_v10 = conversions.to_core_send_message_request(req_v03)
            result = await self.request_handler.on_message_send(
                req_v10, server_context
            )
            if isinstance(result, a2a_pb2.Task):
                return a2a_v0_3_pb2.SendMessageResponse(
                    task=proto_utils.ToProto.task(
                        conversions.to_compat_task(result)
                    )
                )
            return a2a_v0_3_pb2.SendMessageResponse(
                msg=proto_utils.ToProto.message(
                    conversions.to_compat_message(result)
                )
            )

        return await self._handle_unary(
            context, _handler, a2a_v0_3_pb2.SendMessageResponse()
        )

    async def SendStreamingMessage(
        self,
        request: a2a_v0_3_pb2.SendMessageRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterable[a2a_v0_3_pb2.StreamResponse]:
        """Handles the 'SendStreamingMessage' gRPC method (v0.3)."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> AsyncIterable[a2a_v0_3_pb2.StreamResponse]:
            req_v03 = types_v03.SendMessageRequest(
                id=0, params=proto_utils.FromProto.message_send_params(request)
            )
            req_v10 = conversions.to_core_send_message_request(req_v03)
            async for event in self.request_handler.on_message_send_stream(
                req_v10, server_context
            ):
                yield self._event_to_v03_stream_response(event)

        async for item in self._handle_stream(context, _handler):
            yield item

    async def GetTask(
        self,
        request: a2a_v0_3_pb2.GetTaskRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_v0_3_pb2.Task:
        """Handles the 'GetTask' gRPC method (v0.3)."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> a2a_v0_3_pb2.Task:
            req_v03 = types_v03.GetTaskRequest(
                id=0, params=proto_utils.FromProto.task_query_params(request)
            )
            req_v10 = conversions.to_core_get_task_request(req_v03)
            task = await self.request_handler.on_get_task(
                req_v10, server_context
            )
            if not task:
                raise TaskNotFoundError
            return proto_utils.ToProto.task(conversions.to_compat_task(task))

        return await self._handle_unary(context, _handler, a2a_v0_3_pb2.Task())

    async def CancelTask(
        self,
        request: a2a_v0_3_pb2.CancelTaskRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_v0_3_pb2.Task:
        """Handles the 'CancelTask' gRPC method (v0.3)."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> a2a_v0_3_pb2.Task:
            req_v03 = types_v03.CancelTaskRequest(
                id=0, params=proto_utils.FromProto.task_id_params(request)
            )
            req_v10 = conversions.to_core_cancel_task_request(req_v03)
            task = await self.request_handler.on_cancel_task(
                req_v10, server_context
            )
            if not task:
                raise TaskNotFoundError
            return proto_utils.ToProto.task(conversions.to_compat_task(task))

        return await self._handle_unary(context, _handler, a2a_v0_3_pb2.Task())

    async def TaskSubscription(
        self,
        request: a2a_v0_3_pb2.TaskSubscriptionRequest,
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterable[a2a_v0_3_pb2.StreamResponse]:
        """Handles the 'TaskSubscription' gRPC method (v0.3)."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> AsyncIterable[a2a_v0_3_pb2.StreamResponse]:
            req_v03 = types_v03.TaskResubscriptionRequest(
                id=0, params=proto_utils.FromProto.task_id_params(request)
            )
            req_v10 = conversions.to_core_subscribe_to_task_request(req_v03)
            async for event in self.request_handler.on_subscribe_to_task(
                req_v10, server_context
            ):
                yield self._event_to_v03_stream_response(event)

        async for item in self._handle_stream(context, _handler):
            yield item

    async def CreateTaskPushNotificationConfig(
        self,
        request: a2a_v0_3_pb2.CreateTaskPushNotificationConfigRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_v0_3_pb2.TaskPushNotificationConfig:
        """Handles the 'CreateTaskPushNotificationConfig' gRPC method (v0.3)."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> a2a_v0_3_pb2.TaskPushNotificationConfig:
            req_v03 = types_v03.SetTaskPushNotificationConfigRequest(
                id=0,
                params=proto_utils.FromProto.task_push_notification_config_request(
                    request
                ),
            )
            req_v10 = conversions.to_core_create_task_push_notification_config_request(
                req_v03
            )
            res_v10 = await self.request_handler.on_create_task_push_notification_config(
                req_v10, server_context
            )
            return proto_utils.ToProto.task_push_notification_config(
                conversions.to_compat_task_push_notification_config(res_v10)
            )

        return await self._handle_unary(
            context, _handler, a2a_v0_3_pb2.TaskPushNotificationConfig()
        )

    async def GetTaskPushNotificationConfig(
        self,
        request: a2a_v0_3_pb2.GetTaskPushNotificationConfigRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_v0_3_pb2.TaskPushNotificationConfig:
        """Handles the 'GetTaskPushNotificationConfig' gRPC method (v0.3)."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> a2a_v0_3_pb2.TaskPushNotificationConfig:
            task_id, config_id = self._extract_task_and_config_id(request.name)
            req_v03 = types_v03.GetTaskPushNotificationConfigRequest(
                id=0,
                params=types_v03.GetTaskPushNotificationConfigParams(
                    id=task_id, push_notification_config_id=config_id
                ),
            )
            req_v10 = (
                conversions.to_core_get_task_push_notification_config_request(
                    req_v03
                )
            )
            res_v10 = (
                await self.request_handler.on_get_task_push_notification_config(
                    req_v10, server_context
                )
            )
            return proto_utils.ToProto.task_push_notification_config(
                conversions.to_compat_task_push_notification_config(res_v10)
            )

        return await self._handle_unary(
            context, _handler, a2a_v0_3_pb2.TaskPushNotificationConfig()
        )

    async def ListTaskPushNotificationConfig(
        self,
        request: a2a_v0_3_pb2.ListTaskPushNotificationConfigRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_v0_3_pb2.ListTaskPushNotificationConfigResponse:
        """Handles the 'ListTaskPushNotificationConfig' gRPC method (v0.3)."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> a2a_v0_3_pb2.ListTaskPushNotificationConfigResponse:
            task_id = self._extract_task_id(request.parent)
            req_v03 = types_v03.ListTaskPushNotificationConfigRequest(
                id=0,
                params=types_v03.ListTaskPushNotificationConfigParams(
                    id=task_id
                ),
            )
            req_v10 = (
                conversions.to_core_list_task_push_notification_config_request(
                    req_v03
                )
            )
            res_v10 = await self.request_handler.on_list_task_push_notification_configs(
                req_v10, server_context
            )

            return a2a_v0_3_pb2.ListTaskPushNotificationConfigResponse(
                configs=[
                    proto_utils.ToProto.task_push_notification_config(
                        conversions.to_compat_task_push_notification_config(c)
                    )
                    for c in res_v10.configs
                ]
            )

        return await self._handle_unary(
            context,
            _handler,
            a2a_v0_3_pb2.ListTaskPushNotificationConfigResponse(),
        )

    async def GetAgentCard(
        self,
        request: a2a_v0_3_pb2.GetAgentCardRequest,
        context: grpc.aio.ServicerContext,
    ) -> a2a_v0_3_pb2.AgentCard:
        """Get the agent card for the agent served (v0.3)."""
        card_to_serve = self.agent_card
        if self.card_modifier:
            card_to_serve = await maybe_await(self.card_modifier(card_to_serve))
        return proto_utils.ToProto.agent_card(
            conversions.to_compat_agent_card(card_to_serve)
        )

    async def DeleteTaskPushNotificationConfig(
        self,
        request: a2a_v0_3_pb2.DeleteTaskPushNotificationConfigRequest,
        context: grpc.aio.ServicerContext,
    ) -> empty_pb2.Empty:
        """Handles the 'DeleteTaskPushNotificationConfig' gRPC method (v0.3)."""

        async def _handler(
            server_context: ServerCallContext,
        ) -> empty_pb2.Empty:
            task_id, config_id = self._extract_task_and_config_id(request.name)
            req_v03 = types_v03.DeleteTaskPushNotificationConfigRequest(
                id=0,
                params=types_v03.DeleteTaskPushNotificationConfigParams(
                    id=task_id, push_notification_config_id=config_id
                ),
            )
            req_v10 = conversions.to_core_delete_task_push_notification_config_request(
                req_v03
            )
            await self.request_handler.on_delete_task_push_notification_config(
                req_v10, server_context
            )
            return empty_pb2.Empty()

        return await self._handle_unary(context, _handler, empty_pb2.Empty())
