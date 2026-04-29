from unittest.mock import AsyncMock

import pytest

from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.routes.helpers.fastapi import (
    _AGENT_CARD_TAG,
    _JSONRPC_TAG,
    _REST_TAG,
)
from a2a.types.a2a_pb2 import AgentCard, Task
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    PROTOCOL_VERSION_1_0,
    VERSION_HEADER,
)


fastapi = pytest.importorskip('fastapi')
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def agent_card() -> AgentCard:
    return AgentCard(name='Test Agent', version='1.0.0')


@pytest.fixture
def mock_handler() -> AsyncMock:
    return AsyncMock(spec=RequestHandler)


def _build_app(agent_card: AgentCard, mock_handler: AsyncMock) -> FastAPI:
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card),
        jsonrpc_routes=create_jsonrpc_routes(mock_handler, rpc_url='/'),
        rest_routes=create_rest_routes(mock_handler),
    )
    return app


def test_routes_appear_in_openapi_with_tags(
    agent_card: AgentCard, mock_handler: AsyncMock
) -> None:
    """Each group is documented and tagged for the Swagger UI."""
    app = _build_app(agent_card, mock_handler)
    paths = app.openapi()['paths']

    assert paths[AGENT_CARD_WELL_KNOWN_PATH]['get']['tags'] == [_AGENT_CARD_TAG]
    assert paths['/']['post']['tags'] == [_JSONRPC_TAG]
    assert paths['/message:send']['post']['tags'] == [_REST_TAG]
    assert paths['/tasks']['get']['tags'] == [_REST_TAG]


def test_routes_dispatch_under_fastapi(
    agent_card: AgentCard, mock_handler: AsyncMock
) -> None:
    """Re-registered routes still dispatch correctly under a FastAPI app."""
    mock_handler.on_message_send.return_value = Task(id='task-123')

    app = _build_app(agent_card, mock_handler)
    client = TestClient(app)

    assert client.get(AGENT_CARD_WELL_KNOWN_PATH).json()['name'] == 'Test Agent'
    rpc_response = client.post(
        '/', json={'jsonrpc': '2.0', 'id': '1', 'method': 'NoSuchMethod'}
    ).json()
    assert rpc_response['error']['code'] == -32601

    rest_response = client.post(
        '/message:send',
        json={},
        headers={VERSION_HEADER: PROTOCOL_VERSION_1_0},
    )
    assert rest_response.status_code == 200
    assert rest_response.json()['task']['id'] == 'task-123'


def test_tenant_mount_still_dispatches(mock_handler: AsyncMock) -> None:
    """`Mount` entries (tenant routing) keep dispatching after registration."""
    mock_handler.on_message_send.return_value = Task(id='tenant-task')

    app = FastAPI()
    add_a2a_routes_to_fastapi(app, rest_routes=create_rest_routes(mock_handler))
    client = TestClient(app)

    response = client.post(
        '/my-tenant/message:send',
        json={},
        headers={VERSION_HEADER: PROTOCOL_VERSION_1_0},
    )
    assert response.status_code == 200
    context = mock_handler.on_message_send.call_args[0][1]
    assert context.tenant == 'my-tenant'


def test_partial_groups(agent_card: AgentCard, mock_handler: AsyncMock) -> None:
    """Calling with only a subset of groups works and tags only those."""
    app = FastAPI()
    add_a2a_routes_to_fastapi(
        app,
        agent_card_routes=create_agent_card_routes(agent_card),
    )
    paths = app.openapi()['paths']
    assert list(paths.keys()) == [AGENT_CARD_WELL_KNOWN_PATH]


def test_request_body_schemas_are_attached(
    agent_card: AgentCard, mock_handler: AsyncMock
) -> None:
    """JSON-RPC and REST POST bodies expose schemas derived from proto types."""
    app = _build_app(agent_card, mock_handler)
    schema = app.openapi()
    components = schema['components']['schemas']

    assert 'A2ARequest' in components
    assert components['A2ARequest']['properties']['method']['enum']
    rpc_body = schema['paths']['/']['post']['requestBody']
    assert rpc_body['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/A2ARequest'
    }

    send_body = schema['paths']['/message:send']['post']['requestBody']
    assert send_body['content']['application/json']['schema'] == {
        '$ref': '#/components/schemas/SendMessageRequest'
    }

    assert 'Message' in components
    assert 'Part' in components
    assert components['Message']['properties']['role']['enum'] == [
        'ROLE_UNSPECIFIED',
        'ROLE_USER',
        'ROLE_AGENT',
    ]


def test_routes_without_body_have_no_request_body(
    agent_card: AgentCard, mock_handler: AsyncMock
) -> None:
    """GET/DELETE/parameterless POST routes don't get a fabricated body."""
    app = _build_app(agent_card, mock_handler)
    paths = app.openapi()['paths']

    assert 'requestBody' not in paths[AGENT_CARD_WELL_KNOWN_PATH]['get']
    assert 'requestBody' not in paths['/tasks']['get']
    assert 'requestBody' not in paths['/tasks/{id}:cancel']['post']
    assert (
        'requestBody'
        not in paths['/tasks/{id}/pushNotificationConfigs/{push_id}']['delete']
    )


def test_a2a_version_header_on_dispatcher_routes(
    agent_card: AgentCard, mock_handler: AsyncMock
) -> None:
    """JSON-RPC and REST routes declare the version header so Swagger pre-fills it."""
    app = _build_app(agent_card, mock_handler)
    paths = app.openapi()['paths']

    def _has_version_header(op: dict) -> bool:
        return any(
            p.get('name') == VERSION_HEADER for p in op.get('parameters', [])
        )

    assert _has_version_header(paths['/']['post'])
    assert _has_version_header(paths['/message:send']['post'])
    assert _has_version_header(paths['/tasks']['get'])
    assert _has_version_header(paths['/tasks/{id}:cancel']['post'])
    assert not _has_version_header(paths[AGENT_CARD_WELL_KNOWN_PATH]['get'])
