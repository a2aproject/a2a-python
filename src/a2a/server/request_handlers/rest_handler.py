import logging

from collections.abc import AsyncIterable, AsyncIterator
from typing import TYPE_CHECKING, Any

from google.protobuf.json_format import MessageToDict, MessageToJson, Parse


if TYPE_CHECKING:
    from starlette.requests import Request
else:
    try:
        from starlette.requests import Request
    except ImportError:
        Request = Any


from a2a.grpc import a2a_pb2
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import (
    AgentCard,
    GetTaskPushNotificationConfigParams,
    TaskIdParams,
    TaskNotFoundError,
    TaskQueryParams,
)
from a2a.utils import proto_utils
from a2a.utils.errors import ServerError
from a2a.utils.helpers import validate
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)


@trace_class(kind=SpanKind.SERVER)
class RESTHandler:
    """Maps incoming REST-like (JSON+HTTP) requests to the appropriate request handler method and formats responses.

    This uses the protobuf definitions of the gRPC service as the source of truth. By
    doing this, it ensures that this implementation and the gRPC transcoding
    (via Envoy) are equivalent. This handler should be used if using the gRPC handler
    with Envoy is not feasible for a given deployment solution. Use this handler
    and a related application if you desire to ONLY server the RESTful API.
    """

    def __init__(
        self,
        agent_card: AgentCard,
        request_handler: RequestHandler,
    ):
        """Initializes the RESTHandler.

        Args:
          agent_card: The AgentCard describing the agent's capabilities.
          request_handler: The underlying `RequestHandler` instance to delegate requests to.
        """
        self.agent_card = agent_card
        self.request_handler = request_handler

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
            A `dict` containing the result (Task or Message)
        """
        body = await request.body()
        params = a2a_pb2.SendMessageRequest()
        Parse(body, params)
        # Transform the proto object to the python internal objects
        a2a_request = proto_utils.FromProto.message_send_params(
            params,
        )
        task_or_message = await self.request_handler.on_message_send(
            a2a_request, context
        )
        return MessageToDict(
            proto_utils.ToProto.task_or_message(task_or_message)
        )

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

        Yields response objects as they are produced by the underlying handler's stream.

        Args:
            request: The incoming `Request` object.
            context: Context provided by the server.

        Yields:
            JSON serialized objects containing streaming events
            (Task, Message, TaskStatusUpdateEvent, TaskArtifactUpdateEvent) as JSON
        """
        body = await request.body()
        params = a2a_pb2.SendMessageRequest()
        Parse(body, params)
        # Transform the proto object to the python internal objects
        a2a_request = proto_utils.FromProto.message_send_params(
            params,
        )
        async for event in self.request_handler.on_message_send_stream(
            a2a_request, context
        ):
            response = proto_utils.ToProto.stream_response(event)
            yield MessageToJson(response)

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
            A `dict` containing the updated Task
        """
        task_id = request.path_params['id']
        task = await self.request_handler.on_cancel_task(
            TaskIdParams(id=task_id), context
        )
        if task:
            return MessageToDict(proto_utils.ToProto.task(task))
        raise ServerError(error=TaskNotFoundError())

    @validate(
        lambda self: self.agent_card.capabilities.streaming,
        'Streaming is not supported by the agent',
    )
    async def on_resubscribe_to_task(
        self,
        request: Request,
        context: ServerCallContext,
    ) -> AsyncIterable[str]:
        """Handles the 'tasks/resubscribe' REST method.

        Yields response objects as they are produced by the underlying handler's stream.

        Args:
            request: The incoming `Request` object.
            context: Context provided by the server.

        Yields:
            JSON serialized objects containing streaming events
        """
        task_id = request.path_params['id']
        async for event in self.request_handler.on_resubscribe_to_task(
            TaskIdParams(id=task_id), context
        ):
            yield MessageToJson(proto_utils.ToProto.stream_response(event))

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
            A `dict` containing the config
        """
        task_id = request.path_params['id']
        push_id = request.path_params['push_id']
        params = GetTaskPushNotificationConfigParams(
            id=task_id, push_notification_config_id=push_id
        )
        config = (
            await self.request_handler.on_get_task_push_notification_config(
                params, context
            )
        )
        return MessageToDict(
            proto_utils.ToProto.task_push_notification_config(config)
        )

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

        Requires the agent to support push notifications.

        Args:
            request: The incoming `TaskPushNotificationConfig` object.
            context: Context provided by the server.

        Returns:
            A `dict` containing the config object.

        Raises:
            ServerError: If push notifications are not supported by the agent
                (due to the `@validate` decorator), A2AError if processing error is
                found.
        """
        task_id = request.path_params['id']
        body = await request.body()
        params = a2a_pb2.CreateTaskPushNotificationConfigRequest()
        Parse(body, params)
        a2a_request = (
            proto_utils.FromProto.task_push_notification_config_request(
                params,
            )
        )
        a2a_request.task_id = task_id
        config = (
            await self.request_handler.on_set_task_push_notification_config(
                a2a_request, context
            )
        )
        return MessageToDict(
            proto_utils.ToProto.task_push_notification_config(config)
        )

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
            A `Task` object containing the Task.
        """
        task_id = request.path_params['id']
        history_length_str = request.query_params.get('historyLength')
        history_length = int(history_length_str) if history_length_str else None
        params = TaskQueryParams(id=task_id, history_length=history_length)
        task = await self.request_handler.on_get_task(params, context)
        if task:
            return MessageToDict(proto_utils.ToProto.task(task))
        raise ServerError(error=TaskNotFoundError())

    async def list_push_notifications(
        self,
        request: Request,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'tasks/pushNotificationConfig/list' REST method.

        This method is currently not implemented.

        Args:
            request: The incoming `Request` object.
            context: Context provided by the server.

        Returns:
            A list of `dict` representing the `TaskPushNotificationConfig` objects.

        Raises:
            NotImplementedError: This method is not yet implemented.
        """
        raise NotImplementedError('list notifications not implemented')

    async def list_tasks(
        self,
        request: Request,
        context: ServerCallContext,
    ) -> dict[str, Any]:
        """Handles the 'tasks/list' REST method.

        This method is currently not implemented.

        Args:
            request: The incoming `Request` object.
            context: Context provided by the server.

        Returns:
            A list of dict representing the`Task` objects.

        Raises:
            NotImplementedError: This method is not yet implemented.
        """
        raise NotImplementedError('list tasks not implemented')
