import json

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from a2a.client.errors import A2AClientError
from a2a.compat.v0_3.rest_transport import CompatRestTransport
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
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)
from a2a.utils.errors import InvalidParamsError, MethodNotFoundError


@pytest.fixture
def mock_httpx_client():
    return AsyncMock(spec=httpx.AsyncClient)


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
    # The default conversion from 0.3 task generates a TaskStatus with a default empty message with role=ROLE_AGENT
    expected_response.task.status.message.role = Role.ROLE_AGENT
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_rest_transport_get_task(transport):
    async def mock_send_request(*args, **kwargs):
        return {'id': 'task-123'}

    transport._send_request = mock_send_request

    req = GetTaskRequest(id='task-123')
    response = await transport.get_task(req)

    expected_response = Task(id='task-123')
    expected_response.status.message.role = Role.ROLE_AGENT
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_rest_transport_cancel_task(transport):
    async def mock_send_request(*args, **kwargs):
        return {'id': 'task-123'}

    transport._send_request = mock_send_request

    req = CancelTaskRequest(id='task-123')
    response = await transport.cancel_task(req)

    expected_response = Task(id='task-123')
    expected_response.status.message.role = Role.ROLE_AGENT
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
    with pytest.raises(NotImplementedError):
        await transport.list_task_push_notification_configs(
            ListTaskPushNotificationConfigsRequest()
        )


@pytest.mark.asyncio
async def test_compat_rest_transport_delete_task_push_notification_config(
    transport,
):
    with pytest.raises(NotImplementedError):
        await transport.delete_task_push_notification_config(
            DeleteTaskPushNotificationConfigRequest()
        )


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
async def test_compat_rest_transport_get_task_no_history(transport):
    async def mock_execute_request(method, path, context=None, params=None):
        assert 'historyLength' not in params
        return {'id': 'task-123'}

    transport._execute_request = mock_execute_request

    req = GetTaskRequest(id='task-123')
    response = await transport.get_task(req)
    expected_response = Task(id='task-123')
    expected_response.status.message.role = Role.ROLE_AGENT
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_rest_transport_get_task_with_history(transport):
    async def mock_execute_request(method, path, context=None, params=None):
        assert params['historyLength'] == 10
        return {'id': 'task-123'}

    transport._execute_request = mock_execute_request

    req = GetTaskRequest(id='task-123', history_length=10)
    response = await transport.get_task(req)
    expected_response = Task(id='task-123')
    expected_response.status.message.role = Role.ROLE_AGENT
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
    expected_task = Task(id='task-123')
    expected_task.status.message.role = Role.ROLE_AGENT
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
