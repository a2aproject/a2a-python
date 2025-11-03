from unittest.mock import AsyncMock, MagicMock, patch

import grpc
import pytest

from a2a.client.middleware import ClientCallContext
from a2a.client.transports.grpc import GrpcTransport
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.grpc import a2a_pb2, a2a_pb2_grpc
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    Artifact,
    GetTaskPushNotificationConfigParams,
    Message,
    MessageSendParams,
    Part,
    PushNotificationAuthenticationInfo,
    PushNotificationConfig,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)
from a2a.utils import get_text_parts, proto_utils
from a2a.utils.errors import ServerError


@pytest.fixture
def mock_grpc_stub() -> AsyncMock:
    """Provides a mock gRPC stub with methods mocked."""
    stub = AsyncMock(spec=a2a_pb2_grpc.A2AServiceStub)
    stub.SendMessage = AsyncMock()
    stub.SendStreamingMessage = MagicMock()
    stub.GetTask = AsyncMock()
    stub.CancelTask = AsyncMock()
    stub.CreateTaskPushNotificationConfig = AsyncMock()
    stub.GetTaskPushNotificationConfig = AsyncMock()
    stub.TaskSubscription = MagicMock()
    stub.GetAgentCard = AsyncMock()
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
    channel = AsyncMock()
    transport = GrpcTransport(channel=channel, agent_card=sample_agent_card)
    transport.stub = mock_grpc_stub
    return transport


@pytest.fixture
def sample_message_send_params() -> MessageSendParams:
    """Provides a sample MessageSendParams object."""
    return MessageSendParams(
        message=Message(
            role=Role.user,
            message_id='msg-1',
            parts=[Part(root=TextPart(text='Hello'))],
        )
    )


@pytest.fixture
def sample_task() -> Task:
    """Provides a sample Task object."""
    return Task(
        id='task-1',
        context_id='ctx-1',
        status=TaskStatus(state=TaskState.completed),
    )


@pytest.fixture
def sample_message() -> Message:
    """Provides a sample Message object."""
    return Message(
        role=Role.agent,
        message_id='msg-response',
        parts=[Part(root=TextPart(text='Hi there'))],
    )


@pytest.fixture
def sample_artifact() -> Artifact:
    """Provides a sample Artifact object."""
    return Artifact(
        artifact_id='artifact-1',
        name='example.txt',
        description='An example artifact',
        parts=[Part(root=TextPart(text='Hi there'))],
        metadata={},
        extensions=[],
    )


@pytest.fixture
def sample_task_status_update_event() -> TaskStatusUpdateEvent:
    """Provides a sample TaskStatusUpdateEvent."""
    return TaskStatusUpdateEvent(
        task_id='task-1',
        context_id='ctx-1',
        status=TaskStatus(state=TaskState.working),
        final=False,
        metadata={},
    )


@pytest.fixture
def sample_task_artifact_update_event(
    sample_artifact,
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
def sample_authentication_info() -> PushNotificationAuthenticationInfo:
    """Provides a sample AuthenticationInfo object."""
    return PushNotificationAuthenticationInfo(
        schemes=['apikey', 'oauth2'], credentials='secret-token'
    )


@pytest.fixture
def sample_push_notification_config(
    sample_authentication_info: PushNotificationAuthenticationInfo,
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
    sample_message_send_params: MessageSendParams,
    sample_task: Task,
):
    """Test send_message that returns a Task."""
    mock_grpc_stub.SendMessage.return_value = a2a_pb2.SendMessageResponse(
        task=proto_utils.ToProto.task(sample_task)
    )

    response = await grpc_transport.send_message(sample_message_send_params)

    mock_grpc_stub.SendMessage.assert_awaited_once()
    assert isinstance(response, Task)
    assert response.id == sample_task.id


@pytest.mark.asyncio
async def test_send_message_message_response(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_message_send_params: MessageSendParams,
    sample_message: Message,
):
    """Test send_message that returns a Message."""
    mock_grpc_stub.SendMessage.return_value = a2a_pb2.SendMessageResponse(
        msg=proto_utils.ToProto.message(sample_message)
    )

    response = await grpc_transport.send_message(sample_message_send_params)

    mock_grpc_stub.SendMessage.assert_awaited_once()
    assert isinstance(response, Message)
    assert response.message_id == sample_message.message_id
    assert get_text_parts(response.parts) == get_text_parts(
        sample_message.parts
    )


@pytest.mark.asyncio
async def test_send_message_streaming(  # noqa: PLR0913
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_message_send_params: MessageSendParams,
    sample_message: Message,
    sample_task: Task,
    sample_task_status_update_event: TaskStatusUpdateEvent,
    sample_task_artifact_update_event: TaskArtifactUpdateEvent,
):
    """Test send_message_streaming that yields responses."""
    stream = MagicMock()
    stream.read = AsyncMock(
        side_effect=[
            a2a_pb2.StreamResponse(
                msg=proto_utils.ToProto.message(sample_message)
            ),
            a2a_pb2.StreamResponse(task=proto_utils.ToProto.task(sample_task)),
            a2a_pb2.StreamResponse(
                status_update=proto_utils.ToProto.task_status_update_event(
                    sample_task_status_update_event
                )
            ),
            a2a_pb2.StreamResponse(
                artifact_update=proto_utils.ToProto.task_artifact_update_event(
                    sample_task_artifact_update_event
                )
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
    assert isinstance(responses[0], Message)
    assert responses[0].message_id == sample_message.message_id
    assert isinstance(responses[1], Task)
    assert responses[1].id == sample_task.id
    assert isinstance(responses[2], TaskStatusUpdateEvent)
    assert responses[2].task_id == sample_task_status_update_event.task_id
    assert isinstance(responses[3], TaskArtifactUpdateEvent)
    assert responses[3].task_id == sample_task_artifact_update_event.task_id


@pytest.mark.asyncio
async def test_get_task(
    grpc_transport: GrpcTransport, mock_grpc_stub: AsyncMock, sample_task: Task
):
    """Test retrieving a task."""
    mock_grpc_stub.GetTask.return_value = proto_utils.ToProto.task(sample_task)
    params = TaskQueryParams(id=sample_task.id)

    response = await grpc_transport.get_task(params)

    mock_grpc_stub.GetTask.assert_awaited_once_with(
        a2a_pb2.GetTaskRequest(
            name=f'tasks/{sample_task.id}', history_length=None
        ),
        metadata=[],
    )
    assert response.id == sample_task.id


@pytest.mark.asyncio
async def test_get_task_with_history(
    grpc_transport: GrpcTransport, mock_grpc_stub: AsyncMock, sample_task: Task
):
    """Test retrieving a task with history."""
    mock_grpc_stub.GetTask.return_value = proto_utils.ToProto.task(sample_task)
    history_len = 10
    params = TaskQueryParams(id=sample_task.id, history_length=history_len)

    await grpc_transport.get_task(params)

    mock_grpc_stub.GetTask.assert_awaited_once_with(
        a2a_pb2.GetTaskRequest(
            name=f'tasks/{sample_task.id}', history_length=history_len
        ),
        metadata=[],
    )


@pytest.mark.asyncio
async def test_cancel_task(
    grpc_transport: GrpcTransport, mock_grpc_stub: AsyncMock, sample_task: Task
):
    """Test cancelling a task."""
    cancelled_task = sample_task.model_copy()
    cancelled_task.status.state = TaskState.canceled
    mock_grpc_stub.CancelTask.return_value = proto_utils.ToProto.task(
        cancelled_task
    )
    params = TaskIdParams(id=sample_task.id)

    response = await grpc_transport.cancel_task(params)

    mock_grpc_stub.CancelTask.assert_awaited_once_with(
        a2a_pb2.CancelTaskRequest(name=f'tasks/{sample_task.id}'),
        metadata=[],
    )
    assert response.status.state == TaskState.canceled


@pytest.mark.asyncio
async def test_set_task_callback_with_valid_task(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_task_push_notification_config: TaskPushNotificationConfig,
):
    """Test setting a task push notification config with a valid task id."""
    mock_grpc_stub.CreateTaskPushNotificationConfig.return_value = (
        proto_utils.ToProto.task_push_notification_config(
            sample_task_push_notification_config
        )
    )

    response = await grpc_transport.set_task_callback(
        sample_task_push_notification_config
    )

    mock_grpc_stub.CreateTaskPushNotificationConfig.assert_awaited_once_with(
        a2a_pb2.CreateTaskPushNotificationConfigRequest(
            parent=f'tasks/{sample_task_push_notification_config.task_id}',
            config_id=sample_task_push_notification_config.push_notification_config.id,
            config=proto_utils.ToProto.task_push_notification_config(
                sample_task_push_notification_config
            ),
        ),
        metadata=[],
    )
    assert response.task_id == sample_task_push_notification_config.task_id


@pytest.mark.asyncio
async def test_set_task_callback_with_invalid_task(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_task_push_notification_config: TaskPushNotificationConfig,
):
    """Test setting a task push notification config with an invalid task id."""
    mock_grpc_stub.CreateTaskPushNotificationConfig.return_value = a2a_pb2.TaskPushNotificationConfig(
        name=(
            f'invalid-path-to-tasks/{sample_task_push_notification_config.task_id}/'
            f'pushNotificationConfigs/{sample_task_push_notification_config.push_notification_config.id}'
        ),
        push_notification_config=proto_utils.ToProto.push_notification_config(
            sample_task_push_notification_config.push_notification_config
        ),
    )

    with pytest.raises(ServerError) as exc_info:
        await grpc_transport.set_task_callback(
            sample_task_push_notification_config
        )
    assert (
        'Bad TaskPushNotificationConfig resource name'
        in exc_info.value.error.message
    )


@pytest.mark.asyncio
async def test_get_task_callback_with_valid_task(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_task_push_notification_config: TaskPushNotificationConfig,
):
    """Test retrieving a task push notification config with a valid task id."""
    mock_grpc_stub.GetTaskPushNotificationConfig.return_value = (
        proto_utils.ToProto.task_push_notification_config(
            sample_task_push_notification_config
        )
    )
    params = GetTaskPushNotificationConfigParams(
        id=sample_task_push_notification_config.task_id,
        push_notification_config_id=sample_task_push_notification_config.push_notification_config.id,
    )

    response = await grpc_transport.get_task_callback(params)

    mock_grpc_stub.GetTaskPushNotificationConfig.assert_awaited_once_with(
        a2a_pb2.GetTaskPushNotificationConfigRequest(
            name=(
                f'tasks/{params.id}/'
                f'pushNotificationConfigs/{params.push_notification_config_id}'
            ),
        ),
        metadata=[],
    )
    assert response.task_id == sample_task_push_notification_config.task_id


@pytest.mark.asyncio
async def test_get_task_callback_with_invalid_task(
    grpc_transport: GrpcTransport,
    mock_grpc_stub: AsyncMock,
    sample_task_push_notification_config: TaskPushNotificationConfig,
):
    """Test retrieving a task push notification config with an invalid task id."""
    mock_grpc_stub.GetTaskPushNotificationConfig.return_value = a2a_pb2.TaskPushNotificationConfig(
        name=(
            f'invalid-path-to-tasks/{sample_task_push_notification_config.task_id}/'
            f'pushNotificationConfigs/{sample_task_push_notification_config.push_notification_config.id}'
        ),
        push_notification_config=proto_utils.ToProto.push_notification_config(
            sample_task_push_notification_config.push_notification_config
        ),
    )
    params = GetTaskPushNotificationConfigParams(
        id=sample_task_push_notification_config.task_id,
        push_notification_config_id=sample_task_push_notification_config.push_notification_config.id,
    )

    with pytest.raises(ServerError) as exc_info:
        await grpc_transport.get_task_callback(params)
    assert (
        'Bad TaskPushNotificationConfig resource name'
        in exc_info.value.error.message
    )


class TestGrpcTransportExtensions:
    def test_get_metadata_no_initial(self, sample_agent_card: AgentCard):
        extensions = ['test_extension_1', 'test_extension_2']
        transport = GrpcTransport(
            channel=AsyncMock(),
            agent_card=sample_agent_card,
            extensions=extensions,
        )
        metadata = transport._get_metadata(None)
        metadata_dict = dict(metadata)
        assert HTTP_EXTENSION_HEADER in metadata_dict
        actual_extensions = set(metadata_dict[HTTP_EXTENSION_HEADER].split(','))
        assert actual_extensions == set(extensions)

    def test_get_metadata_with_existing(self, sample_agent_card: AgentCard):
        extensions = ['test_extension']
        transport = GrpcTransport(
            channel=AsyncMock(),
            agent_card=sample_agent_card,
            extensions=extensions,
        )
        context = ClientCallContext(
            state={'grpc_metadata': [('x-other', 'Test')]}
        )
        metadata = transport._get_metadata(context)
        metadata_dict = dict(metadata)
        assert metadata_dict[HTTP_EXTENSION_HEADER] == 'test_extension'
        assert metadata_dict['x-other'] == 'Test'

    @pytest.mark.parametrize(
        'existing_header, expected_extensions',
        [
            (
                'test_extension_2, test_extension_3',
                {'test_extension_1', 'test_extension_2', 'test_extension_3'},
            ),
            (
                'test_extension_3',
                {'test_extension_1', 'test_extension_2', 'test_extension_3'},
            ),
        ],
    )
    def test_get_metadata_merge_with_existing(
        self,
        sample_agent_card: AgentCard,
        existing_header: str,
        expected_extensions: set,
    ):
        extensions = ['test_extension_1', 'test_extension_2']
        transport = GrpcTransport(
            channel=AsyncMock(),
            agent_card=sample_agent_card,
            extensions=extensions,
        )
        context = ClientCallContext(
            state={'grpc_metadata': [(HTTP_EXTENSION_HEADER, existing_header)]}
        )
        metadata = transport._get_metadata(context)
        metadata_dict = dict(metadata)
        assert HTTP_EXTENSION_HEADER in metadata_dict
        actual_extensions = set(metadata_dict[HTTP_EXTENSION_HEADER].split(','))
        assert actual_extensions == expected_extensions

    def test_get_metadata_no_extensions(self, sample_agent_card: AgentCard):
        transport = GrpcTransport(
            channel=AsyncMock(),
            agent_card=sample_agent_card,
            extensions=None,
        )
        context = ClientCallContext(
            state={'grpc_metadata': [('x-other', 'Test')]}
        )
        metadata = transport._get_metadata(context)
        metadata_dict = dict(metadata)
        assert HTTP_EXTENSION_HEADER not in metadata_dict
        assert metadata_dict['x-other'] == 'Test'

    def test_get_metadata_empty_extensions(self, sample_agent_card: AgentCard):
        transport = GrpcTransport(
            channel=AsyncMock(),
            agent_card=sample_agent_card,
            extensions=[],
        )
        context = ClientCallContext(
            state={'grpc_metadata': [('x-other', 'Test')]}
        )
        metadata = transport._get_metadata(context)
        metadata_dict = dict(metadata)
        assert HTTP_EXTENSION_HEADER not in metadata_dict
        assert metadata_dict['x-other'] == 'Test'

    @pytest.mark.asyncio
    async def test_send_message_with_extensions(
        self,
        mock_grpc_stub: AsyncMock,
        sample_agent_card: AgentCard,
        sample_message_send_params: MessageSendParams,
    ):
        extensions = ['test_extension_1', 'test_extension_2']
        transport = GrpcTransport(
            channel=AsyncMock(),
            agent_card=sample_agent_card,
            extensions=extensions,
        )
        transport.stub = mock_grpc_stub
        mock_grpc_stub.SendMessage.return_value = a2a_pb2.SendMessageResponse(
            msg=proto_utils.ToProto.message(sample_message_send_params.message)
        )

        await transport.send_message(sample_message_send_params)

        mock_grpc_stub.SendMessage.assert_awaited_once()
        _, kwargs = mock_grpc_stub.SendMessage.call_args
        metadata_dict = dict(kwargs['metadata'])
        assert HTTP_EXTENSION_HEADER in metadata_dict
        assert set(metadata_dict[HTTP_EXTENSION_HEADER].split(',')) == set(
            extensions
        )

    @pytest.mark.asyncio
    async def test_send_message_streaming_with_extensions(
        self,
        mock_grpc_stub: AsyncMock,
        sample_agent_card: AgentCard,
        sample_message_send_params: MessageSendParams,
    ):
        extensions = ['test_extension']
        transport = GrpcTransport(
            channel=AsyncMock(),
            agent_card=sample_agent_card,
            extensions=extensions,
        )
        transport.stub = mock_grpc_stub
        stream = MagicMock()
        stream.read = AsyncMock(side_effect=[grpc.aio.EOF])
        mock_grpc_stub.SendStreamingMessage.return_value = stream

        async for _ in transport.send_message_streaming(
            sample_message_send_params
        ):
            pass

        mock_grpc_stub.SendStreamingMessage.assert_called_once()
        _, kwargs = mock_grpc_stub.SendStreamingMessage.call_args
        metadata_dict = dict(kwargs['metadata'])
        assert HTTP_EXTENSION_HEADER in metadata_dict
        assert metadata_dict[HTTP_EXTENSION_HEADER] == 'test_extension'

    @pytest.mark.asyncio
    async def test_resubscribe_with_extensions(
        self, mock_grpc_stub: AsyncMock, sample_agent_card: AgentCard
    ):
        extensions = ['test_extension']
        transport = GrpcTransport(
            channel=AsyncMock(),
            agent_card=sample_agent_card,
            extensions=extensions,
        )
        transport.stub = mock_grpc_stub
        stream = MagicMock()
        stream.read = AsyncMock(side_effect=[grpc.aio.EOF])
        mock_grpc_stub.TaskSubscription.return_value = stream

        async for _ in transport.resubscribe(TaskIdParams(id='task-1')):
            pass

        mock_grpc_stub.TaskSubscription.assert_called_once()
        _, kwargs = mock_grpc_stub.TaskSubscription.call_args
        metadata_dict = dict(kwargs['metadata'])
        assert HTTP_EXTENSION_HEADER in metadata_dict
        assert metadata_dict[HTTP_EXTENSION_HEADER] == 'test_extension'

    @pytest.mark.asyncio
    async def test_get_task_with_extensions(
        self, mock_grpc_stub: AsyncMock, sample_agent_card: AgentCard
    ):
        extensions = ['test_extension']
        transport = GrpcTransport(
            channel=AsyncMock(),
            agent_card=sample_agent_card,
            extensions=extensions,
        )
        transport.stub = mock_grpc_stub
        mock_grpc_stub.GetTask.return_value = a2a_pb2.Task()

        await transport.get_task(TaskQueryParams(id='task-1'))

        mock_grpc_stub.GetTask.assert_awaited_once()
        _, kwargs = mock_grpc_stub.GetTask.call_args
        metadata_dict = dict(kwargs['metadata'])
        assert HTTP_EXTENSION_HEADER in metadata_dict
        assert metadata_dict[HTTP_EXTENSION_HEADER] == 'test_extension'

    @pytest.mark.asyncio
    async def test_cancel_task_with_extensions(
        self, mock_grpc_stub: AsyncMock, sample_agent_card: AgentCard
    ):
        extensions = ['test_extension']
        transport = GrpcTransport(
            channel=AsyncMock(),
            agent_card=sample_agent_card,
            extensions=extensions,
        )
        transport.stub = mock_grpc_stub
        mock_grpc_stub.CancelTask.return_value = a2a_pb2.Task()

        await transport.cancel_task(TaskIdParams(id='task-1'))

        mock_grpc_stub.CancelTask.assert_awaited_once()
        _, kwargs = mock_grpc_stub.CancelTask.call_args
        metadata_dict = dict(kwargs['metadata'])
        assert HTTP_EXTENSION_HEADER in metadata_dict
        assert metadata_dict[HTTP_EXTENSION_HEADER] == 'test_extension'

    @pytest.mark.asyncio
    async def test_set_task_callback_with_extensions(
        self,
        mock_grpc_stub: AsyncMock,
        sample_agent_card: AgentCard,
        sample_task_push_notification_config: TaskPushNotificationConfig,
    ):
        extensions = ['test_extension']
        transport = GrpcTransport(
            channel=AsyncMock(),
            agent_card=sample_agent_card,
            extensions=extensions,
        )
        transport.stub = mock_grpc_stub
        mock_grpc_stub.CreateTaskPushNotificationConfig.return_value = (
            proto_utils.ToProto.task_push_notification_config(
                sample_task_push_notification_config
            )
        )

        await transport.set_task_callback(sample_task_push_notification_config)

        mock_grpc_stub.CreateTaskPushNotificationConfig.assert_awaited_once()
        _, kwargs = mock_grpc_stub.CreateTaskPushNotificationConfig.call_args
        metadata_dict = dict(kwargs['metadata'])
        assert HTTP_EXTENSION_HEADER in metadata_dict
        assert metadata_dict[HTTP_EXTENSION_HEADER] == 'test_extension'

    @pytest.mark.asyncio
    async def test_get_task_callback_with_extensions(
        self,
        mock_grpc_stub: AsyncMock,
        sample_agent_card: AgentCard,
        sample_task_push_notification_config: TaskPushNotificationConfig,
    ):
        extensions = ['test_extension']
        transport = GrpcTransport(
            channel=AsyncMock(),
            agent_card=sample_agent_card,
            extensions=extensions,
        )
        transport.stub = mock_grpc_stub
        mock_grpc_stub.GetTaskPushNotificationConfig.return_value = (
            proto_utils.ToProto.task_push_notification_config(
                sample_task_push_notification_config
            )
        )

        await transport.get_task_callback(
            GetTaskPushNotificationConfigParams(
                id=sample_task_push_notification_config.task_id,
                push_notification_config_id=sample_task_push_notification_config.push_notification_config.id,
            )
        )

        mock_grpc_stub.GetTaskPushNotificationConfig.assert_awaited_once()
        _, kwargs = mock_grpc_stub.GetTaskPushNotificationConfig.call_args
        metadata_dict = dict(kwargs['metadata'])
        assert HTTP_EXTENSION_HEADER in metadata_dict
        assert metadata_dict[HTTP_EXTENSION_HEADER] == 'test_extension'

    @pytest.mark.asyncio
    async def test_get_card_with_extensions(
        self, mock_grpc_stub: AsyncMock, sample_agent_card: AgentCard
    ):
        extensions = ['test_extension']
        transport = GrpcTransport(
            channel=AsyncMock(),
            agent_card=sample_agent_card,
            extensions=extensions,
        )
        transport.stub = mock_grpc_stub
        mock_grpc_stub.GetAgentCard.return_value = (
            proto_utils.ToProto.agent_card(sample_agent_card)
        )

        await transport.get_card()

        mock_grpc_stub.GetAgentCard.assert_awaited_once()
        _, kwargs = mock_grpc_stub.GetAgentCard.call_args
        metadata_dict = dict(kwargs['metadata'])
        assert HTTP_EXTENSION_HEADER in metadata_dict
        assert metadata_dict[HTTP_EXTENSION_HEADER] == 'test_extension'
