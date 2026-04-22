from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient
from starlette.applications import Starlette

from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import AgentCard


@pytest.fixture
def agent_card():
    return AgentCard()


@pytest.fixture
def mock_handler():
    return AsyncMock(spec=RequestHandler)


def test_routes_creation(agent_card, mock_handler):
    """Tests that create_jsonrpc_routes creates Route objects list."""
    routes = create_jsonrpc_routes(
        request_handler=mock_handler, rpc_url='/a2a/jsonrpc'
    )

    assert isinstance(routes, list)
    assert len(routes) == 1

    from starlette.routing import Route

    assert isinstance(routes[0], Route)
    assert routes[0].methods == {'POST'}


def test_jsonrpc_custom_url(agent_card, mock_handler):
    """Tests that custom rpc_url is respected for routing."""
    custom_url = '/custom/api/jsonrpc'
    routes = create_jsonrpc_routes(
        request_handler=mock_handler, rpc_url=custom_url
    )

    app = Starlette(routes=routes)
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
