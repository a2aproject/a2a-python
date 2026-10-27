import json
import logging

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from a2a.compat.v0_3 import jsonrpc_adapter
from a2a.compat.v0_3.jsonrpc_adapter import JSONRPC03Adapter
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes import create_jsonrpc_routes
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    Message as Message10,
    Part as Part10,
    Role as Role10,
    Task as Task10,
    TaskState as TaskState10,
    TaskStatus as TaskStatus10,
)
from a2a.utils.errors import (
    A2AError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from starlette.applications import Starlette
from starlette.testclient import TestClient


logger = logging.getLogger(__name__)


@pytest.mark.asyncio
async def test_shutdown_grace_period_is_passed_to_event_source_response(
    mock_handler: AsyncMock,
) -> None:
    async def stream_generator() -> AsyncIterator[MagicMock]:
        yield MagicMock()

    adapter = JSONRPC03Adapter(
        http_handler=mock_handler,
        shutdown_grace_period=30.0,
    )
    adapter.handler.on_message_send_stream = MagicMock(
        return_value=stream_generator()
    )
    request_obj = MagicMock(method='message/stream')

    with patch.object(
        jsonrpc_adapter, 'create_event_source_response'
    ) as response_class:
        await adapter._process_streaming_request(
            request_id='1',
            request_obj=request_obj,
            context=ServerCallContext(),
        )

    response_class.assert_called_once()
    assert response_class.call_args.kwargs['shutdown_grace_period'] == 30.0


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


def test_get_task_not_found_v03_compat(
    client: TestClient, mock_handler: AsyncMock
) -> None:
    mock_handler.on_get_task.return_value = None
    request_payload = {
        'jsonrpc': '2.0',
        'id': '4',
        'method': 'tasks/get',
        'params': {'id': 'missing_task_id'},
    }

    response = client.post('/', json=request_payload)
    assert response.status_code == 200
    data = response.json()

    assert data['id'] == '4'
    assert data['error']['code'] == -32001
    assert data['error']['message'] == 'Task not found'


@pytest.mark.parametrize(
    ('error', 'expected_code'),
    [
        (TaskNotFoundError(), -32001),
        (TaskNotCancelableError(), -32002),
        (UnsupportedOperationError(), -32004),
    ],
)
def test_cancel_task_v03_compat_maps_a2a_error_codes(
    client: TestClient,
    mock_handler: AsyncMock,
    error: A2AError,
    expected_code: int,
) -> None:
    mock_handler.on_cancel_task.side_effect = error
    request_payload = {
        'jsonrpc': '2.0',
        'id': '5',
        'method': 'tasks/cancel',
        'params': {'id': 'test_task_id'},
    }

    response = client.post('/', json=request_payload)
    assert response.status_code == 200
    data = response.json()

    assert data['id'] == '5'
    assert data['error']['code'] == expected_code
    assert data['error']['message'] == error.message


def test_message_stream_v03_compat_maps_a2a_error_code(
    client: TestClient, mock_handler: AsyncMock
) -> None:
    async def failing_stream(
        *args: object, **kwargs: object
    ) -> AsyncIterator[MagicMock]:
        raise TaskNotFoundError
        yield MagicMock()

    mock_handler.on_message_send_stream = failing_stream
    request_payload = {
        'jsonrpc': '2.0',
        'id': '6',
        'method': 'message/stream',
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
    events = [
        json.loads(line.removeprefix('data:'))
        for line in response.text.splitlines()
        if line.startswith('data:')
    ]

    assert len(events) == 1
    assert events[0]['id'] == '6'
    assert events[0]['error']['code'] == -32001
    assert events[0]['error']['message'] == 'Task not found'
