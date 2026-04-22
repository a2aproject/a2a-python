import logging

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from starlette.applications import Starlette
from a2a.server.routes import create_jsonrpc_routes
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import (
    AgentCard,
    AgentCapabilities,
    AgentInterface,
    Message as Message10,
    Part as Part10,
    Role as Role10,
    Task as Task10,
    TaskStatus as TaskStatus10,
    TaskState as TaskState10,
)

from a2a.compat.v0_3 import a2a_v0_3_pb2


logger = logging.getLogger(__name__)


@pytest.fixture
def mock_handler():
    handler = AsyncMock(spec=RequestHandler)
    handler.on_message_send.return_value = Message10(
        message_id='test',
        role=Role10.ROLE_AGENT,
        parts=[Part10(text='response message')],
    )
    handler.on_get_task.return_value = Task10(
        id='test_task_id',
        context_id='test_context_id',
        status=TaskStatus10(
            state=TaskState10.TASK_STATE_COMPLETED,
        ),
    )
    return handler


@pytest.fixture
def agent_card():
    card = AgentCard(
        name='TestAgent',
        description='Test Description',
        version='1.0.0',
        capabilities=AgentCapabilities(
            streaming=False, push_notifications=True, extended_agent_card=True
        ),
    )
    interface = card.supported_interfaces.add()
    interface.url = 'http://mockurl.com'
    interface.protocol_binding = 'jsonrpc'
    interface.protocol_version = '0.3'
    return card


@pytest.fixture
def test_app(mock_handler, agent_card):
    mock_handler._agent_card = agent_card
    jsonrpc_routes = create_jsonrpc_routes(
        request_handler=mock_handler,
        enable_v0_3_compat=True,
        rpc_url='/',
    )
    return Starlette(routes=jsonrpc_routes)


@pytest.fixture
def client(test_app):
    return TestClient(test_app)


def test_send_message_v03_compat(
    client: TestClient, mock_handler: AsyncMock
) -> None:
    request_payload = {
        'jsonrpc': '2.0',
        'id': '1',
        'method': 'message/send',
        'params': {
            'message': {
                'messageId': 'req',
                'role': 'user',
                'parts': [{'text': 'hello'}],
            }
        },
    }

    response = client.post('/', json=request_payload)
    assert response.status_code == 200
    data = response.json()

    assert data['jsonrpc'] == '2.0'
    assert data['id'] == '1'
    assert 'result' in data
    assert data['result']['messageId'] == 'test'
    assert data['result']['parts'][0]['text'] == 'response message'


def test_get_task_v03_compat(
    client: TestClient, mock_handler: AsyncMock
) -> None:
    request_payload = {
        'jsonrpc': '2.0',
        'id': '2',
        'method': 'tasks/get',
        'params': {'id': 'test_task_id'},
    }

    response = client.post('/', json=request_payload)
    assert response.status_code == 200
    data = response.json()

    assert data['jsonrpc'] == '2.0'
    assert data['id'] == '2'
    assert 'result' in data
    assert data['result']['id'] == 'test_task_id'
    assert data['result']['status']['state'] == 'completed'


def test_get_extended_agent_card_v03_compat(
    client: TestClient, mock_handler: AsyncMock, agent_card: AgentCard
) -> None:
    """Test that the v0.3 method name 'agent/getAuthenticatedExtendedCard' is correctly routed."""
    mock_handler.on_get_extended_agent_card.return_value = agent_card
    request_payload = {
        'jsonrpc': '2.0',
        'id': '3',
        'method': 'agent/getAuthenticatedExtendedCard',
        'params': {},
    }

    response = client.post('/', json=request_payload)
    assert response.status_code == 200
    data = response.json()

    assert data['jsonrpc'] == '2.0'
    assert data['id'] == '3'
    assert 'result' in data
    # The result should be a v0.3 AgentCard
    assert 'supportsAuthenticatedExtendedCard' in data['result']
