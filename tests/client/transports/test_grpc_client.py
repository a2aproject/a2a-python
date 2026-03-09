from unittest.mock import AsyncMock, MagicMock

import grpc
import pytest

from google.protobuf import any_pb2
from google.rpc import error_details_pb2, status_pb2

from a2a.client.middleware import ClientCallContext
from a2a.client.transports.grpc import GrpcTransport
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.utils.constants import VERSION_HEADER, PROTOCOL_VERSION_CURRENT
from a2a.utils.errors import A2A_ERROR_REASONS
from a2a.types import a2a_pb2
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Artifact,
    AuthenticationInfo,
    CreateTaskPushNotificationConfigRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    Message,
    Part,
    PushNotificationConfig,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils import get_text_parts
from a2a.utils.errors import JSON_RPC_ERROR_CODE_MAP


@pytest.fixture
def mock_grpc_stub() -> AsyncMock:
    """Provides a mock gRPC stub with methods mocked."""
    stub = MagicMock()  # Use MagicMock without spec to avoid auto-spec warnings
    stub.SendMessage = AsyncMock()
    stub.SendStreamingMessage = MagicMock()
    stub.GetTask = AsyncMock()
    stub.ListTasks = AsyncMock()
    stub.CancelTask = AsyncMock()
    stub.CreateTaskPushNotificationConfig = AsyncMock()
    stub.GetTaskPushNotificationConfig = AsyncMock()
    stub.ListTaskPushNotificationConfigs = AsyncMock()
    stub.DeleteTaskPushNotificationConfig = AsyncMock()
    return stub


@pytest.fixture
def sample_agent_card() -> AgentCard:
    """Provides a minimal agent card for initialization."""
    return AgentCard(
        name='gRPC Test Agent',
        description='Agent for testing gRPC client',
        supported_interfaces=[
            AgentInterface(
                url='grpc://localhost:50051', protocol_binding='GRPC'
            )
        ],
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
    )
    transport.stub = mock_grpc_stub
    return transport


@pytest.fixture
def sample_message_send_params() -> SendMessageRequest:
    """Provides a sample SendMessageRequest object."""
    return SendMessageRequest(
        message=Message(
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
def sample_task_2() -> Task:
    """Provides a sample Task object."""
    return Task(
        id='task-2',
        context_id='ctx-2',
        status=TaskStatus(state=TaskState.TASK_STATE_FAILED),
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
    return AuthenticationInfo(scheme='apikey', credentials='secret-token')


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
        task_id='task-1',
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
        context=ClientCallContext(
            service_parameters={
                HTTP_EXTENSION_HEADER: 'https://example.com/test-ext/v3'
            }
        ),
    )

    mock_grpc_stub.SendMessage.assert_awaited_once()
    _, kwargs = mock_grpc_stub.SendMessage.call_args
    assert kwargs['metadata'] == [
        (VERSION_HEADER.lower(), PROTOCOL_VERSION_CURRENT),
        (
            HTTP_EXTENSION_HEADER.lower(),
            'https://example.com/test-ext/v3',
        ),
    ]
    assert response.HasField('task')
    assert response.task.id == sample_task.id


@pytest.mark.asyncio
async def test_send_message_with_timeout_context(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_message_send_params: SendMessageRequest,
    sample_task: Task,
) -> None:
    """Test send_message passes context timeout to grpc stub."""
    from a2a.client.middleware import ClientCallContext

    mock_grpc_stub.SendMessage.return_value = a2a_pb2.SendMessageResponse(
        task=sample_task
    )
    context = ClientCallContext(timeout=12.5)

    await grpc_transport.send_message(
        sample_message_send_params,
        context=context,
    )

    mock_grpc_stub.SendMessage.assert_awaited_once()
    _, kwargs = mock_grpc_stub.SendMessage.call_args
    assert 'timeout' in kwargs
    assert kwargs['timeout'] == 12.5


@pytest.mark.parametrize('error_cls', list(JSON_RPC_ERROR_CODE_MAP.keys()))
@pytest.mark.asyncio
async def test_grpc_mapped_errors_legacy(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_message_send_params: SendMessageRequest,
    error_cls,
) -> None:
    """Test handling of legacy gRPC error responses."""
    error_details = f'{error_cls.__name__}: Mapped Error'

    mock_grpc_stub.SendMessage.side_effect = grpc.aio.AioRpcError(
        code=grpc.StatusCode.INTERNAL,
        initial_metadata=grpc.aio.Metadata(),
        trailing_metadata=grpc.aio.Metadata(),
        details=error_details,
    )

    with pytest.raises(error_cls):
        await grpc_transport.send_message(sample_message_send_params)


@pytest.mark.parametrize('error_cls', list(JSON_RPC_ERROR_CODE_MAP.keys()))
@pytest.mark.asyncio
async def test_grpc_mapped_errors_rich(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_message_send_params: SendMessageRequest,
    error_cls,
) -> None:
    """Test handling of rich gRPC error responses with Status metadata."""

    reason = A2A_ERROR_REASONS.get(error_cls, 'UNKNOWN_ERROR')

    error_info = error_details_pb2.ErrorInfo(
        reason=reason,
        domain='a2a-protocol.org',
    )

    error_details = f'{error_cls.__name__}: Mapped Error'
    status = status_pb2.Status(
        code=grpc.StatusCode.INTERNAL.value[0], message=error_details
    )
    detail = any_pb2.Any()
    detail.Pack(error_info)
    status.details.append(detail)

    mock_grpc_stub.SendMessage.side_effect = grpc.aio.AioRpcError(
        code=grpc.StatusCode.INTERNAL,
        initial_metadata=grpc.aio.Metadata(),
        trailing_metadata=grpc.aio.Metadata(
            ('grpc-status-details-bin', status.SerializeToString()),
        ),
        details='A generic error message',
    )

    with pytest.raises(error_cls) as excinfo:
        await grpc_transport.send_message(sample_message_send_params)

    assert str(excinfo.value) == error_details


@pytest.mark.asyncio
async def test_send_message_message_response(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_message_send_params: SendMessageRequest,
    sample_message: Message,
) -> None:
    """Test send_message that returns a Message."""
    mock_grpc_stub.SendMessage.return_value = a2a_pb2.SendMessageResponse(
        message=sample_message
    )

    response = await grpc_transport.send_message(sample_message_send_params)

    mock_grpc_stub.SendMessage.assert_awaited_once()
    _, kwargs = mock_grpc_stub.SendMessage.call_args
    assert kwargs['metadata'] == [
        (VERSION_HEADER.lower(), PROTOCOL_VERSION_CURRENT),
    ]
    assert response.HasField('message')
    assert response.message.message_id == sample_message.message_id
    assert get_text_parts(response.message.parts) == get_text_parts(
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
            a2a_pb2.StreamResponse(message=sample_message),
            a2a_pb2.StreamResponse(task=sample_task),
            a2a_pb2.StreamResponse(
                status_update=sample_task_status_update_event
            ),
            a2a_pb2.StreamResponse(
                artifact_update=sample_task_artifact_update_event
            ),
            grpc.aio.EOF,  # type: ignore[attr-defined]
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
        (VERSION_HEADER.lower(), PROTOCOL_VERSION_CURRENT),
    ]
    # Responses are StreamResponse proto objects
    assert responses[0].HasField('message')
    assert responses[0].message.message_id == sample_message.message_id
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
    params = GetTaskRequest(id=f'{sample_task.id}')

    response = await grpc_transport.get_task(params)

    mock_grpc_stub.GetTask.assert_awaited_once_with(
        a2a_pb2.GetTaskRequest(id=f'{sample_task.id}', history_length=None),
        metadata=[
            (VERSION_HEADER.lower(), PROTOCOL_VERSION_CURRENT),
        ],
        timeout=None,
    )
    assert response.id == sample_task.id


@pytest.mark.asyncio
async def test_list_tasks(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_task: Task,
    sample_task_2: Task,
):
    """Test listing tasks."""
    mock_grpc_stub.ListTasks.return_value = a2a_pb2.ListTasksResponse(
        tasks=[sample_task, sample_task_2],
        total_size=2,
    )
    params = a2a_pb2.ListTasksRequest()

    result = await grpc_transport.list_tasks(params)

    mock_grpc_stub.ListTasks.assert_awaited_once_with(
        params,
        metadata=[
            (VERSION_HEADER.lower(), PROTOCOL_VERSION_CURRENT),
        ],
        timeout=None,
    )
    assert result.total_size == 2
    assert not result.next_page_token
    assert [t.id for t in result.tasks] == [sample_task.id, sample_task_2.id]


@pytest.mark.asyncio
async def test_get_task_with_history(
    grpc_transport: GrpcTransport, mock_grpc_stub: AsyncMock, sample_task: Task
) -> None:
    """Test retrieving a task with history."""
    mock_grpc_stub.GetTask.return_value = sample_task
    history_len = 10
    params = GetTaskRequest(id=f'{sample_task.id}', history_length=history_len)

    await grpc_transport.get_task(params)

    mock_grpc_stub.GetTask.assert_awaited_once_with(
        a2a_pb2.GetTaskRequest(
            id=f'{sample_task.id}', history_length=history_len
        ),
        metadata=[
            (VERSION_HEADER.lower(), PROTOCOL_VERSION_CURRENT),
        ],
        timeout=None,
    )


@pytest.mark.asyncio
async def test_cancel_task(
    grpc_transport: GrpcTransport, mock_grpc_stub: AsyncMock, sample_task: Task
) -> None:
    """Test cancelling a task."""
    cancelled_task = Task(
        id=sample_task.id,
        context_id=sample_task.context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
    )
    mock_grpc_stub.CancelTask.return_value = cancelled_task
    extensions = 'https://example.com/test-ext/v3'

    request = a2a_pb2.CancelTaskRequest(id=f'{sample_task.id}')
    response = await grpc_transport.cancel_task(
        request,
        context=ClientCallContext(
            service_parameters={HTTP_EXTENSION_HEADER: extensions}
        ),
    )

    mock_grpc_stub.CancelTask.assert_awaited_once_with(
        a2a_pb2.CancelTaskRequest(id=f'{sample_task.id}'),
        metadata=[
            (VERSION_HEADER.lower(), PROTOCOL_VERSION_CURRENT),
            (HTTP_EXTENSION_HEADER.lower(), 'https://example.com/test-ext/v3'),
        ],
        timeout=None,
    )
    assert response.status.state == TaskState.TASK_STATE_CANCELED


@pytest.mark.asyncio
async def test_create_task_push_notification_config_with_valid_task(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_task_push_notification_config: TaskPushNotificationConfig,
) -> None:
    """Test setting a task push notification config with a valid task id."""
    mock_grpc_stub.CreateTaskPushNotificationConfig.return_value = (
        sample_task_push_notification_config
    )

    # Create the request object expected by the transport
    request = CreateTaskPushNotificationConfigRequest(
        task_id='task-1',
        config=sample_task_push_notification_config.push_notification_config,
    )
    response = await grpc_transport.create_task_push_notification_config(
        request
    )

    mock_grpc_stub.CreateTaskPushNotificationConfig.assert_awaited_once_with(
        request,
        metadata=[
            (VERSION_HEADER.lower(), PROTOCOL_VERSION_CURRENT),
        ],
        timeout=None,
    )
    assert response.task_id == sample_task_push_notification_config.task_id


@pytest.mark.asyncio
async def test_create_task_push_notification_config_with_invalid_task(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_push_notification_config: PushNotificationConfig,
) -> None:
    """Test setting a task push notification config with an invalid task name format."""
    # Return a config with an invalid name format
    mock_grpc_stub.CreateTaskPushNotificationConfig.return_value = (
        a2a_pb2.TaskPushNotificationConfig(
            task_id='invalid-path-to-task-1',
            push_notification_config=sample_push_notification_config,
        )
    )

    request = CreateTaskPushNotificationConfigRequest(
        task_id='task-1',
        config=sample_push_notification_config,
    )

    # Note: The transport doesn't validate the response name format
    # It just returns the response from the stub
    response = await grpc_transport.create_task_push_notification_config(
        request
    )
    assert response.task_id == 'invalid-path-to-task-1'


@pytest.mark.asyncio
async def test_get_task_push_notification_config_with_valid_task(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_task_push_notification_config: TaskPushNotificationConfig,
) -> None:
    """Test retrieving a task push notification config with a valid task id."""
    mock_grpc_stub.GetTaskPushNotificationConfig.return_value = (
        sample_task_push_notification_config
    )
    config_id = sample_task_push_notification_config.push_notification_config.id

    response = await grpc_transport.get_task_push_notification_config(
        GetTaskPushNotificationConfigRequest(
            task_id='task-1',
            id=config_id,
        )
    )

    mock_grpc_stub.GetTaskPushNotificationConfig.assert_awaited_once_with(
        a2a_pb2.GetTaskPushNotificationConfigRequest(
            task_id='task-1',
            id=config_id,
        ),
        metadata=[
            (VERSION_HEADER.lower(), PROTOCOL_VERSION_CURRENT),
        ],
        timeout=None,
    )
    assert response.task_id == sample_task_push_notification_config.task_id


@pytest.mark.asyncio
async def test_get_task_push_notification_config_with_invalid_task(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_push_notification_config: PushNotificationConfig,
) -> None:
    """Test retrieving a task push notification config with an invalid task name."""
    mock_grpc_stub.GetTaskPushNotificationConfig.return_value = (
        a2a_pb2.TaskPushNotificationConfig(
            task_id='invalid-path-to-task-1',
            push_notification_config=sample_push_notification_config,
        )
    )

    response = await grpc_transport.get_task_push_notification_config(
        GetTaskPushNotificationConfigRequest(
            task_id='task-1',
            id='config-1',
        )
    )
    # The transport doesn't validate the response name format
    assert response.task_id == 'invalid-path-to-task-1'


@pytest.mark.asyncio
async def test_list_task_push_notification_configs(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_task_push_notification_config: TaskPushNotificationConfig,
) -> None:
    """Test retrieving task push notification configs."""
    mock_grpc_stub.ListTaskPushNotificationConfigs.return_value = (
        a2a_pb2.ListTaskPushNotificationConfigsResponse(
            configs=[sample_task_push_notification_config]
        )
    )

    response = await grpc_transport.list_task_push_notification_configs(
        ListTaskPushNotificationConfigsRequest(task_id='task-1')
    )

    mock_grpc_stub.ListTaskPushNotificationConfigs.assert_awaited_once_with(
        a2a_pb2.ListTaskPushNotificationConfigsRequest(task_id='task-1'),
        metadata=[
            (VERSION_HEADER.lower(), PROTOCOL_VERSION_CURRENT),
        ],
        timeout=None,
    )
    assert len(response.configs) == 1
    assert response.configs[0].task_id == 'task-1'


@pytest.mark.asyncio
async def test_delete_task_push_notification_config(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_task_push_notification_config: TaskPushNotificationConfig,
) -> None:
    """Test deleting task push notification config."""
    mock_grpc_stub.DeleteTaskPushNotificationConfig.return_value = None

    await grpc_transport.delete_task_push_notification_config(
        DeleteTaskPushNotificationConfigRequest(
            task_id='task-1',
            id='config-1',
        )
    )

    mock_grpc_stub.DeleteTaskPushNotificationConfig.assert_awaited_once_with(
        a2a_pb2.DeleteTaskPushNotificationConfigRequest(
            task_id='task-1',
            id='config-1',
        ),
        metadata=[
            (VERSION_HEADER.lower(), PROTOCOL_VERSION_CURRENT),
        ],
        timeout=None,
    )


@pytest.mark.parametrize(
    'input_extensions, expected_metadata',
    [
        (
            None,
            [],
        ),
        (
            ['ext2'],
            [
                (HTTP_EXTENSION_HEADER.lower(), 'ext2'),
            ],
        ),
        (
            ['ext2', 'ext3'],
            [
                (HTTP_EXTENSION_HEADER.lower(), 'ext2,ext3'),
            ],
        ),
    ],
)
def test_get_grpc_metadata(
    grpc_transport: GrpcTransport,
    input_extensions: list[str] | None,
    expected_metadata: list[tuple[str, str]] | None,
) -> None:
    """Tests _get_grpc_metadata for correct metadata generation."""
    context = None
    if input_extensions:
        context = ClientCallContext(
            service_parameters={
                HTTP_EXTENSION_HEADER: ','.join(input_extensions)
            }
        )

    metadata = grpc_transport._get_grpc_metadata(context)
    # Filter out a2a-version as it's not being tested here directly and simplifies the assertion
    filtered_metadata = [m for m in metadata if m[0] != VERSION_HEADER.lower()]
    assert filtered_metadata == expected_metadata
