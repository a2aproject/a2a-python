import json

from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import pytest_asyncio

from a2a.client.errors import A2AClientError
from a2a.compat.v0_3 import types as types_v03
from a2a.compat.v0_3.rest_transport import CompatRestTransport
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import create_rest_routes
from a2a.server.tasks import (
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
)
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTasksRequest,
    Message,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
)
from a2a.utils.errors import InvalidParamsError, MethodNotFoundError
from starlette.applications import Starlette


@pytest.fixture
def mock_httpx_client():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.is_closed = False
    return client


@pytest.fixture
def agent_card():
    return AgentCard(capabilities=AgentCapabilities(extended_agent_card=True))


@pytest.fixture
def transport(mock_httpx_client, agent_card):
    return CompatRestTransport(
        httpx_client=mock_httpx_client,
        agent_card=agent_card,
        url='http://example.com',
    )


@pytest.mark.asyncio
async def test_compat_rest_transport_send_message_response_msg_parsing(
    transport,
):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {
        'msg': {'messageId': 'msg-123', 'role': 'agent'}
    }

    async def mock_send_request(*args, **kwargs):
        return mock_response.json()

    transport._send_request = mock_send_request

    req = SendMessageRequest(
        message=Message(message_id='msg-1', role=Role.ROLE_USER)
    )

    response = await transport.send_message(req)

    expected_response = SendMessageResponse(
        message=Message(message_id='msg-123', role=Role.ROLE_AGENT)
    )
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_rest_transport_send_message_task(transport):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = {'task': {'id': 'task-123'}}

    async def mock_send_request(*args, **kwargs):
        return mock_response.json()

    transport._send_request = mock_send_request

    req = SendMessageRequest(
        message=Message(message_id='msg-1', role=Role.ROLE_USER)
    )

    response = await transport.send_message(req)

    expected_response = SendMessageResponse(
        task=Task(id='task-123', status=Task(id='task-123').status)
    )
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_rest_transport_get_task(transport):
    async def mock_send_request(*args, **kwargs):
        return {'id': 'task-123'}

    transport._send_request = mock_send_request

    req = GetTaskRequest(id='task-123')
    response = await transport.get_task(req)

    expected_response = Task(id='task-123', status=TaskStatus())
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_rest_transport_cancel_task(transport):
    async def mock_send_request(*args, **kwargs):
        return {'id': 'task-123'}

    transport._send_request = mock_send_request

    req = CancelTaskRequest(id='task-123')
    response = await transport.cancel_task(req)

    expected_response = Task(id='task-123', status=TaskStatus())
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_rest_transport_create_task_push_notification_config(
    transport,
):
    async def mock_send_request(*args, **kwargs):
        return {
            'name': 'tasks/task-123/pushNotificationConfigs/push-123',
            'pushNotificationConfig': {'url': 'http://push', 'id': 'push-123'},
        }

    transport._send_request = mock_send_request

    req = TaskPushNotificationConfig(
        task_id='task-123', id='push-123', url='http://push'
    )
    response = await transport.create_task_push_notification_config(req)

    expected_response = TaskPushNotificationConfig(
        id='push-123', task_id='task-123', url='http://push'
    )
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_rest_transport_get_task_push_notification_config(
    transport,
):
    async def mock_send_request(*args, **kwargs):
        return {
            'name': 'tasks/task-123/pushNotificationConfigs/push-123',
            'pushNotificationConfig': {'url': 'http://push', 'id': 'push-123'},
        }

    transport._send_request = mock_send_request

    req = GetTaskPushNotificationConfigRequest(
        task_id='task-123', id='push-123'
    )
    response = await transport.get_task_push_notification_config(req)

    expected_response = TaskPushNotificationConfig(
        id='push-123', task_id='task-123', url='http://push'
    )
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_rest_transport_get_extended_agent_card(transport):
    async def mock_send_request(*args, **kwargs):
        return {
            'name': 'ExtendedAgent',
            'capabilities': {},
            'supportsAuthenticatedExtendedCard': True,
        }

    transport._send_request = mock_send_request

    req = GetExtendedAgentCardRequest()
    response = await transport.get_extended_agent_card(req)

    assert response.name == 'ExtendedAgent'
    assert response.capabilities.extended_agent_card is True


@pytest.mark.asyncio
async def test_compat_rest_transport_get_extended_agent_card_keeps_security(
    transport,
):
    """v0.3 servers send the card as JSON, not as the v0.3 proto."""
    card = types_v03.AgentCard(
        name='ExtendedAgent',
        description='Extended',
        version='1.0.0',
        url='http://example.com',
        capabilities=types_v03.AgentCapabilities(),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        skills=[
            types_v03.AgentSkill(
                id='skill-1',
                name='Skill',
                description='Skill',
                tags=['tag'],
                security=[{'bearer': []}],
            )
        ],
        security_schemes={
            'bearer': types_v03.SecurityScheme(
                root=types_v03.HTTPAuthSecurityScheme(scheme='bearer')
            )
        },
        security=[{'bearer': []}],
        supports_authenticated_extended_card=True,
    )

    async def mock_send_request(*args, **kwargs):
        # Same serialization as REST03Handler.on_get_extended_agent_card.
        return card.model_dump(mode='json', exclude_none=True)

    transport._send_request = mock_send_request

    response = await transport.get_extended_agent_card(
        GetExtendedAgentCardRequest()
    )

    scheme = response.security_schemes['bearer']
    assert scheme.WhichOneof('scheme') == 'http_auth_security_scheme'
    assert scheme.http_auth_security_scheme.scheme == 'bearer'
    assert list(response.security_requirements[0].schemes) == ['bearer']
    assert list(response.skills[0].security_requirements[0].schemes) == [
        'bearer'
    ]


@pytest.mark.asyncio
async def test_compat_rest_transport_get_extended_agent_card_not_supported(
    transport,
):
    transport.agent_card.capabilities.extended_agent_card = False

    req = GetExtendedAgentCardRequest()
    response = await transport.get_extended_agent_card(req)

    assert response == transport.agent_card


@pytest.mark.asyncio
async def test_compat_rest_transport_close(transport, mock_httpx_client):
    await transport.close()
    mock_httpx_client.aclose.assert_called_once()


@pytest.mark.asyncio
async def test_compat_rest_transport_send_message_streaming(transport):
    async def mock_send_stream_request(*args, **kwargs):
        task = Task(id='task-123')
        task.status.message.role = Role.ROLE_AGENT
        yield StreamResponse(task=task)
        yield StreamResponse(message=Message(message_id='msg-123'))

    transport._send_stream_request = mock_send_stream_request

    req = SendMessageRequest(
        message=Message(message_id='msg-1', role=Role.ROLE_USER)
    )

    events = [event async for event in transport.send_message_streaming(req)]

    assert len(events) == 2
    expected_task = Task(id='task-123')
    expected_task.status.message.role = Role.ROLE_AGENT
    assert events[0] == StreamResponse(task=expected_task)
    assert events[1] == StreamResponse(message=Message(message_id='msg-123'))


def create_405_error():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 405
    mock_response.json.return_value = {
        'type': 'MethodNotAllowed',
        'message': 'Method Not Allowed',
    }
    mock_request = MagicMock(spec=httpx.Request)
    mock_request.url = 'http://example.com/v1/tasks/task-123:subscribe'

    status_error = httpx.HTTPStatusError(
        '405 Method Not Allowed', request=mock_request, response=mock_response
    )
    raise A2AClientError('HTTP Error 405') from status_error


def create_500_error():
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 500
    mock_response.json.return_value = {
        'type': 'InternalError',
        'message': 'Internal Error',
    }
    mock_request = MagicMock(spec=httpx.Request)

    status_error = httpx.HTTPStatusError(
        '500 Internal Error', request=mock_request, response=mock_response
    )
    raise A2AClientError('HTTP Error 500') from status_error


@pytest.mark.asyncio
async def test_compat_rest_transport_subscribe_post_works_no_retry(transport):
    """Scenario: POST works, no retry."""

    async def mock_stream(method, path, context=None, json=None):
        assert method == 'POST'
        assert json is None
        task = Task(id='task-123')
        task.status.message.role = Role.ROLE_AGENT
        yield StreamResponse(task=task)

    transport._send_stream_request = mock_stream

    req = SubscribeToTaskRequest(id='task-123')
    events = [event async for event in transport.subscribe(req)]

    assert len(events) == 1
    expected_task = Task(id='task-123')
    expected_task.status.message.role = Role.ROLE_AGENT
    assert events[0] == StreamResponse(task=expected_task)
    assert transport._subscribe_method_override is None


@pytest.mark.asyncio
async def test_compat_rest_transport_subscribe_post_405_retry_get_success(
    transport,
):
    """Scenario: POST returns 405, automatic retry GET. Second call uses GET directly."""
    call_count = 0

    async def mock_stream(method, path, context=None, json=None):
        nonlocal call_count
        call_count += 1
        if method == 'POST':
            assert json is None
            create_405_error()
        if method == 'GET':
            assert json is None
            task = Task(id='task-123')
            task.status.message.role = Role.ROLE_AGENT
            yield StreamResponse(task=task)

    transport._send_stream_request = mock_stream

    req = SubscribeToTaskRequest(id='task-123')
    events = [event async for event in transport.subscribe(req)]

    assert len(events) == 1
    assert call_count == 2
    assert transport._subscribe_method_override == 'GET'

    # Second call should use GET directly
    call_count = 0
    events = [event async for event in transport.subscribe(req)]
    assert len(events) == 1
    assert call_count == 1  # Only GET called
    assert transport._subscribe_method_override == 'GET'


@pytest.mark.asyncio
async def test_compat_rest_transport_subscribe_post_405_get_405_fails(
    transport,
):
    """Scenario: POST return 405, retry GET, return 405 - error. Second call is just POST."""

    method_count = {}

    async def mock_stream(method, path, context=None, json=None):
        method_count[method] = method_count.get(method, 0) + 1
        if method in {'POST', 'GET'}:
            assert json is None
        # To make it an async generator even when it raises
        if False:
            yield
        create_405_error()

    transport._send_stream_request = mock_stream

    req = SubscribeToTaskRequest(id='task-123')
    with pytest.raises(A2AClientError) as exc_info:
        [event async for event in transport.subscribe(req)]

    assert '405' in str(exc_info.value)
    assert transport._subscribe_method_override == 'POST'
    assert method_count == {'POST': 1, 'GET': 1}
    assert transport._subscribe_auto_method_override is False

    # Second call should try POST directly and fail without retry
    with pytest.raises(A2AClientError):
        [event async for event in transport.subscribe(req)]
    assert transport._subscribe_auto_method_override is False
    assert transport._subscribe_method_override == 'POST'
    assert method_count == {'POST': 2, 'GET': 1}


@pytest.mark.asyncio
async def test_compat_rest_transport_subscribe_post_500_no_retry(transport):
    """Scenario: POST return 500, no automatic retry."""
    call_count = 0

    async def mock_stream(method, path, context=None, json=None):
        nonlocal call_count
        call_count += 1
        assert method == 'POST'
        assert json is None
        if False:
            yield
        create_500_error()

    transport._send_stream_request = mock_stream

    req = SubscribeToTaskRequest(id='task-123')
    with pytest.raises(A2AClientError) as exc_info:
        [event async for event in transport.subscribe(req)]

    assert '500' in str(exc_info.value)
    assert call_count == 1  # No retry on 500
    assert transport._subscribe_method_override is None


@pytest.mark.asyncio
async def test_compat_rest_transport_subscribe_method_override_avoids_retry_get(
    mock_httpx_client, agent_card
):
    """Scenario: Init with GET override, server returns 405, no automatic retry."""
    transport = CompatRestTransport(
        httpx_client=mock_httpx_client,
        agent_card=agent_card,
        url='http://example.com',
        subscribe_method_override='GET',
    )
    call_count = 0

    async def mock_stream(method, path, context=None, json=None):
        nonlocal call_count
        call_count += 1
        assert method == 'GET'
        assert json is None
        if False:
            yield
        create_405_error()

    transport._send_stream_request = mock_stream

    req = SubscribeToTaskRequest(id='task-123')
    with pytest.raises(A2AClientError) as exc_info:
        [event async for event in transport.subscribe(req)]

    assert '405' in str(exc_info.value)
    assert call_count == 1


@pytest.mark.asyncio
async def test_compat_rest_transport_subscribe_method_override_avoids_retry_post(
    mock_httpx_client, agent_card
):
    """Scenario: Init with POST override, server returns 405, no automatic retry."""
    transport = CompatRestTransport(
        httpx_client=mock_httpx_client,
        agent_card=agent_card,
        url='http://example.com',
        subscribe_method_override='POST',
    )
    call_count = 0

    async def mock_stream(method, path, context=None, json=None):
        nonlocal call_count
        call_count += 1
        assert method == 'POST'
        assert json is None
        if False:
            yield
        create_405_error()

    transport._send_stream_request = mock_stream

    req = SubscribeToTaskRequest(id='task-123')
    with pytest.raises(A2AClientError) as exc_info:
        [event async for event in transport.subscribe(req)]

    assert '405' in str(exc_info.value)
    assert call_count == 1


def test_compat_rest_transport_handle_http_error(transport):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 400
    mock_response.json.return_value = {
        'type': 'InvalidParamsError',
        'message': 'Invalid parameters',
    }

    mock_request = MagicMock(spec=httpx.Request)
    mock_request.url = 'http://example.com'

    error = httpx.HTTPStatusError(
        'Error', request=mock_request, response=mock_response
    )

    with pytest.raises(InvalidParamsError) as exc_info:
        transport._handle_http_error(error)

    assert str(exc_info.value) == 'Invalid parameters'


def test_compat_rest_transport_handle_http_error_not_found(transport):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 404
    mock_response.json.side_effect = json.JSONDecodeError('msg', 'doc', 0)

    mock_request = MagicMock(spec=httpx.Request)
    mock_request.url = 'http://example.com'

    error = httpx.HTTPStatusError(
        'Error', request=mock_request, response=mock_response
    )

    with pytest.raises(MethodNotFoundError):
        transport._handle_http_error(error)


def test_compat_rest_transport_handle_http_error_generic(transport):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 500
    mock_response.json.side_effect = json.JSONDecodeError('msg', 'doc', 0)

    mock_request = MagicMock(spec=httpx.Request)
    mock_request.url = 'http://example.com'

    error = httpx.HTTPStatusError(
        'Error', request=mock_request, response=mock_response
    )

    with pytest.raises(A2AClientError):
        transport._handle_http_error(error)


@pytest.mark.asyncio
async def test_compat_rest_transport_list_tasks(transport):
    with pytest.raises(NotImplementedError):
        await transport.list_tasks(ListTasksRequest())


@pytest.mark.asyncio
async def test_compat_rest_transport_list_task_push_notification_configs(
    transport,
):
    mock_execute_request = AsyncMock(
        return_value={
            'configs': [
                {
                    'name': 'tasks/task-123/pushNotificationConfigs/push-1',
                    'pushNotificationConfig': {
                        'url': 'http://push-1',
                        'id': 'push-1',
                    },
                },
                {
                    'name': 'tasks/task-123/pushNotificationConfigs/push-2',
                    'pushNotificationConfig': {
                        'url': 'http://push-2',
                        'id': 'push-2',
                    },
                },
            ]
        }
    )
    transport._execute_request = mock_execute_request

    response = await transport.list_task_push_notification_configs(
        ListTaskPushNotificationConfigsRequest(task_id='task-123')
    )

    mock_execute_request.assert_awaited_once_with(
        'GET', '/v1/tasks/task-123/pushNotificationConfigs', context=None
    )
    assert list(response.configs) == [
        TaskPushNotificationConfig(
            id='push-1', task_id='task-123', url='http://push-1'
        ),
        TaskPushNotificationConfig(
            id='push-2', task_id='task-123', url='http://push-2'
        ),
    ]


@pytest.mark.asyncio
async def test_compat_rest_transport_delete_task_push_notification_config(
    transport,
):
    mock_execute_request = AsyncMock(return_value={})
    transport._execute_request = mock_execute_request

    response = await transport.delete_task_push_notification_config(
        DeleteTaskPushNotificationConfigRequest(
            task_id='task-123', id='push-123'
        )
    )

    mock_execute_request.assert_awaited_once_with(
        'DELETE',
        '/v1/tasks/task-123/pushNotificationConfigs/push-123',
        context=None,
    )
    assert response is None


@pytest.mark.asyncio
async def test_compat_rest_transport_send_message_empty(transport):
    async def mock_send_request(*args, **kwargs):
        return {}

    transport._send_request = mock_send_request

    req = SendMessageRequest(
        message=Message(message_id='msg-1', role=Role.ROLE_USER)
    )

    response = await transport.send_message(req)
    assert response == SendMessageResponse()


@pytest.mark.asyncio
async def test_compat_rest_transport_send_message_without_configuration(
    transport,
):
    """No configuration is sent, so the server keeps its blocking default."""
    sent: dict = {}

    async def mock_execute_request(method, path, context=None, json=None):
        sent.update(json)
        return {}

    transport._execute_request = mock_execute_request

    req = SendMessageRequest(
        message=Message(message_id='msg-1', role=Role.ROLE_USER)
    )
    await transport.send_message(req)

    assert 'configuration' not in sent


@pytest.mark.asyncio
async def test_compat_rest_transport_send_message_with_configuration(
    transport,
):
    sent: dict = {}

    async def mock_execute_request(method, path, context=None, json=None):
        sent.update(json)
        return {}

    transport._execute_request = mock_execute_request

    req = SendMessageRequest(
        message=Message(message_id='msg-1', role=Role.ROLE_USER),
        configuration=SendMessageConfiguration(
            accepted_output_modes=['text/plain']
        ),
    )
    await transport.send_message(req)

    assert sent['configuration']['blocking'] is True


@pytest.mark.asyncio
async def test_compat_rest_transport_send_message_uses_json_names(transport):
    """The v0.3 spec requires json_name keys in HTTP+JSON bodies."""
    sent: dict = {}

    async def mock_execute_request(method, path, context=None, json=None):
        sent.update(json)
        return {}

    transport._execute_request = mock_execute_request

    req = SendMessageRequest(
        message=Message(
            message_id='msg-1',
            context_id='ctx-1',
            task_id='task-1',
            role=Role.ROLE_USER,
        ),
        configuration=SendMessageConfiguration(
            accepted_output_modes=['text/plain']
        ),
    )
    await transport.send_message(req)

    assert 'request' not in sent
    assert sent['message']['messageId'] == 'msg-1'
    assert sent['message']['contextId'] == 'ctx-1'
    assert sent['message']['taskId'] == 'task-1'
    assert sent['configuration']['acceptedOutputModes'] == ['text/plain']


@pytest.mark.asyncio
async def test_compat_rest_transport_send_message_streaming_uses_json_names(
    transport,
):
    sent: dict = {}

    async def mock_send_stream_request(method, path, context=None, json=None):
        sent.update(json)
        yield StreamResponse(message=Message(message_id='msg-123'))

    transport._send_stream_request = mock_send_stream_request

    req = SendMessageRequest(
        message=Message(
            message_id='msg-1', context_id='ctx-1', role=Role.ROLE_USER
        )
    )
    _ = [event async for event in transport.send_message_streaming(req)]

    assert 'request' not in sent
    assert sent['message']['messageId'] == 'msg-1'
    assert sent['message']['contextId'] == 'ctx-1'


@pytest.mark.asyncio
async def test_compat_rest_transport_create_task_push_notification_config_uses_json_names(
    transport,
):
    sent: dict = {}

    async def mock_execute_request(method, path, context=None, json=None):
        sent.update(json)
        return {
            'name': 'tasks/task-123/pushNotificationConfigs/push-123',
            'pushNotificationConfig': {'url': 'http://push', 'id': 'push-123'},
        }

    transport._execute_request = mock_execute_request

    req = TaskPushNotificationConfig(
        task_id='task-123', id='push-123', url='http://push'
    )
    await transport.create_task_push_notification_config(req)

    assert sent['configId'] == 'push-123'
    assert sent['config']['pushNotificationConfig']['id'] == 'push-123'
    assert 'config_id' not in sent


@pytest.mark.asyncio
async def test_compat_rest_transport_get_task_no_history(transport):
    async def mock_execute_request(method, path, context=None, params=None):
        assert 'historyLength' not in params
        return {'id': 'task-123'}

    transport._execute_request = mock_execute_request

    req = GetTaskRequest(id='task-123')
    response = await transport.get_task(req)
    expected_response = Task(id='task-123', status=TaskStatus())
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_rest_transport_get_task_with_history(transport):
    async def mock_execute_request(method, path, context=None, params=None):
        assert params['historyLength'] == 10
        return {'id': 'task-123'}

    transport._execute_request = mock_execute_request

    req = GetTaskRequest(id='task-123', history_length=10)
    response = await transport.get_task(req)
    expected_response = Task(id='task-123', status=TaskStatus())
    assert response == expected_response


def test_compat_rest_transport_handle_http_error_invalid_error_type(transport):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 500
    mock_response.json.return_value = {
        'type': 123,
        'message': 'Invalid parameters',
    }

    mock_request = MagicMock(spec=httpx.Request)
    mock_request.url = 'http://example.com'

    error = httpx.HTTPStatusError(
        'Error', request=mock_request, response=mock_response
    )

    with pytest.raises(A2AClientError):
        transport._handle_http_error(error)


@pytest.mark.parametrize(
    ('status_code', 'body', 'expected_error'),
    [
        (401, 'invalid_token', A2AClientError),
        (404, ['Not Found'], MethodNotFoundError),
        (503, None, A2AClientError),
    ],
)
def test_compat_rest_transport_handle_http_error_non_object_body(
    transport, status_code, body, expected_error
):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = status_code
    mock_response.json.return_value = body

    mock_request = MagicMock(spec=httpx.Request)
    mock_request.url = 'http://example.com'

    error = httpx.HTTPStatusError(
        'Error', request=mock_request, response=mock_response
    )

    with pytest.raises(expected_error):
        transport._handle_http_error(error)


def test_compat_rest_transport_handle_http_error_unknown_error_type(transport):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 500
    mock_response.json.return_value = {
        'type': 'SomeUnknownErrorClass',
        'message': 'Unknown',
    }

    mock_request = MagicMock(spec=httpx.Request)
    mock_request.url = 'http://example.com'

    error = httpx.HTTPStatusError(
        'Error', request=mock_request, response=mock_response
    )

    with pytest.raises(A2AClientError):
        transport._handle_http_error(error)


@pytest.mark.asyncio
@patch('a2a.compat.v0_3.rest_transport.send_http_stream_request')
async def test_compat_rest_transport_send_stream_request(
    mock_send_http_stream_request, transport
):
    async def mock_generator(*args, **kwargs):
        yield b'{"task": {"id": "task-123"}}'

    mock_send_http_stream_request.return_value = mock_generator()

    events = [
        event async for event in transport._send_stream_request('POST', '/test')
    ]

    assert len(events) == 1
    expected_task = Task(id='task-123', status=TaskStatus())
    assert events[0] == StreamResponse(task=expected_task)

    mock_send_http_stream_request.assert_called_once_with(
        transport.httpx_client,
        'POST',
        'http://example.com/test',
        transport._handle_http_error,
        json=None,
        headers={'a2a-version': '0.3'},
    )


@pytest.mark.asyncio
@patch('a2a.compat.v0_3.rest_transport.send_http_request')
async def test_compat_rest_transport_execute_request(
    mock_send_http_request, transport
):
    mock_send_http_request.return_value = {'ok': True}
    mock_request = httpx.Request('POST', 'http://example.com')
    transport.httpx_client.build_request.return_value = mock_request

    res = await transport._execute_request(
        'POST', '/test', json={'some': 'data'}
    )
    assert res == {'ok': True}

    # Assert httpx client build_request was called correctly
    transport.httpx_client.build_request.assert_called_once_with(
        'POST',
        'http://example.com/test',
        json={'some': 'data'},
        params=None,
        headers={'a2a-version': '0.3'},
    )
    mock_send_http_request.assert_called_once_with(
        transport.httpx_client, mock_request, transport._handle_http_error
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'history_length, expected_ids',
    [
        (None, ['m0', 'm1', 'm2']),
        (0, []),
        (1, ['m2']),
        (3, ['m0', 'm1', 'm2']),
        (5, ['m0', 'm1', 'm2']),
    ],
)
async def test_get_task_history_length(
    transport: CompatRestTransport,
    history_length: int | None,
    expected_ids: list[str],
) -> None:
    # v0.3 treats zero as unlimited; v1.0 requires an empty local history.
    payload = {
        'id': 'task-1',
        'contextId': 'ctx-1',
        'status': {'state': 'TASK_STATE_WORKING'},
        'history': [
            {'messageId': f'm{i}', 'role': 'ROLE_USER', 'parts': []}
            for i in range(3)
        ],
    }
    with patch.object(
        transport, '_execute_request', new_callable=AsyncMock
    ) as request:
        request.return_value = payload
        req = GetTaskRequest(id='task-1')
        if history_length is not None:
            req.history_length = history_length
        task = await transport.get_task(req)
    assert [message.message_id for message in task.history] == expected_ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'history_length, expected_ids',
    [
        (None, ['m0', 'm1', 'm2']),
        (0, []),
        (1, ['m2']),
        (3, ['m0', 'm1', 'm2']),
        (5, ['m0', 'm1', 'm2']),
    ],
)
async def test_send_message_history_length(
    transport: CompatRestTransport,
    history_length: int | None,
    expected_ids: list[str],
) -> None:
    # v0.3 treats zero as unlimited; v1.0 requires an empty local history.
    payload = {
        'id': 'task-1',
        'contextId': 'ctx-1',
        'status': {'state': 'TASK_STATE_WORKING'},
        'history': [
            {'messageId': f'm{i}', 'role': 'ROLE_USER', 'parts': []}
            for i in range(3)
        ],
    }
    with patch.object(
        transport, '_execute_request', new_callable=AsyncMock
    ) as request:
        request.return_value = {'task': payload}
        req = SendMessageRequest()
        if history_length is not None:
            req.configuration.history_length = history_length
        response = await transport.send_message(req)
        assert response.HasField('task')
        task = response.task
    assert [message.message_id for message in task.history] == expected_ids


@pytest.mark.asyncio
@pytest.mark.parametrize('history_length', [None, 0, 1, 3, 5])
@pytest.mark.parametrize('history_size', [0, 3])
async def test_streaming_task_history_length(
    transport: CompatRestTransport,
    monkeypatch: pytest.MonkeyPatch,
    history_length: int | None,
    history_size: int,
) -> None:
    task = Task(id='task-1', context_id='ctx-1')
    for i in range(history_size):
        task.history.add(message_id=f'm{i}', role=Role.ROLE_USER)
    message = StreamResponse(
        message=Message(message_id='reply', role=Role.ROLE_AGENT)
    )

    async def stream(
        *args: Any, **kwargs: Any
    ) -> AsyncIterator[StreamResponse]:
        yield StreamResponse(task=task)
        yield message
        yield StreamResponse(task=task)

    monkeypatch.setattr(transport, '_send_stream_request', stream)
    request = SendMessageRequest(
        message=Message(message_id='request', role=Role.ROLE_USER)
    )
    if history_length is not None:
        request.configuration.history_length = history_length
    events = [
        event async for event in transport.send_message_streaming(request)
    ]
    expected = [f'm{i}' for i in range(history_size)]
    if history_length is not None:
        expected = expected[-history_length:] if history_length else []
    assert len(events) == 3
    for index in (0, 2):
        assert [
            item.message_id for item in events[index].task.history
        ] == expected
        assert events[index].task.id == task.id
    assert events[1] == message
    assert len(task.history) == history_size


class TestCompatRestTransportPathEncoding:
    """Ids with reserved URL characters reach their own resource.

    Runs a real request handler behind the v0.3 REST routes, so a wrong path
    reads the wrong resource.
    """

    @pytest_asyncio.fixture
    async def transport(self) -> AsyncGenerator[CompatRestTransport, None]:
        task_store = InMemoryTaskStore()
        await task_store.save(
            Task(
                id='t1',
                context_id='c1',
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            ),
            ServerCallContext(),
        )
        agent_card = AgentCard(
            capabilities=AgentCapabilities(push_notifications=True)
        )
        request_handler = DefaultRequestHandler(
            None,
            task_store,
            agent_card,
            push_config_store=InMemoryPushNotificationConfigStore(),
        )
        app = Starlette(
            routes=create_rest_routes(
                request_handler=request_handler, enable_v0_3_compat=True
            )
        )
        httpx_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
        transport = CompatRestTransport(
            httpx_client, agent_card, 'http://agent'
        )
        yield transport
        await transport.close()

    @pytest.mark.asyncio
    async def test_get_push_config_does_not_return_config_named_by_prefix(
        self, transport: CompatRestTransport
    ):
        for config_id, url in (
            ('alerts', 'https://hooks.example.com/alerts'),
            ('alerts#ops', 'https://hooks.example.com/ops'),
        ):
            await transport.create_task_push_notification_config(
                TaskPushNotificationConfig(task_id='t1', id=config_id, url=url)
            )

        config = await transport.get_task_push_notification_config(
            GetTaskPushNotificationConfigRequest(task_id='t1', id='alerts#ops')
        )

        assert config.id == 'alerts#ops'
        assert config.url == 'https://hooks.example.com/ops'

    @pytest.mark.asyncio
    async def test_get_task_does_not_return_task_named_by_id_prefix(
        self, transport: CompatRestTransport
    ):
        # The v0.3 client maps the server's 404 for an unknown task to
        # MethodNotFoundError.
        with pytest.raises(MethodNotFoundError):
            await transport.get_task(GetTaskRequest(id='t1?x'))
