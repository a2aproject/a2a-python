import logging

from collections.abc import AsyncIterable, AsyncIterator
from typing import TYPE_CHECKING, Any

from google.protobuf.json_format import MessageToDict, MessageToJson, Parse


if TYPE_CHECKING:
    from starlette.requests import Request

    from a2a.server.request_handlers.request_handler import RequestHandler
    from a2a.types.a2a_pb2 import AgentCard

    _package_starlette_installed = True
else:
    try:
        from starlette.requests import Request

        _package_starlette_installed = True
    except ImportError:
        Request = Any

        _package_starlette_installed = False

from a2a.compat.v0_3 import a2a_v0_3_pb2 as pb2_v03
from a2a.compat.v0_3 import proto_utils
from a2a.compat.v0_3 import types as types_v03
from a2a.compat.v0_3.request_handler import RequestHandler03
from a2a.server.context import ServerCallContext
from a2a.utils.helpers import validate
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


@trace_class(kind=SpanKind.SERVER)
class REST03Handler:
    """Maps incoming REST-like (JSON+HTTP) requests to the appropriate request handler method and formats responses for v0.3 compatibility."""

    def __init__(
        self,
        agent_card: 'AgentCard',
        request_handler: 'RequestHandler',
    ):
        """Initializes the REST03Handler.

        Args:
          agent_card: The AgentCard describing the agent's capabilities (v1.0).
          request_handler: The underlying `RequestHandler` instance to delegate requests to (v1.0).
        """
        self.agent_card = agent_card
        self.handler03 = RequestHandler03(request_handler=request_handler)

    async def on_message_send(
        self,
        request: Request,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'message/send' REST method.

        Args:
            request: The incoming `Request` object.
            context: Context provided by the server.

        Returns:
            A `dict` containing the result (Task or Message) in v0.3 format.
        """
        body = await request.body()
        v03_pb_msg = pb2_v03.SendMessageRequest()
        Parse(body, v03_pb_msg, ignore_unknown_fields=True)
        v03_params_msg = proto_utils.FromProto.message_send_params(v03_pb_msg)
        rpc_req = types_v03.SendMessageRequest(id='', params=v03_params_msg)

        v03_resp = await self.handler03.on_message_send(rpc_req, context)

        pb2_v03_resp = proto_utils.ToProto.task_or_message(v03_resp)
        return MessageToDict(pb2_v03_resp)

    @validate(
        lambda self: self.agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def on_message_send_stream(
        self,
        request: Request,
        context: ServerCallContext,
    ) -> AsyncIterator[str]:
        """Handles the 'message/stream' REST method.

        Args:
            request: The incoming `Request` object.
            context: Context provided by the server.

        Yields:
            JSON serialized objects containing streaming events in v0.3 format.
        """
        body = await request.body()
        v03_pb_msg = pb2_v03.SendMessageRequest()
        Parse(body, v03_pb_msg, ignore_unknown_fields=True)
        v03_params_msg = proto_utils.FromProto.message_send_params(v03_pb_msg)
        rpc_req = types_v03.SendMessageRequest(id='', params=v03_params_msg)

        async for v03_stream_resp in self.handler03.on_message_send_stream(
            rpc_req, context
        ):
            v03_pb_resp = proto_utils.ToProto.stream_response(
                v03_stream_resp.result
            )
            yield MessageToJson(v03_pb_resp)

    async def on_cancel_task(
        self,
        request: Request,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'tasks/cancel' REST method.

        Args:
            request: The incoming `Request` object.
            context: Context provided by the server.

        Returns:
            A `dict` containing the updated Task in v0.3 format.
        """
        task_id = request.path_params['id']
        rpc_req = types_v03.CancelTaskRequest(
            id='',
            params=types_v03.TaskIdParams(id=task_id),
        )

        v03_resp = await self.handler03.on_cancel_task(rpc_req, context)
        pb2_v03_task = proto_utils.ToProto.task(v03_resp)
        return MessageToDict(pb2_v03_task)

    @validate(
        lambda self: self.agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def on_subscribe_to_task(
        self,
        request: Request,
        context: ServerCallContext,
    ) -> AsyncIterable[str]:
        """Handles the 'tasks/{id}:subscribe' REST method.

        Args:
            request: The incoming `Request` object.
            context: Context provided by the server.

        Yields:
            JSON serialized objects containing streaming events in v0.3 format.
        """
        task_id = request.path_params['id']
        rpc_req = types_v03.TaskResubscriptionRequest(
            id='',
            params=types_v03.TaskIdParams(id=task_id),
        )

        async for v03_stream_resp in self.handler03.on_subscribe_to_task(
            rpc_req, context
        ):
            v03_pb_resp = proto_utils.ToProto.stream_response(
                v03_stream_resp.result
            )
            yield MessageToJson(v03_pb_resp)

    async def get_push_notification(
        self,
        request: Request,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'tasks/pushNotificationConfig/get' REST method.

        Args:
            request: The incoming `Request` object.
            context: Context provided by the server.

        Returns:
            A `dict` containing the config in v0.3 format.
        """
        task_id = request.path_params['id']
        push_id = request.path_params['push_id']

        rpc_req = types_v03.GetTaskPushNotificationConfigRequest(
            id='',
            params=types_v03.GetTaskPushNotificationConfigParams(
                id=task_id, push_notification_config_id=push_id
            ),
        )

        v03_resp = await self.handler03.on_get_task_push_notification_config(
            rpc_req, context
        )
        pb2_v03_config = proto_utils.ToProto.task_push_notification_config(
            v03_resp
        )
        return MessageToDict(pb2_v03_config)

    @validate(
        lambda self: self.agent_card.capabilities.push_notifications,
        'Push notifications are not supported by the agent',
    )
    async def set_push_notification(
        self,
        request: Request,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'tasks/pushNotificationConfig/set' REST method.

        Args:
            request: The incoming `Request` object.
            context: Context provided by the server.

        Returns:
            A `dict` containing the config object in v0.3 format.
        """
        task_id = request.path_params['id']
        body = await request.body()

        v03_pb_push = pb2_v03.CreateTaskPushNotificationConfigRequest()
        Parse(body, v03_pb_push, ignore_unknown_fields=True)

        v03_params_push = (
            proto_utils.FromProto.task_push_notification_config_request(
                v03_pb_push
            )
        )
        v03_params_push.task_id = task_id

        rpc_req_push = types_v03.SetTaskPushNotificationConfigRequest(
            id='',
            params=v03_params_push,
        )

        v03_resp = await self.handler03.on_create_task_push_notification_config(
            rpc_req_push, context
        )
        pb2_v03_config = proto_utils.ToProto.task_push_notification_config(
            v03_resp
        )
        return MessageToDict(pb2_v03_config)

    async def on_get_task(
        self,
        request: Request,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'v1/tasks/{id}' REST method.

        Args:
            request: The incoming `Request` object.
            context: Context provided by the server.

        Returns:
            A `Task` object containing the Task in v0.3 format.
        """
        task_id = request.path_params['id']
        history_length_str = request.query_params.get('historyLength')
        history_length = int(history_length_str) if history_length_str else None

        rpc_req = types_v03.GetTaskRequest(
            id='',
            params=types_v03.TaskQueryParams(
                id=task_id, history_length=history_length
            ),
        )

        v03_resp = await self.handler03.on_get_task(rpc_req, context)
        pb2_v03_task = proto_utils.ToProto.task(v03_resp)
        return MessageToDict(pb2_v03_task)

    async def list_push_notifications(
        self,
        request: Request,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'tasks/pushNotificationConfig/list' REST method."""
        task_id = request.path_params['id']

        rpc_req = types_v03.ListTaskPushNotificationConfigRequest(
            id='',
            params=types_v03.ListTaskPushNotificationConfigParams(id=task_id),
        )

        v03_resp = await self.handler03.on_list_task_push_notification_configs(
            rpc_req, context
        )

        pb2_v03_resp = pb2_v03.ListTaskPushNotificationConfigResponse(
            configs=[
                proto_utils.ToProto.task_push_notification_config(c)
                for c in v03_resp
            ]
        )

        return MessageToDict(pb2_v03_resp)

    async def list_tasks(
        self,
        request: Request,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'tasks/list' REST method."""
        raise NotImplementedError('list tasks not implemented')
