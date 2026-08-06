from unittest.mock import AsyncMock

import pytest

from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.types.a2a_pb2 import AgentCard
from starlette.applications import Starlette
from starlette.testclient import TestClient


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
    # Both the exact path and the trailing-slash variant are registered so
    # that clients sending either form (httpx normalizes empty paths to a
    # trailing slash) reach the endpoint.
    assert len(routes) == 2

    from starlette.routing import Route

    for route in routes:
        assert isinstance(route, Route)
        assert route.methods == {'POST'}

    assert {route.path for route in routes} == {'/a2a/jsonrpc', '/a2a/jsonrpc/'}


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
