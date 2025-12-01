from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from a2a.client.transports.grpc import GrpcTransport
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.types import a2a_pb2, a2a_pb2_grpc
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    Artifact,
    AuthenticationInfo,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    Message,
    Part,
    PushNotificationConfig,
    Role,
    SendMessageRequest,
    SetTaskPushNotificationConfigRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils import get_text_parts, proto_utils
from a2a.utils.errors import ServerError


@pytest.fixture
def mock_grpc_stub() -> AsyncMock:
    """Provides a mock gRPC stub with methods mocked."""
    stub = MagicMock()  # Use MagicMock without spec to avoid auto-spec warnings
    stub.SendMessage = AsyncMock()
    stub.SendStreamingMessage = MagicMock()
    stub.GetTask = AsyncMock()
    stub.CancelTask = AsyncMock()
    stub.SetTaskPushNotificationConfig = AsyncMock()
    stub.GetTaskPushNotificationConfig = AsyncMock()
    return stub


@pytest.fixture
def sample_agent_card() -> AgentCard:
    """Provides a minimal agent card for initialization."""
    return AgentCard(
        name='gRPC Test Agent',
        description='Agent for testing gRPC client',
        url='grpc://localhost:50051',
        version='1.0',
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        skills=[],
    )


@pytest.fixture
def grpc_transport(
    mock_grpc_stub: AsyncMock, sample_agent_card: AgentCard
) -> GrpcTransport:
    """Provides a GrpcTransport instance."""
    channel = MagicMock()  # Use MagicMock instead of AsyncMock
    transport = GrpcTransport(
        channel=channel,
        agent_card=sample_agent_card,
        extensions=[
            'https://example.com/test-ext/v1',
            'https://example.com/test-ext/v2',
        ],
    )
    transport.stub = mock_grpc_stub
    return transport


@pytest.fixture
def sample_message_send_params() -> SendMessageRequest:
    """Provides a sample SendMessageRequest object."""
    return SendMessageRequest(
        request=Message(
            role=Role.ROLE_USER,
            message_id='msg-1',
            parts=[Part(text='Hello')],
        )
    )


@pytest.fixture
def sample_task() -> Task:
    """Provides a sample Task object."""
    return Task(
        id='task-1',
        context_id='ctx-1',
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
    )


@pytest.fixture
def sample_message() -> Message:
    """Provides a sample Message object."""
    return Message(
        role=Role.ROLE_AGENT,
        message_id='msg-response',
        parts=[Part(text='Hi there')],
    )


@pytest.fixture
def sample_artifact() -> Artifact:
    """Provides a sample Artifact object."""
    return Artifact(
        artifact_id='artifact-1',
        name='example.txt',
        description='An example artifact',
        parts=[Part(text='Hi there')],
        metadata={},
        extensions=[],
    )


@pytest.fixture
def sample_task_status_update_event() -> TaskStatusUpdateEvent:
    """Provides a sample TaskStatusUpdateEvent."""
    return TaskStatusUpdateEvent(
        task_id='task-1',
        context_id='ctx-1',
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        final=False,
        metadata={},
    )


@pytest.fixture
def sample_task_artifact_update_event(
    sample_artifact: Artifact,
) -> TaskArtifactUpdateEvent:
    """Provides a sample TaskArtifactUpdateEvent."""
    return TaskArtifactUpdateEvent(
        task_id='task-1',
        context_id='ctx-1',
        artifact=sample_artifact,
        append=True,
        last_chunk=True,
        metadata={},
    )


@pytest.fixture
def sample_authentication_info() -> AuthenticationInfo:
    """Provides a sample AuthenticationInfo object."""
    return AuthenticationInfo(
        schemes=['apikey', 'oauth2'], credentials='secret-token'
    )


@pytest.fixture
def sample_push_notification_config(
    sample_authentication_info: AuthenticationInfo,
) -> PushNotificationConfig:
    """Provides a sample PushNotificationConfig object."""
    return PushNotificationConfig(
        id='config-1',
        url='https://example.com/notify',
        token='example-token',
        authentication=sample_authentication_info,
    )


@pytest.fixture
def sample_task_push_notification_config(
    sample_push_notification_config: PushNotificationConfig,
) -> TaskPushNotificationConfig:
    """Provides a sample TaskPushNotificationConfig object."""
    return TaskPushNotificationConfig(
        name='tasks/task-1',
        push_notification_config=sample_push_notification_config,
    )


@pytest.mark.asyncio
async def test_send_message_task_response(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_message_send_params: SendMessageRequest,
    sample_task: Task,
) -> None:
    """Test send_message that returns a Task."""
    mock_grpc_stub.SendMessage.return_value = a2a_pb2.SendMessageResponse(
        task=sample_task
    )

    response = await grpc_transport.send_message(
        sample_message_send_params,
        extensions=['https://example.com/test-ext/v3'],
    )

    mock_grpc_stub.SendMessage.assert_awaited_once()
    _, kwargs = mock_grpc_stub.SendMessage.call_args
    assert kwargs['metadata'] == [
        (
            HTTP_EXTENSION_HEADER,
            'https://example.com/test-ext/v3',
        )
    ]
    assert response.HasField('task')
    assert response.task.id == sample_task.id


@pytest.mark.asyncio
async def test_send_message_message_response(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_message_send_params: SendMessageRequest,
    sample_message: Message,
) -> None:
    """Test send_message that returns a Message."""
    mock_grpc_stub.SendMessage.return_value = a2a_pb2.SendMessageResponse(
        msg=sample_message
    )

    response = await grpc_transport.send_message(sample_message_send_params)

    mock_grpc_stub.SendMessage.assert_awaited_once()
    _, kwargs = mock_grpc_stub.SendMessage.call_args
    assert kwargs['metadata'] == [
        (
            HTTP_EXTENSION_HEADER,
            'https://example.com/test-ext/v1,https://example.com/test-ext/v2',
        )
    ]
    assert response.HasField('msg')
    assert response.msg.message_id == sample_message.message_id
    assert get_text_parts(response.msg.parts) == get_text_parts(
        sample_message.parts
    )


@pytest.mark.asyncio
async def test_send_message_streaming(  # noqa: PLR0913
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_message_send_params: SendMessageRequest,
    sample_message: Message,
    sample_task: Task,
    sample_task_status_update_event: TaskStatusUpdateEvent,
    sample_task_artifact_update_event: TaskArtifactUpdateEvent,
) -> None:
    """Test send_message_streaming that yields responses."""
    stream = MagicMock()
    stream.read = AsyncMock(
        side_effect=[
            a2a_pb2.StreamResponse(msg=sample_message),
            a2a_pb2.StreamResponse(task=sample_task),
            a2a_pb2.StreamResponse(
                status_update=sample_task_status_update_event
            ),
            a2a_pb2.StreamResponse(
                artifact_update=sample_task_artifact_update_event
            ),
            grpc.aio.EOF,
        ]
    )
    mock_grpc_stub.SendStreamingMessage.return_value = stream

    responses = [
        response
        async for response in grpc_transport.send_message_streaming(
            sample_message_send_params
        )
    ]

    mock_grpc_stub.SendStreamingMessage.assert_called_once()
    _, kwargs = mock_grpc_stub.SendStreamingMessage.call_args
    assert kwargs['metadata'] == [
        (
            HTTP_EXTENSION_HEADER,
            'https://example.com/test-ext/v1,https://example.com/test-ext/v2',
        )
    ]
    # Responses are StreamResponse proto objects
    assert responses[0].HasField('msg')
    assert responses[0].msg.message_id == sample_message.message_id
    assert responses[1].HasField('task')
    assert responses[1].task.id == sample_task.id
    assert responses[2].HasField('status_update')
    assert (
        responses[2].status_update.task_id
        == sample_task_status_update_event.task_id
    )
    assert responses[3].HasField('artifact_update')
    assert (
        responses[3].artifact_update.task_id
        == sample_task_artifact_update_event.task_id
    )


@pytest.mark.asyncio
async def test_get_task(
    grpc_transport: GrpcTransport, mock_grpc_stub: AsyncMock, sample_task: Task
) -> None:
    """Test retrieving a task."""
    mock_grpc_stub.GetTask.return_value = sample_task
    params = GetTaskRequest(name=f'tasks/{sample_task.id}')

    response = await grpc_transport.get_task(params)

    mock_grpc_stub.GetTask.assert_awaited_once_with(
        a2a_pb2.GetTaskRequest(
            name=f'tasks/{sample_task.id}', history_length=None
        ),
        metadata=[
            (
                HTTP_EXTENSION_HEADER,
                'https://example.com/test-ext/v1,https://example.com/test-ext/v2',
            )
        ],
    )
    assert response.id == sample_task.id


@pytest.mark.asyncio
async def test_get_task_with_history(
    grpc_transport: GrpcTransport, mock_grpc_stub: AsyncMock, sample_task: Task
) -> None:
    """Test retrieving a task with history."""
    mock_grpc_stub.GetTask.return_value = sample_task
    history_len = 10
    params = GetTaskRequest(
        name=f'tasks/{sample_task.id}', history_length=history_len
    )

    await grpc_transport.get_task(params)

    mock_grpc_stub.GetTask.assert_awaited_once_with(
        a2a_pb2.GetTaskRequest(
            name=f'tasks/{sample_task.id}', history_length=history_len
        ),
        metadata=[
            (
                HTTP_EXTENSION_HEADER,
                'https://example.com/test-ext/v1,https://example.com/test-ext/v2',
            )
        ],
    )


@pytest.mark.asyncio
async def test_cancel_task(
    grpc_transport: GrpcTransport, mock_grpc_stub: AsyncMock, sample_task: Task
) -> None:
    """Test cancelling a task."""
    cancelled_task = Task(
        id=sample_task.id,
        context_id=sample_task.context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_CANCELLED),
    )
    mock_grpc_stub.CancelTask.return_value = cancelled_task
    extensions = [
        'https://example.com/test-ext/v3',
    ]
    request = a2a_pb2.CancelTaskRequest(name=f'tasks/{sample_task.id}')
    response = await grpc_transport.cancel_task(request, extensions=extensions)

    mock_grpc_stub.CancelTask.assert_awaited_once_with(
        a2a_pb2.CancelTaskRequest(name=f'tasks/{sample_task.id}'),
        metadata=[(HTTP_EXTENSION_HEADER, 'https://example.com/test-ext/v3')],
    )
    assert response.status.state == TaskState.TASK_STATE_CANCELLED


@pytest.mark.asyncio
async def test_set_task_callback_with_valid_task(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_task_push_notification_config: TaskPushNotificationConfig,
) -> None:
    """Test setting a task push notification config with a valid task id."""
    mock_grpc_stub.SetTaskPushNotificationConfig.return_value = (
        sample_task_push_notification_config
    )

    # Create the request object expected by the transport
    request = SetTaskPushNotificationConfigRequest(
        parent='tasks/task-1',
        config_id=sample_task_push_notification_config.push_notification_config.id,
        config=sample_task_push_notification_config,
    )
    response = await grpc_transport.set_task_callback(request)

    mock_grpc_stub.SetTaskPushNotificationConfig.assert_awaited_once_with(
        request,
        metadata=[
            (
                HTTP_EXTENSION_HEADER,
                'https://example.com/test-ext/v1,https://example.com/test-ext/v2',
            )
        ],
    )
    assert response.name == sample_task_push_notification_config.name


@pytest.mark.asyncio
async def test_set_task_callback_with_invalid_task(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_push_notification_config: PushNotificationConfig,
) -> None:
    """Test setting a task push notification config with an invalid task name format."""
    # Return a config with an invalid name format
    mock_grpc_stub.SetTaskPushNotificationConfig.return_value = a2a_pb2.TaskPushNotificationConfig(
        name='invalid-path-to-tasks/task-1/pushNotificationConfigs/config-1',
        push_notification_config=sample_push_notification_config,
    )

    request = SetTaskPushNotificationConfigRequest(
        parent='tasks/task-1',
        config_id='config-1',
        config=TaskPushNotificationConfig(
            name='tasks/task-1/pushNotificationConfigs/config-1',
            push_notification_config=sample_push_notification_config,
        ),
    )

    # Note: The transport doesn't validate the response name format
    # It just returns the response from the stub
    response = await grpc_transport.set_task_callback(request)
    assert (
        response.name
        == 'invalid-path-to-tasks/task-1/pushNotificationConfigs/config-1'
    )


@pytest.mark.asyncio
async def test_get_task_callback_with_valid_task(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_task_push_notification_config: TaskPushNotificationConfig,
) -> None:
    """Test retrieving a task push notification config with a valid task id."""
    mock_grpc_stub.GetTaskPushNotificationConfig.return_value = (
        sample_task_push_notification_config
    )
    config_id = sample_task_push_notification_config.push_notification_config.id

    response = await grpc_transport.get_task_callback(
        GetTaskPushNotificationConfigRequest(
            name=f'tasks/task-1/pushNotificationConfigs/{config_id}'
        )
    )

    mock_grpc_stub.GetTaskPushNotificationConfig.assert_awaited_once_with(
        a2a_pb2.GetTaskPushNotificationConfigRequest(
            name=f'tasks/task-1/pushNotificationConfigs/{config_id}',
        ),
        metadata=[
            (
                HTTP_EXTENSION_HEADER,
                'https://example.com/test-ext/v1,https://example.com/test-ext/v2',
            )
        ],
    )
    assert response.name == sample_task_push_notification_config.name


@pytest.mark.asyncio
async def test_get_task_callback_with_invalid_task(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_push_notification_config: PushNotificationConfig,
) -> None:
    """Test retrieving a task push notification config with an invalid task name."""
    mock_grpc_stub.GetTaskPushNotificationConfig.return_value = a2a_pb2.TaskPushNotificationConfig(
        name='invalid-path-to-tasks/task-1/pushNotificationConfigs/config-1',
        push_notification_config=sample_push_notification_config,
    )

    response = await grpc_transport.get_task_callback(
        GetTaskPushNotificationConfigRequest(
            name='tasks/task-1/pushNotificationConfigs/config-1'
        )
    )
    # The transport doesn't validate the response name format
    assert (
        response.name
        == 'invalid-path-to-tasks/task-1/pushNotificationConfigs/config-1'
    )


@pytest.mark.parametrize(
    'initial_extensions, input_extensions, expected_metadata',
    [
        (
            None,
            None,
            None,
        ),  # Case 1: No initial, No input
        (
            ['ext1'],
            None,
            [(HTTP_EXTENSION_HEADER, 'ext1')],
        ),  # Case 2: Initial, No input
        (
            None,
            ['ext2'],
            [(HTTP_EXTENSION_HEADER, 'ext2')],
        ),  # Case 3: No initial, Input
        (
            ['ext1'],
            ['ext2'],
            [(HTTP_EXTENSION_HEADER, 'ext2')],
        ),  # Case 4: Initial, Input (override)
        (
            ['ext1'],
            ['ext2', 'ext3'],
            [(HTTP_EXTENSION_HEADER, 'ext2,ext3')],
        ),  # Case 5: Initial, Multiple inputs (override)
        (
            ['ext1', 'ext2'],
            ['ext3'],
            [(HTTP_EXTENSION_HEADER, 'ext3')],
        ),  # Case 6: Multiple initial, Single input (override)
    ],
)
def test_get_grpc_metadata(
    grpc_transport: GrpcTransport,
    initial_extensions: list[str] | None,
    input_extensions: list[str] | None,
    expected_metadata: list[tuple[str, str]] | None,
) -> None:
    """Tests _get_grpc_metadata for correct metadata generation and self.extensions update."""
    grpc_transport.extensions = initial_extensions
    metadata = grpc_transport._get_grpc_metadata(input_extensions)
    assert metadata == expected_metadata
