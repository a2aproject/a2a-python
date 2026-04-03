import logging
import typing

from collections.abc import AsyncIterable

from a2a.compat.v0_3 import conversions
from a2a.compat.v0_3 import types as types_v03
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import Task
from a2a.utils import proto_utils as core_proto_utils
from a2a.utils.errors import TaskNotFoundError


logger = logging.getLogger(__name__)


class RequestHandler03:
    """A protocol-agnostic v0.3 RequestHandler that delegates to the v1.0 RequestHandler."""

    def __init__(self, request_handler: RequestHandler):
        self.request_handler = request_handler

    async def on_message_send(
        self,
        request: types_v03.SendMessageRequest,
        context: ServerCallContext,
    ) -> types_v03.Task | types_v03.Message:
        """Sends a message using v0.3 protocol types."""
        v10_req = conversions.to_core_send_message_request(request)
        task_or_message = await self.request_handler.on_message_send(
            v10_req, context
        )
        if isinstance(task_or_message, Task):
            return conversions.to_compat_task(task_or_message)
        return conversions.to_compat_message(task_or_message)

    async def on_message_send_stream(
        self,
        request: types_v03.SendMessageRequest,
        context: ServerCallContext,
    ) -> AsyncIterable[types_v03.SendStreamingMessageSuccessResponse]:
        """Sends a message stream using v0.3 protocol types."""
        v10_req = conversions.to_core_send_message_request(request)
        async for event in self.request_handler.on_message_send_stream(
            v10_req, context
        ):
            v10_stream_resp = core_proto_utils.to_stream_response(event)
            yield conversions.to_compat_stream_response(
                v10_stream_resp, request.id
            )

    async def on_cancel_task(
        self,
        request: types_v03.CancelTaskRequest,
        context: ServerCallContext,
    ) -> types_v03.Task:
        """Cancels a task using v0.3 protocol types."""
        v10_req = conversions.to_core_cancel_task_request(request)
        v10_task = await self.request_handler.on_cancel_task(v10_req, context)
        if v10_task:
            return conversions.to_compat_task(v10_task)
        raise TaskNotFoundError

    async def on_subscribe_to_task(
        self,
        request: types_v03.TaskResubscriptionRequest,
        context: ServerCallContext,
    ) -> AsyncIterable[types_v03.SendStreamingMessageSuccessResponse]:
        """Subscribes to a task using v0.3 protocol types."""
        v10_req = conversions.to_core_subscribe_to_task_request(request)
        async for event in self.request_handler.on_subscribe_to_task(
            v10_req, context
        ):
            v10_stream_resp = core_proto_utils.to_stream_response(event)
            yield conversions.to_compat_stream_response(
                v10_stream_resp, request.id
            )

    async def on_get_task_push_notification_config(
        self,
        request: types_v03.GetTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> types_v03.TaskPushNotificationConfig:
        """Gets a push notification config using v0.3 protocol types."""
        v10_req = conversions.to_core_get_task_push_notification_config_request(
            request
        )
        v10_config = (
            await self.request_handler.on_get_task_push_notification_config(
                v10_req, context
            )
        )
        return conversions.to_compat_task_push_notification_config(v10_config)

    async def on_create_task_push_notification_config(
        self,
        request: types_v03.SetTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> types_v03.TaskPushNotificationConfig:
        """Creates a push notification config using v0.3 protocol types."""
        v10_req = (
            conversions.to_core_create_task_push_notification_config_request(
                request
            )
        )
        v10_config = (
            await self.request_handler.on_create_task_push_notification_config(
                v10_req, context
            )
        )
        return conversions.to_compat_task_push_notification_config(v10_config)

    async def on_get_task(
        self,
        request: types_v03.GetTaskRequest,
        context: ServerCallContext,
    ) -> types_v03.Task:
        """Gets a task using v0.3 protocol types."""
        v10_req = conversions.to_core_get_task_request(request)
        v10_task = await self.request_handler.on_get_task(v10_req, context)
        if v10_task:
            return conversions.to_compat_task(v10_task)
        raise TaskNotFoundError

    async def on_list_task_push_notification_configs(
        self,
        request: types_v03.ListTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> list[types_v03.TaskPushNotificationConfig]:
        """Lists push notification configs using v0.3 protocol types."""
        v10_req = (
            conversions.to_core_list_task_push_notification_config_request(
                request
            )
        )
        v10_resp = (
            await self.request_handler.on_list_task_push_notification_configs(
                v10_req, context
            )
        )
        v03_resp = (
            conversions.to_compat_list_task_push_notification_config_response(
                v10_resp, request.id
            )
        )
        if isinstance(
            v03_resp.root,
            types_v03.ListTaskPushNotificationConfigSuccessResponse,
        ):
            return typing.cast(
                'list[types_v03.TaskPushNotificationConfig]',
                v03_resp.root.result,
            )
        return []

    async def on_delete_task_push_notification_config(
        self,
        request: types_v03.DeleteTaskPushNotificationConfigRequest,
        context: ServerCallContext,
    ) -> None:
        """Deletes a push notification config using v0.3 protocol types."""
        v10_req = (
            conversions.to_core_delete_task_push_notification_config_request(
                request
            )
        )
        await self.request_handler.on_delete_task_push_notification_config(
            v10_req, context
        )

    async def on_get_extended_agent_card(
        self,
        request: types_v03.GetAuthenticatedExtendedCardRequest,
        context: ServerCallContext,
    ) -> types_v03.AgentCard:
        """Gets the authenticated extended agent card using v0.3 protocol types."""
        v10_req = conversions.to_core_get_extended_agent_card_request(request)
        v10_card = await self.request_handler.on_get_extended_agent_card(
            v10_req, context
        )
        return conversions.to_compat_agent_card(v10_card)
