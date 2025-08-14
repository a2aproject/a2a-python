from unittest.mock import AsyncMock

import pytest

from a2a.client.transports.grpc import GrpcTransport
from a2a.grpc import a2a_pb2, a2a_pb2_grpc
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    Message,
    MessageSendParams,
    Part,
    Role,
    Task,
    TaskIdParams,
    TaskQueryParams,
    TaskState,
    TaskStatus,
    TextPart,
)
from a2a.utils import get_text_parts, proto_utils


# Fixtures
@pytest.fixture
def mock_grpc_stub() -> AsyncMock:
    """Provides a mock gRPC stub with methods mocked."""
    stub = AsyncMock(spec=a2a_pb2_grpc.A2AServiceStub)
    stub.SendMessage = AsyncMock()
    stub.SendStreamingMessage = AsyncMock()
    stub.GetTask = AsyncMock()
    stub.CancelTask = AsyncMock()
    stub.CreateTaskPushNotification = AsyncMock()
    stub.GetTaskPushNotification = AsyncMock()
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
        )
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
        )
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
        a2a_pb2.CancelTaskRequest(name=f'tasks/{sample_task.id}')
    )
    assert response.status.state == TaskState.canceled
