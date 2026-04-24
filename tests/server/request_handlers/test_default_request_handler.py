import asyncio
import contextlib
import logging
import time
import uuid

from typing import cast
from unittest.mock import (
    AsyncMock,
    MagicMock,
    PropertyMock,
    patch,
)

import pytest

from a2a.auth.user import UnauthenticatedUser, User
from a2a.server.agent_execution import (
    AgentExecutor,
    RequestContext,
    RequestContextBuilder,
    SimpleRequestContextBuilder,
)
from a2a.server.context import ServerCallContext
from a2a.server.events import (
    EventQueue,
    EventQueueLegacy,
    InMemoryQueueManager,
    QueueManager,
)
from a2a.server.request_handlers import (
    LegacyRequestHandler as DefaultRequestHandler,
)
from a2a.server.tasks import (
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
    PushNotificationConfigStore,
    PushNotificationSender,
    ResultAggregator,
    TaskStore,
    TaskUpdater,
)
from a2a.types import (
    ExtendedAgentCardNotConfiguredError,
    InternalError,
    InvalidParamsError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    Artifact,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
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


class MockAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        task_updater = TaskUpdater(
            event_queue,
            context.task_id,  # type: ignore[arg-type]
            context.context_id,  # type: ignore[arg-type]
        )
        async for i in self._run():
            parts = [Part(text=f'Event {i}')]
            try:
                await task_updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=task_updater.new_agent_message(parts),
                )
            except RuntimeError:
                # Stop processing when the event loop is closed
                break

    async def _run(self):
        for i in range(1_000_000):  # Simulate a long-running stream
            yield i

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


# Helper to create a simple task for tests
def create_sample_task(
    task_id='task1',
    status_state=TaskState.TASK_STATE_SUBMITTED,
    context_id='ctx1',
) -> Task:
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=status_state),
    )


# Helper to create ServerCallContext
def create_server_call_context() -> ServerCallContext:
    # Assuming UnauthenticatedUser is available or can be imported

    return ServerCallContext(user=UnauthenticatedUser())


@pytest.fixture
def agent_card():
    """Provides a standard AgentCard with streaming and push notifications enabled for tests."""
    return AgentCard(
        name='test_agent',
        version='1.0',
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
    )


def test_init_default_dependencies(agent_card):
    """Test that default dependencies are created if not provided."""
    agent_executor = MockAgentExecutor()
    task_store = InMemoryTaskStore()

    handler = DefaultRequestHandler(
        agent_executor=agent_executor,
        task_store=task_store,
        agent_card=agent_card,
    )

    assert isinstance(handler._queue_manager, InMemoryQueueManager)
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
async def test_on_get_task_not_found(agent_card):
    """Test on_get_task when task_store.get returns None."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        agent_card=agent_card,
    )

    params = GetTaskRequest(id='non_existent_task')

    context = create_server_call_context()
    with pytest.raises(TaskNotFoundError):
        await request_handler.on_get_task(params, context)

    mock_task_store.get.assert_awaited_once_with('non_existent_task', context)


@pytest.mark.asyncio
async def test_on_list_tasks_success(agent_card):
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
        tasks=[
            create_sample_task(task_id='task1'),
            task2,
        ],
        next_page_token='123',
    )
    mock_task_store.list.return_value = mock_page
    request_handler = DefaultRequestHandler(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=mock_task_store,
        agent_card=agent_card,
    )
    params = ListTasksRequest(include_artifacts=True, page_size=10)
    context = create_server_call_context()

    result = await request_handler.on_list_tasks(params, context)

    mock_task_store.list.assert_awaited_once_with(params, context)
    assert result.tasks == mock_page.tasks
    assert result.next_page_token == mock_page.next_page_token


@pytest.mark.asyncio
async def test_on_list_tasks_excludes_artifacts(agent_card):
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
        tasks=[
            create_sample_task(task_id='task1'),
            task2,
        ],
        next_page_token='123',
    )
    mock_task_store.list.return_value = mock_page
    request_handler = DefaultRequestHandler(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=mock_task_store,
        agent_card=agent_card,
    )
    params = ListTasksRequest(include_artifacts=False, page_size=10)
    context = create_server_call_context()

    result = await request_handler.on_list_tasks(params, context)

    assert not result.tasks[1].artifacts


@pytest.mark.asyncio
async def test_on_list_tasks_applies_history_length(agent_card):
    """Test on_list_tasks applies history length filter."""
    mock_task_store = AsyncMock(spec=TaskStore)
    history = [
        new_text_message('Hello 1!'),
        new_text_message('Hello 2!'),
    ]
    task2 = create_sample_task(task_id='task2')
    task2.history.extend(history)
    mock_page = ListTasksResponse(
        tasks=[
            create_sample_task(task_id='task1'),
            task2,
        ],
        next_page_token='123',
    )
    mock_task_store.list.return_value = mock_page
    request_handler = DefaultRequestHandler(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=mock_task_store,
        agent_card=agent_card,
    )
    params = ListTasksRequest(history_length=1, page_size=10)
    context = create_server_call_context()

    result = await request_handler.on_list_tasks(params, context)

    assert result.tasks[1].history == [history[1]]


@pytest.mark.asyncio
async def test_on_list_tasks_negative_history_length_error(agent_card):
    """Test on_list_tasks raises error for negative history length."""
    mock_task_store = AsyncMock(spec=TaskStore)
    request_handler = DefaultRequestHandler(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=mock_task_store,
        agent_card=agent_card,
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

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        agent_card=agent_card,
    )
    params = CancelTaskRequest(id='task_not_found_for_cancel')

    context = create_server_call_context()
    with pytest.raises(TaskNotFoundError):
        await request_handler.on_cancel_task(params, context)

    mock_task_store.get.assert_awaited_once_with(
        'task_not_found_for_cancel', context
    )


@pytest.mark.asyncio
async def test_on_cancel_task_queue_tap_returns_none(agent_card):
    """Test on_cancel_task when queue_manager.tap returns None."""
    mock_task_store = AsyncMock(spec=TaskStore)
    sample_task = create_sample_task(task_id='tap_none_task')
    mock_task_store.get.return_value = sample_task

    mock_queue_manager = AsyncMock(spec=QueueManager)
    mock_queue_manager.tap.return_value = (
        None  # Simulate queue not found / tap returns None
    )

    mock_agent_executor = AsyncMock(
        spec=AgentExecutor
    )  # Use AsyncMock for agent_executor

    # Mock ResultAggregator and its consume_all method
    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    mock_result_aggregator_instance.consume_all.return_value = (
        create_sample_task(
            task_id='tap_none_task',
            status_state=TaskState.TASK_STATE_CANCELED,  # Expected final state
        )
    )

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
        agent_card=agent_card,
    )

    context = create_server_call_context()
    with patch(
        'a2a.server.request_handlers.default_request_handler.ResultAggregator',
        return_value=mock_result_aggregator_instance,
    ):
        params = CancelTaskRequest(id='tap_none_task')
        result_task = await request_handler.on_cancel_task(params, context)

    mock_task_store.get.assert_awaited_once_with('tap_none_task', context)
    mock_queue_manager.tap.assert_awaited_once_with('tap_none_task')
    # agent_executor.cancel should be called with a new EventQueue if tap returned None
    mock_agent_executor.cancel.assert_awaited_once()
    # Verify the EventQueue passed to cancel was a new one
    call_args_list = mock_agent_executor.cancel.call_args_list
    args, _ = call_args_list[0]
    assert isinstance(
        args[1], EventQueue
    )  # args[1] is the event_queue argument

    mock_result_aggregator_instance.consume_all.assert_awaited_once()
    assert result_task is not None
    assert result_task.status.state == TaskState.TASK_STATE_CANCELED


@pytest.mark.asyncio
async def test_on_cancel_task_cancels_running_agent(agent_card):
    """Test on_cancel_task cancels a running agent task."""
    task_id = 'running_agent_task_to_cancel'
    sample_task = create_sample_task(task_id=task_id)
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = sample_task

    mock_queue_manager = AsyncMock(spec=QueueManager)
    mock_event_queue = AsyncMock(spec=EventQueueLegacy)
    mock_queue_manager.tap.return_value = mock_event_queue

    mock_agent_executor = AsyncMock(spec=AgentExecutor)

    # Mock ResultAggregator
    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    mock_result_aggregator_instance.consume_all.return_value = (
        create_sample_task(
            task_id=task_id, status_state=TaskState.TASK_STATE_CANCELED
        )
    )

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
        agent_card=agent_card,
    )

    # Simulate a running agent task
    mock_producer_task = AsyncMock(spec=asyncio.Task)
    request_handler._running_agents[task_id] = mock_producer_task

    context = create_server_call_context()
    with patch(
        'a2a.server.request_handlers.default_request_handler.ResultAggregator',
        return_value=mock_result_aggregator_instance,
    ):
        params = CancelTaskRequest(id=f'{task_id}')
        await request_handler.on_cancel_task(params, context)

    mock_producer_task.cancel.assert_called_once()
    mock_agent_executor.cancel.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_cancel_task_completes_during_cancellation(agent_card):
    """Test on_cancel_task fails to cancel a task due to concurrent task completion."""
    task_id = 'running_agent_task_to_cancel'
    sample_task = create_sample_task(task_id=task_id)
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = sample_task

    mock_queue_manager = AsyncMock(spec=QueueManager)
    mock_event_queue = AsyncMock(spec=EventQueueLegacy)
    mock_queue_manager.tap.return_value = mock_event_queue

    mock_agent_executor = AsyncMock(spec=AgentExecutor)

    # Mock ResultAggregator
    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    mock_result_aggregator_instance.consume_all.return_value = (
        create_sample_task(
            task_id=task_id, status_state=TaskState.TASK_STATE_COMPLETED
        )
    )

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
        agent_card=agent_card,
    )

    # Simulate a running agent task
    mock_producer_task = AsyncMock(spec=asyncio.Task)
    request_handler._running_agents[task_id] = mock_producer_task

    with patch(
        'a2a.server.request_handlers.default_request_handler.ResultAggregator',
        return_value=mock_result_aggregator_instance,
    ):
        params = CancelTaskRequest(id=f'{task_id}')
        with pytest.raises(TaskNotCancelableError):
            await request_handler.on_cancel_task(
                params, create_server_call_context()
            )

    mock_producer_task.cancel.assert_called_once()
    mock_agent_executor.cancel.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_cancel_task_invalid_result_type(agent_card):
    """Test on_cancel_task when result_aggregator returns a Message instead of a Task."""
    task_id = 'cancel_invalid_result_task'
    sample_task = create_sample_task(task_id=task_id)
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = sample_task

    mock_queue_manager = AsyncMock(spec=QueueManager)
    mock_event_queue = AsyncMock(spec=EventQueueLegacy)
    mock_queue_manager.tap.return_value = mock_event_queue

    mock_agent_executor = AsyncMock(spec=AgentExecutor)

    # Mock ResultAggregator to return a Message
    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    mock_result_aggregator_instance.consume_all.return_value = Message(
        message_id='unexpected_msg',
        role=Role.ROLE_AGENT,
        parts=[Part(text='Test')],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
        agent_card=agent_card,
    )

    with patch(
        'a2a.server.request_handlers.default_request_handler.ResultAggregator',
        return_value=mock_result_aggregator_instance,
    ):
        params = CancelTaskRequest(id=f'{task_id}')
        with pytest.raises(InternalError) as exc_info:
            await request_handler.on_cancel_task(
                params, create_server_call_context()
            )

    assert (
        'Agent did not return valid response for cancel'
        in exc_info.value.message
    )


@pytest.mark.asyncio
async def test_on_message_send_with_push_notification(agent_card):
    """Test on_message_send sets push notification info if provided."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_push_notification_store = AsyncMock(spec=PushNotificationConfigStore)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)
    mock_request_context_builder = AsyncMock(spec=RequestContextBuilder)

    task_id = 'push_task_1'
    context_id = 'push_ctx_1'
    sample_initial_task = create_sample_task(
        task_id=task_id,
        context_id=context_id,
        status_state=TaskState.TASK_STATE_SUBMITTED,
    )

    # TaskManager will be created inside on_message_send.
    # We need to mock task_store.get to return None initially for TaskManager to create a new task.
    # Then, TaskManager.update_with_message will be called.
    # For simplicity in this unit test, let's assume TaskManager correctly sets up the task
    # and the task object (with IDs) is available for _request_context_builder.build

    mock_task_store.get.return_value = (
        None  # Simulate new task scenario for TaskManager
    )

    # Mock _request_context_builder.build to return a context with the generated/confirmed IDs
    mock_request_context = MagicMock(spec=RequestContext)
    mock_request_context.task_id = task_id
    mock_request_context.context_id = context_id
    mock_request_context_builder.build.return_value = mock_request_context

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        push_config_store=mock_push_notification_store,
        request_context_builder=mock_request_context_builder,
        agent_card=agent_card,
    )

    push_config = TaskPushNotificationConfig(url='http://callback.com/push')
    message_config = SendMessageConfiguration(
        task_push_notification_config=push_config,
        accepted_output_modes=['text/plain'],  # Added required field
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_push',
            parts=[Part(text='Test')],
            task_id=task_id,
            context_id=context_id,
        ),
        configuration=message_config,
    )

    # Mock ResultAggregator and its consume_and_break_on_interrupt
    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    final_task_result = create_sample_task(
        task_id=task_id,
        context_id=context_id,
        status_state=TaskState.TASK_STATE_COMPLETED,
    )
    mock_result_aggregator_instance.consume_and_break_on_interrupt.return_value = (
        final_task_result,
        False,
        None,
    )

    # Mock the current_result async property to return the final task result
    # current_result is an async property, so accessing it returns a coroutine
    async def mock_current_result():
        return final_task_result

    type(mock_result_aggregator_instance).current_result = property(
        lambda self: mock_current_result()
    )

    context = create_server_call_context()
    with (
        patch(
            'a2a.server.request_handlers.default_request_handler.ResultAggregator',
            return_value=mock_result_aggregator_instance,
        ),
        patch(
            'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
            return_value=sample_initial_task,
        ),
        patch(
            'a2a.server.request_handlers.default_request_handler.TaskManager.update_with_message',
            return_value=sample_initial_task,
        ),
    ):  # Ensure task object is returned
        await request_handler.on_message_send(params, context)

    mock_push_notification_store.set_info.assert_awaited_once_with(
        task_id, push_config, context
    )
    # Other assertions for full flow if needed (e.g., agent execution)
    mock_agent_executor.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_message_send_with_push_notification_in_non_blocking_request(
    agent_card,
):
    """Test that push notification callback is called during background event processing for non-blocking requests."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_push_notification_store = AsyncMock(spec=PushNotificationConfigStore)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)
    mock_request_context_builder = AsyncMock(spec=RequestContextBuilder)
    mock_push_sender = AsyncMock()

    task_id = 'non_blocking_task_1'
    context_id = 'non_blocking_ctx_1'

    # Create a task that will be returned after the first event
    initial_task = create_sample_task(
        task_id=task_id,
        context_id=context_id,
        status_state=TaskState.TASK_STATE_WORKING,
    )

    # Create a final task that will be available during background processing
    final_task = create_sample_task(
        task_id=task_id,
        context_id=context_id,
        status_state=TaskState.TASK_STATE_COMPLETED,
    )

    mock_task_store.get.return_value = None

    # Mock request context
    mock_request_context = MagicMock(spec=RequestContext)
    mock_request_context.task_id = task_id
    mock_request_context.context_id = context_id
    mock_request_context_builder.build.return_value = mock_request_context

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        push_config_store=mock_push_notification_store,
        request_context_builder=mock_request_context_builder,
        push_sender=mock_push_sender,
        agent_card=agent_card,
    )

    # Configure push notification
    push_config = TaskPushNotificationConfig(url='http://callback.com/push')
    message_config = SendMessageConfiguration(
        task_push_notification_config=push_config,
        accepted_output_modes=['text/plain'],
        return_immediately=True,
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_non_blocking',
            parts=[Part(text='Test')],
            task_id=task_id,
            context_id=context_id,
        ),
        configuration=message_config,
    )

    # Mock ResultAggregator with custom behavior
    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)

    # First call returns the initial task and indicates interruption (non-blocking)
    mock_result_aggregator_instance.consume_and_break_on_interrupt.return_value = (
        initial_task,
        True,  # interrupted = True for non-blocking
        MagicMock(spec=asyncio.Task),  # background task
    )

    # Mock the current_result async property to return the final task
    # current_result is an async property, so accessing it returns a coroutine
    async def mock_current_result():
        return final_task

    type(mock_result_aggregator_instance).current_result = property(
        lambda self: mock_current_result()
    )

    # Track if the event_callback was passed to consume_and_break_on_interrupt
    event_callback_passed = False
    event_callback_received = None

    async def mock_consume_and_break_on_interrupt(
        consumer, blocking=True, event_callback=None
    ):
        nonlocal event_callback_passed, event_callback_received
        event_callback_passed = event_callback is not None
        event_callback_received = event_callback
        if event_callback_received:
            await event_callback_received(final_task)
        return (
            initial_task,
            True,
            MagicMock(spec=asyncio.Task),
        )  # interrupted = True for non-blocking

    mock_result_aggregator_instance.consume_and_break_on_interrupt = (
        mock_consume_and_break_on_interrupt
    )

    context = create_server_call_context()
    with (
        patch(
            'a2a.server.request_handlers.default_request_handler.ResultAggregator',
            return_value=mock_result_aggregator_instance,
        ),
        patch(
            'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
            return_value=initial_task,
        ),
        patch(
            'a2a.server.request_handlers.default_request_handler.TaskManager.update_with_message',
            return_value=initial_task,
        ),
    ):
        # Execute the non-blocking request
        result = await request_handler.on_message_send(params, context)

    # Verify the result is the initial task (non-blocking behavior)
    assert result == initial_task

    # Verify that the event_callback was passed to consume_and_break_on_interrupt
    assert event_callback_passed, (
        'event_callback should have been passed to consume_and_break_on_interrupt'
    )
    assert event_callback_received is not None, (
        'event_callback should not be None'
    )

    # Verify that the push notification was sent with the final task
    mock_push_sender.send_notification.assert_called_with(task_id, final_task)

    # Verify that the push notification config was stored
    mock_push_notification_store.set_info.assert_awaited_once_with(
        task_id, push_config, context
    )


@pytest.mark.asyncio
async def test_on_message_send_with_push_notification_no_existing_Task(
    agent_card,
):
    """Test on_message_send for new task sets push notification info if provided."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_push_notification_store = AsyncMock(spec=PushNotificationConfigStore)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)
    mock_request_context_builder = AsyncMock(spec=RequestContextBuilder)

    task_id = 'push_task_1'
    context_id = 'push_ctx_1'

    mock_task_store.get.return_value = (
        None  # Simulate new task scenario for TaskManager
    )

    # Mock _request_context_builder.build to return a context with the generated/confirmed IDs
    mock_request_context = MagicMock(spec=RequestContext)
    mock_request_context.task_id = task_id
    mock_request_context.context_id = context_id
    mock_request_context_builder.build.return_value = mock_request_context

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        push_config_store=mock_push_notification_store,
        request_context_builder=mock_request_context_builder,
        agent_card=agent_card,
    )

    push_config = TaskPushNotificationConfig(url='http://callback.com/push')
    message_config = SendMessageConfiguration(
        task_push_notification_config=push_config,
        accepted_output_modes=['text/plain'],  # Added required field
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_push',
            parts=[Part(text='Test')],
        ),
        configuration=message_config,
    )

    # Mock ResultAggregator and its consume_and_break_on_interrupt
    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    final_task_result = create_sample_task(
        task_id=task_id,
        context_id=context_id,
        status_state=TaskState.TASK_STATE_COMPLETED,
    )
    mock_result_aggregator_instance.consume_and_break_on_interrupt.return_value = (
        final_task_result,
        False,
        None,
    )

    # Mock the current_result async property to return the final task result
    # current_result is an async property, so accessing it returns a coroutine
    async def mock_current_result():
        return final_task_result

    type(mock_result_aggregator_instance).current_result = property(
        lambda self: mock_current_result()
    )

    context = create_server_call_context()
    with (
        patch(
            'a2a.server.request_handlers.default_request_handler.ResultAggregator',
            return_value=mock_result_aggregator_instance,
        ),
        patch(
            'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
            return_value=None,
        ),
    ):
        await request_handler.on_message_send(params, context)

    mock_push_notification_store.set_info.assert_awaited_once_with(
        task_id, push_config, context
    )
    # Other assertions for full flow if needed (e.g., agent execution)
    mock_agent_executor.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_message_send_no_result_from_aggregator(agent_card):
    """Test on_message_send when aggregator returns (None, False). Completes unsuccessfully and raises InternalError."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)
    mock_request_context_builder = AsyncMock(spec=RequestContextBuilder)

    task_id = 'no_result_task'
    # Mock _request_context_builder.build
    mock_request_context = MagicMock(spec=RequestContext)
    mock_request_context.task_id = task_id
    mock_request_context_builder.build.return_value = mock_request_context

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        request_context_builder=mock_request_context_builder,
        agent_card=agent_card,
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_no_res',
            parts=[Part(text='Test')],
        )
    )

    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    mock_result_aggregator_instance.consume_and_break_on_interrupt.return_value = (
        None,
        False,
        None,
    )

    with (
        patch(
            'a2a.server.request_handlers.default_request_handler.ResultAggregator',
            return_value=mock_result_aggregator_instance,
        ),
        patch(
            'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
            return_value=None,
        ),
    ):  # TaskManager.get_task for initial task
        with pytest.raises(InternalError):
            await request_handler.on_message_send(
                params, create_server_call_context()
            )


@pytest.mark.asyncio
async def test_on_message_send_task_id_mismatch(agent_card):
    """Test on_message_send returns InternalError if aggregator returns mismatched Task ID."""
    """Test on_message_send when result task ID doesn't match request context task ID."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)
    mock_request_context_builder = AsyncMock(spec=RequestContextBuilder)

    context_task_id = 'context_task_id_1'
    result_task_id = 'DIFFERENT_task_id_1'  # Mismatch

    # Mock _request_context_builder.build
    mock_request_context = MagicMock(spec=RequestContext)
    mock_request_context.task_id = context_task_id
    mock_request_context_builder.build.return_value = mock_request_context

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        request_context_builder=mock_request_context_builder,
        agent_card=agent_card,
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_id_mismatch',
            parts=[Part(text='Test')],
        )
    )

    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    mismatched_task = create_sample_task(task_id=result_task_id)
    mock_result_aggregator_instance.consume_and_break_on_interrupt.return_value = (
        mismatched_task,
        False,
        None,
    )

    with (
        patch(
            'a2a.server.request_handlers.default_request_handler.ResultAggregator',
            return_value=mock_result_aggregator_instance,
        ),
        patch(
            'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
            return_value=None,
        ),
    ):
        with pytest.raises(InternalError) as exc_info:
            await request_handler.on_message_send(
                params, create_server_call_context()
            )

    assert 'Task ID mismatch' in exc_info.value.message  # type: ignore


class HelloAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        task = context.current_task
        if not task:
            assert context.message is not None, (
                'A message is required to create a new task'
            )
            task = new_task_from_user_message(context.message)  # type: ignore
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)

        try:
            parts = [Part(text='I am working')]
            await updater.update_status(
                TaskState.TASK_STATE_WORKING,
                message=updater.new_agent_message(parts),
            )
        except Exception as e:
            # Stop processing when the event loop is closed
            logging.warning('Error: %s', e)
            return
        await updater.add_artifact(
            [Part(text='Hello world!')],
            name='conversion_result',
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


@pytest.mark.asyncio
async def test_on_message_send_non_blocking(agent_card):
    task_store = InMemoryTaskStore()
    push_store = InMemoryPushNotificationConfigStore()

    request_handler = DefaultRequestHandler(
        agent_executor=HelloAgentExecutor(),
        task_store=task_store,
        push_config_store=push_store,
        agent_card=agent_card,
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_push',
            parts=[Part(text='Hi')],
        ),
        configuration=SendMessageConfiguration(
            return_immediately=True, accepted_output_modes=['text/plain']
        ),
    )

    context = create_server_call_context()
    result = await request_handler.on_message_send(params, context)

    assert result is not None
    assert isinstance(result, Task)
    assert result.status.state == TaskState.TASK_STATE_SUBMITTED

    # Polling for 500ms until task is completed.
    task: Task | None = None
    for _ in range(5):
        await asyncio.sleep(0.1)
        task = await task_store.get(result.id, context)
        assert task is not None
        if task.status.state == TaskState.TASK_STATE_COMPLETED:
            break

    assert task is not None
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert (
        result.history
        and task.history
        and len(result.history) == len(task.history)
    )


@pytest.mark.asyncio
async def test_on_message_send_limit_history(agent_card):
    task_store = InMemoryTaskStore()
    push_store = InMemoryPushNotificationConfigStore()

    request_handler = DefaultRequestHandler(
        agent_executor=HelloAgentExecutor(),
        task_store=task_store,
        push_config_store=push_store,
        agent_card=agent_card,
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
async def test_on_get_task_limit_history(agent_card):
    task_store = InMemoryTaskStore()
    push_store = InMemoryPushNotificationConfigStore()

    request_handler = DefaultRequestHandler(
        agent_executor=HelloAgentExecutor(),
        task_store=task_store,
        push_config_store=push_store,
        agent_card=agent_card,
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_push',
            parts=[Part(text='Hi')],
        ),
        configuration=SendMessageConfiguration(
            accepted_output_modes=['text/plain'],
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


@pytest.mark.asyncio
async def test_on_message_send_interrupted_flow(agent_card):
    """Test on_message_send when flow is interrupted (e.g., auth_required)."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)
    mock_request_context_builder = AsyncMock(spec=RequestContextBuilder)

    task_id = 'interrupted_task_1'
    # Mock _request_context_builder.build
    mock_request_context = MagicMock(spec=RequestContext)
    mock_request_context.task_id = task_id
    mock_request_context_builder.build.return_value = mock_request_context

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        request_context_builder=mock_request_context_builder,
        agent_card=agent_card,
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_interrupt',
            parts=[Part(text='Test')],
        )
    )

    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    interrupt_task_result = create_sample_task(
        task_id=task_id, status_state=TaskState.TASK_STATE_AUTH_REQUIRED
    )
    mock_result_aggregator_instance.consume_and_break_on_interrupt.return_value = (
        interrupt_task_result,
        True,
        MagicMock(spec=asyncio.Task),  # background task
    )  # Interrupted = True

    # Collect coroutines passed to create_task so we can close them
    created_coroutines = []

    def capture_create_task(coro):
        created_coroutines.append(coro)
        return MagicMock()

    # Patch asyncio.create_task to verify _cleanup_producer is scheduled
    with (
        patch(
            'asyncio.create_task', side_effect=capture_create_task
        ) as mock_asyncio_create_task,
        patch(
            'a2a.server.request_handlers.default_request_handler.ResultAggregator',
            return_value=mock_result_aggregator_instance,
        ),
        patch(
            'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
            return_value=None,
        ),
    ):
        result = await request_handler.on_message_send(
            params, create_server_call_context()
        )

    assert result == interrupt_task_result
    assert (
        mock_asyncio_create_task.call_count == 2
    )  # First for _run_event_stream, second for _cleanup_producer

    # Check that the second call to create_task was for _cleanup_producer
    found_cleanup_call = False
    for coro in created_coroutines:
        if hasattr(coro, '__name__') and coro.__name__ == '_cleanup_producer':
            found_cleanup_call = True
            break
    assert found_cleanup_call, (
        '_cleanup_producer was not scheduled with asyncio.create_task'
    )

    # Close coroutines to avoid RuntimeWarning about unawaited coroutines
    for coro in created_coroutines:
        coro.close()


@pytest.mark.asyncio
async def test_on_message_send_stream_with_push_notification(agent_card):
    """Test on_message_send_stream sets and uses push notification info."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_push_config_store = AsyncMock(spec=PushNotificationConfigStore)
    mock_push_sender = AsyncMock(spec=PushNotificationSender)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)
    mock_request_context_builder = AsyncMock(spec=RequestContextBuilder)

    task_id = 'stream_push_task_1'
    context_id = 'stream_push_ctx_1'

    # Initial task state for TaskManager
    initial_task_for_tm = create_sample_task(
        task_id=task_id,
        context_id=context_id,
        status_state=TaskState.TASK_STATE_SUBMITTED,
    )

    # Task state for RequestContext
    task_for_rc = create_sample_task(
        task_id=task_id,
        context_id=context_id,
        status_state=TaskState.TASK_STATE_WORKING,
    )  # Example state after message update

    mock_task_store.get.return_value = None  # New task for TaskManager

    mock_request_context = MagicMock(spec=RequestContext)
    mock_request_context.task_id = task_id
    mock_request_context.context_id = context_id
    mock_request_context_builder.build.return_value = mock_request_context

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        push_config_store=mock_push_config_store,
        push_sender=mock_push_sender,
        request_context_builder=mock_request_context_builder,
        agent_card=agent_card,
    )

    push_config = TaskPushNotificationConfig(
        url='http://callback.stream.com/push'
    )
    message_config = SendMessageConfiguration(
        task_push_notification_config=push_config,
        accepted_output_modes=['text/plain'],  # Added required field
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_stream_push',
            parts=[Part(text='Test')],
            task_id=task_id,
            context_id=context_id,
        ),
        configuration=message_config,
    )

    # Latch to ensure background execute is scheduled before asserting
    execute_called = asyncio.Event()

    async def exec_side_effect(*args, **kwargs):
        execute_called.set()

    mock_agent_executor.execute.side_effect = exec_side_effect

    # Mock ResultAggregator and its consume_and_emit
    mock_result_aggregator_instance = MagicMock(
        spec=ResultAggregator
    )  # Use MagicMock for easier property mocking

    # Events to be yielded by consume_and_emit
    event1_task_update = create_sample_task(
        task_id=task_id,
        context_id=context_id,
        status_state=TaskState.TASK_STATE_WORKING,
    )
    event2_final_task = create_sample_task(
        task_id=task_id,
        context_id=context_id,
        status_state=TaskState.TASK_STATE_COMPLETED,
    )

    async def event_stream_gen():
        yield event1_task_update
        yield event2_final_task

    # consume_and_emit is called by `async for ... in result_aggregator.consume_and_emit(consumer)`
    # This means result_aggregator.consume_and_emit(consumer) must directly return an async iterable.
    # If consume_and_emit is an async method, this is problematic in the product code.
    # For the test, we make the mock of consume_and_emit a synchronous method
    # that returns the async generator object.
    def sync_get_event_stream_gen(*args, **kwargs):
        return event_stream_gen()

    mock_result_aggregator_instance.consume_and_emit = MagicMock(
        side_effect=sync_get_event_stream_gen
    )

    # Mock current_result as an async property returning events sequentially.
    async def to_coro(val):
        return val

    type(mock_result_aggregator_instance).current_result = PropertyMock(
        side_effect=[to_coro(event1_task_update), to_coro(event2_final_task)]
    )

    context = create_server_call_context()
    with (
        patch(
            'a2a.server.request_handlers.default_request_handler.ResultAggregator',
            return_value=mock_result_aggregator_instance,
        ),
        patch(
            'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
            return_value=initial_task_for_tm,
        ),
        patch(
            'a2a.server.request_handlers.default_request_handler.TaskManager.update_with_message',
            return_value=task_for_rc,
        ),
    ):
        # Consume the stream
        async for _ in request_handler.on_message_send_stream(params, context):
            pass

    await asyncio.wait_for(execute_called.wait(), timeout=0.1)

    # Assertions
    # 1. set_info called once at the beginning if task exists (or after task is created from message)
    mock_push_config_store.set_info.assert_any_call(
        task_id, push_config, context
    )

    # 2. send_notification called for each task event yielded by aggregator
    assert mock_push_sender.send_notification.await_count == 2
    mock_push_sender.send_notification.assert_any_await(
        task_id, event1_task_update
    )
    mock_push_sender.send_notification.assert_any_await(
        task_id, event2_final_task
    )

    mock_agent_executor.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_disconnect_then_resubscribe_receives_future_events(
    agent_card,
):
    """Start streaming, disconnect, then resubscribe and ensure subsequent events are streamed."""
    # Arrange
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)

    # Use a real queue manager so taps receive future events
    queue_manager = InMemoryQueueManager()

    task_id = 'reconn_task_1'
    context_id = 'reconn_ctx_1'

    # Task exists and is non-final
    task_for_resub = create_sample_task(
        task_id=task_id,
        context_id=context_id,
        status_state=TaskState.TASK_STATE_WORKING,
    )
    mock_task_store.get.return_value = task_for_resub

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        queue_manager=queue_manager,
        agent_card=agent_card,
    )

    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_reconn',
            parts=[Part(text='Test')],
            task_id=task_id,
            context_id=context_id,
        )
    )

    # Producer behavior: emit one event, then later emit second event
    exec_started = asyncio.Event()
    allow_second_event = asyncio.Event()
    allow_finish = asyncio.Event()

    first_event = create_sample_task(
        task_id=task_id,
        context_id=context_id,
        status_state=TaskState.TASK_STATE_WORKING,
    )
    second_event = create_sample_task(
        task_id=task_id,
        context_id=context_id,
        status_state=TaskState.TASK_STATE_COMPLETED,
    )

    async def exec_side_effect(_request, queue: EventQueue):
        exec_started.set()
        await queue.enqueue_event(first_event)
        await allow_second_event.wait()
        await queue.enqueue_event(second_event)
        await allow_finish.wait()

    mock_agent_executor.execute.side_effect = exec_side_effect

    # Start streaming and consume first event
    agen = request_handler.on_message_send_stream(
        params, create_server_call_context()
    )
    first = await agen.__anext__()
    assert first == first_event

    # Simulate client disconnect
    await asyncio.wait_for(agen.aclose(), timeout=0.1)

    # Resubscribe and start consuming future events
    resub_gen = request_handler.on_subscribe_to_task(
        SubscribeToTaskRequest(id=f'{task_id}'),
        create_server_call_context(),
    )

    # Allow producer to emit the next event
    allow_second_event.set()

    first_subscribe_event = await anext(resub_gen)
    assert first_subscribe_event == task_for_resub

    received = await anext(resub_gen)
    assert received == second_event

    # Finish producer to allow cleanup paths to complete
    allow_finish.set()


@pytest.mark.asyncio
async def test_on_message_send_stream_client_disconnect_triggers_background_cleanup_and_producer_continues(
    agent_card,
):
    """Simulate client disconnect: stream stops early, cleanup is scheduled in background,
    producer keeps running, and cleanup completes after producer finishes."""
    # Arrange
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_queue_manager = AsyncMock(spec=QueueManager)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)
    mock_request_context_builder = AsyncMock(spec=RequestContextBuilder)

    task_id = 'disc_task_1'
    context_id = 'disc_ctx_1'

    # Return an existing task from the store to avoid "task not found" error
    existing_task = create_sample_task(task_id=task_id, context_id=context_id)
    mock_task_store.get.return_value = existing_task

    # RequestContext with IDs
    mock_request_context = MagicMock(spec=RequestContext)
    mock_request_context.task_id = task_id
    mock_request_context.context_id = context_id
    mock_request_context_builder.build.return_value = mock_request_context

    # Queue used by _run_event_stream; must support close()
    mock_queue = AsyncMock(spec=EventQueueLegacy)
    mock_queue_manager.create_or_tap.return_value = mock_queue

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
        request_context_builder=mock_request_context_builder,
        agent_card=agent_card,
    )

    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='mid',
            parts=[Part(text='Test')],
            task_id=task_id,
            context_id=context_id,
        )
    )

    # Agent executor runs in background until we allow it to finish
    execute_started = asyncio.Event()
    execute_finish = asyncio.Event()

    async def exec_side_effect(*_args, **_kwargs):
        execute_started.set()
        await execute_finish.wait()

    mock_agent_executor.execute.side_effect = exec_side_effect

    # ResultAggregator emits one Task event (so the stream yields once)
    first_event = create_sample_task(task_id=task_id, context_id=context_id)

    async def single_event_stream():
        yield first_event
        # will never yield again; client will disconnect

    mock_result_aggregator_instance = MagicMock(spec=ResultAggregator)
    mock_result_aggregator_instance.consume_and_emit.return_value = (
        single_event_stream()
    )
    # Signal when background consume_all is started
    bg_started = asyncio.Event()

    async def mock_consume_all(_consumer):
        bg_started.set()
        # emulate short-running background work
        await asyncio.sleep(0)

    mock_result_aggregator_instance.consume_all = mock_consume_all

    produced_task: asyncio.Task | None = None
    cleanup_task: asyncio.Task | None = None

    orig_create_task = asyncio.create_task

    def create_task_spy(coro):
        nonlocal produced_task, cleanup_task
        task = orig_create_task(coro)
        # Inspect the coroutine name to make the spy more robust
        if coro.__name__ == '_run_event_stream':
            produced_task = task
        elif coro.__name__ == '_cleanup_producer':
            cleanup_task = task
        return task

    with (
        patch(
            'a2a.server.request_handlers.default_request_handler.ResultAggregator',
            return_value=mock_result_aggregator_instance,
        ),
        patch('asyncio.create_task', side_effect=create_task_spy),
    ):
        # Act: start stream and consume only the first event, then disconnect
        agen = request_handler.on_message_send_stream(
            params, create_server_call_context()
        )
        first = await agen.__anext__()
        assert first == first_event
        # Simulate client disconnect
        await asyncio.wait_for(agen.aclose(), timeout=0.1)

    # Assert cleanup was scheduled and producer was started
    assert produced_task is not None
    assert cleanup_task is not None

    # Assert background consume_all started
    await asyncio.wait_for(bg_started.wait(), timeout=0.2)

    # execute should have started
    await asyncio.wait_for(execute_started.wait(), timeout=0.1)

    # Producer should still be running (not finished immediately on disconnect)
    assert not produced_task.done()

    # Allow executor to finish, which should complete producer and then cleanup
    execute_finish.set()
    await asyncio.wait_for(produced_task, timeout=0.2)
    await asyncio.wait_for(cleanup_task, timeout=0.2)

    # Queue close awaited by _run_event_stream
    mock_queue.close.assert_awaited_once()
    # QueueManager close called by _cleanup_producer
    mock_queue_manager.close.assert_awaited_once_with(task_id)
    # Running agents is cleared
    assert task_id not in request_handler._running_agents

    # Cleanup any lingering background tasks started by on_message_send_stream
    # (e.g., background_consume)
    for t in list(request_handler._background_tasks):
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t


@pytest.mark.asyncio
async def test_disconnect_persists_final_task_to_store(agent_card):
    """After client disconnect, ensure background consumer persists final Task to store."""
    task_store = InMemoryTaskStore()
    queue_manager = InMemoryQueueManager()

    # Custom agent that emits a working update then a completed final update
    class FinishingAgent(AgentExecutor):
        def __init__(self):
            self.allow_finish = asyncio.Event()

        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            updater = TaskUpdater(
                event_queue,
                cast('str', context.task_id),
                cast('str', context.context_id),
            )
            await updater.update_status(TaskState.TASK_STATE_WORKING)
            await self.allow_finish.wait()
            await updater.update_status(TaskState.TASK_STATE_COMPLETED)

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            return None

    agent = FinishingAgent()

    handler = DefaultRequestHandler(
        agent_executor=agent,
        task_store=task_store,
        queue_manager=queue_manager,
        agent_card=agent_card,
    )

    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_persist',
            parts=[Part(text='Test')],
        )
    )

    # Start streaming and consume the first event (working)
    agen = handler.on_message_send_stream(params, create_server_call_context())
    first = await agen.__anext__()
    if isinstance(first, TaskStatusUpdateEvent):
        assert first.status.state == TaskState.TASK_STATE_WORKING
        task_id = first.task_id
    else:
        assert (
            isinstance(first, Task)
            and first.status.state == TaskState.TASK_STATE_WORKING
        )
        task_id = first.id

    # Disconnect client
    await asyncio.wait_for(agen.aclose(), timeout=0.1)

    # Finish agent and allow background consumer to persist final state
    agent.allow_finish.set()

    # Wait until background_consume task for this task_id is gone
    await wait_until(
        lambda: all(
            not t.get_name().startswith(f'background_consume:{task_id}')
            for t in handler._background_tasks
        ),
        timeout=1.0,
        interval=0.01,
    )

    # Verify task is persisted as completed
    persisted = await task_store.get(task_id, create_server_call_context())
    assert persisted is not None
    assert persisted.status.state == TaskState.TASK_STATE_COMPLETED


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
async def test_background_cleanup_task_is_tracked_and_cleared(agent_card):
    """Ensure background cleanup task is tracked while pending and removed when done."""
    # Arrange
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_queue_manager = AsyncMock(spec=QueueManager)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)
    mock_request_context_builder = AsyncMock(spec=RequestContextBuilder)

    task_id = 'track_task_1'
    context_id = 'track_ctx_1'

    # Return an existing task from the store to avoid "task not found" error
    existing_task = create_sample_task(task_id=task_id, context_id=context_id)
    mock_task_store.get.return_value = existing_task

    # RequestContext with IDs
    mock_request_context = MagicMock(spec=RequestContext)
    mock_request_context.task_id = task_id
    mock_request_context.context_id = context_id
    mock_request_context_builder.build.return_value = mock_request_context

    mock_queue = AsyncMock(spec=EventQueueLegacy)
    mock_queue_manager.create_or_tap.return_value = mock_queue

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
        request_context_builder=mock_request_context_builder,
        agent_card=agent_card,
    )

    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='mid_track',
            parts=[Part(text='Test')],
            task_id=task_id,
            context_id=context_id,
        )
    )

    # Agent executor runs in background until we allow it to finish
    execute_started = asyncio.Event()
    execute_finish = asyncio.Event()

    async def exec_side_effect(*_args, **_kwargs):
        execute_started.set()
        await execute_finish.wait()

    mock_agent_executor.execute.side_effect = exec_side_effect

    # ResultAggregator emits one Task event (so the stream yields once)
    first_event = create_sample_task(task_id=task_id, context_id=context_id)

    async def single_event_stream():
        yield first_event

    mock_result_aggregator_instance = MagicMock(spec=ResultAggregator)
    mock_result_aggregator_instance.consume_and_emit.return_value = (
        single_event_stream()
    )

    produced_task: asyncio.Task | None = None
    cleanup_task: asyncio.Task | None = None

    orig_create_task = asyncio.create_task

    def create_task_spy(coro):
        nonlocal produced_task, cleanup_task
        task = orig_create_task(coro)
        if coro.__name__ == '_run_event_stream':
            produced_task = task
        elif coro.__name__ == '_cleanup_producer':
            cleanup_task = task
        return task

    with (
        patch(
            'a2a.server.request_handlers.default_request_handler.ResultAggregator',
            return_value=mock_result_aggregator_instance,
        ),
        patch('asyncio.create_task', side_effect=create_task_spy),
    ):
        # Act: start stream and consume only the first event, then disconnect
        agen = request_handler.on_message_send_stream(
            params, create_server_call_context()
        )
        first = await agen.__anext__()
        assert first == first_event
        # Simulate client disconnect
        await asyncio.wait_for(agen.aclose(), timeout=0.1)

    assert produced_task is not None
    assert cleanup_task is not None

    # Background cleanup task should be tracked while producer is still running
    await asyncio.wait_for(execute_started.wait(), timeout=0.1)
    assert cleanup_task in request_handler._background_tasks

    # Allow executor to finish; this should complete producer, then cleanup
    execute_finish.set()
    await asyncio.wait_for(produced_task, timeout=0.1)
    await asyncio.wait_for(cleanup_task, timeout=0.1)

    # Wait for callback to remove task from tracking
    await wait_until(
        lambda: cleanup_task not in request_handler._background_tasks,
        timeout=0.1,
    )

    # Cleanup any lingering background tasks
    for t in list(request_handler._background_tasks):
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t


@pytest.mark.asyncio
async def test_on_message_send_stream_task_id_mismatch(agent_card):
    """Test on_message_send_stream raises error if yielded task ID mismatches."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_agent_executor = AsyncMock(
        spec=AgentExecutor
    )  # Only need a basic mock
    mock_request_context_builder = AsyncMock(spec=RequestContextBuilder)

    context_task_id = 'stream_task_id_ctx'
    mismatched_task_id = 'DIFFERENT_stream_task_id'

    mock_request_context = MagicMock(spec=RequestContext)
    mock_request_context.task_id = context_task_id
    mock_request_context_builder.build.return_value = mock_request_context

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        request_context_builder=mock_request_context_builder,
        agent_card=agent_card,
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_stream_mismatch',
            parts=[Part(text='Test')],
        )
    )

    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    mismatched_task_event = create_sample_task(
        task_id=mismatched_task_id
    )  # Task with different ID

    async def event_stream_gen_mismatch():
        yield mismatched_task_event

    mock_result_aggregator_instance.consume_and_emit.return_value = (
        event_stream_gen_mismatch()
    )

    with (
        patch(
            'a2a.server.request_handlers.default_request_handler.ResultAggregator',
            return_value=mock_result_aggregator_instance,
        ),
        patch(
            'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
            return_value=None,
        ),
    ):
        with pytest.raises(InternalError) as exc_info:
            async for _ in request_handler.on_message_send_stream(
                params, create_server_call_context()
            ):
                pass  # Consume the stream to trigger the error

    assert 'Task ID mismatch' in exc_info.value.message  # type: ignore


@pytest.mark.asyncio
async def test_cleanup_producer_task_id_not_in_running_agents(agent_card):
    """Test _cleanup_producer when task_id is not in _running_agents (e.g., already cleaned up)."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_queue_manager = AsyncMock(spec=QueueManager)
    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
        agent_card=agent_card,
    )

    task_id = 'task_already_cleaned'

    # Create a real, completed asyncio.Task for the test
    async def noop_coro_for_task():
        pass

    mock_producer_task = asyncio.create_task(noop_coro_for_task())
    await asyncio.sleep(
        0
    )  # Ensure the task has a chance to complete/be scheduled

    # Call cleanup directly, ensuring task_id is NOT in _running_agents
    # This simulates a race condition or double cleanup.
    if task_id in request_handler._running_agents:
        del request_handler._running_agents[task_id]  # Ensure it's not there

    try:
        await request_handler._cleanup_producer(mock_producer_task, task_id)
    except Exception as e:
        pytest.fail(f'_cleanup_producer raised an exception unexpectedly: {e}')

    # Verify queue_manager.close was still called
    mock_queue_manager.close.assert_awaited_once_with(task_id)
    # No error should be raised by pop if key is missing and default is None.


@pytest.mark.asyncio
async def test_set_task_push_notification_config_no_notifier(agent_card):
    """Test on_create_task_push_notification_config when _push_config_store is None."""
    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=AsyncMock(spec=TaskStore),
        push_config_store=None,  # Explicitly None,
        agent_card=agent_card,
    )
    params = TaskPushNotificationConfig(
        task_id='task1',
        url='http://example.com',
    )

    with pytest.raises(PushNotificationNotSupportedError):
        await request_handler.on_create_task_push_notification_config(
            params, create_server_call_context()
        )


@pytest.mark.asyncio
async def test_set_task_push_notification_config_task_not_found(agent_card):
    """Test on_create_task_push_notification_config when task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None  # Task not found
    mock_push_store = AsyncMock(spec=PushNotificationConfigStore)
    mock_push_sender = AsyncMock(spec=PushNotificationSender)

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=mock_push_store,
        push_sender=mock_push_sender,
        agent_card=agent_card,
    )
    params = TaskPushNotificationConfig(
        task_id='non_existent_task',
        url='http://example.com',
    )

    context = create_server_call_context()
    with pytest.raises(TaskNotFoundError):
        await request_handler.on_create_task_push_notification_config(
            params, context
        )
    mock_task_store.get.assert_awaited_once_with('non_existent_task', context)
    mock_push_store.set_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_task_push_notification_config_no_store(agent_card):
    """Test on_get_task_push_notification_config when _push_config_store is None."""
    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=AsyncMock(spec=TaskStore),
        push_config_store=None,  # Explicitly None,
        agent_card=agent_card,
    )
    params = GetTaskPushNotificationConfigRequest(
        task_id='task1',
        id='task_push_notification_config',
    )

    with pytest.raises(PushNotificationNotSupportedError):
        await request_handler.on_get_task_push_notification_config(
            params, create_server_call_context()
        )


@pytest.mark.asyncio
async def test_get_task_push_notification_config_task_not_found(agent_card):
    """Test on_get_task_push_notification_config when task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None  # Task not found
    mock_push_store = AsyncMock(spec=PushNotificationConfigStore)

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=mock_push_store,
        agent_card=agent_card,
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
async def test_get_task_push_notification_config_info_not_found(agent_card):
    """Test on_get_task_push_notification_config when push_config_store.get_info returns None."""
    mock_task_store = AsyncMock(spec=TaskStore)

    sample_task = create_sample_task(task_id='non_existent_task')
    mock_task_store.get.return_value = sample_task

    mock_push_store = AsyncMock(spec=PushNotificationConfigStore)
    mock_push_store.get_info.return_value = None  # Info not found

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=mock_push_store,
        agent_card=agent_card,
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
async def test_get_task_push_notification_config_info_with_config(agent_card):
    """Test on_get_task_push_notification_config with valid push config id"""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = Task(id='task_1', context_id='ctx_1')

    push_store = InMemoryPushNotificationConfigStore()

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=agent_card,
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
async def test_get_task_push_notification_config_info_with_config_no_id(
    agent_card,
):
    """Test on_get_task_push_notification_config with no push config id"""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = Task(id='task_1', context_id='ctx_1')

    push_store = InMemoryPushNotificationConfigStore()

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=agent_card,
    )

    set_config_params = TaskPushNotificationConfig(
        task_id='task_1',
        url='http://1.example.com',
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
async def test_on_subscribe_to_task_task_not_found(agent_card):
    """Test on_subscribe_to_task when the task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None  # Task not found

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        agent_card=agent_card,
    )
    params = SubscribeToTaskRequest(id='resub_task_not_found')

    context = create_server_call_context()
    with pytest.raises(TaskNotFoundError):
        # Need to consume the async generator to trigger the error
        async for _ in request_handler.on_subscribe_to_task(params, context):
            pass
    mock_task_store.get.assert_awaited_once_with(
        'resub_task_not_found', context
    )


@pytest.mark.asyncio
async def test_on_subscribe_to_task_queue_not_found(agent_card):
    """Test on_subscribe_to_task when the queue is not found by queue_manager.tap."""
    mock_task_store = AsyncMock(spec=TaskStore)
    sample_task = create_sample_task(task_id='resub_queue_not_found')
    mock_task_store.get.return_value = sample_task

    mock_queue_manager = AsyncMock(spec=QueueManager)
    mock_queue_manager.tap.return_value = None  # Queue not found

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
        agent_card=agent_card,
    )
    params = SubscribeToTaskRequest(id='resub_queue_not_found')

    context = create_server_call_context()
    with pytest.raises(TaskNotFoundError):
        async for _ in request_handler.on_subscribe_to_task(params, context):
            pass
    mock_task_store.get.assert_awaited_once_with(
        'resub_queue_not_found', context
    )
    mock_queue_manager.tap.assert_awaited_once_with('resub_queue_not_found')


@pytest.mark.asyncio
async def test_on_message_send_stream(agent_card):
    request_handler = DefaultRequestHandler(
        MockAgentExecutor(),
        InMemoryTaskStore(),
        agent_card=agent_card,
    )
    message_params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg-123',
            parts=[Part(text='How are you?')],
        ),
    )

    async def consume_stream():
        events = []
        async for event in request_handler.on_message_send_stream(
            message_params, create_server_call_context()
        ):
            events.append(event)
            if len(events) >= 3:
                break  # Stop after a few events

        return events

    # Consume first 3 events from the stream and measure time
    start = time.perf_counter()
    events = await consume_stream()
    elapsed = time.perf_counter() - start

    # Assert we received events quickly
    assert len(events) == 3
    assert elapsed < 0.5

    texts = [p.text for e in events for p in e.status.message.parts]
    assert texts == ['Event 0', 'Event 1', 'Event 2']


@pytest.mark.asyncio
async def test_list_task_push_notification_config_no_store(agent_card):
    """Test on_list_task_push_notification_configs when _push_config_store is None."""
    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=AsyncMock(spec=TaskStore),
        push_config_store=None,  # Explicitly None,
        agent_card=agent_card,
    )
    params = ListTaskPushNotificationConfigsRequest(task_id='task1')

    with pytest.raises(PushNotificationNotSupportedError):
        await request_handler.on_list_task_push_notification_configs(
            params, create_server_call_context()
        )


@pytest.mark.asyncio
async def test_list_task_push_notification_config_task_not_found(agent_card):
    """Test on_list_task_push_notification_configs when task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None  # Task not found
    mock_push_store = AsyncMock(spec=PushNotificationConfigStore)

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=mock_push_store,
        agent_card=agent_card,
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
async def test_list_no_task_push_notification_config_info(agent_card):
    """Test on_get_task_push_notification_config when push_config_store.get_info returns []"""
    mock_task_store = AsyncMock(spec=TaskStore)

    sample_task = create_sample_task(task_id='non_existent_task')
    mock_task_store.get.return_value = sample_task

    push_store = InMemoryPushNotificationConfigStore()

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=agent_card,
    )
    params = ListTaskPushNotificationConfigsRequest(task_id='non_existent_task')

    result = await request_handler.on_list_task_push_notification_configs(
        params, create_server_call_context()
    )
    assert result.configs == []


@pytest.mark.asyncio
async def test_list_task_push_notification_config_info_with_config(agent_card):
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

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=agent_card,
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
async def test_list_task_push_notification_config_info_with_config_and_no_id(
    agent_card,
):
    """Test on_list_task_push_notification_configs with no push config id"""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = Task(id='task_1', context_id='ctx_1')

    push_store = InMemoryPushNotificationConfigStore()

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=agent_card,
    )

    # multiple calls without config id should replace the existing
    set_config_params1 = TaskPushNotificationConfig(
        task_id='task_1',
        url='http://1.example.com',
    )
    await request_handler.on_create_task_push_notification_config(
        set_config_params1, create_server_call_context()
    )

    set_config_params2 = TaskPushNotificationConfig(
        task_id='task_1',
        url='http://2.example.com',
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
async def test_delete_task_push_notification_config_no_store(agent_card):
    """Test on_delete_task_push_notification_config when _push_config_store is None."""
    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=AsyncMock(spec=TaskStore),
        push_config_store=None,  # Explicitly None,
        agent_card=agent_card,
    )
    params = DeleteTaskPushNotificationConfigRequest(
        task_id='task1', id='config1'
    )

    with pytest.raises(PushNotificationNotSupportedError):
        await request_handler.on_delete_task_push_notification_config(
            params, create_server_call_context()
        )


@pytest.mark.asyncio
async def test_delete_task_push_notification_config_task_not_found(agent_card):
    """Test on_delete_task_push_notification_config when task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None  # Task not found
    mock_push_store = AsyncMock(spec=PushNotificationConfigStore)

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=mock_push_store,
        agent_card=agent_card,
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
async def test_delete_no_task_push_notification_config_info(agent_card):
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

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=agent_card,
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
async def test_delete_task_push_notification_config_info_with_config(
    agent_card,
):
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

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=agent_card,
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
async def test_delete_task_push_notification_config_info_with_config_and_no_id(
    agent_card,
):
    """Test on_list_task_push_notification_configs with no push config id"""
    mock_task_store = AsyncMock(spec=TaskStore)

    sample_task = create_sample_task(task_id='non_existent_task')
    mock_task_store.get.return_value = sample_task

    push_config = TaskPushNotificationConfig(url='http://example.com')

    # insertion without id should replace the existing config
    push_store = InMemoryPushNotificationConfigStore()
    context = create_server_call_context()
    await push_store.set_info('task_1', push_config, context)
    await push_store.set_info('task_1', push_config, context)

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=agent_card,
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
async def test_on_message_send_task_in_terminal_state(
    terminal_state, agent_card
):
    """Test on_message_send when task is already in a terminal state."""
    state_name = TaskState.Name(terminal_state)
    task_id = f'terminal_task_{state_name}'
    terminal_task = create_sample_task(
        task_id=task_id, status_state=terminal_state
    )

    mock_task_store = AsyncMock(spec=TaskStore)
    # The get method of TaskManager calls task_store.get.
    # We mock TaskManager.get_task which is an async method.
    # So we should patch that instead.

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        agent_card=agent_card,
    )

    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_terminal',
            parts=[Part(text='Test')],
            task_id=task_id,
        )
    )

    # Patch the TaskManager's get_task method to return our terminal task
    with patch(
        'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
        return_value=terminal_task,
    ):
        with pytest.raises(InvalidParamsError) as exc_info:
            await request_handler.on_message_send(
                params, create_server_call_context()
            )

    assert (
        f'Task {task_id} is in terminal state: {terminal_state}'
        in exc_info.value.message
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal_state', TERMINAL_TASK_STATES)
async def test_on_message_send_stream_task_in_terminal_state(
    terminal_state, agent_card
):
    """Test on_message_send_stream when task is already in a terminal state."""
    state_name = TaskState.Name(terminal_state)
    task_id = f'terminal_stream_task_{state_name}'
    terminal_task = create_sample_task(
        task_id=task_id, status_state=terminal_state
    )

    mock_task_store = AsyncMock(spec=TaskStore)

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        agent_card=agent_card,
    )

    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_terminal_stream',
            parts=[Part(text='Test')],
            task_id=task_id,
        )
    )

    with patch(
        'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
        return_value=terminal_task,
    ):
        with pytest.raises(InvalidParamsError) as exc_info:
            async for _ in request_handler.on_message_send_stream(
                params, create_server_call_context()
            ):
                pass  # pragma: no cover

    assert (
        f'Task {task_id} is in terminal state: {terminal_state}'
        in exc_info.value.message
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal_state', TERMINAL_TASK_STATES)
async def test_on_subscribe_to_task_in_terminal_state(
    terminal_state, agent_card
):
    """Test on_subscribe_to_task when task is in a terminal state."""
    state_name = TaskState.Name(terminal_state)
    task_id = f'resub_terminal_task_{state_name}'
    terminal_task = create_sample_task(
        task_id=task_id, status_state=terminal_state
    )

    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = terminal_task

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        queue_manager=AsyncMock(spec=QueueManager),
        agent_card=agent_card,
    )
    params = SubscribeToTaskRequest(id=f'{task_id}')

    context = create_server_call_context()

    with pytest.raises(UnsupportedOperationError) as exc_info:
        async for _ in request_handler.on_subscribe_to_task(params, context):
            pass  # pragma: no cover

    assert (
        f'Task {task_id} is in terminal state: {terminal_state}'
        in exc_info.value.message
    )
    mock_task_store.get.assert_awaited_once_with(f'{task_id}', context)


@pytest.mark.asyncio
async def test_on_message_send_task_id_provided_but_task_not_found(agent_card):
    """Test on_message_send when task_id is provided but task doesn't exist."""
    task_id = 'nonexistent_task'
    mock_task_store = AsyncMock(spec=TaskStore)

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        agent_card=agent_card,
    )

    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_nonexistent',
            parts=[Part(text='Hello')],
            task_id=task_id,
            context_id='ctx1',
        )
    )

    # Mock TaskManager.get_task to return None (task not found)
    with patch(
        'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
        return_value=None,
    ):
        with pytest.raises(TaskNotFoundError) as exc_info:
            await request_handler.on_message_send(
                params, create_server_call_context()
            )

    assert (
        f'Task {task_id} was specified but does not exist'
        in exc_info.value.message
    )


@pytest.mark.asyncio
async def test_on_message_send_stream_task_id_provided_but_task_not_found(
    agent_card,
):
    """Test on_message_send_stream when task_id is provided but task doesn't exist."""
    task_id = 'nonexistent_stream_task'
    mock_task_store = AsyncMock(spec=TaskStore)

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        agent_card=agent_card,
    )

    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg_nonexistent_stream',
            parts=[Part(text='Hello')],
            task_id=task_id,
            context_id='ctx1',
        )
    )

    # Mock TaskManager.get_task to return None (task not found)
    with patch(
        'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
        return_value=None,
    ):
        with pytest.raises(TaskNotFoundError) as exc_info:
            # Need to consume the async generator to trigger the error
            async for _ in request_handler.on_message_send_stream(
                params, create_server_call_context()
            ):
                pass

    assert (
        f'Task {task_id} was specified but does not exist'
        in exc_info.value.message
    )


class HelloWorldAgentExecutor(AgentExecutor):
    """Test Agent Implementation."""

    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue,
    ) -> None:
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


# Repro is straight from the https://github.com/a2aproject/a2a-python/issues/609.
# It uses timeout to test against infinite wait, if it's going to be flaky,
# we should reconsider the approach.
@pytest.mark.asyncio
@pytest.mark.timeout(1)
async def test_on_message_send_error_does_not_hang(agent_card):
    """Test that if the consumer raises an exception during blocking wait, the producer is cancelled and no deadlock occurs."""
    agent = HelloWorldAgentExecutor()
    task_store = AsyncMock(spec=TaskStore)
    task_store.save.side_effect = RuntimeError('This is an Error!')

    request_handler = DefaultRequestHandler(
        agent_executor=agent,
        task_store=task_store,
        agent_card=agent_card,
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
async def test_on_get_task_negative_history_length_error(agent_card):
    """Test on_get_task raises error for negative history length."""
    mock_task_store = AsyncMock(spec=TaskStore)
    request_handler = DefaultRequestHandler(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=mock_task_store,
        agent_card=agent_card,
    )
    # GetTaskRequest also has history_length
    params = GetTaskRequest(id='task1', history_length=-1)
    context = create_server_call_context()

    with pytest.raises(InvalidParamsError) as exc_info:
        await request_handler.on_get_task(params, context)

    assert 'history length must be non-negative' in exc_info.value.message


@pytest.mark.asyncio
async def test_on_list_tasks_page_size_too_small(agent_card):
    """Test on_list_tasks raises error for page_size < 1."""
    mock_task_store = AsyncMock(spec=TaskStore)
    request_handler = DefaultRequestHandler(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=mock_task_store,
        agent_card=agent_card,
    )
    params = ListTasksRequest(page_size=0)
    context = create_server_call_context()

    with pytest.raises(InvalidParamsError) as exc_info:
        await request_handler.on_list_tasks(params, context)

    assert 'minimum page size is 1' in exc_info.value.message


@pytest.mark.asyncio
async def test_on_list_tasks_page_size_too_large(agent_card):
    """Test on_list_tasks raises error for page_size > 100."""
    mock_task_store = AsyncMock(spec=TaskStore)
    request_handler = DefaultRequestHandler(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=mock_task_store,
        agent_card=agent_card,
    )
    params = ListTasksRequest(page_size=101)
    context = create_server_call_context()

    with pytest.raises(InvalidParamsError) as exc_info:
        await request_handler.on_list_tasks(params, context)

    assert 'maximum page size is 100' in exc_info.value.message


@pytest.mark.asyncio
async def test_on_message_send_negative_history_length_error(agent_card):
    """Test on_message_send raises error for negative history length in configuration."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)
    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        agent_card=agent_card,
    )

    message_config = SendMessageConfiguration(
        history_length=-1,
        accepted_output_modes=['text/plain'],
    )
    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER, message_id='msg1', parts=[Part(text='Test')]
        ),
        configuration=message_config,
    )
    context = create_server_call_context()

    with pytest.raises(InvalidParamsError) as exc_info:
        await request_handler.on_message_send(params, context)

    assert 'history length must be non-negative' in exc_info.value.message


@pytest.mark.asyncio
async def test_on_get_extended_agent_card_success(agent_card):
    """Test on_get_extended_agent_card when extended_agent_card is supported."""
    agent_card.capabilities.extended_agent_card = True

    extended_agent_card = AgentCard(
        name='Extended Agent',
        description='An extended agent',
        version='1.0.0',
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=True,
            extended_agent_card=True,
        ),
    )

    request_handler = DefaultRequestHandler(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=AsyncMock(spec=TaskStore),
        agent_card=agent_card,
        extended_agent_card=extended_agent_card,
    )

    params = GetExtendedAgentCardRequest()
    context = create_server_call_context()

    result = await request_handler.on_get_extended_agent_card(params, context)

    assert result == extended_agent_card


@pytest.mark.asyncio
async def test_on_message_send_stream_unsupported(agent_card):
    """Test on_message_send_stream when streaming is unsupported."""
    agent_card.capabilities.streaming = False

    request_handler = DefaultRequestHandler(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=AsyncMock(spec=TaskStore),
        agent_card=agent_card,
    )

    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            message_id='msg-unsupported',
            parts=[Part(text='hi')],
        )
    )

    context = create_server_call_context()

    with pytest.raises(UnsupportedOperationError):
        async for _ in request_handler.on_message_send_stream(params, context):
            pass


@pytest.mark.asyncio
async def test_on_get_extended_agent_card_unsupported(agent_card):
    """Test on_get_extended_agent_card when extended_agent_card is unsupported."""
    agent_card.capabilities.extended_agent_card = False

    request_handler = DefaultRequestHandler(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=AsyncMock(spec=TaskStore),
        agent_card=agent_card,
    )

    params = GetExtendedAgentCardRequest()
    context = create_server_call_context()

    with pytest.raises(UnsupportedOperationError):
        await request_handler.on_get_extended_agent_card(params, context)


@pytest.mark.asyncio
async def test_on_create_task_push_notification_config_unsupported(agent_card):
    """Test on_create_task_push_notification_config when push_notifications is unsupported."""
    agent_card.capabilities.push_notifications = False

    request_handler = DefaultRequestHandler(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=AsyncMock(spec=TaskStore),
        agent_card=agent_card,
    )

    params = TaskPushNotificationConfig(url='http://callback.com/push')

    context = create_server_call_context()

    with pytest.raises(PushNotificationNotSupportedError):
        await request_handler.on_create_task_push_notification_config(
            params, context
        )


@pytest.mark.asyncio
async def test_on_subscribe_to_task_unsupported(agent_card):
    """Test on_subscribe_to_task when streaming is unsupported."""
    agent_card.capabilities.streaming = False

    request_handler = DefaultRequestHandler(
        agent_executor=AsyncMock(spec=AgentExecutor),
        task_store=AsyncMock(spec=TaskStore),
        agent_card=agent_card,
    )

    params = SubscribeToTaskRequest(id='some_task')
    context = create_server_call_context()

    with pytest.raises(UnsupportedOperationError):
        # We need to exhaust the generator to trigger the decorator evaluation
        async for _ in request_handler.on_subscribe_to_task(params, context):
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
async def test_on_list_task_push_notification_configs_is_owner_scoped(
    agent_card,
):
    """Bob must not see Alice's configs via tasks/pushNotificationConfig/list.

    Both users have access to the shared task (the mocked TaskStore
    returns it for any caller), but listing must only return the
    caller's own configs.
    """
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = create_sample_task(task_id='shared-task')

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

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=agent_card,
    )

    alice_listing = (
        await request_handler.on_list_task_push_notification_configs(
            ListTaskPushNotificationConfigsRequest(task_id='shared-task'),
            alice_ctx,
        )
    )
    assert {c.id for c in alice_listing.configs} == {'alice-cfg'}
    # Sanity: Bob's secret is not in the response.
    assert all(c.token != 'bob-secret' for c in alice_listing.configs), (
        'Listing for Alice must not expose Bob-owned tokens'
    )

    bob_listing = await request_handler.on_list_task_push_notification_configs(
        ListTaskPushNotificationConfigsRequest(task_id='shared-task'),
        bob_ctx,
    )
    assert {c.id for c in bob_listing.configs} == {'bob-cfg'}
    assert all(c.token != 'alice-secret' for c in bob_listing.configs), (
        'Listing for Bob must not expose Alice-owned tokens'
    )


@pytest.mark.asyncio
async def test_on_list_task_push_notification_configs_returns_empty_for_third_user(
    agent_card,
):
    """A third user with task access but no registered configs sees an empty list."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = create_sample_task(task_id='shared-task')

    push_store = InMemoryPushNotificationConfigStore()
    await push_store.set_info(
        'shared-task',
        TaskPushNotificationConfig(
            task_id='shared-task',
            id='alice-cfg',
            url='http://alice.example.com/cb',
        ),
        _ctx('alice'),
    )

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=agent_card,
    )

    carol_listing = (
        await request_handler.on_list_task_push_notification_configs(
            ListTaskPushNotificationConfigsRequest(task_id='shared-task'),
            _ctx('carol'),
        )
    )
    assert carol_listing.configs == []


@pytest.mark.asyncio
async def test_on_get_task_push_notification_config_is_owner_scoped(
    agent_card,
):
    """Bob cannot fetch Alice's config by ID via tasks/pushNotificationConfig/get.

    Even when Bob can read the task and knows (or guesses) the
    config_id, the handler must raise TaskNotFoundError because Alice's
    config is not in Bob's owner partition.
    """
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = create_sample_task(task_id='shared-task')

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

    request_handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
        agent_card=agent_card,
    )

    # Alice can read her own config.
    alice_view = await request_handler.on_get_task_push_notification_config(
        GetTaskPushNotificationConfigRequest(
            task_id='shared-task', id='alice-cfg'
        ),
        alice_ctx,
    )
    assert alice_view.id == 'alice-cfg'
    assert alice_view.token == 'alice-secret'

    # Bob cannot, even guessing the exact config_id.
    with pytest.raises(TaskNotFoundError):
        await request_handler.on_get_task_push_notification_config(
            GetTaskPushNotificationConfigRequest(
                task_id='shared-task', id='alice-cfg'
            ),
            _ctx('bob'),
        )
