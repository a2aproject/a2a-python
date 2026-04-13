from unittest.mock import AsyncMock

import pytest

from a2a.client import (
    Client,
    ClientConfig,
    ClientCallContext,
    create_text_client,
    minimal_agent_card,
    TextClient,
)
from a2a.types import Part, StreamResponse


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
    # Verify request construction
    args, _ = mock_client.send_message.call_args
    request = args[0]
    assert request.message.parts[0].text == 'Hi'


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


@pytest.mark.asyncio
async def test_close(text_client: TextClient, mock_client: AsyncMock) -> None:
    await text_client.close()
    mock_client.close.assert_awaited_once()
