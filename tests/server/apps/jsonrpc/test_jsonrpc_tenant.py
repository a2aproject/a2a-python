from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from a2a.server.apps.jsonrpc.starlette_app import A2AStarletteApplication
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import AgentCard, Message, Part, Role


@pytest.fixture
def mock_handler():
    handler = AsyncMock(spec=RequestHandler)
    # Return a proto Message object directly - the handler wraps it in SendMessageResponse
    handler.on_message_send.return_value = Message(
        message_id='test',
        role=Role.ROLE_AGENT,
        parts=[Part(text='response message')],
    )
    return handler


@pytest.fixture
def test_app(mock_handler):
    mock_agent_card = MagicMock(spec=AgentCard)
    mock_agent_card.url = 'http://mockurl.com'
    # Set up capabilities.streaming to avoid validation issues
    mock_agent_card.capabilities = MagicMock()
    mock_agent_card.capabilities.streaming = False

    return A2AStarletteApplication(
        agent_card=mock_agent_card, http_handler=mock_handler
    )


@pytest.fixture
def client(test_app):
    return TestClient(test_app.build(rpc_url='/jsonrpc'))


def _make_send_message_request(
    text: str = 'hi', tenant: str | None = None
) -> dict:
    """Helper to create a JSON-RPC send message request."""
    params = {
        'message': {
            'messageId': '1',
            'role': 'ROLE_USER',
            'parts': [{'text': text}],
        }
    }
    if tenant:
        params['tenant'] = tenant

    return {
        'jsonrpc': '2.0',
        'id': '1',
        'method': 'SendMessage',
        'params': params,
    }


def test_tenant_extraction_from_params(client, mock_handler):
    tenant_id = 'my-tenant-123'
    response = client.post(
        '/jsonrpc',
        json=_make_send_message_request(tenant=tenant_id),
    )
    response.raise_for_status()

    mock_handler.on_message_send.assert_called_once()
    call_context = mock_handler.on_message_send.call_args[0][1]
    assert isinstance(call_context, ServerCallContext)
    assert call_context.tenant == tenant_id


def test_no_tenant_extraction(client, mock_handler):
    response = client.post(
        '/jsonrpc',
        json=_make_send_message_request(tenant=None),
    )
    response.raise_for_status()

    mock_handler.on_message_send.assert_called_once()
    call_context = mock_handler.on_message_send.call_args[0][1]
    assert isinstance(call_context, ServerCallContext)
    assert call_context.tenant == ''
