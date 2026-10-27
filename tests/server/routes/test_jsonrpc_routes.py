from unittest.mock import AsyncMock, patch

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


def test_shutdown_grace_period_is_forwarded_to_dispatcher(mock_handler) -> None:
    """Tests that the route factory configures SSE cooperative shutdown."""
    with patch(
        'a2a.server.routes.jsonrpc_routes.JsonRpcDispatcher'
    ) as dispatcher_class:
        create_jsonrpc_routes(
            request_handler=mock_handler,
            rpc_url='/a2a/jsonrpc',
        )
        dispatcher_class.assert_called_once_with(
            request_handler=mock_handler,
            context_builder=None,
            enable_v0_3_compat=False,
            shutdown_grace_period=0,
        )

        dispatcher_class.reset_mock()
        create_jsonrpc_routes(
            request_handler=mock_handler,
            rpc_url='/a2a/jsonrpc',
            shutdown_grace_period=30.0,
        )

        dispatcher_class.assert_called_once_with(
            request_handler=mock_handler,
            context_builder=None,
            enable_v0_3_compat=False,
            shutdown_grace_period=30.0,
        )
