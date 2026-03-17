import logging

from collections.abc import AsyncIterator
from typing import Any

from google.protobuf.json_format import (
    MessageToDict,
)

from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import a2a_pb2
from a2a.types.a2a_pb2 import (
    AgentCard,
)
from a2a.utils import proto_utils
from a2a.utils.errors import TaskNotFoundError
from a2a.utils.helpers import validate, validate_async_generator
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


@trace_class(kind=SpanKind.SERVER)
class RESTHandlerV2:
    """Maps incoming REST-like (JSON+HTTP) requests to the appropriate request handler method and formats responses."""

    def __init__(
        self,
        agent_card: AgentCard,
        request_handler: RequestHandler,
    ):
        self.agent_card = agent_card
        self.request_handler = request_handler

    async def on_message_send(
        self,
        params: a2a_pb2.SendMessageRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        task_or_message = await self.request_handler.on_message_send(
            params, context
        )
        if isinstance(task_or_message, a2a_pb2.Task):
            response = a2a_pb2.SendMessageResponse(task=task_or_message)
        else:
            response = a2a_pb2.SendMessageResponse(message=task_or_message)
        return MessageToDict(response)

    @validate_async_generator(
        lambda self: self.agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def on_message_send_stream(
        self,
        params: a2a_pb2.SendMessageRequest,
        context: ServerCallContext,
    ) -> AsyncIterator[dict[str, Any]]:
        async for event in self.request_handler.on_message_send_stream(
            params, context
        ):
            response = proto_utils.to_stream_response(event)
            yield MessageToDict(response)

    async def on_cancel_task(
        self,
        params: a2a_pb2.CancelTaskRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        task = await self.request_handler.on_cancel_task(params, context)
        if task:
            return MessageToDict(task)
        raise TaskNotFoundError

    @validate_async_generator(
        lambda self: self.agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def on_subscribe_to_task(
        self,
        params: a2a_pb2.SubscribeToTaskRequest,
        context: ServerCallContext,
    ) -> AsyncIterator[dict[str, Any]]:
        async for event in self.request_handler.on_subscribe_to_task(
            params, context
        ):
            yield MessageToDict(proto_utils.to_stream_response(event))

    async def get_push_notification(
        self,
        params: a2a_pb2.GetTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        config = (
            await self.request_handler.on_get_task_push_notification_config(
                params, context
            )
        )
        return MessageToDict(config)

    @validate(
        lambda self: self.agent_card.capabilities.push_notifications,
        'Push notifications are not supported by the agent',
    )
    async def set_push_notification(
        self,
        params: a2a_pb2.TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        config = (
            await self.request_handler.on_create_task_push_notification_config(
                params, context
            )
        )
        return MessageToDict(config)

    async def on_get_task(
        self,
        params: a2a_pb2.GetTaskRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        task = await self.request_handler.on_get_task(params, context)
        if task:
            return MessageToDict(task)
        raise TaskNotFoundError

    async def delete_push_notification(
        self,
        params: a2a_pb2.DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        await self.request_handler.on_delete_task_push_notification_config(
            params, context
        )
        return {}

    async def list_tasks(
        self,
        params: a2a_pb2.ListTasksRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        result = await self.request_handler.on_list_tasks(params, context)
        return MessageToDict(result, always_print_fields_with_no_presence=True)

    async def list_push_notifications(
        self,
        params: a2a_pb2.ListTaskPushNotificationConfigsRequest,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        result = (
            await self.request_handler.on_list_task_push_notification_configs(
                params, context
            )
        )
        return MessageToDict(result)
