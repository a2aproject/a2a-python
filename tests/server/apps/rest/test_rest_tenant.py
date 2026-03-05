import pytest
from unittest.mock import MagicMock
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from google.protobuf import json_format

from a2a.server.apps.rest.fastapi_app import A2ARESTFastAPIApplication
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import (
    AgentCard,
    Message,
    Role,
    Part,
    SendMessageRequest,
    SendMessageConfiguration,
)


@pytest.fixture
async def agent_card() -> AgentCard:
    mock_agent_card = MagicMock(spec=AgentCard)
    mock_agent_card.url = 'http://mockurl.com'
    mock_capabilities = MagicMock()
    mock_capabilities.streaming = False
    mock_agent_card.capabilities = mock_capabilities
    return mock_agent_card


@pytest.fixture
async def request_handler() -> RequestHandler:
    handler = MagicMock(spec=RequestHandler)
    # Return a default response so the test doesn't crash on return value expectation
    handler.on_message_send.return_value = Message(
        message_id='test',
        role=Role.ROLE_AGENT,
        parts=[Part(text='response message')],
    )
    return handler


@pytest.fixture
async def app(
    agent_card: AgentCard, request_handler: RequestHandler
) -> FastAPI:
    return A2ARESTFastAPIApplication(agent_card, request_handler).build(
        agent_card_url='/well-known/agent.json', rpc_url=''
    )


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url='http://test')


@pytest.mark.anyio
async def test_tenant_extraction_from_path(
    client: AsyncClient, request_handler: MagicMock
) -> None:
    request = SendMessageRequest(
        message=Message(),
        configuration=SendMessageConfiguration(),
    )

    # Test with tenant in URL
    tenant_id = 'my-tenant-123'
    response = await client.post(
        f'/{tenant_id}/message:send', json=json_format.MessageToDict(request)
    )
    response.raise_for_status()

    # Verify handler was called
    assert request_handler.on_message_send.called

    # Verify call context has tenant
    args, _ = request_handler.on_message_send.call_args
    # args[0] is the request proto, args[1] is the ServerCallContext
    context = args[1]
    assert context.tenant == tenant_id


@pytest.mark.anyio
async def test_no_tenant_extraction(
    client: AsyncClient, request_handler: MagicMock
) -> None:
    request = SendMessageRequest(
        message=Message(),
        configuration=SendMessageConfiguration(),
    )

    # Test without tenant in URL
    response = await client.post(
        '/message:send', json=json_format.MessageToDict(request)
    )
    response.raise_for_status()

    # Verify call context has empty string tenant (default)
    args, _ = request_handler.on_message_send.call_args
    context = args[1]
    assert context.tenant == ''
