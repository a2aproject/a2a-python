from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a.client.base_client import BaseClient
from a2a.client.client import ClientConfig
from a2a.client.transports.base import ClientTransport
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    Message,
    MessageSendConfiguration,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)


@pytest.fixture
def mock_transport():
    return AsyncMock(spec=ClientTransport)


@pytest.fixture
def sample_agent_card():
    return AgentCard(
        name='Test Agent',
        description='An agent for testing',
        url='http://test.com',
        version='1.0',
        capabilities=AgentCapabilities(streaming=True),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        skills=[],
    )


@pytest.fixture
def sample_message():
    return Message(
        role=Role.user,
        message_id='msg-1',
        parts=[Part(root=TextPart(text='Hello'))],
    )


@pytest.fixture
def base_client(sample_agent_card, mock_transport):
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
):
    async def create_stream(*args, **kwargs):
        yield Task(
            id='task-123',
            context_id='ctx-456',
            status=TaskStatus(state=TaskState.completed),
        )

    mock_transport.send_message_streaming.return_value = create_stream()

    events = [event async for event in base_client.send_message(sample_message)]

    mock_transport.send_message_streaming.assert_called_once()
    assert not mock_transport.send_message.called
    assert len(events) == 1
    assert events[0][0].id == 'task-123'


@pytest.mark.asyncio
async def test_send_message_non_streaming(
    base_client: BaseClient, mock_transport: MagicMock, sample_message: Message
):
    base_client._config.streaming = False
    mock_transport.send_message.return_value = Task(
        id='task-456',
        context_id='ctx-789',
        status=TaskStatus(state=TaskState.completed),
    )

    events = [event async for event in base_client.send_message(sample_message)]

    mock_transport.send_message.assert_called_once()
    assert not mock_transport.send_message_streaming.called
    assert len(events) == 1
    assert events[0][0].id == 'task-456'


@pytest.mark.asyncio
async def test_send_message_non_streaming_agent_capability_false(
    base_client: BaseClient, mock_transport: MagicMock, sample_message: Message
):
    base_client._card.capabilities.streaming = False
    mock_transport.send_message.return_value = Task(
        id='task-789',
        context_id='ctx-101',
        status=TaskStatus(state=TaskState.completed),
    )

    events = [event async for event in base_client.send_message(sample_message)]

    mock_transport.send_message.assert_called_once()
    assert not mock_transport.send_message_streaming.called
    assert len(events) == 1
    assert events[0][0].id == 'task-789'


@pytest.mark.asyncio
async def test_send_message_uses_callsite_configuration_partial_override_non_streaming(
    base_client: BaseClient, mock_transport: MagicMock, sample_message: Message
):
    base_client._config.streaming = False
    mock_transport.send_message.return_value = Task(
        id='task-cfg-ns-1',
        context_id='ctx-cfg-ns-1',
        status=TaskStatus(state=TaskState.completed),
    )

    cfg = MessageSendConfiguration(history_length=2)
    events = [
        ev
        async for ev in base_client.send_message(
            sample_message, configuration=cfg
        )
    ]

    mock_transport.send_message.assert_called_once()
    assert not mock_transport.send_message_streaming.called
    assert len(events) == 1
    task, _ = events[0]
    assert task.id == 'task-cfg-ns-1'

    params = mock_transport.send_message.await_args.args[0]
    assert params.configuration.history_length == 2
    assert params.configuration.blocking == (not base_client._config.polling)
    assert (
        params.configuration.accepted_output_modes
        == base_client._config.accepted_output_modes
    )


@pytest.mark.asyncio
async def test_send_message_ignores_none_fields_in_callsite_configuration_non_streaming(
    base_client: BaseClient, mock_transport: MagicMock, sample_message: Message
):
    base_client._config.streaming = False
    mock_transport.send_message.return_value = Task(
        id='task-cfg-ns-2',
        context_id='ctx-cfg-ns-2',
        status=TaskStatus(state=TaskState.completed),
    )

    cfg = MessageSendConfiguration(history_length=None, blocking=None)
    events = [
        ev
        async for ev in base_client.send_message(
            sample_message, configuration=cfg
        )
    ]

    mock_transport.send_message.assert_called_once()
    assert len(events) == 1
    task, _ = events[0]
    assert task.id == 'task-cfg-ns-2'

    params = mock_transport.send_message.await_args.args[0]
    assert params.configuration.history_length is None
    assert params.configuration.blocking == (not base_client._config.polling)
    assert (
        params.configuration.accepted_output_modes
        == base_client._config.accepted_output_modes
    )


@pytest.mark.asyncio
async def test_send_message_uses_callsite_configuration_partial_override_streaming(
    base_client: BaseClient, mock_transport: MagicMock, sample_message: Message
):
    base_client._config.streaming = True
    base_client._card.capabilities.streaming = True

    async def create_stream(*args, **kwargs):
        yield Task(
            id='task-cfg-s-1',
            context_id='ctx-cfg-s-1',
            status=TaskStatus(state=TaskState.completed),
        )

    mock_transport.send_message_streaming.return_value = create_stream()

    cfg = MessageSendConfiguration(history_length=0)
    events = [
        ev
        async for ev in base_client.send_message(
            sample_message, configuration=cfg
        )
    ]

    mock_transport.send_message_streaming.assert_called_once()
    assert not mock_transport.send_message.called
    assert len(events) == 1
    task, _ = events[0]
    assert task.id == 'task-cfg-s-1'

    params = mock_transport.send_message_streaming.call_args.args[0]
    assert params.configuration.history_length == 0
    assert params.configuration.blocking == (not base_client._config.polling)
    assert (
        params.configuration.accepted_output_modes
        == base_client._config.accepted_output_modes
    )
