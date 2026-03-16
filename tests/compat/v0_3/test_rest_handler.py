import json

from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a.compat.v0_3 import types as types_v03
from a2a.compat.v0_3.rest_handler import REST03Handler
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import AgentCard


@pytest.fixture
def mock_core_handler():
    return AsyncMock(spec=RequestHandler)


@pytest.fixture
def agent_card():
    card = MagicMock(spec=AgentCard)
    card.capabilities = MagicMock()
    card.capabilities.streaming = True
    card.capabilities.push_notifications = True
    return card


@pytest.fixture
def rest_handler(agent_card, mock_core_handler):
    handler = REST03Handler(
        agent_card=agent_card, request_handler=mock_core_handler
    )
    # Mock the internal handler03 for easier testing of translations
    handler.handler03 = AsyncMock()
    return handler


@pytest.fixture
def mock_context():
    return MagicMock(spec=ServerCallContext)


@pytest.fixture
def mock_request():
    req = MagicMock()
    req.path_params = {}
    req.query_params = {}
    return req


@pytest.mark.anyio
async def test_on_message_send(rest_handler, mock_request, mock_context):
    request_body = {
        'request': {
            'messageId': 'msg-1',
            'role': 'ROLE_USER',
            'content': [{'text': 'Hello'}],
        }
    }
    mock_request.body = AsyncMock(
        return_value=json.dumps(request_body).encode('utf-8')
    )

    # Configure handler03 to return a types_v03.Message
    rest_handler.handler03.on_message_send.return_value = types_v03.Message(
        message_id='msg-2', role='agent', parts=[types_v03.TextPart(text='Hi')]
    )

    result = await rest_handler.on_message_send(mock_request, mock_context)

    assert result == {
        'message': {
            'messageId': 'msg-2',
            'role': 'ROLE_AGENT',
            'content': [{'text': 'Hi'}],
        }
    }

    rest_handler.handler03.on_message_send.assert_called_once()
    called_req = rest_handler.handler03.on_message_send.call_args[0][0]
    assert isinstance(called_req, types_v03.SendMessageRequest)
    assert called_req.params.message.message_id == 'msg-1'


@pytest.mark.anyio
async def test_on_message_send_stream(rest_handler, mock_request, mock_context):
    request_body = {
        'request': {
            'messageId': 'msg-1',
            'role': 'ROLE_USER',
            'content': [{'text': 'Hello'}],
        }
    }
    mock_request.body = AsyncMock(
        return_value=json.dumps(request_body).encode('utf-8')
    )

    async def mock_stream(*args, **kwargs):
        yield types_v03.SendStreamingMessageSuccessResponse(
            id='req-1',
            result=types_v03.Message(
                message_id='msg-2',
                role='agent',
                parts=[types_v03.TextPart(text='Chunk')],
            ),
        )

    rest_handler.handler03.on_message_send_stream = MagicMock(
        side_effect=mock_stream
    )

    results = [
        json.loads(chunk)
        async for chunk in rest_handler.on_message_send_stream(
            mock_request, mock_context
        )
    ]

    assert results == [
        {
            'message': {
                'messageId': 'msg-2',
                'role': 'ROLE_AGENT',
                'content': [{'text': 'Chunk'}],
            }
        }
    ]


@pytest.mark.anyio
async def test_on_cancel_task(rest_handler, mock_request, mock_context):
    mock_request.path_params = {'id': 'task-1'}

    rest_handler.handler03.on_cancel_task.return_value = types_v03.Task(
        id='task-1',
        context_id='ctx-1',
        status=types_v03.TaskStatus(state='canceled'),
    )

    result = await rest_handler.on_cancel_task(mock_request, mock_context)

    assert result == {
        'id': 'task-1',
        'contextId': 'ctx-1',
        'status': {'state': 'TASK_STATE_CANCELLED'},
    }

    rest_handler.handler03.on_cancel_task.assert_called_once()
    called_req = rest_handler.handler03.on_cancel_task.call_args[0][0]
    assert called_req.params.id == 'task-1'


@pytest.mark.anyio
async def test_on_subscribe_to_task(rest_handler, mock_request, mock_context):
    mock_request.path_params = {'id': 'task-1'}

    async def mock_stream(*args, **kwargs):
        yield types_v03.SendStreamingMessageSuccessResponse(
            id='req-1',
            result=types_v03.Message(
                message_id='msg-2',
                role='agent',
                parts=[types_v03.TextPart(text='Update')],
            ),
        )

    rest_handler.handler03.on_subscribe_to_task = MagicMock(
        side_effect=mock_stream
    )

    results = [
        json.loads(chunk)
        async for chunk in rest_handler.on_subscribe_to_task(
            mock_request, mock_context
        )
    ]

    assert results == [
        {
            'message': {
                'messageId': 'msg-2',
                'role': 'ROLE_AGENT',
                'content': [{'text': 'Update'}],
            }
        }
    ]


@pytest.mark.anyio
async def test_get_push_notification(rest_handler, mock_request, mock_context):
    mock_request.path_params = {'id': 'task-1', 'push_id': 'push-1'}

    rest_handler.handler03.on_get_task_push_notification_config.return_value = (
        types_v03.TaskPushNotificationConfig(
            task_id='task-1',
            push_notification_config=types_v03.PushNotificationConfig(
                id='push-1', url='http://example.com'
            ),
        )
    )

    result = await rest_handler.get_push_notification(
        mock_request, mock_context
    )

    assert result == {
        'name': 'tasks/task-1/pushNotificationConfigs/push-1',
        'pushNotificationConfig': {
            'id': 'push-1',
            'url': 'http://example.com',
        },
    }


@pytest.mark.anyio
async def test_set_push_notification(rest_handler, mock_request, mock_context):
    mock_request.path_params = {'id': 'task-1'}
    request_body = {
        'parent': 'tasks/task-1',
        'config': {'pushNotificationConfig': {'url': 'http://example.com'}},
    }
    mock_request.body = AsyncMock(
        return_value=json.dumps(request_body).encode('utf-8')
    )

    rest_handler.handler03.on_create_task_push_notification_config.return_value = types_v03.TaskPushNotificationConfig(
        task_id='task-1',
        push_notification_config=types_v03.PushNotificationConfig(
            id='push-1', url='http://example.com'
        ),
    )

    result = await rest_handler.set_push_notification(
        mock_request, mock_context
    )

    assert result == {
        'name': 'tasks/task-1/pushNotificationConfigs/push-1',
        'pushNotificationConfig': {
            'id': 'push-1',
            'url': 'http://example.com',
        },
    }

    rest_handler.handler03.on_create_task_push_notification_config.assert_called_once()
    called_req = rest_handler.handler03.on_create_task_push_notification_config.call_args[
        0
    ][0]
    assert called_req.params.task_id == 'task-1'
    assert (
        called_req.params.push_notification_config.url == 'http://example.com'
    )


@pytest.mark.anyio
async def test_on_get_task(rest_handler, mock_request, mock_context):
    mock_request.path_params = {'id': 'task-1'}
    mock_request.query_params = {'historyLength': '5'}

    rest_handler.handler03.on_get_task.return_value = types_v03.Task(
        id='task-1',
        context_id='ctx-1',
        status=types_v03.TaskStatus(state='working'),
    )

    result = await rest_handler.on_get_task(mock_request, mock_context)

    assert result == {
        'id': 'task-1',
        'contextId': 'ctx-1',
        'status': {'state': 'TASK_STATE_WORKING'},
    }

    rest_handler.handler03.on_get_task.assert_called_once()
    called_req = rest_handler.handler03.on_get_task.call_args[0][0]
    assert called_req.params.id == 'task-1'
    assert called_req.params.history_length == 5


@pytest.mark.anyio
async def test_list_push_notifications(
    rest_handler, mock_request, mock_context
):
    mock_request.path_params = {'id': 'task-1'}
    rest_handler.handler03.on_list_task_push_notification_configs = AsyncMock(
        return_value=[
            types_v03.TaskPushNotificationConfig(
                task_id='task-1',
                push_notification_config=types_v03.PushNotificationConfig(
                    id='push-1',
                    url='http://example.com/notify',
                ),
            )
        ]
    )

    result = await rest_handler.list_push_notifications(
        mock_request, mock_context
    )

    assert result == {
        'configs': [
            {
                'name': 'tasks/task-1/pushNotificationConfigs/push-1',
                'pushNotificationConfig': {
                    'id': 'push-1',
                    'url': 'http://example.com/notify',
                },
            }
        ]
    }

    rest_handler.handler03.on_list_task_push_notification_configs.assert_called_once()
    called_req = (
        rest_handler.handler03.on_list_task_push_notification_configs.call_args[
            0
        ][0]
    )
    assert called_req.params.id == 'task-1'


@pytest.mark.anyio
async def test_list_tasks(rest_handler, mock_request, mock_context):
    with pytest.raises(NotImplementedError):
        await rest_handler.list_tasks(mock_request, mock_context)
