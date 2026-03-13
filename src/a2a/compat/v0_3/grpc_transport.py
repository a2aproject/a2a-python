import logging

from collections.abc import AsyncGenerator, Callable
from functools import wraps
from typing import Any, NoReturn

from a2a.client.errors import A2AClientError, A2AClientTimeoutError
from a2a.utils.errors import JSON_RPC_ERROR_CODE_MAP


try:
    import grpc  # type: ignore[reportMissingModuleSource]
except ImportError as e:
    raise ImportError(
        'A2AGrpcClient requires grpcio and grpcio-tools to be installed. '
        'Install with: '
        "'pip install a2a-sdk[grpc]'"
    ) from e


from a2a.client.client import ClientCallContext, ClientConfig
from a2a.client.optionals import Channel
from a2a.client.transports.base import ClientTransport
from a2a.compat.v0_3 import (
    a2a_v0_3_pb2,
    a2a_v0_3_pb2_grpc,
    conversions,
    proto_utils,
)
from a2a.compat.v0_3 import (
    types as types_v03,
)
from a2a.types import a2a_pb2
from a2a.utils.constants import PROTOCOL_VERSION_0_3, VERSION_HEADER
from a2a.utils.telemetry import SpanKind, trace_class


logger = logging.getLogger(__name__)

_A2A_ERROR_NAME_TO_CLS = {
    error_type.__name__: error_type for error_type in JSON_RPC_ERROR_CODE_MAP
}


def _map_grpc_error(e: grpc.aio.AioRpcError) -> NoReturn:
    if e.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
        raise A2AClientTimeoutError('Client Request timed out') from e

    details = e.details()
    if isinstance(details, str) and ': ' in details:
        error_type_name, error_message = details.split(': ', 1)
        exception_cls = _A2A_ERROR_NAME_TO_CLS.get(error_type_name)
        if exception_cls:
            raise exception_cls(error_message) from e
    raise A2AClientError(f'gRPC Error {e.code().name}: {e.details()}') from e


def _handle_grpc_exception(func: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except grpc.aio.AioRpcError as e:
            _map_grpc_error(e)

    return wrapper


def _handle_grpc_stream_exception(
    func: Callable[..., Any],
) -> Callable[..., Any]:
    @wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            async for item in func(*args, **kwargs):
                yield item
        except grpc.aio.AioRpcError as e:
            _map_grpc_error(e)

    return wrapper


@trace_class(kind=SpanKind.CLIENT)
class CompatGrpcTransport(ClientTransport):
    """A backward compatible gRPC transport for A2A v0.3."""

    def __init__(self, channel: Channel, agent_card: a2a_pb2.AgentCard | None):
        """Initializes the CompatGrpcTransport."""
        self.agent_card = agent_card
        self.channel = channel
        self.stub = a2a_v0_3_pb2_grpc.A2AServiceStub(channel)

    @classmethod
    def create(
        cls,
        card: a2a_pb2.AgentCard,
        url: str,
        config: ClientConfig,
    ) -> 'CompatGrpcTransport':
        """Creates a gRPC transport for the A2A client."""
        if config.grpc_channel_factory is None:
            raise ValueError('grpc_channel_factory is required when using gRPC')
        return cls(config.grpc_channel_factory(url), card)

    @_handle_grpc_exception
    async def send_message(
        self,
        request: a2a_pb2.SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> a2a_pb2.SendMessageResponse:
        """Sends a non-streaming message request to the agent (v0.3)."""
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

        resp_proto = await self.stub.SendMessage(
            req_proto,
            metadata=self._get_grpc_metadata(context),
        )

        which = resp_proto.WhichOneof('payload')
        if which == 'task':
            return a2a_pb2.SendMessageResponse(
                task=conversions.to_core_task(
                    proto_utils.FromProto.task(resp_proto.task)
                )
            )
        if which == 'msg':
            return a2a_pb2.SendMessageResponse(
                message=conversions.to_core_message(
                    proto_utils.FromProto.message(resp_proto.msg)
                )
            )
        return a2a_pb2.SendMessageResponse()

    @_handle_grpc_stream_exception
    async def send_message_streaming(
        self,
        request: a2a_pb2.SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[a2a_pb2.StreamResponse]:
        """Sends a streaming message request to the agent (v0.3)."""
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

        stream = self.stub.SendStreamingMessage(
            req_proto,
            metadata=self._get_grpc_metadata(context),
        )
        while True:
            response = await stream.read()
            if response == grpc.aio.EOF:  # type: ignore[attr-defined]
                break
            yield conversions.to_core_stream_response(
                types_v03.SendStreamingMessageSuccessResponse(
                    result=proto_utils.FromProto.stream_response(response)
                )
            )

    @_handle_grpc_stream_exception
    async def subscribe(
        self,
        request: a2a_pb2.SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncGenerator[a2a_pb2.StreamResponse]:
        """Reconnects to get task updates (v0.3)."""
        req_proto = a2a_v0_3_pb2.TaskSubscriptionRequest(
            name=f'tasks/{request.id}'
        )

        stream = self.stub.TaskSubscription(
            req_proto,
            metadata=self._get_grpc_metadata(context),
        )
        while True:
            response = await stream.read()
            if response == grpc.aio.EOF:  # type: ignore[attr-defined]
                break
            yield conversions.to_core_stream_response(
                types_v03.SendStreamingMessageSuccessResponse(
                    result=proto_utils.FromProto.stream_response(response)
                )
            )

    @_handle_grpc_exception
    async def get_task(
        self,
        request: a2a_pb2.GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> a2a_pb2.Task:
        """Retrieves the current state and history of a specific task (v0.3)."""
        req_proto = a2a_v0_3_pb2.GetTaskRequest(
            name=f'tasks/{request.id}',
            history_length=request.history_length,
        )
        resp_proto = await self.stub.GetTask(
            req_proto,
            metadata=self._get_grpc_metadata(context),
        )
        return conversions.to_core_task(proto_utils.FromProto.task(resp_proto))

    @_handle_grpc_exception
    async def list_tasks(
        self,
        request: a2a_pb2.ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> a2a_pb2.ListTasksResponse:
        """Retrieves tasks for an agent (v0.3 - NOT SUPPORTED in v0.3)."""
        # v0.3 proto doesn't have ListTasks.
        raise NotImplementedError(
            'ListTasks is not supported in A2A v0.3 gRPC.'
        )

    @_handle_grpc_exception
    async def cancel_task(
        self,
        request: a2a_pb2.CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> a2a_pb2.Task:
        """Requests the agent to cancel a specific task (v0.3)."""
        req_proto = a2a_v0_3_pb2.CancelTaskRequest(name=f'tasks/{request.id}')
        resp_proto = await self.stub.CancelTask(
            req_proto,
            metadata=self._get_grpc_metadata(context),
        )
        return conversions.to_core_task(proto_utils.FromProto.task(resp_proto))

    @_handle_grpc_exception
    async def create_task_push_notification_config(
        self,
        request: a2a_pb2.TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> a2a_pb2.TaskPushNotificationConfig:
        """Sets or updates the push notification configuration (v0.3)."""
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
        resp_proto = await self.stub.CreateTaskPushNotificationConfig(
            req_proto,
            metadata=self._get_grpc_metadata(context),
        )
        return conversions.to_core_task_push_notification_config(
            proto_utils.FromProto.task_push_notification_config(resp_proto)
        )

    @_handle_grpc_exception
    async def get_task_push_notification_config(
        self,
        request: a2a_pb2.GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> a2a_pb2.TaskPushNotificationConfig:
        """Retrieves the push notification configuration (v0.3)."""
        req_proto = a2a_v0_3_pb2.GetTaskPushNotificationConfigRequest(
            name=f'tasks/{request.task_id}/pushNotificationConfigs/{request.id}'
        )
        resp_proto = await self.stub.GetTaskPushNotificationConfig(
            req_proto,
            metadata=self._get_grpc_metadata(context),
        )
        return conversions.to_core_task_push_notification_config(
            proto_utils.FromProto.task_push_notification_config(resp_proto)
        )

    @_handle_grpc_exception
    async def list_task_push_notification_configs(
        self,
        request: a2a_pb2.ListTaskPushNotificationConfigsRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> a2a_pb2.ListTaskPushNotificationConfigsResponse:
        """Lists push notification configurations for a specific task (v0.3)."""
        req_proto = a2a_v0_3_pb2.ListTaskPushNotificationConfigRequest(
            parent=f'tasks/{request.task_id}'
        )
        resp_proto = await self.stub.ListTaskPushNotificationConfig(
            req_proto,
            metadata=self._get_grpc_metadata(context),
        )
        return conversions.to_core_list_task_push_notification_config_response(
            proto_utils.FromProto.list_task_push_notification_config_response(
                resp_proto
            )
        )

    @_handle_grpc_exception
    async def delete_task_push_notification_config(
        self,
        request: a2a_pb2.DeleteTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> None:
        """Deletes the push notification configuration (v0.3)."""
        req_proto = a2a_v0_3_pb2.DeleteTaskPushNotificationConfigRequest(
            name=f'tasks/{request.task_id}/pushNotificationConfigs/{request.id}'
        )
        await self.stub.DeleteTaskPushNotificationConfig(
            req_proto,
            metadata=self._get_grpc_metadata(context),
        )

    @_handle_grpc_exception
    async def get_extended_agent_card(
        self,
        request: a2a_pb2.GetExtendedAgentCardRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> a2a_pb2.AgentCard:
        """Retrieves the agent's card (v0.3)."""
        req_proto = a2a_v0_3_pb2.GetAgentCardRequest()
        resp_proto = await self.stub.GetAgentCard(
            req_proto,
            metadata=self._get_grpc_metadata(context),
        )
        card = conversions.to_core_agent_card(
            proto_utils.FromProto.agent_card(resp_proto)
        )

        self.agent_card = card
        return card

    async def close(self) -> None:
        """Closes the gRPC channel."""
        await self.channel.close()

    def _get_grpc_metadata(
        self, context: ClientCallContext | None = None
    ) -> list[tuple[str, str]]:
        """Creates gRPC metadata for extensions."""
        metadata = [(VERSION_HEADER.lower(), PROTOCOL_VERSION_0_3)]

        if context and context.service_parameters:
            for key, value in context.service_parameters.items():
                metadata.append((key.lower(), value))

        return metadata
