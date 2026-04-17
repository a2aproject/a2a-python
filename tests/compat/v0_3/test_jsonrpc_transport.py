from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from a2a.client.errors import A2AClientError
from a2a.compat.v0_3.jsonrpc_transport import CompatJsonRpcTransport
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    Message,
    Role,
    SendMessageRequest,
    SendMessageResponse,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
)
from a2a.utils.errors import InvalidParamsError


@pytest.fixture
def mock_httpx_client():
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def agent_card():
    return AgentCard(capabilities=AgentCapabilities(extended_agent_card=True))


@pytest.fixture
def transport(mock_httpx_client, agent_card):
    return CompatJsonRpcTransport(
        httpx_client=mock_httpx_client,
        agent_card=agent_card,
        url='http://example.com',
    )


@pytest.mark.asyncio
async def test_compat_jsonrpc_transport_send_message_response_msg_parsing(
    transport,
):
    async def mock_send_request(*args, **kwargs):
        return {
            'result': {
                'messageId': 'msg-123',
                'role': 'agent',
                'parts': [{'text': 'Hello'}],
            }
        }

    transport._send_request = mock_send_request

    req = SendMessageRequest(
        message=Message(message_id='msg-1', role=Role.ROLE_USER)
    )

    response = await transport.send_message(req)

    expected_response = SendMessageResponse(
        message=Message(
            message_id='msg-123',
            role=Role.ROLE_AGENT,
            parts=[{'text': 'Hello'}],
        )
    )
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_jsonrpc_transport_send_message_task(transport):
    async def mock_send_request(*args, **kwargs):
        return {
            'result': {
                'id': 'task-123',
                'contextId': 'ctx-456',
                'status': {
                    'state': 'working',
                    'message': {
                        'messageId': 'msg-123',
                        'role': 'agent',
                        'parts': [],
                    },
                },
            }
        }

    transport._send_request = mock_send_request

    req = SendMessageRequest(
        message=Message(message_id='msg-1', role=Role.ROLE_USER)
    )

    response = await transport.send_message(req)

    expected_response = SendMessageResponse(
        task=Task(
            id='task-123',
            context_id='ctx-456',
            status={
                'state': TaskState.TASK_STATE_WORKING,
                'message': {'message_id': 'msg-123', 'role': Role.ROLE_AGENT},
            },
        )
    )
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_jsonrpc_transport_get_task(transport):
    async def mock_send_request(*args, **kwargs):
        return {
            'result': {
                'id': 'task-123',
                'contextId': 'ctx-456',
                'status': {
                    'state': 'completed',
                    'message': {
                        'messageId': 'msg-789',
                        'role': 'agent',
                        'parts': [{'text': 'Done'}],
                    },
                },
            }
        }

    transport._send_request = mock_send_request

    req = GetTaskRequest(id='task-123')
    response = await transport.get_task(req)

    expected_response = Task(
        id='task-123',
        context_id='ctx-456',
        status={
            'state': TaskState.TASK_STATE_COMPLETED,
            'message': {
                'message_id': 'msg-789',
                'role': Role.ROLE_AGENT,
                'parts': [{'text': 'Done'}],
            },
        },
    )
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_jsonrpc_transport_cancel_task(transport):
    async def mock_send_request(*args, **kwargs):
        return {
            'result': {
                'id': 'task-123',
                'contextId': 'ctx-456',
                'status': {
                    'state': 'canceled',
                    'message': {
                        'messageId': 'msg-789',
                        'role': 'agent',
                        'parts': [{'text': 'Cancelled'}],
                    },
                },
            }
        }

    transport._send_request = mock_send_request

    req = CancelTaskRequest(id='task-123')
    response = await transport.cancel_task(req)

    expected_response = Task(
        id='task-123',
        context_id='ctx-456',
        status={
            'state': TaskState.TASK_STATE_CANCELED,
            'message': {
                'message_id': 'msg-789',
                'role': Role.ROLE_AGENT,
                'parts': [{'text': 'Cancelled'}],
            },
        },
    )
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_jsonrpc_transport_create_task_push_notification_config(
    transport,
):
    async def mock_send_request(*args, **kwargs):
        return {
            'result': {
                'taskId': 'task-123',
                'name': 'tasks/task-123/pushNotificationConfigs/push-123',
                'pushNotificationConfig': {
                    'url': 'http://push',
                    'id': 'push-123',
                },
            }
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
async def test_compat_jsonrpc_transport_get_task_push_notification_config(
    transport,
):
    async def mock_send_request(*args, **kwargs):
        return {
            'result': {
                'taskId': 'task-123',
                'name': 'tasks/task-123/pushNotificationConfigs/push-123',
                'pushNotificationConfig': {
                    'url': 'http://push',
                    'id': 'push-123',
                },
            }
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
async def test_compat_jsonrpc_transport_list_task_push_notification_configs(
    transport,
):
    async def mock_send_request(*args, **kwargs):
        return {
            'result': [
                {
                    'taskId': 'task-123',
                    'name': 'tasks/task-123/pushNotificationConfigs/push-123',
                    'pushNotificationConfig': {
                        'url': 'http://push',
                        'id': 'push-123',
                    },
                }
            ]
        }

    transport._send_request = mock_send_request

    req = ListTaskPushNotificationConfigsRequest(task_id='task-123')
    response = await transport.list_task_push_notification_configs(req)

    expected_response = ListTaskPushNotificationConfigsResponse(
        configs=[
            TaskPushNotificationConfig(
                id='push-123', task_id='task-123', url='http://push'
            )
        ]
    )
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_jsonrpc_transport_delete_task_push_notification_config(
    transport,
):
    async def mock_send_request(*args, **kwargs):
        return {'result': {}}

    transport._send_request = mock_send_request

    req = DeleteTaskPushNotificationConfigRequest(
        task_id='task-123', id='push-123'
    )
    assert await transport.delete_task_push_notification_config(req) is None


@pytest.mark.asyncio
async def test_compat_jsonrpc_transport_get_extended_agent_card(transport):
    async def mock_send_request(*args, **kwargs):
        return {
            'result': {
                'name': 'ExtendedAgent',
                'url': 'http://agent',
                'version': '1.0.0',
                'description': 'Description',
                'skills': [],
                'defaultInputModes': [],
                'defaultOutputModes': [],
                'capabilities': {},
                'supportsAuthenticatedExtendedCard': True,
            }
        }

    transport._send_request = mock_send_request

    req = GetExtendedAgentCardRequest()
    response = await transport.get_extended_agent_card(req)

    expected_response = AgentCard(
        name='ExtendedAgent',
        version='1.0.0',
        description='Description',
        capabilities=AgentCapabilities(extended_agent_card=True),
    )
    expected_response.supported_interfaces.add(
        url='http://agent',
        protocol_binding='JSONRPC',
        protocol_version='0.3.0',
    )
    assert response == expected_response


@pytest.mark.asyncio
async def test_compat_jsonrpc_transport_get_extended_agent_card_not_supported(
    transport,
):
    transport.agent_card.capabilities.extended_agent_card = False

    req = GetExtendedAgentCardRequest()
    response = await transport.get_extended_agent_card(req)

    assert response == transport.agent_card


@pytest.mark.asyncio
async def test_compat_jsonrpc_transport_get_extended_agent_card_method_name(
    transport,
):
    """Verify the correct v0.3 method name 'agent/getAuthenticatedExtendedCard' is used."""
    captured_request: dict | None = None

    async def mock_send_request(data, *args, **kwargs):
        nonlocal captured_request
        captured_request = data
        return {
            'result': {
                'name': 'ExtendedAgent',
                'url': 'http://agent',
                'version': '1.0.0',
                'description': 'Description',
                'skills': [],
                'defaultInputModes': [],
                'defaultOutputModes': [],
                'capabilities': {},
                'supportsAuthenticatedExtendedCard': True,
            }
        }

    transport._send_request = mock_send_request

    req = GetExtendedAgentCardRequest()
    await transport.get_extended_agent_card(req)

    assert captured_request is not None
    assert captured_request['method'] == 'agent/getAuthenticatedExtendedCard'


@pytest.mark.asyncio
async def test_compat_jsonrpc_transport_close(transport, mock_httpx_client):
    await transport.close()
    mock_httpx_client.aclose.assert_called_once()


@pytest.mark.asyncio
async def test_compat_jsonrpc_transport_send_message_streaming(transport):
    async def mock_send_stream_request(*args, **kwargs):
        task = Task(id='task-123', context_id='ctx')
        task.status.message.role = Role.ROLE_AGENT
        yield StreamResponse(task=task)
        yield StreamResponse(
            message=Message(message_id='msg-123', role=Role.ROLE_AGENT)
        )

    transport._send_stream_request = mock_send_stream_request

    req = SendMessageRequest(
        message=Message(message_id='msg-1', role=Role.ROLE_USER)
    )

    events = [event async for event in transport.send_message_streaming(req)]

    assert len(events) == 2
    expected_task = Task(id='task-123', context_id='ctx')
    expected_task.status.message.role = Role.ROLE_AGENT
    assert events[0] == StreamResponse(task=expected_task)
    assert events[1] == StreamResponse(
        message=Message(message_id='msg-123', role=Role.ROLE_AGENT)
    )


@pytest.mark.asyncio
async def test_compat_jsonrpc_transport_subscribe(transport):
    async def mock_send_stream_request(*args, **kwargs):
        task = Task(id='task-123', context_id='ctx')
        task.status.message.role = Role.ROLE_AGENT
        yield StreamResponse(task=task)

    transport._send_stream_request = mock_send_stream_request

    req = SubscribeToTaskRequest(id='task-123')
    events = [event async for event in transport.subscribe(req)]

    assert len(events) == 1
    expected_task = Task(id='task-123', context_id='ctx')
    expected_task.status.message.role = Role.ROLE_AGENT
    assert events[0] == StreamResponse(task=expected_task)


def test_compat_jsonrpc_transport_handle_http_error(transport):
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 400

    mock_request = MagicMock(spec=httpx.Request)
    mock_request.url = 'http://example.com'

    error = httpx.HTTPStatusError(
        'Error', request=mock_request, response=mock_response
    )

    with pytest.raises(A2AClientError) as exc_info:
        transport._handle_http_error(error)

    assert str(exc_info.value) == 'HTTP Error: 400'


def test_compat_jsonrpc_transport_create_jsonrpc_error(transport):
    error_dict = {'code': -32602, 'message': 'Invalid parameters'}

    error = transport._create_jsonrpc_error(error_dict)
    assert isinstance(error, InvalidParamsError)
    assert str(error) == 'Invalid parameters'


def test_compat_jsonrpc_transport_create_jsonrpc_error_unknown(transport):
    error_dict = {'code': -12345, 'message': 'Unknown Error'}

    error = transport._create_jsonrpc_error(error_dict)
    assert isinstance(error, A2AClientError)
    assert str(error) == 'Unknown Error'


@pytest.mark.asyncio
async def test_compat_jsonrpc_transport_list_tasks(transport):
    with pytest.raises(NotImplementedError):
        await transport.list_tasks(ListTasksRequest())


@pytest.mark.asyncio
async def test_compat_jsonrpc_transport_send_message_empty(transport):
    async def mock_send_request(*args, **kwargs):
        return {'result': {}}

    transport._send_request = mock_send_request

    req = SendMessageRequest(
        message=Message(message_id='msg-1', role=Role.ROLE_USER)
    )

    response = await transport.send_message(req)
    assert response == SendMessageResponse()


@pytest.mark.asyncio
@patch('a2a.compat.v0_3.jsonrpc_transport.send_http_stream_request')
async def test_compat_jsonrpc_transport_send_stream_request(
    mock_send_http_stream_request, transport
):
    async def mock_generator(*args, **kwargs):
        yield b'{"result": {"id": "task-123", "contextId": "ctx-456", "kind": "task", "status": {"state": "working", "message": {"messageId": "msg-1", "role": "agent", "parts": []}}}}'

    mock_send_http_stream_request.return_value = mock_generator()

    events = [
        event
        async for event in transport._send_stream_request({'some': 'data'})
    ]

    assert len(events) == 1
    expected_task = Task(id='task-123', context_id='ctx-456')
    expected_task.status.state = TaskState.TASK_STATE_WORKING
    expected_task.status.message.message_id = 'msg-1'
    expected_task.status.message.role = Role.ROLE_AGENT
    assert events[0] == StreamResponse(task=expected_task)

    mock_send_http_stream_request.assert_called_once_with(
        transport.httpx_client,
        'POST',
        'http://example.com',
        transport._handle_http_error,
        json={'some': 'data'},
        headers={'a2a-version': '0.3'},
    )


@pytest.mark.asyncio
@patch('a2a.compat.v0_3.jsonrpc_transport.send_http_request')
async def test_compat_jsonrpc_transport_send_request(
    mock_send_http_request, transport
):
    mock_send_http_request.return_value = {'result': {'ok': True}}
    mock_request = httpx.Request('POST', 'http://example.com')
    transport.httpx_client.build_request.return_value = mock_request

    res = await transport._send_request({'some': 'data'})
    assert res == {'result': {'ok': True}}

    transport.httpx_client.build_request.assert_called_once_with(
        'POST',
        'http://example.com',
        json={'some': 'data'},
        headers={'a2a-version': '0.3'},
    )
    mock_send_http_request.assert_called_once_with(
        transport.httpx_client, mock_request, transport._handle_http_error
    )


@pytest.mark.asyncio
@patch('a2a.compat.v0_3.jsonrpc_transport.send_http_request')
async def test_compat_jsonrpc_transport_mirrors_extension_header(
    mock_send_http_request, transport
):
    """Compat client must also emit the legacy X-A2A-Extensions header so
    that v0.3 servers (which only know that name) understand the request."""
    from a2a.client.client import ClientCallContext

    mock_send_http_request.return_value = {'result': {'ok': True}}
    transport.httpx_client.build_request.return_value = httpx.Request(
        'POST', 'http://example.com'
    )

    context = ClientCallContext(
        service_parameters={'A2A-Extensions': 'foo,bar'}
    )

    await transport._send_request({'some': 'data'}, context=context)

    _, kwargs = transport.httpx_client.build_request.call_args
    headers = kwargs['headers']
    assert headers['A2A-Extensions'] == 'foo,bar'
    assert headers['X-A2A-Extensions'] == 'foo,bar'
