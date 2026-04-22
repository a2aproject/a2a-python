from unittest.mock import AsyncMock

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient
from starlette.routing import BaseRoute, Route

from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.types.a2a_pb2 import AgentCard, Task, ListTasksResponse


@pytest.fixture
def agent_card():
    return AgentCard()


@pytest.fixture
def mock_handler():
    return AsyncMock(spec=RequestHandler)


def test_routes_creation(agent_card, mock_handler):
    """Tests that create_rest_routes creates Route objects list."""
    routes = create_rest_routes(request_handler=mock_handler)

    assert isinstance(routes, list)
    assert len(routes) > 0
    assert all((isinstance(r, BaseRoute) for r in routes))


def test_routes_creation_v03_compat(agent_card, mock_handler):
    """Tests that create_rest_routes creates more routes with enable_v0_3_compat."""
    mock_handler._agent_card = agent_card
    routes_without_compat = create_rest_routes(
        request_handler=mock_handler, enable_v0_3_compat=False
    )
    routes_with_compat = create_rest_routes(
        request_handler=mock_handler, enable_v0_3_compat=True
    )

    assert len(routes_with_compat) > len(routes_without_compat)


def test_rest_endpoints_routing(agent_card, mock_handler):
    """Tests that mounted routes route to the handler endpoints."""
    mock_handler.on_message_send.return_value = Task(id='123')

    routes = create_rest_routes(request_handler=mock_handler)
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

    routes = create_rest_routes(request_handler=mock_handler)
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

    routes = create_rest_routes(request_handler=mock_handler)
    app = Starlette(routes=routes)
    client = TestClient(app)

    response = client.get('/tasks', headers={'A2A-Version': '1.0'})
    assert response.status_code == 200
    assert mock_handler.on_list_tasks.called
