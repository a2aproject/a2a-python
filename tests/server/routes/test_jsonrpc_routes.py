# ruff: noqa: INP001
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient
from starlette.middleware import Middleware

from a2a.server.routes.jsonrpc_routes import JsonRpcRoutes
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import AgentCard


@pytest.fixture
def agent_card():
    return AgentCard()


@pytest.fixture
def mock_handler():
    return AsyncMock(spec=RequestHandler)


def test_routes_creation(agent_card, mock_handler):
    """Tests that JsonRpcRoutes creates Route objects list."""
    jsonrpc_routes = JsonRpcRoutes(
        agent_card=agent_card, request_handler=mock_handler
    )

    assert hasattr(jsonrpc_routes, 'routes')
    assert isinstance(jsonrpc_routes.routes, list)
    assert len(jsonrpc_routes.routes) == 1

    from starlette.routing import Route

    assert isinstance(jsonrpc_routes.routes[0], Route)
    assert jsonrpc_routes.routes[0].methods == {'POST'}


def test_jsonrpc_custom_url(agent_card, mock_handler):
    """Tests that custom rpc_url is respected for routing."""
    custom_url = '/custom/api/jsonrpc'
    jsonrpc_routes = JsonRpcRoutes(
        agent_card=agent_card, request_handler=mock_handler, rpc_url=custom_url
    )

    from starlette.applications import Starlette

    app = Starlette(routes=jsonrpc_routes.routes)
    client = TestClient(app)

    # Check that default path returns 404
    assert client.post('/a2a/jsonrpc', json={}).status_code == 404

    # Check that custom path routes to dispatcher (which will return JSON-RPC response, even if error)
    response = client.post(
        custom_url, json={'jsonrpc': '2.0', 'id': '1', 'method': 'foo'}
    )
    assert response.status_code == 200
    resp_json = response.json()
    assert 'error' in resp_json
    # Method not found error from dispatcher
    assert resp_json['error']['code'] == -32601


def test_jsonrpc_with_middleware(agent_card, mock_handler):
    """Tests that middleware is applied to the route."""
    middleware_called = False

    class MyMiddleware:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            nonlocal middleware_called
            middleware_called = True
            await self.app(scope, receive, send)

    jsonrpc_routes = JsonRpcRoutes(
        agent_card=agent_card,
        request_handler=mock_handler,
        middleware=[Middleware(MyMiddleware)],
        rpc_url='/',
    )

    from starlette.applications import Starlette

    app = Starlette(routes=jsonrpc_routes.routes)
    client = TestClient(app)

    # Call to trigger middleware
    # Empty JSON might raise error, let's send a base valid format for dispatcher
    client.post(
        '/', json={'jsonrpc': '2.0', 'id': '1', 'method': 'SendMessage'}
    )
    assert middleware_called is True
