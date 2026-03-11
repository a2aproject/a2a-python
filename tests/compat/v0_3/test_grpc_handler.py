import grpc
import grpc.aio
import pytest
from unittest.mock import AsyncMock, MagicMock, ANY

from a2a.compat.v0_3 import (
    a2a_v0_3_pb2,
    grpc_handler as compat_grpc_handler,
)
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import RequestHandler
from a2a.types import a2a_pb2
from a2a.utils.errors import TaskNotFoundError, InvalidParamsError


@pytest.fixture
def mock_request_handler() -> AsyncMock:
    return AsyncMock(spec=RequestHandler)


@pytest.fixture
def mock_grpc_context() -> AsyncMock:
    context = AsyncMock(spec=grpc.aio.ServicerContext)
    context.abort = AsyncMock()
    context.set_trailing_metadata = MagicMock()
    context.invocation_metadata = MagicMock(return_value=grpc.aio.Metadata())
    return context


@pytest.fixture
def sample_agent_card() -> a2a_pb2.AgentCard:
    return a2a_pb2.AgentCard(
        name='Test Agent',
        description='A test agent',
        version='1.0.0',
    )


@pytest.fixture
def handler(
    mock_request_handler: AsyncMock, sample_agent_card: a2a_pb2.AgentCard
) -> compat_grpc_handler.CompatGrpcHandler:
    return compat_grpc_handler.CompatGrpcHandler(
        agent_card=sample_agent_card, request_handler=mock_request_handler
    )


@pytest.mark.asyncio
async def test_send_message_success_task(
    handler: compat_grpc_handler.CompatGrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    request = a2a_v0_3_pb2.SendMessageRequest(
        request=a2a_v0_3_pb2.Message(
            message_id='msg-1', role=a2a_v0_3_pb2.Role.ROLE_USER
        )
    )
    mock_request_handler.on_message_send.return_value = a2a_pb2.Task(
        id='task-1', context_id='ctx-1'
    )

    response = await handler.SendMessage(request, mock_grpc_context)

    expected_req = a2a_pb2.SendMessageRequest(
        message=a2a_pb2.Message(
            message_id='msg-1', role=a2a_pb2.Role.ROLE_USER
        ),
        configuration=a2a_pb2.SendMessageConfiguration(
            history_length=0, return_immediately=True
        ),
    )
    mock_request_handler.on_message_send.assert_called_once_with(
        expected_req, ANY
    )

    expected_res = a2a_v0_3_pb2.SendMessageResponse(
        task=a2a_v0_3_pb2.Task(
            id='task-1', context_id='ctx-1', status=a2a_v0_3_pb2.TaskStatus()
        )
    )
    assert response == expected_res


@pytest.mark.asyncio
async def test_send_message_success_message(
    handler: compat_grpc_handler.CompatGrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    request = a2a_v0_3_pb2.SendMessageRequest(
        request=a2a_v0_3_pb2.Message(
            message_id='msg-1', role=a2a_v0_3_pb2.Role.ROLE_USER
        )
    )
    mock_request_handler.on_message_send.return_value = a2a_pb2.Message(
        message_id='msg-2', role=a2a_pb2.Role.ROLE_AGENT
    )

    response = await handler.SendMessage(request, mock_grpc_context)

    expected_req = a2a_pb2.SendMessageRequest(
        message=a2a_pb2.Message(
            message_id='msg-1', role=a2a_pb2.Role.ROLE_USER
        ),
        configuration=a2a_pb2.SendMessageConfiguration(
            history_length=0, return_immediately=True
        ),
    )
    mock_request_handler.on_message_send.assert_called_once_with(
        expected_req, ANY
    )

    expected_res = a2a_v0_3_pb2.SendMessageResponse(
        msg=a2a_v0_3_pb2.Message(
            message_id='msg-2', role=a2a_v0_3_pb2.Role.ROLE_AGENT
        )
    )
    assert response == expected_res


@pytest.mark.asyncio
async def test_send_streaming_message_success(
    handler: compat_grpc_handler.CompatGrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    async def mock_stream(*args, **kwargs):
        yield a2a_pb2.Task(id='task-1', context_id='ctx-1')
        yield a2a_pb2.Message(message_id='msg-2', role=a2a_pb2.Role.ROLE_AGENT)
        yield a2a_pb2.TaskStatusUpdateEvent(
            task_id='task-1',
            context_id='ctx-1',
            status=a2a_pb2.TaskStatus(
                state=a2a_pb2.TaskState.TASK_STATE_WORKING
            ),
        )
        yield a2a_pb2.TaskArtifactUpdateEvent(
            task_id='task-1',
            context_id='ctx-1',
            artifact=a2a_pb2.Artifact(artifact_id='art-1'),
        )

    mock_request_handler.on_message_send_stream.side_effect = mock_stream
    request = a2a_v0_3_pb2.SendMessageRequest(
        request=a2a_v0_3_pb2.Message(
            message_id='msg-1', role=a2a_v0_3_pb2.Role.ROLE_USER
        )
    )

    responses = []
    async for res in handler.SendStreamingMessage(request, mock_grpc_context):
        responses.append(res)

    expected_req = a2a_pb2.SendMessageRequest(
        message=a2a_pb2.Message(
            message_id='msg-1', role=a2a_pb2.Role.ROLE_USER
        ),
        configuration=a2a_pb2.SendMessageConfiguration(
            history_length=0, return_immediately=True
        ),
    )
    mock_request_handler.on_message_send_stream.assert_called_once_with(
        expected_req, ANY
    )

    expected_responses = [
        a2a_v0_3_pb2.StreamResponse(
            task=a2a_v0_3_pb2.Task(
                id='task-1',
                context_id='ctx-1',
                status=a2a_v0_3_pb2.TaskStatus(),
            )
        ),
        a2a_v0_3_pb2.StreamResponse(
            msg=a2a_v0_3_pb2.Message(
                message_id='msg-2', role=a2a_v0_3_pb2.Role.ROLE_AGENT
            )
        ),
        a2a_v0_3_pb2.StreamResponse(
            status_update=a2a_v0_3_pb2.TaskStatusUpdateEvent(
                task_id='task-1',
                context_id='ctx-1',
                status=a2a_v0_3_pb2.TaskStatus(
                    state=a2a_v0_3_pb2.TaskState.TASK_STATE_WORKING
                ),
            )
        ),
        a2a_v0_3_pb2.StreamResponse(
            artifact_update=a2a_v0_3_pb2.TaskArtifactUpdateEvent(
                task_id='task-1',
                context_id='ctx-1',
                artifact=a2a_v0_3_pb2.Artifact(artifact_id='art-1'),
            )
        ),
    ]
    assert responses == expected_responses


@pytest.mark.asyncio
async def test_get_task_success(
    handler: compat_grpc_handler.CompatGrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    request = a2a_v0_3_pb2.GetTaskRequest(name='tasks/task-1')
    mock_request_handler.on_get_task.return_value = a2a_pb2.Task(
        id='task-1', context_id='ctx-1'
    )

    response = await handler.GetTask(request, mock_grpc_context)

    expected_req = a2a_pb2.GetTaskRequest(id='task-1')
    mock_request_handler.on_get_task.assert_called_once_with(expected_req, ANY)

    expected_res = a2a_v0_3_pb2.Task(
        id='task-1', context_id='ctx-1', status=a2a_v0_3_pb2.TaskStatus()
    )
    assert response == expected_res


@pytest.mark.asyncio
async def test_get_task_not_found(
    handler: compat_grpc_handler.CompatGrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    request = a2a_v0_3_pb2.GetTaskRequest(name='tasks/task-1')
    mock_request_handler.on_get_task.return_value = None

    await handler.GetTask(request, mock_grpc_context)

    expected_req = a2a_pb2.GetTaskRequest(id='task-1')
    mock_request_handler.on_get_task.assert_called_once_with(expected_req, ANY)
    mock_grpc_context.abort.assert_called()
    assert mock_grpc_context.abort.call_args[0][0] == grpc.StatusCode.NOT_FOUND


@pytest.mark.asyncio
async def test_cancel_task_success(
    handler: compat_grpc_handler.CompatGrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    request = a2a_v0_3_pb2.CancelTaskRequest(name='tasks/task-1')
    mock_request_handler.on_cancel_task.return_value = a2a_pb2.Task(
        id='task-1', context_id='ctx-1'
    )

    response = await handler.CancelTask(request, mock_grpc_context)

    expected_req = a2a_pb2.CancelTaskRequest(id='task-1')
    mock_request_handler.on_cancel_task.assert_called_once_with(
        expected_req, ANY
    )

    expected_res = a2a_v0_3_pb2.Task(
        id='task-1', context_id='ctx-1', status=a2a_v0_3_pb2.TaskStatus()
    )
    assert response == expected_res


@pytest.mark.asyncio
async def test_task_subscription_success(
    handler: compat_grpc_handler.CompatGrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    async def mock_stream(*args, **kwargs):
        yield a2a_pb2.TaskStatusUpdateEvent(
            task_id='task-1',
            context_id='ctx-1',
            status=a2a_pb2.TaskStatus(
                state=a2a_pb2.TaskState.TASK_STATE_WORKING
            ),
        )

    mock_request_handler.on_subscribe_to_task.side_effect = mock_stream
    request = a2a_v0_3_pb2.TaskSubscriptionRequest(name='tasks/task-1')

    responses = []
    async for res in handler.TaskSubscription(request, mock_grpc_context):
        responses.append(res)

    expected_req = a2a_pb2.SubscribeToTaskRequest(id='task-1')
    mock_request_handler.on_subscribe_to_task.assert_called_once_with(
        expected_req, ANY
    )

    expected_responses = [
        a2a_v0_3_pb2.StreamResponse(
            status_update=a2a_v0_3_pb2.TaskStatusUpdateEvent(
                task_id='task-1',
                context_id='ctx-1',
                status=a2a_v0_3_pb2.TaskStatus(
                    state=a2a_v0_3_pb2.TaskState.TASK_STATE_WORKING
                ),
            )
        )
    ]
    assert responses == expected_responses


@pytest.mark.asyncio
async def test_create_push_config_success(
    handler: compat_grpc_handler.CompatGrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    request = a2a_v0_3_pb2.CreateTaskPushNotificationConfigRequest(
        parent='tasks/task-1',
        config=a2a_v0_3_pb2.TaskPushNotificationConfig(
            push_notification_config=a2a_v0_3_pb2.PushNotificationConfig(
                url='http://example.com'
            )
        ),
    )
    mock_request_handler.on_create_task_push_notification_config.return_value = a2a_pb2.TaskPushNotificationConfig(
        task_id='task-1',
        url='http://example.com',
        id='cfg-1',
    )

    response = await handler.CreateTaskPushNotificationConfig(
        request, mock_grpc_context
    )

    expected_req = a2a_pb2.TaskPushNotificationConfig(
        task_id='task-1',
        url='http://example.com',
    )
    mock_request_handler.on_create_task_push_notification_config.assert_called_once_with(
        expected_req, ANY
    )

    expected_res = a2a_v0_3_pb2.TaskPushNotificationConfig(
        name='tasks/task-1/pushNotificationConfigs/cfg-1',
        push_notification_config=a2a_v0_3_pb2.PushNotificationConfig(
            url='http://example.com', id='cfg-1'
        ),
    )
    assert response == expected_res


@pytest.mark.asyncio
async def test_get_push_config_success(
    handler: compat_grpc_handler.CompatGrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    request = a2a_v0_3_pb2.GetTaskPushNotificationConfigRequest(
        name='tasks/task-1/pushNotificationConfigs/cfg-1'
    )
    mock_request_handler.on_get_task_push_notification_config.return_value = (
        a2a_pb2.TaskPushNotificationConfig(
            task_id='task-1',
            url='http://example.com',
            id='cfg-1',
        )
    )

    response = await handler.GetTaskPushNotificationConfig(
        request, mock_grpc_context
    )

    expected_req = a2a_pb2.GetTaskPushNotificationConfigRequest(
        task_id='task-1', id='cfg-1'
    )
    mock_request_handler.on_get_task_push_notification_config.assert_called_once_with(
        expected_req, ANY
    )

    expected_res = a2a_v0_3_pb2.TaskPushNotificationConfig(
        name='tasks/task-1/pushNotificationConfigs/cfg-1',
        push_notification_config=a2a_v0_3_pb2.PushNotificationConfig(
            url='http://example.com', id='cfg-1'
        ),
    )
    assert response == expected_res


@pytest.mark.asyncio
async def test_list_push_config_success(
    handler: compat_grpc_handler.CompatGrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    request = a2a_v0_3_pb2.ListTaskPushNotificationConfigRequest(
        parent='tasks/task-1'
    )
    mock_request_handler.on_list_task_push_notification_configs.return_value = (
        a2a_pb2.ListTaskPushNotificationConfigsResponse(
            configs=[
                a2a_pb2.TaskPushNotificationConfig(
                    task_id='task-1', url='http://example.com', id='cfg-1'
                )
            ]
        )
    )

    response = await handler.ListTaskPushNotificationConfig(
        request, mock_grpc_context
    )

    expected_req = a2a_pb2.ListTaskPushNotificationConfigsRequest(
        task_id='task-1'
    )
    mock_request_handler.on_list_task_push_notification_configs.assert_called_once_with(
        expected_req, ANY
    )

    expected_res = a2a_v0_3_pb2.ListTaskPushNotificationConfigResponse(
        configs=[
            a2a_v0_3_pb2.TaskPushNotificationConfig(
                name='tasks/task-1/pushNotificationConfigs/cfg-1',
                push_notification_config=a2a_v0_3_pb2.PushNotificationConfig(
                    url='http://example.com', id='cfg-1'
                ),
            )
        ]
    )
    assert response == expected_res


@pytest.mark.asyncio
async def test_get_agent_card_success(
    handler: compat_grpc_handler.CompatGrpcHandler,
    mock_grpc_context: AsyncMock,
) -> None:
    request = a2a_v0_3_pb2.GetAgentCardRequest()
    response = await handler.GetAgentCard(request, mock_grpc_context)

    expected_res = a2a_v0_3_pb2.AgentCard(
        name='Test Agent',
        description='A test agent',
        version='1.0.0',
        protocol_version='0.3.0',
        preferred_transport='JSONRPC',
        capabilities=a2a_v0_3_pb2.AgentCapabilities(),
    )
    assert response == expected_res


@pytest.mark.asyncio
async def test_delete_push_config_success(
    handler: compat_grpc_handler.CompatGrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    request = a2a_v0_3_pb2.DeleteTaskPushNotificationConfigRequest(
        name='tasks/task-1/pushNotificationConfigs/cfg-1'
    )
    mock_request_handler.on_delete_task_push_notification_config.return_value = None

    from google.protobuf import empty_pb2

    response = await handler.DeleteTaskPushNotificationConfig(
        request, mock_grpc_context
    )

    expected_req = a2a_pb2.DeleteTaskPushNotificationConfigRequest(
        task_id='task-1', id='cfg-1'
    )
    mock_request_handler.on_delete_task_push_notification_config.assert_called_once_with(
        expected_req, ANY
    )

    assert isinstance(response, empty_pb2.Empty)


@pytest.mark.asyncio
async def test_extract_task_id_invalid(
    handler: compat_grpc_handler.CompatGrpcHandler,
):
    with pytest.raises(InvalidParamsError):
        handler._extract_task_id('invalid-name')


@pytest.mark.asyncio
async def test_extract_task_and_config_id_invalid(
    handler: compat_grpc_handler.CompatGrpcHandler,
):
    with pytest.raises(InvalidParamsError):
        handler._extract_task_and_config_id('invalid-name')


@pytest.mark.asyncio
async def test_handle_unary_extension_metadata(
    handler: compat_grpc_handler.CompatGrpcHandler,
    mock_request_handler: AsyncMock,
    mock_grpc_context: AsyncMock,
) -> None:
    async def mock_func(server_context: ServerCallContext):
        server_context.activated_extensions.add('ext-1')
        return a2a_pb2.Task()

    await handler._handle_unary(mock_grpc_context, mock_func, a2a_pb2.Task())

    expected_metadata = [(HTTP_EXTENSION_HEADER.lower(), 'ext-1')]
    mock_grpc_context.set_trailing_metadata.assert_called_once_with(
        expected_metadata
    )
