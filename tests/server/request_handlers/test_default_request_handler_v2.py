import asyncio
import logging
import time
import uuid

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from a2a.auth.user import UnauthenticatedUser, User
from a2a.server.agent_execution import (
    RequestContextBuilder,
    AgentExecutor,
    RequestContext,
    SimpleRequestContextBuilder,
)
from a2a.server.agent_execution.active_task_registry import ActiveTaskRegistry
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue, InMemoryQueueManager, QueueManager
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.tasks import (
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
    PushNotificationConfigStore,
    PushNotificationSender,
    TaskStore,
    TaskUpdater,
)
from a2a.types import (
    InternalError,
    InvalidAgentResponseError,
    InvalidParamsError,
    TaskNotFoundError,
    PushNotificationNotSupportedError,
)
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    Artifact,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.helpers.proto_helpers import (
    new_text_message,
    new_task_from_user_message,
)


def create_default_agent_card():
    """Provides a standard AgentCard with streaming and push notifications enabled for tests."""
    return AgentCard(
        name='test_agent',
        version='1.0',
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
    )


class MockAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        if context.message:
            await event_queue.enqueue_event(
                new_task_from_user_message(context.message)
            )

        task_updater = TaskUpdater(
            event_queue,
            str(context.task_id or ''),
            str(context.context_id or ''),
        )

        async for i in self._run():
            parts = [Part(text=f'Event {i}')]
            try:
                await task_updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=task_updater.new_agent_message(parts),
                )
            except RuntimeError:
                break

    async def _run(self):
        for i in range(1000000):
            yield i

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


def create_sample_task(
    task_id='task1',
    status_state=TaskState.TASK_STATE_SUBMITTED,
    context_id='ctx1',
) -> Task:
    return Task(
        id=task_id, context_id=context_id, status=TaskStatus(state=status_state)
    )


def create_server_call_context() -> ServerCallContext:
    return ServerCallContext(user=UnauthenticatedUser())


def test_init_default_dependencies():
    """Test that default dependencies are created if not provided."""
    agent_executor = MockAgentExecutor()
    task_store = InMemoryTaskStore()
    handler = DefaultRequestHandlerV2(
        agent_executor=agent_executor,
        task_store=task_store,
        agent_card=create_default_agent_card(),
    )
    assert isinstance(handler._active_task_registry, ActiveTaskRegistry)
    assert isinstance(
        handler._request_context_builder, SimpleRequestContextBuilder
    )
    assert handler._push_config_store is None
    assert handler._push_sender is None
    assert (
        handler._request_context_builder._should_populate_referred_tasks
        is False
    )
    assert handler._request_context_builder._task_store == task_store


@pytest.mark.asyncio
async def test_on_get_task_not_found():
    """Test on_get_task when task_store.get returns None."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        agent_card=create_default_agent_card(),
    )
    params = GetTaskRequest(id='non_existent_task')
    context = create_server_call_context()
    with pytest.raises(TaskNotFoundError):
        await request_handler.on_get_task(params, context)
    mock_task_store.get.assert_awaited_once_with('non_existent_task', context)


@pytest.mark.asyncio
async def test_on_list_tasks_success():
    """Test on_list_tasks successfully returns a page of tasks ."""
    mock_task_store = AsyncMock(spec=TaskStore)
    task2 = create_sample_task(task_id='task2')
    task2.artifacts.extend(
        [
            Artifact(
                artifact_id='artifact1',
                parts=[Part(text='Hello world!')],
                name='conversion_result',
            )
        ]
    )
    mock_page = ListTasksResponse(
        tasks=[create_sample_task(task_id='task1'), task2],
        next_page_token='123',  # noqa: S106
    )
    mock_task_store.list.return_value = mock_page
    request_handler = DefaultRequestHandlerV2(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=mock_task_store,
        agent_card=create_default_agent_card(),
    )
    params = ListTasksRequest(include_artifacts=True, page_size=10)
    context = create_server_call_context()
    result = await request_handler.on_list_tasks(params, context)
    mock_task_store.list.assert_awaited_once_with(params, context)
    assert result.tasks == mock_page.tasks
    assert result.next_page_token == mock_page.next_page_token


@pytest.mark.asyncio
async def test_on_list_tasks_excludes_artifacts():
    """Test on_list_tasks excludes artifacts from returned tasks."""
    mock_task_store = AsyncMock(spec=TaskStore)
    task2 = create_sample_task(task_id='task2')
    task2.artifacts.extend(
        [
            Artifact(
                artifact_id='artifact1',
                parts=[Part(text='Hello world!')],
                name='conversion_result',
            )
        ]
    )
    mock_page = ListTasksResponse(
        tasks=[create_sample_task(task_id='task1'), task2],
        next_page_token='123',  # noqa: S106
    )
    mock_task_store.list.return_value = mock_page
    request_handler = DefaultRequestHandlerV2(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=mock_task_store,
        agent_card=create_default_agent_card(),
    )
    params = ListTasksRequest(include_artifacts=False, page_size=10)
    context = create_server_call_context()
    result = await request_handler.on_list_tasks(params, context)
    assert not result.tasks[1].artifacts


@pytest.mark.asyncio
async def test_on_list_tasks_applies_history_length():
    """Test on_list_tasks applies history length filter."""
    mock_task_store = AsyncMock(spec=TaskStore)
    history = [
        new_text_message('Hello 1!'),
        new_text_message('Hello 2!'),
    ]
    task2 = create_sample_task(task_id='task2')
    task2.history.extend(history)
    mock_page = ListTasksResponse(
        tasks=[create_sample_task(task_id='task1'), task2],
        next_page_token='123',  # noqa: S106
    )
    mock_task_store.list.return_value = mock_page
    request_handler = DefaultRequestHandlerV2(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=mock_task_store,
        agent_card=create_default_agent_card(),
    )
    params = ListTasksRequest(history_length=1, page_size=10)
    context = create_server_call_context()
    result = await request_handler.on_list_tasks(params, context)
    assert result.tasks[1].history == [history[1]]


@pytest.mark.asyncio
async def test_on_list_tasks_negative_history_length_error():
    """Test on_list_tasks raises error for negative history length."""
    mock_task_store = AsyncMock(spec=TaskStore)
    request_handler = DefaultRequestHandlerV2(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=mock_task_store,
        agent_card=create_default_agent_card(),
    )
    params = ListTasksRequest(history_length=-1, page_size=10)
    context = create_server_call_context()
    with pytest.raises(InvalidParamsError) as exc_info:
        await request_handler.on_list_tasks(params, context)
    assert 'history length must be non-negative' in exc_info.value.message


@pytest.mark.asyncio
async def test_on_cancel_task_task_not_found():
    """Test on_cancel_task when the task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        agent_card=create_default_agent_card(),
    )
    params = CancelTaskRequest(id='task_not_found_for_cancel')
    context = create_server_call_context()
    with pytest.raises(TaskNotFoundError):
        await request_handler.on_cancel_task(params, context)
    mock_task_store.get.assert_awaited_once_with(
        'task_not_found_for_cancel', context
    )


class HelloAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        task = context.current_task
        if not task:
            assert context.message is not None, (
                'A message is required to create a new task'
            )
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        try:
            parts = [Part(text='I am working')]
            await updater.update_status(
                TaskState.TASK_STATE_WORKING,
                message=updater.new_agent_message(parts),
            )
        except Exception as e:  # noqa: BLE001
            logging.warning('Error: %s', e)
            return
        await updater.add_artifact(
            [Part(text='Hello world!')], name='conversion_result'
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


@pytest.mark.asyncio
async def test_on_get_task_limit_history():
    task_store = InMemoryTaskStore()
    push_store = InMemoryPushNotificationConfigStore()
    request_handler = DefaultRequestHandlerV2(
        agent_executor=HelloAgentExecutor(),
        task_store=task_store,
        push_config_store=push_store,
        agent_card=create_default_agent_card(),
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER, message_id='msg_push', parts=[Part(text='Hi')]
        ),
        configuration=SendMessageConfiguration(
            accepted_output_modes=['text/plain']
        ),
    )
    result = await request_handler.on_message_send(
        params, create_server_call_context()
    )
    assert result is not None
    assert isinstance(result, Task)
    get_task_result = await request_handler.on_get_task(
        GetTaskRequest(id=result.id, history_length=1),
        create_server_call_context(),
    )
    assert get_task_result is not None
    assert isinstance(get_task_result, Task)
    assert (
        get_task_result.history is not None
        and len(get_task_result.history) == 1
    )


async def wait_until(predicate, timeout: float = 0.2, interval: float = 0.0):
    """Await until predicate() is True or timeout elapses."""
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while True:
        if predicate():
            return
        if loop.time() >= end:
            raise AssertionError('condition not met within timeout')
        await asyncio.sleep(interval)


@pytest.mark.asyncio
async def test_set_task_push_notification_config_no_notifier():
    """Test on_create_task_push_notification_config when _push_config_store is None."""
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=AsyncMock(spec=TaskStore),
        push_config_store=None,
        agent_card=create_default_agent_card(),
    )
    params = TaskPushNotificationConfig(
        task_id='task1', url='http://example.com'
    )
    with pytest.raises(PushNotificationNotSupportedError):
        await request_handler.on_create_task_push_notification_config(
            params, create_server_call_context()
        )


@pytest.mark.asyncio
async def test_set_task_push_notification_config_task_not_found():
    """Test on_create_task_push_notification_config when task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None
    mock_push_store = AsyncMock(spec=PushNotificationConfigStore)
    mock_push_sender = AsyncMock(spec=PushNotificationSender)
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=mock_push_store,
        push_sender=mock_push_sender,
        agent_card=create_default_agent_card(),
    )
    params = TaskPushNotificationConfig(
        task_id='non_existent_task', url='http://example.com'
    )
    context = create_server_call_context()
    with pytest.raises(TaskNotFoundError):
        await request_handler.on_create_task_push_notification_config(
            params, context
        )
    mock_task_store.get.assert_awaited_once_with('non_existent_task', context)
    mock_push_store.set_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_task_push_notification_config_no_store():
    """Test on_get_task_push_notification_config when _push_config_store is None."""
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=AsyncMock(spec=TaskStore),
        push_config_store=None,
        agent_card=create_default_agent_card(),
    )
    params = GetTaskPushNotificationConfigRequest(
        task_id='task1', id='task_push_notification_config'
    )
    with pytest.raises(PushNotificationNotSupportedError):
        await request_handler.on_get_task_push_notification_config(
            params, create_server_call_context()
        )


@pytest.mark.asyncio
async def test_get_task_push_notification_config_task_not_found():
    """Test on_get_task_push_notification_config when task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None
    mock_push_store = AsyncMock(spec=PushNotificationConfigStore)
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=mock_push_store,
        agent_card=create_default_agent_card(),
    )
    params = GetTaskPushNotificationConfigRequest(
        task_id='non_existent_task', id='task_push_notification_config'
    )
    context = create_server_call_context()
    with pytest.raises(TaskNotFoundError):
        await request_handler.on_get_task_push_notification_config(
            params, context
        )
    mock_task_store.get.assert_awaited_once_with('non_existent_task', context)
    mock_push_store.get_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_task_push_notification_config_info_not_found():
    """Test on_get_task_push_notification_config when push_config_store.get_info returns None."""
    mock_task_store = AsyncMock(spec=TaskStore)
    sample_task = create_sample_task(task_id='non_existent_task')
    mock_task_store.get.return_value = sample_task
    mock_push_store = AsyncMock(spec=PushNotificationConfigStore)
    mock_push_store.get_info.return_value = None
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=mock_push_store,
        agent_card=create_default_agent_card(),
    )
    params = GetTaskPushNotificationConfigRequest(
        task_id='non_existent_task', id='task_push_notification_config'
    )
    context = create_server_call_context()
    with pytest.raises(TaskNotFoundError):
        await request_handler.on_get_task_push_notification_config(
            params, context
        )
    mock_task_store.get.assert_awaited_once_with('non_existent_task', context)
    mock_push_store.get_info.assert_awaited_once_with(
        'non_existent_task', context
    )


@pytest.mark.asyncio
async def test_get_task_push_notification_config_info_with_config():
    """Test on_get_task_push_notification_config with valid push config id"""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = Task(id='task_1', context_id='ctx_1')
    push_store = InMemoryPushNotificationConfigStore()
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=create_default_agent_card(),
    )
    set_config_params = TaskPushNotificationConfig(
        task_id='task_1', id='config_id', url='http://1.example.com'
    )
    context = create_server_call_context()
    await request_handler.on_create_task_push_notification_config(
        set_config_params, context
    )
    params = GetTaskPushNotificationConfigRequest(
        task_id='task_1', id='config_id'
    )
    result: TaskPushNotificationConfig = (
        await request_handler.on_get_task_push_notification_config(
            params, context
        )
    )
    assert result is not None
    assert result.task_id == 'task_1'
    assert result.url == set_config_params.url
    assert result.id == 'config_id'


@pytest.mark.asyncio
async def test_get_task_push_notification_config_info_with_config_no_id():
    """Test on_get_task_push_notification_config with no push config id"""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = Task(id='task_1', context_id='ctx_1')
    push_store = InMemoryPushNotificationConfigStore()
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=create_default_agent_card(),
    )
    set_config_params = TaskPushNotificationConfig(
        task_id='task_1', url='http://1.example.com'
    )
    await request_handler.on_create_task_push_notification_config(
        set_config_params, create_server_call_context()
    )
    params = GetTaskPushNotificationConfigRequest(task_id='task_1', id='task_1')
    result: TaskPushNotificationConfig = (
        await request_handler.on_get_task_push_notification_config(
            params, create_server_call_context()
        )
    )
    assert result is not None
    assert result.task_id == 'task_1'
    assert result.url == set_config_params.url
    assert result.id == 'task_1'


@pytest.mark.asyncio
async def test_on_subscribe_to_task_task_not_found():
    """Test on_subscribe_to_task when the task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        agent_card=create_default_agent_card(),
    )
    params = SubscribeToTaskRequest(id='resub_task_not_found')
    context = create_server_call_context()
    with pytest.raises(TaskNotFoundError):
        async for _ in request_handler.on_subscribe_to_task(params, context):
            pass
    mock_task_store.get.assert_awaited_once_with(
        'resub_task_not_found', context
    )


@pytest.mark.asyncio
async def test_on_message_send_stream():
    request_handler = DefaultRequestHandlerV2(
        MockAgentExecutor(),
        InMemoryTaskStore(),
        create_default_agent_card(),
    )
    message_params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg-123',
            parts=[Part(text='How are you?')],
        )
    )

    async def consume_stream():
        events = []
        async for event in request_handler.on_message_send_stream(
            message_params, create_server_call_context()
        ):
            events.append(event)
            if len(events) >= 3:
                break
        return events

    start = time.perf_counter()
    events = await consume_stream()
    elapsed = time.perf_counter() - start
    assert len(events) == 3
    assert elapsed < 0.5
    task, event0, event1 = events
    assert isinstance(task, Task)
    assert task.history[0].parts[0].text == 'How are you?'

    assert isinstance(event0, TaskStatusUpdateEvent)
    assert event0.status.message.parts[0].text == 'Event 0'

    assert isinstance(event1, TaskStatusUpdateEvent)
    assert event1.status.message.parts[0].text == 'Event 1'


@pytest.mark.asyncio
async def test_list_task_push_notification_config_no_store():
    """Test on_list_task_push_notification_configs when _push_config_store is None."""
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=AsyncMock(spec=TaskStore),
        push_config_store=None,
        agent_card=create_default_agent_card(),
    )
    params = ListTaskPushNotificationConfigsRequest(task_id='task1')
    with pytest.raises(PushNotificationNotSupportedError):
        await request_handler.on_list_task_push_notification_configs(
            params, create_server_call_context()
        )


@pytest.mark.asyncio
async def test_list_task_push_notification_config_task_not_found():
    """Test on_list_task_push_notification_configs when task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None
    mock_push_store = AsyncMock(spec=PushNotificationConfigStore)
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=mock_push_store,
        agent_card=create_default_agent_card(),
    )
    params = ListTaskPushNotificationConfigsRequest(task_id='non_existent_task')
    context = create_server_call_context()
    with pytest.raises(TaskNotFoundError):
        await request_handler.on_list_task_push_notification_configs(
            params, context
        )
    mock_task_store.get.assert_awaited_once_with('non_existent_task', context)
    mock_push_store.get_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_no_task_push_notification_config_info():
    """Test on_get_task_push_notification_config when push_config_store.get_info returns []"""
    mock_task_store = AsyncMock(spec=TaskStore)
    sample_task = create_sample_task(task_id='non_existent_task')
    mock_task_store.get.return_value = sample_task
    push_store = InMemoryPushNotificationConfigStore()
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=create_default_agent_card(),
    )
    params = ListTaskPushNotificationConfigsRequest(task_id='non_existent_task')
    result = await request_handler.on_list_task_push_notification_configs(
        params, create_server_call_context()
    )
    assert result.configs == []


@pytest.mark.asyncio
async def test_list_task_push_notification_config_info_with_config():
    """Test on_list_task_push_notification_configs with push config+id"""
    mock_task_store = AsyncMock(spec=TaskStore)
    sample_task = create_sample_task(task_id='non_existent_task')
    mock_task_store.get.return_value = sample_task
    push_config1 = TaskPushNotificationConfig(
        task_id='task_1', id='config_1', url='http://example.com'
    )
    push_config2 = TaskPushNotificationConfig(
        task_id='task_1', id='config_2', url='http://example.com'
    )
    push_store = InMemoryPushNotificationConfigStore()
    context = create_server_call_context()
    await push_store.set_info('task_1', push_config1, context)
    await push_store.set_info('task_1', push_config2, context)
    await push_store.set_info('task_2', push_config1, context)
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=create_default_agent_card(),
    )
    params = ListTaskPushNotificationConfigsRequest(task_id='task_1')
    result = await request_handler.on_list_task_push_notification_configs(
        params, create_server_call_context()
    )
    assert len(result.configs) == 2
    assert result.configs[0].task_id == 'task_1'
    assert result.configs[0] == push_config1
    assert result.configs[1].task_id == 'task_1'
    assert result.configs[1] == push_config2


@pytest.mark.asyncio
async def test_list_task_push_notification_config_info_with_config_and_no_id():
    """Test on_list_task_push_notification_configs with no push config id"""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = Task(id='task_1', context_id='ctx_1')
    push_store = InMemoryPushNotificationConfigStore()
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=create_default_agent_card(),
    )
    set_config_params1 = TaskPushNotificationConfig(
        task_id='task_1', url='http://1.example.com'
    )
    await request_handler.on_create_task_push_notification_config(
        set_config_params1, create_server_call_context()
    )
    set_config_params2 = TaskPushNotificationConfig(
        task_id='task_1', url='http://2.example.com'
    )
    await request_handler.on_create_task_push_notification_config(
        set_config_params2, create_server_call_context()
    )
    params = ListTaskPushNotificationConfigsRequest(task_id='task_1')
    result = await request_handler.on_list_task_push_notification_configs(
        params, create_server_call_context()
    )
    assert len(result.configs) == 1
    assert result.configs[0].task_id == 'task_1'
    assert result.configs[0].url == set_config_params2.url
    assert result.configs[0].id == 'task_1'


@pytest.mark.asyncio
async def test_delete_task_push_notification_config_no_store():
    """Test on_delete_task_push_notification_config when _push_config_store is None."""
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=AsyncMock(spec=TaskStore),
        push_config_store=None,
        agent_card=create_default_agent_card(),
    )
    params = DeleteTaskPushNotificationConfigRequest(
        task_id='task1', id='config1'
    )
    with pytest.raises(PushNotificationNotSupportedError) as exc_info:
        await request_handler.on_delete_task_push_notification_config(
            params, create_server_call_context()
        )
    assert isinstance(exc_info.value, PushNotificationNotSupportedError)


@pytest.mark.asyncio
async def test_delete_task_push_notification_config_task_not_found():
    """Test on_delete_task_push_notification_config when task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None
    mock_push_store = AsyncMock(spec=PushNotificationConfigStore)
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=mock_push_store,
        agent_card=create_default_agent_card(),
    )
    params = DeleteTaskPushNotificationConfigRequest(
        task_id='non_existent_task', id='config1'
    )
    context = create_server_call_context()
    with pytest.raises(TaskNotFoundError):
        await request_handler.on_delete_task_push_notification_config(
            params, context
        )
    mock_task_store.get.assert_awaited_once_with('non_existent_task', context)
    mock_push_store.get_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_no_task_push_notification_config_info():
    """Test on_delete_task_push_notification_config without config info"""
    mock_task_store = AsyncMock(spec=TaskStore)
    sample_task = create_sample_task(task_id='task_1')
    mock_task_store.get.return_value = sample_task
    push_store = InMemoryPushNotificationConfigStore()
    await push_store.set_info(
        'task_2',
        TaskPushNotificationConfig(id='config_1', url='http://example.com'),
        create_server_call_context(),
    )
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=create_default_agent_card(),
    )
    params = DeleteTaskPushNotificationConfigRequest(
        task_id='task1', id='config_non_existant'
    )
    result = await request_handler.on_delete_task_push_notification_config(
        params, create_server_call_context()
    )
    assert result is None
    params = DeleteTaskPushNotificationConfigRequest(
        task_id='task2', id='config_non_existant'
    )
    result = await request_handler.on_delete_task_push_notification_config(
        params, create_server_call_context()
    )
    assert result is None


@pytest.mark.asyncio
async def test_delete_task_push_notification_config_info_with_config():
    """Test on_list_task_push_notification_configs with push config+id"""
    mock_task_store = AsyncMock(spec=TaskStore)
    sample_task = create_sample_task(task_id='non_existent_task')
    mock_task_store.get.return_value = sample_task
    push_config1 = TaskPushNotificationConfig(
        task_id='task_1', id='config_1', url='http://example.com'
    )
    push_config2 = TaskPushNotificationConfig(
        task_id='task_1', id='config_2', url='http://example.com'
    )
    push_store = InMemoryPushNotificationConfigStore()
    context = create_server_call_context()
    await push_store.set_info('task_1', push_config1, context)
    await push_store.set_info('task_1', push_config2, context)
    await push_store.set_info('task_2', push_config1, context)
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=create_default_agent_card(),
    )
    params = DeleteTaskPushNotificationConfigRequest(
        task_id='task_1', id='config_1'
    )
    result1 = await request_handler.on_delete_task_push_notification_config(
        params, create_server_call_context()
    )
    assert result1 is None
    result2 = await request_handler.on_list_task_push_notification_configs(
        ListTaskPushNotificationConfigsRequest(task_id='task_1'),
        create_server_call_context(),
    )
    assert len(result2.configs) == 1
    assert result2.configs[0].task_id == 'task_1'
    assert result2.configs[0] == push_config2


@pytest.mark.asyncio
async def test_delete_task_push_notification_config_info_with_config_and_no_id():
    """Test on_list_task_push_notification_configs with no push config id"""
    mock_task_store = AsyncMock(spec=TaskStore)
    sample_task = create_sample_task(task_id='non_existent_task')
    mock_task_store.get.return_value = sample_task
    push_config = TaskPushNotificationConfig(url='http://example.com')
    push_store = InMemoryPushNotificationConfigStore()
    context = create_server_call_context()
    await push_store.set_info('task_1', push_config, context)
    await push_store.set_info('task_1', push_config, context)
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=create_default_agent_card(),
    )
    params = DeleteTaskPushNotificationConfigRequest(
        task_id='task_1', id='task_1'
    )
    result = await request_handler.on_delete_task_push_notification_config(
        params, create_server_call_context()
    )
    assert result is None
    result2 = await request_handler.on_list_task_push_notification_configs(
        ListTaskPushNotificationConfigsRequest(task_id='task_1'),
        create_server_call_context(),
    )
    assert len(result2.configs) == 0


TERMINAL_TASK_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_REJECTED,
}


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal_state', TERMINAL_TASK_STATES)
async def test_on_message_send_task_in_terminal_state(terminal_state):
    """Test on_message_send when task is already in a terminal state."""
    state_name = TaskState.Name(terminal_state)
    task_id = f'terminal_task_{state_name}'
    terminal_task = create_sample_task(
        task_id=task_id, status_state=terminal_state
    )
    mock_task_store = AsyncMock(spec=TaskStore)
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        agent_card=create_default_agent_card(),
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_terminal',
            parts=[Part(text='hello')],
            task_id=task_id,
        )
    )
    with (
        patch(
            'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
            return_value=terminal_task,
        ),
        pytest.raises(InvalidParamsError) as exc_info,
    ):
        await request_handler.on_message_send(
            params, create_server_call_context()
        )
    assert (
        f'Task {task_id} is in terminal state: {terminal_state}'
        in exc_info.value.message
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal_state', TERMINAL_TASK_STATES)
async def test_on_message_send_stream_task_in_terminal_state(terminal_state):
    """Test on_message_send_stream when task is already in a terminal state."""
    state_name = TaskState.Name(terminal_state)
    task_id = f'terminal_stream_task_{state_name}'
    terminal_task = create_sample_task(
        task_id=task_id, status_state=terminal_state
    )
    mock_task_store = AsyncMock(spec=TaskStore)
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        agent_card=create_default_agent_card(),
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_terminal_stream',
            parts=[Part(text='hello')],
            task_id=task_id,
        )
    )
    with (
        patch(
            'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
            return_value=terminal_task,
        ),
        pytest.raises(InvalidParamsError) as exc_info,
    ):
        async for _ in request_handler.on_message_send_stream(
            params, create_server_call_context()
        ):
            pass
    assert (
        f'Task {task_id} is in terminal state: {terminal_state}'
        in exc_info.value.message
    )


@pytest.mark.asyncio
async def test_on_message_send_task_id_provided_but_task_not_found():
    """Test on_message_send when task_id is provided but task doesn't exist."""
    pass


@pytest.mark.asyncio
async def test_on_message_send_stream_task_id_provided_but_task_not_found():
    """Test on_message_send_stream when task_id is provided but task doesn't exist."""
    pass


class HelloWorldAgentExecutor(AgentExecutor):
    """Test Agent Implementation."""

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        if context.message:
            await event_queue.enqueue_event(
                new_task_from_user_message(context.message)
            )
        updater = TaskUpdater(
            event_queue,
            task_id=context.task_id or str(uuid.uuid4()),
            context_id=context.context_id or str(uuid.uuid4()),
        )
        await updater.update_status(TaskState.TASK_STATE_WORKING)
        await updater.complete()

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise NotImplementedError('cancel not supported')


@pytest.mark.asyncio
@pytest.mark.timeout(1)
async def test_on_message_send_error_does_not_hang():
    """Test that if the consumer raises an exception during blocking wait, the producer is cancelled and no deadlock occurs."""
    agent = HelloWorldAgentExecutor()
    task_store = AsyncMock(spec=TaskStore)
    task_store.get.return_value = None
    task_store.save.side_effect = RuntimeError('This is an Error!')

    request_handler = DefaultRequestHandlerV2(
        agent_executor=agent,
        task_store=task_store,
        agent_card=create_default_agent_card(),
    )

    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_error_blocking',
            parts=[Part(text='Test message')],
        )
    )
    with pytest.raises(RuntimeError, match='This is an Error!'):
        await request_handler.on_message_send(
            params, create_server_call_context()
        )


@pytest.mark.asyncio
async def test_on_get_task_negative_history_length_error():
    """Test on_get_task raises error for negative history length."""
    mock_task_store = AsyncMock(spec=TaskStore)
    request_handler = DefaultRequestHandlerV2(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=mock_task_store,
        agent_card=create_default_agent_card(),
    )
    params = GetTaskRequest(id='task1', history_length=-1)
    context = create_server_call_context()
    with pytest.raises(InvalidParamsError) as exc_info:
        await request_handler.on_get_task(params, context)
    assert 'history length must be non-negative' in exc_info.value.message


@pytest.mark.asyncio
async def test_on_list_tasks_page_size_too_small():
    """Test on_list_tasks raises error for page_size < 1."""
    mock_task_store = AsyncMock(spec=TaskStore)
    request_handler = DefaultRequestHandlerV2(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=mock_task_store,
        agent_card=create_default_agent_card(),
    )
    params = ListTasksRequest(page_size=0)
    context = create_server_call_context()
    with pytest.raises(InvalidParamsError) as exc_info:
        await request_handler.on_list_tasks(params, context)
    assert 'minimum page size is 1' in exc_info.value.message


@pytest.mark.asyncio
async def test_on_list_tasks_page_size_too_large():
    """Test on_list_tasks raises error for page_size > 100."""
    mock_task_store = AsyncMock(spec=TaskStore)
    request_handler = DefaultRequestHandlerV2(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=mock_task_store,
        agent_card=create_default_agent_card(),
    )
    params = ListTasksRequest(page_size=101)
    context = create_server_call_context()
    with pytest.raises(InvalidParamsError) as exc_info:
        await request_handler.on_list_tasks(params, context)
    assert 'maximum page size is 100' in exc_info.value.message


@pytest.mark.asyncio
async def test_on_message_send_negative_history_length_error():
    """Test on_message_send raises error for negative history length in configuration."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)
    request_handler = DefaultRequestHandlerV2(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        agent_card=create_default_agent_card(),
    )
    message_config = SendMessageConfiguration(
        history_length=-1, accepted_output_modes=['text/plain']
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER, message_id='msg1', parts=[Part(text='hello')]
        ),
        configuration=message_config,
    )
    context = create_server_call_context()
    with pytest.raises(InvalidParamsError) as exc_info:
        await request_handler.on_message_send(params, context)
    assert 'history length must be non-negative' in exc_info.value.message


@pytest.mark.asyncio
async def test_on_message_send_limit_history():
    task_store = InMemoryTaskStore()
    push_store = InMemoryPushNotificationConfigStore()

    request_handler = DefaultRequestHandlerV2(
        agent_executor=HelloAgentExecutor(),
        task_store=task_store,
        push_config_store=push_store,
        agent_card=create_default_agent_card(),
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_push',
            parts=[Part(text='Hi')],
        ),
        configuration=SendMessageConfiguration(
            accepted_output_modes=['text/plain'],
            history_length=1,
        ),
    )

    context = create_server_call_context()
    result = await request_handler.on_message_send(params, context)

    # verify that history_length is honored
    assert result is not None
    assert isinstance(result, Task)
    assert result.history is not None and len(result.history) == 1
    assert result.status.state == TaskState.TASK_STATE_COMPLETED

    # verify that history is still persisted to the store
    task = await task_store.get(result.id, context)
    assert task is not None
    assert task.history is not None and len(task.history) > 1


@pytest.mark.asyncio
async def test_on_message_send_stream_task_id_mismatch():
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)
    mock_request_context_builder = AsyncMock(spec=RequestContextBuilder)

    context_task_id = 'context_task_id_stream_1'
    result_task_id = 'DIFFERENT_task_id_stream_1'

    mock_request_context = MagicMock()
    mock_request_context.task_id = context_task_id
    mock_request_context_builder.build.return_value = mock_request_context

    request_handler = DefaultRequestHandlerV2(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        request_context_builder=mock_request_context_builder,
        agent_card=create_default_agent_card(),
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_id_mismatch_stream',
            parts=[Part(text='hello')],
        )
    )

    mismatched_task = create_sample_task(task_id=result_task_id)

    async def mock_subscribe(request=None, include_initial_task=False):
        yield mismatched_task

    mock_active_task = MagicMock()
    mock_active_task.subscribe.side_effect = mock_subscribe
    mock_active_task.start = AsyncMock()
    mock_active_task.enqueue_request = AsyncMock()

    with (
        patch.object(
            request_handler._active_task_registry,
            'get_or_create',
            return_value=mock_active_task,
        ),
        patch(
            'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
            return_value=None,
        ),
    ):
        stream = request_handler.on_message_send_stream(
            params, context=MagicMock()
        )
        with pytest.raises(InternalError) as exc_info:
            async for _ in stream:
                pass
        assert 'Task ID mismatch' in exc_info.value.message


@pytest.mark.asyncio
async def test_on_message_send_non_blocking():
    task_store = InMemoryTaskStore()
    push_store = InMemoryPushNotificationConfigStore()

    request_handler = DefaultRequestHandlerV2(
        agent_executor=HelloAgentExecutor(),
        task_store=task_store,
        push_config_store=push_store,
        agent_card=create_default_agent_card(),
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_push_non_blocking',
            parts=[Part(text='Hi')],
        ),
        configuration=SendMessageConfiguration(
            return_immediately=True,
        ),
    )

    context = create_server_call_context()
    result = await request_handler.on_message_send(params, context)

    # non-blocking should return the task immediately
    assert result is not None
    assert isinstance(result, Task)
    assert result.status.state == TaskState.TASK_STATE_SUBMITTED


@pytest.mark.asyncio
async def test_on_message_send_with_push_notification():
    task_store = InMemoryTaskStore()
    push_store = AsyncMock(spec=PushNotificationConfigStore)

    request_handler = DefaultRequestHandlerV2(
        agent_executor=HelloAgentExecutor(),
        task_store=task_store,
        push_config_store=push_store,
        agent_card=create_default_agent_card(),
    )
    push_config = TaskPushNotificationConfig(url='http://example.com/webhook')
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_push_1',
            parts=[Part(text='Hi')],
        ),
        configuration=SendMessageConfiguration(
            task_push_notification_config=push_config
        ),
    )

    context = create_server_call_context()
    result = await request_handler.on_message_send(params, context)

    assert result is not None
    assert isinstance(result, Task)
    push_store.set_info.assert_awaited_once_with(
        result.id, push_config, context
    )


class MultipleMessagesAgentExecutor(AgentExecutor):
    """Misbehaving agent that yields more than one Message."""

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        await event_queue.enqueue_event(
            new_text_message('first', role=Role.ROLE_AGENT)
        )
        await event_queue.enqueue_event(
            new_text_message('second', role=Role.ROLE_AGENT)
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


class MessageAfterTaskEventAgentExecutor(AgentExecutor):
    """Misbehaving agent that yields a task-mode event then a Message."""

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        task = new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.update_status(TaskState.TASK_STATE_WORKING)
        await event_queue.enqueue_event(
            new_text_message('stray message', role=Role.ROLE_AGENT)
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


class TaskEventAfterMessageAgentExecutor(AgentExecutor):
    """Misbehaving agent that yields a Message and then a task-mode event."""

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        await event_queue.enqueue_event(
            new_text_message('only message', role=Role.ROLE_AGENT)
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=str(context.task_id or ''),
                context_id=str(context.context_id or ''),
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


class EventAfterTerminalStateAgentExecutor(AgentExecutor):
    """Misbehaving agent that yields an event after reaching a terminal state."""

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        task = new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.complete()
        await event_queue.enqueue_event(
            new_text_message('after terminal', role=Role.ROLE_AGENT)
        )

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


@pytest.mark.asyncio
@pytest.mark.timeout(1)
async def test_on_message_send_stream_rejects_multiple_messages():
    """Stream surfaces InvalidAgentResponseError when the agent yields a
    second Message after the first one (see comment in on_message_send_stream)."""
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MultipleMessagesAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=create_default_agent_card(),
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_multi_stream',
            parts=[Part(text='Hi')],
        )
    )
    with pytest.raises(InvalidAgentResponseError, match='Multiple Message'):
        async for _ in request_handler.on_message_send_stream(
            params, create_server_call_context()
        ):
            pass


@pytest.mark.asyncio
@pytest.mark.timeout(1)
async def test_on_message_send_stream_rejects_message_after_task_event():
    """Stream surfaces InvalidAgentResponseError when the agent yields a
    Message after entering task mode (see comment in on_message_send_stream)."""
    request_handler = DefaultRequestHandlerV2(
        agent_executor=MessageAfterTaskEventAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=create_default_agent_card(),
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_after_task_stream',
            parts=[Part(text='Hi')],
        )
    )
    with pytest.raises(
        InvalidAgentResponseError, match='Message object in task mode'
    ):
        async for _ in request_handler.on_message_send_stream(
            params, create_server_call_context()
        ):
            pass


@pytest.mark.asyncio
@pytest.mark.timeout(1)
async def test_on_message_send_stream_rejects_task_event_after_message():
    """Stream surfaces InvalidAgentResponseError when the agent yields a
    task-mode event after a Message (see comment in on_message_send_stream)."""
    request_handler = DefaultRequestHandlerV2(
        agent_executor=TaskEventAfterMessageAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=create_default_agent_card(),
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_then_task_stream',
            parts=[Part(text='Hi')],
        )
    )
    with pytest.raises(InvalidAgentResponseError, match='in message mode'):
        async for _ in request_handler.on_message_send_stream(
            params, create_server_call_context()
        ):
            pass


@pytest.mark.asyncio
@pytest.mark.timeout(1)
async def test_on_message_send_stream_rejects_event_after_terminal_state():
    """Stream surfaces InvalidAgentResponseError when the agent yields an event
    after reaching a terminal state (see comment in on_message_send_stream)."""
    request_handler = DefaultRequestHandlerV2(
        agent_executor=EventAfterTerminalStateAgentExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=create_default_agent_card(),
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_after_terminal_stream',
            parts=[Part(text='Hi')],
        )
    )
    with pytest.raises(
        InvalidAgentResponseError, match='Message object in task mode'
    ):
        async for _ in request_handler.on_message_send_stream(
            params, create_server_call_context()
        ):
            pass


class _NamedUser(User):
    """Minimal authenticated test user identified by ``user_name``."""

    def __init__(self, user_name: str) -> None:
        self._user_name = user_name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._user_name


def _ctx(user_name: str) -> ServerCallContext:
    return ServerCallContext(user=_NamedUser(user_name))


@pytest.mark.asyncio
async def test_on_list_task_push_notification_configs_is_owner_scoped():
    """v2 handler: Bob must not see Alice's configs via .../list."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = Task(
        id='shared-task', context_id='ctx_1'
    )

    push_store = InMemoryPushNotificationConfigStore()
    alice_ctx = _ctx('alice')
    bob_ctx = _ctx('bob')

    alice_cfg = TaskPushNotificationConfig(
        task_id='shared-task',
        id='alice-cfg',
        url='http://alice.example.com/cb',
        token='alice-secret',
    )
    bob_cfg = TaskPushNotificationConfig(
        task_id='shared-task',
        id='bob-cfg',
        url='http://bob.example.com/cb',
        token='bob-secret',
    )
    await push_store.set_info('shared-task', alice_cfg, alice_ctx)
    await push_store.set_info('shared-task', bob_cfg, bob_ctx)

    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=create_default_agent_card(),
    )

    alice_listing = (
        await request_handler.on_list_task_push_notification_configs(
            ListTaskPushNotificationConfigsRequest(task_id='shared-task'),
            alice_ctx,
        )
    )
    assert {c.id for c in alice_listing.configs} == {'alice-cfg'}
    assert all(c.token != 'bob-secret' for c in alice_listing.configs)

    bob_listing = await request_handler.on_list_task_push_notification_configs(
        ListTaskPushNotificationConfigsRequest(task_id='shared-task'),
        bob_ctx,
    )
    assert {c.id for c in bob_listing.configs} == {'bob-cfg'}
    assert all(c.token != 'alice-secret' for c in bob_listing.configs)


@pytest.mark.asyncio
async def test_on_get_task_push_notification_config_is_owner_scoped():
    """v2 handler: Bob cannot fetch Alice's config by ID via .../get."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = Task(
        id='shared-task', context_id='ctx_1'
    )

    push_store = InMemoryPushNotificationConfigStore()
    alice_ctx = _ctx('alice')
    await push_store.set_info(
        'shared-task',
        TaskPushNotificationConfig(
            task_id='shared-task',
            id='alice-cfg',
            url='http://alice.example.com/cb',
            token='alice-secret',
        ),
        alice_ctx,
    )

    request_handler = DefaultRequestHandlerV2(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=create_default_agent_card(),
    )

    alice_view = await request_handler.on_get_task_push_notification_config(
        GetTaskPushNotificationConfigRequest(
            task_id='shared-task', id='alice-cfg'
        ),
        alice_ctx,
    )
    assert alice_view.id == 'alice-cfg'
    assert alice_view.token == 'alice-secret'

    with pytest.raises(TaskNotFoundError):
        await request_handler.on_get_task_push_notification_config(
            GetTaskPushNotificationConfigRequest(
                task_id='shared-task', id='alice-cfg'
            ),
            _ctx('bob'),
        )
