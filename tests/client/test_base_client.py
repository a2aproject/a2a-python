from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a.client.base_client import BaseClient
from a2a.client.client import ClientConfig
from a2a.client.transports.base import ClientTransport
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentInterface,
    AgentCard,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageResponse,
    StreamResponse,
    Task,
    TaskState,
    TaskStatus,
)


@pytest.fixture
def mock_transport() -> AsyncMock:
    return AsyncMock(spec=ClientTransport)


@pytest.fixture
def sample_agent_card() -> AgentCard:
    return AgentCard(
        name='Test Agent',
        description='An agent for testing',
        supported_interfaces=[
            AgentInterface(url='http://test.com', protocol_binding='HTTP+JSON')
        ],
        version='1.0',
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        skills=[],
    )


@pytest.fixture
def sample_message() -> Message:
    return Message(
        role=Role.ROLE_USER,
        message_id='msg-1',
        parts=[Part(text='Hello')],
    )


@pytest.fixture
def base_client(
    sample_agent_card: AgentCard, mock_transport: AsyncMock
) -> BaseClient:
    config = ClientConfig(streaming=True)
    return BaseClient(
        card=sample_agent_card,
        config=config,
        transport=mock_transport,
        consumers=[],
        middleware=[],
    )


@pytest.mark.asyncio
async def test_send_message_streaming(
    base_client: BaseClient, mock_transport: MagicMock, sample_message: Message
) -> None:
    async def create_stream(*args, **kwargs):
        task = Task(
            id='task-123',
            context_id='ctx-456',
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )
        stream_response = StreamResponse()
        stream_response.task.CopyFrom(task)
        yield stream_response

    mock_transport.send_message_streaming.return_value = create_stream()

    meta = {'test': 1}
    stream = base_client.send_message(sample_message, request_metadata=meta)
    events = [event async for event in stream]

    mock_transport.send_message_streaming.assert_called_once()
    assert (
        mock_transport.send_message_streaming.call_args[0][0].metadata == meta
    )
    assert not mock_transport.send_message.called
    assert len(events) == 1
    # events[0] is (StreamResponse, Task) tuple
    stream_response, tracked_task = events[0]
    assert stream_response.task.id == 'task-123'
    assert tracked_task.id == 'task-123'


@pytest.mark.asyncio
async def test_send_message_non_streaming(
    base_client: BaseClient, mock_transport: MagicMock, sample_message: Message
) -> None:
    base_client._config.streaming = False
    task = Task(
        id='task-456',
        context_id='ctx-789',
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
    )
    response = SendMessageResponse()
    response.task.CopyFrom(task)
    mock_transport.send_message.return_value = response

    meta = {'test': 1}
    stream = base_client.send_message(sample_message, request_metadata=meta)
    events = [event async for event in stream]

    mock_transport.send_message.assert_called_once()
    assert mock_transport.send_message.call_args[0][0].metadata == meta
    assert not mock_transport.send_message_streaming.called
    assert len(events) == 1
    stream_response, tracked_task = events[0]
    assert stream_response.task.id == 'task-456'
    assert tracked_task.id == 'task-456'


@pytest.mark.asyncio
async def test_send_message_non_streaming_agent_capability_false(
    base_client: BaseClient, mock_transport: MagicMock, sample_message: Message
) -> None:
    base_client._card.capabilities.streaming = False
    task = Task(
        id='task-789',
        context_id='ctx-101',
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
    )
    response = SendMessageResponse()
    response.task.CopyFrom(task)
    mock_transport.send_message.return_value = response

    events = [event async for event in base_client.send_message(sample_message)]

    mock_transport.send_message.assert_called_once()
    assert not mock_transport.send_message_streaming.called
    assert len(events) == 1
    stream_response, tracked_task = events[0]
    assert stream_response.task.id == 'task-789'
    assert tracked_task.id == 'task-789'


@pytest.mark.asyncio
async def test_send_message_callsite_config_overrides_non_streaming(
    base_client: BaseClient, mock_transport: MagicMock, sample_message: Message
):
    base_client._config.streaming = False
    task = Task(
        id='task-cfg-ns-1',
        context_id='ctx-cfg-ns-1',
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
    )
    response = SendMessageResponse()
    response.task.CopyFrom(task)
    mock_transport.send_message.return_value = response

    cfg = SendMessageConfiguration(
        history_length=2,
        blocking=False,
        accepted_output_modes=['application/json'],
    )
    events = [
        event
        async for event in base_client.send_message(
            sample_message, configuration=cfg
        )
    ]

    mock_transport.send_message.assert_called_once()
    assert not mock_transport.send_message_streaming.called
    assert len(events) == 1
    stream_response, _ = events[0]
    assert stream_response.task.id == 'task-cfg-ns-1'

    params = mock_transport.send_message.call_args[0][0]
    assert params.configuration.history_length == 2
    assert params.configuration.blocking is False
    assert params.configuration.accepted_output_modes == ['application/json']


@pytest.mark.asyncio
async def test_send_message_callsite_config_overrides_streaming(
    base_client: BaseClient, mock_transport: MagicMock, sample_message: Message
):
    base_client._config.streaming = True
    base_client._card.capabilities.streaming = True

    async def create_stream(*args, **kwargs):
        task = Task(
            id='task-cfg-s-1',
            context_id='ctx-cfg-s-1',
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )
        stream_response = StreamResponse()
        stream_response.task.CopyFrom(task)
        yield stream_response

    mock_transport.send_message_streaming.return_value = create_stream()

    cfg = SendMessageConfiguration(
        history_length=0,
        blocking=True,
        accepted_output_modes=['text/plain'],
    )
    events = [
        event
        async for event in base_client.send_message(
            sample_message, configuration=cfg
        )
    ]

    mock_transport.send_message_streaming.assert_called_once()
    assert not mock_transport.send_message.called
    assert len(events) == 1
    stream_response, _ = events[0]
    assert stream_response.task.id == 'task-cfg-s-1'

    params = mock_transport.send_message_streaming.call_args[0][0]
    assert params.configuration.history_length == 0
    assert params.configuration.blocking is True
    assert params.configuration.accepted_output_modes == ['text/plain']
