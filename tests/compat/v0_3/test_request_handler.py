from unittest.mock import AsyncMock, MagicMock

import pytest

from a2a.compat.v0_3 import types as types_v03
from a2a.compat.v0_3.request_handler import RequestHandler03
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    ListTaskPushNotificationConfigsResponse as V10ListPushConfigsResp,
    Message as V10Message,
    Part as V10Part,
    Task as V10Task,
    TaskPushNotificationConfig as V10PushConfig,
    TaskState as V10TaskState,
    TaskStatus as V10TaskStatus,
)
from a2a.utils.errors import TaskNotFoundError


@pytest.fixture
def mock_core_handler():
    handler = AsyncMock(spec=RequestHandler)

    handler.agent_card = AgentCard(
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=True,
            extended_agent_card=True,
        )
    )
    return handler


@pytest.fixture
def v03_handler(mock_core_handler):
    return RequestHandler03(request_handler=mock_core_handler)


@pytest.fixture
def mock_context():
    return MagicMock(spec=ServerCallContext)


@pytest.mark.anyio
async def test_on_message_send_returns_message(
    v03_handler, mock_core_handler, mock_context
):
    v03_req = types_v03.SendMessageRequest(
        id='req-1',
        method='message/send',
        params=types_v03.MessageSendParams(
            message=types_v03.Message(
                message_id='msg-1',
                role='user',
                parts=[types_v03.TextPart(text='Hello')],
            )
        ),
    )

    mock_core_handler.on_message_send.return_value = V10Message(
        message_id='msg-2', role=2, parts=[V10Part(text='Hi there')]
    )

    result = await v03_handler.on_message_send(v03_req, mock_context)

    assert isinstance(result, types_v03.Message)
    assert result.message_id == 'msg-2'
    assert result.role == 'agent'
    assert len(result.parts) == 1
    assert result.parts[0].root.text == 'Hi there'


@pytest.mark.anyio
async def test_on_message_send_returns_task(
    v03_handler, mock_core_handler, mock_context
):
    v03_req = types_v03.SendMessageRequest(
        id='req-1',
        method='message/send',
        params=types_v03.MessageSendParams(
            message=types_v03.Message(
                message_id='msg-1',
                role='user',
                parts=[types_v03.TextPart(text='Hello')],
            )
        ),
    )

    mock_core_handler.on_message_send.return_value = V10Task(
        id='task-1',
        context_id='ctx-1',
        status=V10TaskStatus(state=V10TaskState.TASK_STATE_WORKING),
    )

    result = await v03_handler.on_message_send(v03_req, mock_context)

    assert isinstance(result, types_v03.Task)
    assert result.id == 'task-1'
    assert result.context_id == 'ctx-1'
    assert result.status.state == 'working'


@pytest.mark.anyio
async def test_on_message_send_stream(
    v03_handler, mock_core_handler, mock_context
):
    v03_req = types_v03.SendMessageRequest(
        id='req-1',
        method='message/send',
        params=types_v03.MessageSendParams(
            message=types_v03.Message(
                message_id='msg-1',
                role='user',
                parts=[types_v03.TextPart(text='Hello')],
            )
        ),
    )

    async def mock_stream(*args, **kwargs):
        yield V10Message(
            message_id='msg-2',
            role=2,
            parts=[V10Part(text='Chunk 1')],
        )
        yield V10Message(
            message_id='msg-2',
            role=2,
            parts=[V10Part(text='Chunk 2')],
        )

    mock_core_handler.on_message_send_stream.side_effect = mock_stream

    results = [
        chunk
        async for chunk in v03_handler.on_message_send_stream(
            v03_req, mock_context
        )
    ]

    assert len(results) == 2
    assert all(
        isinstance(r, types_v03.SendStreamingMessageSuccessResponse)
        for r in results
    )
    assert results[0].result.parts[0].root.text == 'Chunk 1'
    assert results[1].result.parts[0].root.text == 'Chunk 2'


@pytest.mark.anyio
async def test_on_cancel_task(v03_handler, mock_core_handler, mock_context):
    v03_req = types_v03.CancelTaskRequest(
        id='req-1',
        method='tasks/cancel',
        params=types_v03.TaskIdParams(id='task-1'),
    )

    mock_core_handler.on_cancel_task.return_value = V10Task(
        id='task-1',
        status=V10TaskStatus(state=V10TaskState.TASK_STATE_CANCELED),
    )

    result = await v03_handler.on_cancel_task(v03_req, mock_context)

    assert isinstance(result, types_v03.Task)
    assert result.id == 'task-1'
    assert result.status.state == 'canceled'


@pytest.mark.anyio
async def test_on_cancel_task_not_found(
    v03_handler, mock_core_handler, mock_context
):
    v03_req = types_v03.CancelTaskRequest(
        id='req-1',
        method='tasks/cancel',
        params=types_v03.TaskIdParams(id='task-1'),
    )

    mock_core_handler.on_cancel_task.return_value = None

    with pytest.raises(TaskNotFoundError):
        await v03_handler.on_cancel_task(v03_req, mock_context)


@pytest.mark.anyio
async def test_on_subscribe_to_task(
    v03_handler, mock_core_handler, mock_context
):
    v03_req = types_v03.TaskResubscriptionRequest(
        id='req-1',
        method='tasks/resubscribe',
        params=types_v03.TaskIdParams(id='task-1'),
    )

    async def mock_stream(*args, **kwargs):
        yield V10Message(
            message_id='msg-2',
            role=2,
            parts=[V10Part(text='Update 1')],
        )

    mock_core_handler.on_subscribe_to_task.side_effect = mock_stream

    results = [
        chunk
        async for chunk in v03_handler.on_subscribe_to_task(
            v03_req, mock_context
        )
    ]

    assert len(results) == 1
    assert results[0].result.parts[0].root.text == 'Update 1'


@pytest.mark.anyio
async def test_on_get_task_push_notification_config(
    v03_handler, mock_core_handler, mock_context
):
    v03_req = types_v03.GetTaskPushNotificationConfigRequest(
        id='req-1',
        method='tasks/pushNotificationConfig/get',
        params=types_v03.GetTaskPushNotificationConfigParams(
            id='task-1', push_notification_config_id='push-1'
        ),
    )

    mock_core_handler.on_get_task_push_notification_config.return_value = (
        V10PushConfig(id='push-1', url='http://example.com')
    )

    result = await v03_handler.on_get_task_push_notification_config(
        v03_req, mock_context
    )

    assert isinstance(result, types_v03.TaskPushNotificationConfig)
    assert result.push_notification_config.id == 'push-1'
    assert result.push_notification_config.url == 'http://example.com'


@pytest.mark.anyio
async def test_on_create_task_push_notification_config(
    v03_handler, mock_core_handler, mock_context
):
    v03_req = types_v03.SetTaskPushNotificationConfigRequest(
        id='req-1',
        method='tasks/pushNotificationConfig/set',
        params=types_v03.TaskPushNotificationConfig(
            task_id='task-1',
            push_notification_config=types_v03.PushNotificationConfig(
                url='http://example.com'
            ),
        ),
    )

    mock_core_handler.on_create_task_push_notification_config.return_value = (
        V10PushConfig(id='push-1', url='http://example.com')
    )

    result = await v03_handler.on_create_task_push_notification_config(
        v03_req, mock_context
    )

    assert isinstance(result, types_v03.TaskPushNotificationConfig)
    assert result.push_notification_config.id == 'push-1'
    assert result.push_notification_config.url == 'http://example.com'


@pytest.mark.anyio
async def test_on_get_task(v03_handler, mock_core_handler, mock_context):
    v03_req = types_v03.GetTaskRequest(
        id='req-1',
        method='tasks/get',
        params=types_v03.TaskQueryParams(id='task-1'),
    )

    mock_core_handler.on_get_task.return_value = V10Task(
        id='task-1', status=V10TaskStatus(state=V10TaskState.TASK_STATE_WORKING)
    )

    result = await v03_handler.on_get_task(v03_req, mock_context)

    assert isinstance(result, types_v03.Task)
    assert result.id == 'task-1'
    assert result.status.state == 'working'


@pytest.mark.anyio
async def test_on_get_task_not_found(
    v03_handler, mock_core_handler, mock_context
):
    v03_req = types_v03.GetTaskRequest(
        id='req-1',
        method='tasks/get',
        params=types_v03.TaskQueryParams(id='task-1'),
    )

    mock_core_handler.on_get_task.return_value = None

    with pytest.raises(TaskNotFoundError):
        await v03_handler.on_get_task(v03_req, mock_context)


@pytest.mark.anyio
async def test_on_list_task_push_notification_configs(
    v03_handler, mock_core_handler, mock_context
):
    v03_req = types_v03.ListTaskPushNotificationConfigRequest(
        id='req-1',
        method='tasks/pushNotificationConfig/list',
        params=types_v03.ListTaskPushNotificationConfigParams(id='task-1'),
    )

    mock_core_handler.on_list_task_push_notification_configs.return_value = (
        V10ListPushConfigsResp(
            configs=[
                V10PushConfig(id='push-1', url='http://example1.com'),
                V10PushConfig(id='push-2', url='http://example2.com'),
            ]
        )
    )

    result = await v03_handler.on_list_task_push_notification_configs(
        v03_req, mock_context
    )

    assert isinstance(result, list)
    assert len(result) == 2
    assert result[0].push_notification_config.id == 'push-1'
    assert result[1].push_notification_config.id == 'push-2'


@pytest.mark.anyio
async def test_on_delete_task_push_notification_config(
    v03_handler, mock_core_handler, mock_context
):
    v03_req = types_v03.DeleteTaskPushNotificationConfigRequest(
        id='req-1',
        method='tasks/pushNotificationConfig/delete',
        params=types_v03.DeleteTaskPushNotificationConfigParams(
            id='task-1', push_notification_config_id='push-1'
        ),
    )

    mock_core_handler.on_delete_task_push_notification_config.return_value = (
        None
    )

    result = await v03_handler.on_delete_task_push_notification_config(
        v03_req, mock_context
    )

    assert result is None
    mock_core_handler.on_delete_task_push_notification_config.assert_called_once()


@pytest.mark.anyio
async def test_on_get_extended_agent_card_success(
    v03_handler, mock_core_handler, mock_context
):
    v03_req = types_v03.GetAuthenticatedExtendedCardRequest(id=0)

    mock_core_handler.on_get_extended_agent_card.return_value = AgentCard(
        name='Extended Agent',
        description='An extended test agent',
        version='1.0.0',
        supported_interfaces=[
            AgentInterface(
                url='http://jsonrpc.v03.com',
                protocol_version='0.3',
            )
        ],
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=True,
            extended_agent_card=True,
        ),
    )

    result = await v03_handler.on_get_extended_agent_card(v03_req, mock_context)

    assert isinstance(result, types_v03.AgentCard)
    assert result.name == 'Extended Agent'
    assert result.capabilities.streaming is True
    assert result.capabilities.push_notifications is True
    mock_core_handler.on_get_extended_agent_card.assert_called_once()
