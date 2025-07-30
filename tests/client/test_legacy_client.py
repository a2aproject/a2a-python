"""Tests for the legacy client compatibility layer."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from a2a.client import A2AClient, A2AGrpcClient
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    Message,
    MessageSendParams,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskQueryParams,
    TaskState,
    TaskStatus,
    TextPart,
)


@pytest.fixture
def mock_httpx_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def mock_grpc_stub() -> AsyncMock:
    stub = AsyncMock()
    stub._channel = MagicMock()
    return stub


@pytest.fixture
def jsonrpc_agent_card() -> AgentCard:
    return AgentCard(
        name='Test Agent',
        description='A test agent',
        url='http://test.agent.com/rpc',
        version='1.0.0',
        capabilities=AgentCapabilities(streaming=True),
        skills=[],
        default_input_modes=[],
        default_output_modes=[],
        preferred_transport='jsonrpc',
    )


@pytest.fixture
def grpc_agent_card() -> AgentCard:
    return AgentCard(
        name='Test Agent',
        description='A test agent',
        url='http://test.agent.com/rpc',
        version='1.0.0',
        capabilities=AgentCapabilities(streaming=True),
        skills=[],
        default_input_modes=[],
        default_output_modes=[],
        preferred_transport='grpc',
    )


@pytest.mark.asyncio
async def test_a2a_client_send_message(
    mock_httpx_client: AsyncMock, jsonrpc_agent_card: AgentCard
):
    client = A2AClient(
        httpx_client=mock_httpx_client, agent_card=jsonrpc_agent_card
    )

    # Mock the underlying transport's send_message method
    mock_response_task = Task(
        id='task-123',
        context_id='ctx-456',
        status=TaskStatus(state=TaskState.completed),
    )

    client._transport.send_message = AsyncMock(return_value=mock_response_task)

    message = Message(
        message_id='msg-123',
        role=Role.user,
        parts=[Part(root=TextPart(text='Hello'))],
    )
    request = SendMessageRequest(
        id='req-123', params=MessageSendParams(message=message)
    )
    response = await client.send_message(request)

    assert response.root.result.id == 'task-123'


@pytest.mark.asyncio
async def test_a2a_grpc_client_get_task(
    mock_grpc_stub: AsyncMock, grpc_agent_card: AgentCard
):
    client = A2AGrpcClient(grpc_stub=mock_grpc_stub, agent_card=grpc_agent_card)

    mock_response_task = Task(
        id='task-456',
        context_id='ctx-789',
        status=TaskStatus(state=TaskState.working),
    )

    client.get_task = AsyncMock(return_value=mock_response_task)

    params = TaskQueryParams(id='task-456')
    response = await client.get_task(params)

    assert response.id == 'task-456'
    client.get_task.assert_awaited_once_with(params)
