import json

from unittest.mock import AsyncMock

import httpx
import pytest

from starlette.applications import Starlette
from starlette.routing import BaseRoute
from starlette.testclient import TestClient

from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    ListTasksResponse,
    Message,
    Part,
    Role,
    Task,
)
from a2a.utils.errors import InternalError


@pytest.fixture
def agent_card():
    return AgentCard()


@pytest.fixture
def mock_handler():
    return AsyncMock(spec=RequestHandler)


def test_routes_creation(agent_card, mock_handler):
    """Tests that create_rest_routes creates Route objects list."""
    routes = create_rest_routes(
        agent_card=agent_card, request_handler=mock_handler
    )

    assert isinstance(routes, list)
    assert len(routes) > 0
    assert all(isinstance(r, BaseRoute) for r in routes)


def test_routes_creation_v03_compat(agent_card, mock_handler):
    """Tests that create_rest_routes creates more routes with enable_v0_3_compat."""
    routes_without_compat = create_rest_routes(
        agent_card=agent_card,
        request_handler=mock_handler,
        enable_v0_3_compat=False,
    )
    routes_with_compat = create_rest_routes(
        agent_card=agent_card,
        request_handler=mock_handler,
        enable_v0_3_compat=True,
    )

    assert len(routes_with_compat) > len(routes_without_compat)


def test_rest_endpoints_routing(agent_card, mock_handler):
    """Tests that mounted routes route to the handler endpoints."""
    mock_handler.on_message_send.return_value = Task(id='123')

    routes = create_rest_routes(
        agent_card=agent_card, request_handler=mock_handler
    )
    app = Starlette(routes=routes)
    client = TestClient(app)

    # Test POST /message:send
    response = client.post(
        '/message:send', json={}, headers={'A2A-Version': '1.0'}
    )
    assert response.status_code == 200
    assert response.json()['task']['id'] == '123'
    assert mock_handler.on_message_send.called


def test_rest_endpoints_routing_tenant(agent_card, mock_handler):
    """Tests that mounted routes with {tenant} route to the handler endpoints."""
    mock_handler.on_message_send.return_value = Task(id='123')

    routes = create_rest_routes(
        agent_card=agent_card, request_handler=mock_handler
    )
    app = Starlette(routes=routes)
    client = TestClient(app)

    # Test POST /{tenant}/message:send
    response = client.post(
        '/my-tenant/message:send', json={}, headers={'A2A-Version': '1.0'}
    )
    assert response.status_code == 200

    # Verify that tenant was set in call context
    call_args = mock_handler.on_message_send.call_args
    assert call_args is not None
    # call_args[0] is positional args. In on_message_send(params, context):
    context = call_args[0][1]
    assert context.tenant == 'my-tenant'


def test_rest_list_tasks(agent_card, mock_handler):
    """Tests that list tasks endpoint is routed to the handler."""
    mock_handler.on_list_tasks.return_value = ListTasksResponse()

    routes = create_rest_routes(
        agent_card=agent_card, request_handler=mock_handler
    )
    app = Starlette(routes=routes)
    client = TestClient(app)

    response = client.get('/tasks', headers={'A2A-Version': '1.0'})
    assert response.status_code == 200
    assert mock_handler.on_list_tasks.called


@pytest.fixture
def streaming_agent_card():
    return AgentCard(
        capabilities=AgentCapabilities(streaming=True),
    )


@pytest.fixture
def streaming_app(streaming_agent_card, mock_handler):
    routes = create_rest_routes(
        agent_card=streaming_agent_card, request_handler=mock_handler
    )
    return Starlette(routes=routes)


@pytest.fixture
def streaming_client(streaming_app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=streaming_app),
        base_url='http://test',
        headers={'A2A-Version': '1.0'},
    )


@pytest.mark.asyncio
async def test_streaming_mid_stream_error_emits_sse_error_event(
    streaming_client, mock_handler
):
    """Test that mid-stream errors are sent as SSE error events."""

    async def mock_stream_then_error(*args, **kwargs):
        yield Message(
            message_id='stream_msg_1',
            role=Role.ROLE_AGENT,
            parts=[Part(text='First chunk')],
        )
        raise InternalError(message='Something went wrong mid-stream')

    mock_handler.on_message_send_stream.side_effect = mock_stream_then_error

    response = await streaming_client.post(
        '/message:stream',
        headers={'Accept': 'text/event-stream'},
        json={},
    )

    response.raise_for_status()
    assert 'text/event-stream' in response.headers.get('content-type', '')

    lines = [line.strip() for line in response.text.strip().splitlines()]

    # Should have a normal data event followed by an error event
    data_lines = [
        json.loads(line[6:]) for line in lines if line.startswith('data: ')
    ]
    assert len(data_lines) >= 1
    assert 'message' in data_lines[0]
    assert data_lines[0]['message']['messageId'] == 'stream_msg_1'

    # Should contain an SSE error event
    error_event_lines = [line for line in lines if line == 'event: error']
    assert len(error_event_lines) == 1

    # Find the error data after the error event
    error_data = None
    for i, line in enumerate(lines):
        if line == 'event: error':
            for j in range(i + 1, len(lines)):
                if lines[j].startswith('data: '):
                    error_data = json.loads(lines[j][6:])
                    break
            break

    assert error_data is not None
    assert error_data['error']['code'] == 500
    assert error_data['error']['status'] == 'INTERNAL'
    assert 'Something went wrong mid-stream' in error_data['error']['message']


@pytest.mark.asyncio
async def test_streaming_mid_stream_unknown_error_emits_sse_error_event(
    streaming_client, mock_handler
):
    """Test that non-A2AError mid-stream errors also produce SSE error events."""

    async def mock_stream_then_error(*args, **kwargs):
        yield Message(
            message_id='stream_msg_1',
            role=Role.ROLE_AGENT,
            parts=[Part(text='First chunk')],
        )
        raise RuntimeError('Unexpected failure')

    mock_handler.on_message_send_stream.side_effect = mock_stream_then_error

    response = await streaming_client.post(
        '/message:stream',
        headers={'Accept': 'text/event-stream'},
        json={},
    )

    response.raise_for_status()

    lines = [line.strip() for line in response.text.strip().splitlines()]

    error_event_lines = [line for line in lines if line == 'event: error']
    assert len(error_event_lines) == 1

    error_data = None
    for i, line in enumerate(lines):
        if line == 'event: error':
            for j in range(i + 1, len(lines)):
                if lines[j].startswith('data: '):
                    error_data = json.loads(lines[j][6:])
                    break
            break

    assert error_data is not None
    assert error_data['error']['code'] == 500
    assert error_data['error']['status'] == 'INTERNAL'
