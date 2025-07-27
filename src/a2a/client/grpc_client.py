import logging

from collections.abc import AsyncGenerator


try:
    import grpc
except ImportError as e:
    raise ImportError(
        'A2AGrpcClient requires grpcio and grpcio-tools to be installed. '
        'Install with: '
        "'pip install a2a-sdk[grpc]'"
    ) from e


from a2a.grpc import a2a_pb2, a2a_pb2_grpc
from a2a.types import (
    AgentCard,
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
class A2AGrpcClient:
    """A2A Client for interacting with an A2A agent via gRPC."""

    def __init__(
        self,
        grpc_stub: a2a_pb2_grpc.A2AServiceStub,
        agent_card: AgentCard,
    ):
        """Initializes the A2AGrpcClient.

        Requires an `AgentCard`

        Args:
            grpc_stub: A grpc client stub.
            agent_card: The agent card object.
        """
        self.agent_card = agent_card
        self.stub = grpc_stub

    async def send_message(
        self,
        request: MessageSendParams,
    ) -> Task | Message:
        """Sends a non-streaming message request to the agent.

        Args:
            request: The `MessageSendParams` object containing the message and configuration.

        Returns:
            A `Task` or `Message` object containing the agent's response.
        """
        response = await self.stub.SendMessage(
            a2a_pb2.SendMessageRequest(
                request=proto_utils.ToProto.message(request.message),
                configuration=proto_utils.ToProto.message_send_configuration(
                    request.configuration
                ),
                metadata=proto_utils.ToProto.metadata(request.metadata),
            )
        )
        if response.task:
            return proto_utils.FromProto.task(response.task)
        return proto_utils.FromProto.message(response.msg)

    async def send_message_streaming(
        self,
        request: MessageSendParams,
    ) -> AsyncGenerator[
        Message | Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
    ]:
        """Sends a streaming message request to the agent and yields responses as they arrive.

        This method uses gRPC streams to receive a stream of updates from the
        agent.

        Args:
            request: The `MessageSendParams` object containing the message and configuration.

        Yields:
            `Message` or `Task` or `TaskStatusUpdateEvent` or
            `TaskArtifactUpdateEvent` objects as they are received in the
            stream.
        """
        stream = self.stub.SendStreamingMessage(
            a2a_pb2.SendMessageRequest(
                request=proto_utils.ToProto.message(request.message),
                configuration=proto_utils.ToProto.message_send_configuration(
                    request.configuration
                ),
                metadata=proto_utils.ToProto.metadata(request.metadata),
            )
        )
        while True:
            response = await stream.read()
            if response == grpc.aio.EOF:  # pyright: ignore [reportAttributeAccessIssue]
                break
            if response.HasField('msg'):
                yield proto_utils.FromProto.message(response.msg)
            elif response.HasField('task'):
                yield proto_utils.FromProto.task(response.task)
            elif response.HasField('status_update'):
                yield proto_utils.FromProto.task_status_update_event(
                    response.status_update
                )
            elif response.HasField('artifact_update'):
                yield proto_utils.FromProto.task_artifact_update_event(
                    response.artifact_update
                )

    async def get_task(
        self,
        request: TaskQueryParams,
    ) -> Task:
        """Retrieves the current state and history of a specific task.

        Args:
            request: The `TaskQueryParams` object specifying the task ID

        Returns:
            A `Task` object containing the Task or None.
        """
        task = await self.stub.GetTask(
            a2a_pb2.GetTaskRequest(name=f'tasks/{request.id}')
        )
        return proto_utils.FromProto.task(task)

    async def cancel_task(
        self,
        request: TaskIdParams,
    ) -> Task:
        """Requests the agent to cancel a specific task.

        Args:
            request: The `TaskIdParams` object specifying the task ID.

        Returns:
            A `Task` object containing the updated Task
        """
        task = await self.stub.CancelTask(
            a2a_pb2.CancelTaskRequest(name=f'tasks/{request.id}')
        )
        return proto_utils.FromProto.task(task)

    async def set_task_callback(
        self,
        request: TaskPushNotificationConfig,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task.

        Args:
            request: The `TaskPushNotificationConfig` object specifying the task ID and configuration.

        Returns:
            A `TaskPushNotificationConfig` object containing the config.
        """
        config = await self.stub.CreateTaskPushNotificationConfig(
            a2a_pb2.CreateTaskPushNotificationConfigRequest(
                parent='',
                config_id='',
                config=proto_utils.ToProto.task_push_notification_config(
                    request
                ),
            )
        )
        return proto_utils.FromProto.task_push_notification_config(config)

    async def get_task_callback(
        self,
        request: TaskIdParams,  # TODO: Update to a push id params
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task.

        Args:
            request: The `TaskIdParams` object specifying the task ID.

        Returns:
            A `TaskPushNotificationConfig` object containing the configuration.
        """
        config = await self.stub.GetTaskPushNotificationConfig(
            a2a_pb2.GetTaskPushNotificationConfigRequest(
                name=f'tasks/{request.id}/pushNotification/undefined',
            )
        )
        return proto_utils.FromProto.task_push_notification_config(config)
