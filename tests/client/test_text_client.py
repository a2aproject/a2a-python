from unittest.mock import AsyncMock

import pytest

from a2a.client import (
    Client,
    ClientCallContext,
    ClientConfig,
    TextClient,
    create_text_client,
    minimal_agent_card,
)
from a2a.types import Part, StreamResponse, TaskState


@pytest.fixture
def mock_client() -> AsyncMock:
    return AsyncMock(spec=Client)


@pytest.fixture
def text_client(mock_client: AsyncMock) -> TextClient:
    return TextClient(mock_client)


def test_client_property(
    text_client: TextClient, mock_client: AsyncMock
) -> None:
    assert text_client.client is mock_client


@pytest.mark.asyncio
async def test_create_client_and_wrap() -> None:
    # Create a minimal card
    card = minimal_agent_card(url='http://test.com', transports=['JSONRPC'])

    config = ClientConfig(supported_protocol_bindings=['JSONRPC'])

    text_client = await create_text_client(card, client_config=config)

    assert isinstance(text_client, TextClient)
    assert isinstance(text_client.client, Client)

    # Clean up
    await text_client.close()


@pytest.mark.asyncio
async def test_send_text_message(
    text_client: TextClient, mock_client: AsyncMock
) -> None:
    async def create_stream(*args, **kwargs):
        # Event 0: task (ignored)
        resp0 = StreamResponse()
        resp0.task.id = 'task-1'
        yield resp0

        # Event 1: direct message
        resp1 = StreamResponse()
        resp1.message.parts.append(Part(text='Hello'))
        yield resp1

        # Event 2: status update without message
        resp2 = StreamResponse()
        resp2.status_update.status.state = 1
        yield resp2

        # Event 3: status update with message
        resp3 = StreamResponse()
        resp3.status_update.status.message.parts.append(Part(text='Processing'))
        yield resp3

        # Event 4: artifact update
        resp4 = StreamResponse()
        resp4.artifact_update.artifact.parts.append(Part(text='World!'))
        yield resp4

    mock_client.send_message.return_value = create_stream()

    response = await text_client.send_text_message('Hi')

    assert response == 'Hello Processing World!'
    mock_client.send_message.assert_called_once()
    args, _ = mock_client.send_message.call_args
    request = args[0]
    assert request.message.parts[0].text == 'Hi'


@pytest.mark.asyncio
async def test_send_text_message_custom_delimiter(
    text_client: TextClient, mock_client: AsyncMock
) -> None:
    async def create_stream(*args, **kwargs):
        resp1 = StreamResponse()
        resp1.message.parts.append(Part(text='Hello'))
        yield resp1
        resp2 = StreamResponse()
        resp2.artifact_update.artifact.parts.append(Part(text='World'))
        yield resp2

    mock_client.send_message.return_value = create_stream()
    response = await text_client.send_text_message('Hi', delimiter='\n')
    assert response == 'Hello\nWorld'


@pytest.mark.asyncio
async def test_send_text_message_forwards_context(
    text_client: TextClient, mock_client: AsyncMock
) -> None:
    async def empty_stream(*args, **kwargs):
        return
        yield

    mock_client.send_message.return_value = empty_stream()
    context = ClientCallContext()

    await text_client.send_text_message('Hi', context=context)

    _, kwargs = mock_client.send_message.call_args
    assert kwargs['context'] is context


def test_reset_session_changes_context_id(text_client: TextClient) -> None:
    # Access internal state only to verify reset behaviour, not as public API
    original = text_client._context_id
    text_client.reset_session()
    assert text_client._context_id != original
    assert text_client._task_id is None


@pytest.mark.asyncio
async def test_send_text_message_sets_task_id_from_task_event(
    text_client: TextClient, mock_client: AsyncMock
) -> None:
    async def create_stream(*args, **kwargs):
        resp = StreamResponse()
        resp.task.id = 'task-123'
        yield resp

    mock_client.send_message.return_value = create_stream()
    await text_client.send_text_message('Hi')
    assert text_client._task_id == 'task-123'


@pytest.mark.asyncio
async def test_send_text_message_sets_task_id_from_status_update(
    text_client: TextClient, mock_client: AsyncMock
) -> None:
    async def create_stream(*args, **kwargs):
        resp = StreamResponse()
        resp.status_update.task_id = 'task-456'
        resp.status_update.status.state = 1
        yield resp

    mock_client.send_message.return_value = create_stream()
    await text_client.send_text_message('Hi')
    assert text_client._task_id == 'task-456'


@pytest.mark.asyncio
async def test_session_ids_passed_in_request(
    text_client: TextClient, mock_client: AsyncMock
) -> None:
    async def create_stream(*args, **kwargs):
        resp = StreamResponse()
        resp.task.id = 'task-789'
        yield resp

    mock_client.send_message.return_value = create_stream()
    context_id = text_client._context_id

    await text_client.send_text_message('Hi')

    args, _ = mock_client.send_message.call_args
    request = args[0]
    assert request.message.context_id == context_id
    assert not request.message.task_id

    # Second call carries the task_id from the first
    async def create_stream2(*args, **kwargs):
        return
        yield

    mock_client.send_message.return_value = create_stream2()
    await text_client.send_text_message('Follow up')

    args, _ = mock_client.send_message.call_args
    request = args[0]
    assert request.message.context_id == context_id
    assert request.message.task_id == 'task-789'


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'terminal_state',
    [
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    ],
)
async def test_task_id_cleared_on_terminal_state(
    text_client: TextClient,
    mock_client: AsyncMock,
    terminal_state: TaskState,
) -> None:
    async def create_stream(*args, **kwargs):
        resp = StreamResponse()
        resp.status_update.task_id = 'task-abc'
        resp.status_update.status.state = terminal_state
        yield resp

    mock_client.send_message.return_value = create_stream()
    await text_client.send_text_message('Hi')
    assert text_client._task_id is None


@pytest.mark.asyncio
async def test_async_context_manager(mock_client: AsyncMock) -> None:
    async with TextClient(mock_client) as client:
        assert isinstance(client, TextClient)
        mock_client.close.assert_not_awaited()
    mock_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close(text_client: TextClient, mock_client: AsyncMock) -> None:
    await text_client.close()
    mock_client.close.assert_awaited_once()
