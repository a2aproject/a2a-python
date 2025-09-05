import asyncio
import logging
import time

from unittest.mock import (
    AsyncMock,
    MagicMock,
    PropertyMock,
    patch,
)

import pytest

from a2a.server.agent_execution import (
    AgentExecutor,
    RequestContext,
    RequestContextBuilder,
    SimpleRequestContextBuilder,
)
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue, InMemoryQueueManager, QueueManager
from a2a.server.request_handlers import DefaultRequestHandler
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
    DeleteTaskPushNotificationConfigParams,
    GetTaskPushNotificationConfigParams,
    InternalError,
    InvalidParamsError,
    ListTaskPushNotificationConfigParams,
    Message,
    MessageSendConfiguration,
    MessageSendParams,
    Part,
    PushNotificationConfig,
    Role,
    Task,
    TaskIdParams,
    TaskNotFoundError,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskState,
    TaskStatus,
    TextPart,
    UnsupportedOperationError,
)
from a2a.utils import (
    new_task,
)


class DummyAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        task_updater = TaskUpdater(
            event_queue, context.task_id, context.context_id
        )
        async for i in self._run():
            parts = [Part(root=TextPart(text=f'Event {i}'))]
            try:
                await task_updater.update_status(
                    TaskState.working,
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
    task_id='task1', status_state=TaskState.submitted, context_id='ctx1'
) -> Task:
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=status_state),
    )


# Helper to create ServerCallContext
def create_server_call_context() -> ServerCallContext:
    # Assuming UnauthenticatedUser is available or can be imported
    from a2a.auth.user import UnauthenticatedUser

    return ServerCallContext(user=UnauthenticatedUser())


def test_init_default_dependencies():
    """Test that default dependencies are created if not provided."""
    agent_executor = DummyAgentExecutor()
    task_store = InMemoryTaskStore()

    handler = DefaultRequestHandler(
        agent_executor=agent_executor, task_store=task_store
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
async def test_on_get_task_not_found():
    """Test on_get_task when task_store.get returns None."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(), task_store=mock_task_store
    )

    params = TaskQueryParams(id='non_existent_task')

    from a2a.utils.errors import ServerError  # Local import for ServerError

    context = create_server_call_context()
    with pytest.raises(ServerError) as exc_info:
        await request_handler.on_get_task(params, context)

    assert isinstance(exc_info.value.error, TaskNotFoundError)
    mock_task_store.get.assert_awaited_once_with('non_existent_task', context)


@pytest.mark.asyncio
async def test_on_cancel_task_task_not_found():
    """Test on_cancel_task when the task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(), task_store=mock_task_store
    )
    params = TaskIdParams(id='task_not_found_for_cancel')

    from a2a.utils.errors import ServerError  # Local import

    context = create_server_call_context()
    with pytest.raises(ServerError) as exc_info:
        await request_handler.on_cancel_task(params, context)

    assert isinstance(exc_info.value.error, TaskNotFoundError)
    mock_task_store.get.assert_awaited_once_with(
        'task_not_found_for_cancel', context
    )


@pytest.mark.asyncio
async def test_on_cancel_task_queue_tap_returns_none():
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
            status_state=TaskState.canceled,  # Expected final state
        )
    )

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
    )

    context = create_server_call_context()
    with patch(
        'a2a.server.request_handlers.default_request_handler.ResultAggregator',
        return_value=mock_result_aggregator_instance,
    ):
        params = TaskIdParams(id='tap_none_task')
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
    assert result_task.status.state == TaskState.canceled


@pytest.mark.asyncio
async def test_on_cancel_task_cancels_running_agent():
    """Test on_cancel_task cancels a running agent task."""
    task_id = 'running_agent_task_to_cancel'
    sample_task = create_sample_task(task_id=task_id)
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = sample_task

    mock_queue_manager = AsyncMock(spec=QueueManager)
    mock_event_queue = AsyncMock(spec=EventQueue)
    mock_queue_manager.tap.return_value = mock_event_queue

    mock_agent_executor = AsyncMock(spec=AgentExecutor)

    # Mock ResultAggregator
    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    mock_result_aggregator_instance.consume_all.return_value = (
        create_sample_task(task_id=task_id, status_state=TaskState.canceled)
    )

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
    )

    # Simulate a running agent task
    mock_producer_task = AsyncMock(spec=asyncio.Task)
    request_handler._running_agents[task_id] = mock_producer_task

    context = create_server_call_context()
    with patch(
        'a2a.server.request_handlers.default_request_handler.ResultAggregator',
        return_value=mock_result_aggregator_instance,
    ):
        params = TaskIdParams(id=task_id)
        await request_handler.on_cancel_task(params, context)

    mock_producer_task.cancel.assert_called_once()
    mock_agent_executor.cancel.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_cancel_task_completes_during_cancellation():
    """Test on_cancel_task fails to cancel a task due to concurrent task completion."""
    task_id = 'running_agent_task_to_cancel'
    sample_task = create_sample_task(task_id=task_id)
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = sample_task

    mock_queue_manager = AsyncMock(spec=QueueManager)
    mock_event_queue = AsyncMock(spec=EventQueue)
    mock_queue_manager.tap.return_value = mock_event_queue

    mock_agent_executor = AsyncMock(spec=AgentExecutor)

    # Mock ResultAggregator
    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    mock_result_aggregator_instance.consume_all.return_value = (
        create_sample_task(task_id=task_id, status_state=TaskState.completed)
    )

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
    )

    # Simulate a running agent task
    mock_producer_task = AsyncMock(spec=asyncio.Task)
    request_handler._running_agents[task_id] = mock_producer_task

    from a2a.utils.errors import (
        ServerError,  # Local import
        TaskNotCancelableError,  # Local import
    )

    with patch(
        'a2a.server.request_handlers.default_request_handler.ResultAggregator',
        return_value=mock_result_aggregator_instance,
    ):
        params = TaskIdParams(id=task_id)
        with pytest.raises(ServerError) as exc_info:
            await request_handler.on_cancel_task(
                params, create_server_call_context()
            )

    mock_producer_task.cancel.assert_called_once()
    mock_agent_executor.cancel.assert_awaited_once()
    assert isinstance(exc_info.value.error, TaskNotCancelableError)


@pytest.mark.asyncio
async def test_on_cancel_task_invalid_result_type():
    """Test on_cancel_task when result_aggregator returns a Message instead of a Task."""
    task_id = 'cancel_invalid_result_task'
    sample_task = create_sample_task(task_id=task_id)
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = sample_task

    mock_queue_manager = AsyncMock(spec=QueueManager)
    mock_event_queue = AsyncMock(spec=EventQueue)
    mock_queue_manager.tap.return_value = mock_event_queue

    mock_agent_executor = AsyncMock(spec=AgentExecutor)

    # Mock ResultAggregator to return a Message
    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    mock_result_aggregator_instance.consume_all.return_value = Message(
        message_id='unexpected_msg', role=Role.agent, parts=[]
    )

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
    )

    from a2a.utils.errors import ServerError  # Local import

    with patch(
        'a2a.server.request_handlers.default_request_handler.ResultAggregator',
        return_value=mock_result_aggregator_instance,
    ):
        params = TaskIdParams(id=task_id)
        with pytest.raises(ServerError) as exc_info:
            await request_handler.on_cancel_task(
                params, create_server_call_context()
            )

    assert isinstance(exc_info.value.error, InternalError)
    assert (
        'Agent did not return valid response for cancel'
        in exc_info.value.error.message
    )  # type: ignore


@pytest.mark.asyncio
async def test_on_message_send_with_push_notification():
    """Test on_message_send sets push notification info if provided."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_push_notification_store = AsyncMock(spec=PushNotificationConfigStore)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)
    mock_request_context_builder = AsyncMock(spec=RequestContextBuilder)

    task_id = 'push_task_1'
    context_id = 'push_ctx_1'
    sample_initial_task = create_sample_task(
        task_id=task_id, context_id=context_id, status_state=TaskState.submitted
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
    )

    push_config = PushNotificationConfig(url='http://callback.com/push')
    message_config = MessageSendConfiguration(
        push_notification_config=push_config,
        accepted_output_modes=['text/plain'],  # Added required field
    )
    params = MessageSendParams(
        message=Message(
            role=Role.user,
            message_id='msg_push',
            parts=[],
            task_id=task_id,
            context_id=context_id,
        ),
        configuration=message_config,
    )

    # Mock ResultAggregator and its consume_and_break_on_interrupt
    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    final_task_result = create_sample_task(
        task_id=task_id, context_id=context_id, status_state=TaskState.completed
    )
    mock_result_aggregator_instance.consume_and_break_on_interrupt.return_value = (
        final_task_result,
        False,
    )

    # Mock the current_result property to return the final task result
    async def get_current_result():
        return final_task_result

    # Configure the 'current_result' property on the type of the mock instance
    type(mock_result_aggregator_instance).current_result = PropertyMock(
        return_value=get_current_result()
    )

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
        await request_handler.on_message_send(
            params, create_server_call_context()
        )

    mock_push_notification_store.set_info.assert_awaited_once_with(
        task_id, push_config
    )
    # Other assertions for full flow if needed (e.g., agent execution)
    mock_agent_executor.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_message_send_with_push_notification_in_non_blocking_request():
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
        task_id=task_id, context_id=context_id, status_state=TaskState.working
    )

    # Create a final task that will be available during background processing
    final_task = create_sample_task(
        task_id=task_id, context_id=context_id, status_state=TaskState.completed
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
    )

    # Configure push notification
    push_config = PushNotificationConfig(url='http://callback.com/push')
    message_config = MessageSendConfiguration(
        push_notification_config=push_config,
        accepted_output_modes=['text/plain'],
        blocking=False,  # Non-blocking request
    )
    params = MessageSendParams(
        message=Message(
            role=Role.user,
            message_id='msg_non_blocking',
            parts=[],
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
    )

    # Mock the current_result property to return the final task
    async def get_current_result():
        return final_task

    type(mock_result_aggregator_instance).current_result = PropertyMock(
        return_value=get_current_result()
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
        return initial_task, True  # interrupted = True for non-blocking

    mock_result_aggregator_instance.consume_and_break_on_interrupt = (
        mock_consume_and_break_on_interrupt
    )

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
        result = await request_handler.on_message_send(
            params, create_server_call_context()
        )

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
    mock_push_sender.send_notification.assert_called_with(final_task)

    # Verify that the push notification config was stored
    mock_push_notification_store.set_info.assert_awaited_once_with(
        task_id, push_config
    )


@pytest.mark.asyncio
async def test_on_message_send_with_push_notification_no_existing_Task():
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
    )

    push_config = PushNotificationConfig(url='http://callback.com/push')
    message_config = MessageSendConfiguration(
        push_notification_config=push_config,
        accepted_output_modes=['text/plain'],  # Added required field
    )
    params = MessageSendParams(
        message=Message(role=Role.user, message_id='msg_push', parts=[]),
        configuration=message_config,
    )

    # Mock ResultAggregator and its consume_and_break_on_interrupt
    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    final_task_result = create_sample_task(
        task_id=task_id, context_id=context_id, status_state=TaskState.completed
    )
    mock_result_aggregator_instance.consume_and_break_on_interrupt.return_value = (
        final_task_result,
        False,
    )

    # Mock the current_result property to return the final task result
    async def get_current_result():
        return final_task_result

    # Configure the 'current_result' property on the type of the mock instance
    type(mock_result_aggregator_instance).current_result = PropertyMock(
        return_value=get_current_result()
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
        await request_handler.on_message_send(
            params, create_server_call_context()
        )

    mock_push_notification_store.set_info.assert_awaited_once_with(
        task_id, push_config
    )
    # Other assertions for full flow if needed (e.g., agent execution)
    mock_agent_executor.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_message_send_no_result_from_aggregator():
    """Test on_message_send when aggregator returns (None, False)."""
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
    )
    params = MessageSendParams(
        message=Message(role=Role.user, message_id='msg_no_res', parts=[])
    )

    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    mock_result_aggregator_instance.consume_and_break_on_interrupt.return_value = (
        None,
        False,
    )

    from a2a.utils.errors import ServerError  # Local import

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
        with pytest.raises(ServerError) as exc_info:
            await request_handler.on_message_send(
                params, create_server_call_context()
            )

    assert isinstance(exc_info.value.error, InternalError)


@pytest.mark.asyncio
async def test_on_message_send_task_id_mismatch():
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
    )
    params = MessageSendParams(
        message=Message(role=Role.user, message_id='msg_id_mismatch', parts=[])
    )

    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    mismatched_task = create_sample_task(task_id=result_task_id)
    mock_result_aggregator_instance.consume_and_break_on_interrupt.return_value = (
        mismatched_task,
        False,
    )

    from a2a.utils.errors import ServerError  # Local import

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
        with pytest.raises(ServerError) as exc_info:
            await request_handler.on_message_send(
                params, create_server_call_context()
            )

    assert isinstance(exc_info.value.error, InternalError)
    assert 'Task ID mismatch' in exc_info.value.error.message  # type: ignore


class HelloAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        task = context.current_task
        if not task:
            assert context.message is not None, (
                'A message is required to create a new task'
            )
            task = new_task(context.message)  # type: ignore
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)

        try:
            parts = [Part(root=TextPart(text='I am working'))]
            await updater.update_status(
                TaskState.working,
                message=updater.new_agent_message(parts),
            )
        except Exception as e:
            # Stop processing when the event loop is closed
            logging.warning('Error: %s', e)
            return
        await updater.add_artifact(
            [Part(root=TextPart(text='Hello world!'))],
            name='conversion_result',
        )
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


@pytest.mark.asyncio
async def test_on_message_send_non_blocking():
    task_store = InMemoryTaskStore()
    push_store = InMemoryPushNotificationConfigStore()

    request_handler = DefaultRequestHandler(
        agent_executor=HelloAgentExecutor(),
        task_store=task_store,
        push_config_store=push_store,
    )
    params = MessageSendParams(
        message=Message(
            role=Role.user,
            message_id='msg_push',
            parts=[Part(root=TextPart(text='Hi'))],
        ),
        configuration=MessageSendConfiguration(
            blocking=False, accepted_output_modes=['text/plain']
        ),
    )

    result = await request_handler.on_message_send(
        params, create_server_call_context()
    )

    assert result is not None
    assert isinstance(result, Task)
    assert result.status.state == TaskState.submitted

    # Polling for 500ms until task is completed.
    task: Task | None = None
    for _ in range(5):
        await asyncio.sleep(0.1)
        task = await task_store.get(result.id)
        assert task is not None
        if task.status.state == TaskState.completed:
            break

    assert task is not None
    assert task.status.state == TaskState.completed


@pytest.mark.asyncio
async def test_on_message_send_interrupted_flow():
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
    )
    params = MessageSendParams(
        message=Message(role=Role.user, message_id='msg_interrupt', parts=[])
    )

    mock_result_aggregator_instance = AsyncMock(spec=ResultAggregator)
    interrupt_task_result = create_sample_task(
        task_id=task_id, status_state=TaskState.auth_required
    )
    mock_result_aggregator_instance.consume_and_break_on_interrupt.return_value = (
        interrupt_task_result,
        True,
    )  # Interrupted = True

    # Patch asyncio.create_task to verify _cleanup_producer is scheduled
    with (
        patch('asyncio.create_task') as mock_asyncio_create_task,
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
    for call_args_tuple in mock_asyncio_create_task.call_args_list:
        created_coro = call_args_tuple[0][0]
        if (
            hasattr(created_coro, '__name__')
            and created_coro.__name__ == '_cleanup_producer'
        ):
            found_cleanup_call = True
            break
    assert found_cleanup_call, (
        '_cleanup_producer was not scheduled with asyncio.create_task'
    )


@pytest.mark.asyncio
async def test_on_message_send_stream_with_push_notification():
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
        task_id=task_id, context_id=context_id, status_state=TaskState.submitted
    )

    # Task state for RequestContext
    task_for_rc = create_sample_task(
        task_id=task_id, context_id=context_id, status_state=TaskState.working
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
    )

    push_config = PushNotificationConfig(url='http://callback.stream.com/push')
    message_config = MessageSendConfiguration(
        push_notification_config=push_config,
        accepted_output_modes=['text/plain'],  # Added required field
    )
    params = MessageSendParams(
        message=Message(
            role=Role.user,
            message_id='msg_stream_push',
            parts=[],
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
        task_id=task_id, context_id=context_id, status_state=TaskState.working
    )
    event2_final_task = create_sample_task(
        task_id=task_id, context_id=context_id, status_state=TaskState.completed
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

    # Mock current_result property to return appropriate awaitables
    # Coroutines that will be returned by successive accesses to current_result
    async def current_result_coro1():
        return event1_task_update

    async def current_result_coro2():
        return event2_final_task

    # Use unittest.mock.PropertyMock for async property
    # We need to patch 'ResultAggregator.current_result' when this instance is used.
    # This is complex because ResultAggregator is instantiated inside the handler.
    # Easier: If mock_result_aggregator_instance is a MagicMock, we can assign a callable.
    # This part is tricky. Let's assume current_result is an async method for easier mocking first.
    # If it's truly a property, the mocking is harder with instance mocks.
    # Let's adjust the mock_result_aggregator_instance.current_result to be an AsyncMock directly
    # This means the code would call `await result_aggregator.current_result()`
    # But the actual code is `await result_aggregator.current_result`
    # This implies `result_aggregator.current_result` IS an awaitable.
    # So, we can mock it with a side_effect that returns awaitables (coroutines).

    # Create simple awaitables (coroutines) for side_effect
    async def get_event1():
        return event1_task_update

    async def get_event2():
        return event2_final_task

    # Make the current_result attribute of the mock instance itself an awaitable
    # This still means current_result is not callable.
    # For an async property, the mock needs to have current_result as a non-AsyncMock attribute
    # that is itself an awaitable.

    # Let's try to mock the property at the type level for ResultAggregator temporarily
    # This is not ideal as it affects all instances.

    # Alternative: Configure the AsyncMock for current_result to return a coroutine
    # when it's awaited. This is not directly supported by AsyncMock for property access.

    # Simplest for now: Assume `current_result` attribute of the mocked `ResultAggregator` instance
    # can be sequentially awaited if it's a list of awaitables that a test runner can handle.
    # This is likely to fail again but will clarify the exact point of await.
    # The error "TypeError: object AsyncMock can't be used in 'await' expression" means
    # `mock_result_aggregator_instance.current_result` is an AsyncMock, and that's what's awaited.
    # This AsyncMock needs to have a __await__ method.

    # Let's make the side_effect of the AsyncMock `current_result` provide the values.
    # This assumes that `await mock.property` somehow triggers a call to the mock.
    # This is not how AsyncMock works.

    # The code is `await result_aggregator.current_result`.
    # `result_aggregator` is an instance of `ResultAggregator`.
    # `current_result` is an async property.
    # So `result_aggregator.current_result` evaluates to a coroutine.
    # We need `mock_result_aggregator_instance.current_result` to be a coroutine,
    # or a list of coroutines if accessed multiple times.
    # This is best done by mocking the property itself.
    # Let's assume it's called twice.

    # We will patch ResultAggregator to be our mock_result_aggregator_instance
    # Then, we need to control what its `current_result` property returns.
    # We can use a PropertyMock for this, attached to the type of mock_result_aggregator_instance.

    # For this specific test, let's make current_result a simple async def method on the mock instance
    # This means we are slightly diverging from the "property" nature just for this mock.
    # Mock current_result property to return appropriate awaitables (coroutines) sequentially.
    async def get_event1_coro():
        return event1_task_update

    async def get_event2_coro():
        return event2_final_task

    # Configure the 'current_result' property on the type of the mock instance
    # This makes accessing `instance.current_result` call the side_effect function,
    # which then cycles through our list of coroutines.
    # We need a new PropertyMock for each instance, or patch the class.
    # Since mock_result_aggregator_instance is already created, we attach to its type.
    # This can be tricky. A more direct way is to ensure the instance's attribute `current_result`
    # behaves as desired. If `mock_result_aggregator_instance` is a `MagicMock`, its attributes are also mocks.

    # Let's make `current_result` a MagicMock whose side_effect returns the coroutines.
    # This means when `result_aggregator.current_result` is accessed, this mock is "called".
    # This isn't quite right for a property. A property isn't "called" on access.

    # Correct approach for mocking an async property on an instance mock:
    # Set the attribute `current_result` on the instance `mock_result_aggregator_instance`
    # to be a `PropertyMock` if we were patching the class.
    # Since we have the instance, we can try to replace its `current_result` attribute.
    # The instance `mock_result_aggregator_instance` is a `MagicMock`.
    # We can make `mock_result_aggregator_instance.current_result` a `PropertyMock`
    # that returns a coroutine. For multiple calls, `side_effect` on `PropertyMock` is a list of return_values.

    # Create a PropertyMock that will cycle through coroutines
    # This requires Python 3.8+ for PropertyMock to be directly usable with side_effect list for properties.
    # For older versions or for clarity with async properties, directly mocking the attribute
    # to be a series of awaitables is hard.
    # The easiest is to ensure `current_result` is an AsyncMock that returns the values.
    # The product code `await result_aggregator.current_result` means `current_result` must be an awaitable.

    # Let's make current_result an AsyncMock whose __call__ returns the sequence.
    # Mock current_result as an async property
    # Create coroutines that will be the "result" of awaiting the property
    async def get_current_result_coro1():
        return event1_task_update

    async def get_current_result_coro2():
        return event2_final_task

    # Configure the 'current_result' property on the mock_result_aggregator_instance
    # using PropertyMock attached to its type. This makes instance.current_result return
    # items from side_effect sequentially on each access.
    # Since current_result is an async property, these items should be coroutines.
    # We need to ensure that mock_result_aggregator_instance itself is the one patched.
    # The patch for ResultAggregator returns this instance.
    # So, we configure PropertyMock on the type of this specific mock instance.
    # This is slightly unusual; typically PropertyMock is used when patching a class.
    # A more straightforward approach for an instance is if its type is already a mock.
    # As mock_result_aggregator_instance is a MagicMock, we can configure its 'current_result'
    # attribute to be a PropertyMock.

    # Let's directly assign a PropertyMock to the type of the instance for `current_result`
    # This ensures that when `instance.current_result` is accessed, the PropertyMock's logic is triggered.
    # However, PropertyMock is usually used with `patch.object` or by setting it on the class.
    #
    # A simpler way for MagicMock instance:
    # `mock_result_aggregator_instance.current_result` is already a MagicMock (or AsyncMock if spec'd).
    # We need to make it return a coroutine upon access.
    # The most direct way to mock an async property on a MagicMock instance
    # such that it returns a sequence of awaitables:
    async def side_effect_current_result():
        yield event1_task_update
        yield event2_final_task

    # Create an async generator from the side effect
    current_result_gen = side_effect_current_result()

    # Make current_result return the next item from this generator (wrapped in a coroutine)
    # each time it's accessed.
    async def get_next_current_result():
        try:
            return await current_result_gen.__anext__()
        except StopAsyncIteration:
            # Handle case where it's awaited more times than values provided
            return None  # Or raise an error

    # Since current_result is a property, accessing it should return a coroutine.
    # We can achieve this by making mock_result_aggregator_instance.current_result
    # a MagicMock whose side_effect returns these coroutines.
    # This is still tricky because it's a property access.

    # Let's use the PropertyMock on the class being mocked via the patch.
    # Setup for consume_and_emit
    def sync_get_event_stream_gen_for_prop_test(*args, **kwargs):
        return event_stream_gen()

    mock_result_aggregator_instance.consume_and_emit = MagicMock(
        side_effect=sync_get_event_stream_gen_for_prop_test
    )

    # Configure current_result on the type of the mock_result_aggregator_instance
    # This makes it behave like a property that returns items from side_effect on access.
    type(mock_result_aggregator_instance).current_result = PropertyMock(
        side_effect=[get_current_result_coro1(), get_current_result_coro2()]
    )

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
        async for _ in request_handler.on_message_send_stream(
            params, create_server_call_context()
        ):
            pass

    await asyncio.wait_for(execute_called.wait(), timeout=0.1)

    # Assertions
    # 1. set_info called once at the beginning if task exists (or after task is created from message)
    mock_push_config_store.set_info.assert_any_call(task_id, push_config)

    # 2. send_notification called for each task event yielded by aggregator
    assert mock_push_sender.send_notification.await_count == 2
    mock_push_sender.send_notification.assert_any_await(event1_task_update)
    mock_push_sender.send_notification.assert_any_await(event2_final_task)

    mock_agent_executor.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_stream_disconnect_then_resubscribe_receives_future_events():
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
        task_id=task_id, context_id=context_id, status_state=TaskState.working
    )
    mock_task_store.get.return_value = task_for_resub

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        queue_manager=queue_manager,
    )

    params = MessageSendParams(
        message=Message(
            role=Role.user,
            message_id='msg_reconn',
            parts=[],
            task_id=task_id,
            context_id=context_id,
        )
    )

    # Producer behavior: emit one event, then later emit second event
    exec_started = asyncio.Event()
    allow_second_event = asyncio.Event()
    allow_finish = asyncio.Event()

    first_event = create_sample_task(
        task_id=task_id, context_id=context_id, status_state=TaskState.working
    )
    second_event = create_sample_task(
        task_id=task_id, context_id=context_id, status_state=TaskState.completed
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
    resub_gen = request_handler.on_resubscribe_to_task(
        TaskIdParams(id=task_id), create_server_call_context()
    )

    # Allow producer to emit the next event
    allow_second_event.set()

    received = await resub_gen.__anext__()
    assert received == second_event

    # Finish producer to allow cleanup paths to complete
    allow_finish.set()


@pytest.mark.asyncio
async def test_on_message_send_stream_client_disconnect_triggers_background_cleanup_and_producer_continues():
    """Simulate client disconnect: stream stops early, cleanup is scheduled in background,
    producer keeps running, and cleanup completes after producer finishes."""
    # Arrange
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_queue_manager = AsyncMock(spec=QueueManager)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)
    mock_request_context_builder = AsyncMock(spec=RequestContextBuilder)

    task_id = 'disc_task_1'
    context_id = 'disc_ctx_1'

    # RequestContext with IDs
    mock_request_context = MagicMock(spec=RequestContext)
    mock_request_context.task_id = task_id
    mock_request_context.context_id = context_id
    mock_request_context_builder.build.return_value = mock_request_context

    # Queue used by _run_event_stream; must support close()
    mock_queue = AsyncMock(spec=EventQueue)
    mock_queue_manager.create_or_tap.return_value = mock_queue

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
        request_context_builder=mock_request_context_builder,
    )

    params = MessageSendParams(
        message=Message(
            role=Role.user,
            message_id='mid',
            parts=[],
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
async def test_background_cleanup_task_is_tracked_and_cleared():
    """Ensure background cleanup task is tracked while pending and removed when done."""
    # Arrange
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_queue_manager = AsyncMock(spec=QueueManager)
    mock_agent_executor = AsyncMock(spec=AgentExecutor)
    mock_request_context_builder = AsyncMock(spec=RequestContextBuilder)

    task_id = 'track_task_1'
    context_id = 'track_ctx_1'

    # RequestContext with IDs
    mock_request_context = MagicMock(spec=RequestContext)
    mock_request_context.task_id = task_id
    mock_request_context.context_id = context_id
    mock_request_context_builder.build.return_value = mock_request_context

    mock_queue = AsyncMock(spec=EventQueue)
    mock_queue_manager.create_or_tap.return_value = mock_queue

    request_handler = DefaultRequestHandler(
        agent_executor=mock_agent_executor,
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
        request_context_builder=mock_request_context_builder,
    )

    params = MessageSendParams(
        message=Message(
            role=Role.user,
            message_id='mid_track',
            parts=[],
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


@pytest.mark.asyncio
async def test_on_message_send_stream_task_id_mismatch():
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
    )
    params = MessageSendParams(
        message=Message(
            role=Role.user, message_id='msg_stream_mismatch', parts=[]
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

    from a2a.utils.errors import ServerError  # Local import

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
        with pytest.raises(ServerError) as exc_info:
            async for _ in request_handler.on_message_send_stream(
                params, create_server_call_context()
            ):
                pass  # Consume the stream to trigger the error

    assert isinstance(exc_info.value.error, InternalError)
    assert 'Task ID mismatch' in exc_info.value.error.message  # type: ignore


@pytest.mark.asyncio
async def test_cleanup_producer_task_id_not_in_running_agents():
    """Test _cleanup_producer when task_id is not in _running_agents (e.g., already cleaned up)."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_queue_manager = AsyncMock(spec=QueueManager)
    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
    )

    task_id = 'task_already_cleaned'

    # Create a real, completed asyncio.Task for the test
    async def dummy_coro_for_task():
        pass

    mock_producer_task = asyncio.create_task(dummy_coro_for_task())
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
async def test_set_task_push_notification_config_no_notifier():
    """Test on_set_task_push_notification_config when _push_config_store is None."""
    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=AsyncMock(spec=TaskStore),
        push_config_store=None,  # Explicitly None
    )
    params = TaskPushNotificationConfig(
        task_id='task1',
        push_notification_config=PushNotificationConfig(
            url='http://example.com'
        ),
    )
    from a2a.utils.errors import ServerError  # Local import

    with pytest.raises(ServerError) as exc_info:
        await request_handler.on_set_task_push_notification_config(
            params, create_server_call_context()
        )
    assert isinstance(exc_info.value.error, UnsupportedOperationError)


@pytest.mark.asyncio
async def test_set_task_push_notification_config_task_not_found():
    """Test on_set_task_push_notification_config when task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None  # Task not found
    mock_push_store = AsyncMock(spec=PushNotificationConfigStore)
    mock_push_sender = AsyncMock(spec=PushNotificationSender)

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=mock_push_store,
        push_sender=mock_push_sender,
    )
    params = TaskPushNotificationConfig(
        task_id='non_existent_task',
        push_notification_config=PushNotificationConfig(
            url='http://example.com'
        ),
    )
    from a2a.utils.errors import ServerError  # Local import

    context = create_server_call_context()
    with pytest.raises(ServerError) as exc_info:
        await request_handler.on_set_task_push_notification_config(
            params, context
        )

    assert isinstance(exc_info.value.error, TaskNotFoundError)
    mock_task_store.get.assert_awaited_once_with('non_existent_task', context)
    mock_push_store.set_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_task_push_notification_config_no_store():
    """Test on_get_task_push_notification_config when _push_config_store is None."""
    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=AsyncMock(spec=TaskStore),
        push_config_store=None,  # Explicitly None
    )
    params = GetTaskPushNotificationConfigParams(id='task1')
    from a2a.utils.errors import ServerError  # Local import

    with pytest.raises(ServerError) as exc_info:
        await request_handler.on_get_task_push_notification_config(
            params, create_server_call_context()
        )
    assert isinstance(exc_info.value.error, UnsupportedOperationError)


@pytest.mark.asyncio
async def test_get_task_push_notification_config_task_not_found():
    """Test on_get_task_push_notification_config when task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None  # Task not found
    mock_push_store = AsyncMock(spec=PushNotificationConfigStore)

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=mock_push_store,
    )
    params = GetTaskPushNotificationConfigParams(id='non_existent_task')
    from a2a.utils.errors import ServerError  # Local import

    context = create_server_call_context()
    with pytest.raises(ServerError) as exc_info:
        await request_handler.on_get_task_push_notification_config(
            params, context
        )

    assert isinstance(exc_info.value.error, TaskNotFoundError)
    mock_task_store.get.assert_awaited_once_with('non_existent_task', context)
    mock_push_store.get_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_task_push_notification_config_info_not_found():
    """Test on_get_task_push_notification_config when push_config_store.get_info returns None."""
    mock_task_store = AsyncMock(spec=TaskStore)

    sample_task = create_sample_task(task_id='non_existent_task')
    mock_task_store.get.return_value = sample_task

    mock_push_store = AsyncMock(spec=PushNotificationConfigStore)
    mock_push_store.get_info.return_value = None  # Info not found

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=mock_push_store,
    )
    params = GetTaskPushNotificationConfigParams(id='non_existent_task')
    from a2a.utils.errors import ServerError  # Local import

    context = create_server_call_context()
    with pytest.raises(ServerError) as exc_info:
        await request_handler.on_get_task_push_notification_config(
            params, context
        )

    assert isinstance(
        exc_info.value.error, InternalError
    )  # Current code raises InternalError
    mock_task_store.get.assert_awaited_once_with('non_existent_task', context)
    mock_push_store.get_info.assert_awaited_once_with('non_existent_task')


@pytest.mark.asyncio
async def test_get_task_push_notification_config_info_with_config():
    """Test on_get_task_push_notification_config with valid push config id"""
    mock_task_store = AsyncMock(spec=TaskStore)

    push_store = InMemoryPushNotificationConfigStore()

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
    )

    set_config_params = TaskPushNotificationConfig(
        task_id='task_1',
        push_notification_config=PushNotificationConfig(
            id='config_id', url='http://1.example.com'
        ),
    )
    context = create_server_call_context()
    await request_handler.on_set_task_push_notification_config(
        set_config_params, context
    )

    params = GetTaskPushNotificationConfigParams(
        id='task_1', push_notification_config_id='config_id'
    )

    result: TaskPushNotificationConfig = (
        await request_handler.on_get_task_push_notification_config(
            params, context
        )
    )

    assert result is not None
    assert result.task_id == 'task_1'
    assert (
        result.push_notification_config.url
        == set_config_params.push_notification_config.url
    )
    assert result.push_notification_config.id == 'config_id'


@pytest.mark.asyncio
async def test_get_task_push_notification_config_info_with_config_no_id():
    """Test on_get_task_push_notification_config with no push config id"""
    mock_task_store = AsyncMock(spec=TaskStore)

    push_store = InMemoryPushNotificationConfigStore()

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
    )

    set_config_params = TaskPushNotificationConfig(
        task_id='task_1',
        push_notification_config=PushNotificationConfig(
            url='http://1.example.com'
        ),
    )
    await request_handler.on_set_task_push_notification_config(
        set_config_params, create_server_call_context()
    )

    params = TaskIdParams(id='task_1')

    result: TaskPushNotificationConfig = (
        await request_handler.on_get_task_push_notification_config(
            params, create_server_call_context()
        )
    )

    assert result is not None
    assert result.task_id == 'task_1'
    assert (
        result.push_notification_config.url
        == set_config_params.push_notification_config.url
    )
    assert result.push_notification_config.id == 'task_1'


@pytest.mark.asyncio
async def test_on_resubscribe_to_task_task_not_found():
    """Test on_resubscribe_to_task when the task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None  # Task not found

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(), task_store=mock_task_store
    )
    params = TaskIdParams(id='resub_task_not_found')

    from a2a.utils.errors import ServerError  # Local import

    context = create_server_call_context()
    with pytest.raises(ServerError) as exc_info:
        # Need to consume the async generator to trigger the error
        async for _ in request_handler.on_resubscribe_to_task(params, context):
            pass

    assert isinstance(exc_info.value.error, TaskNotFoundError)
    mock_task_store.get.assert_awaited_once_with(
        'resub_task_not_found', context
    )


@pytest.mark.asyncio
async def test_on_resubscribe_to_task_queue_not_found():
    """Test on_resubscribe_to_task when the queue is not found by queue_manager.tap."""
    mock_task_store = AsyncMock(spec=TaskStore)
    sample_task = create_sample_task(task_id='resub_queue_not_found')
    mock_task_store.get.return_value = sample_task

    mock_queue_manager = AsyncMock(spec=QueueManager)
    mock_queue_manager.tap.return_value = None  # Queue not found

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        queue_manager=mock_queue_manager,
    )
    params = TaskIdParams(id='resub_queue_not_found')

    from a2a.utils.errors import ServerError  # Local import

    context = create_server_call_context()
    with pytest.raises(ServerError) as exc_info:
        async for _ in request_handler.on_resubscribe_to_task(params, context):
            pass

    assert isinstance(
        exc_info.value.error, TaskNotFoundError
    )  # Should be TaskNotFoundError as per spec
    mock_task_store.get.assert_awaited_once_with(
        'resub_queue_not_found', context
    )
    mock_queue_manager.tap.assert_awaited_once_with('resub_queue_not_found')


@pytest.mark.asyncio
async def test_on_message_send_stream():
    request_handler = DefaultRequestHandler(
        DummyAgentExecutor(), InMemoryTaskStore()
    )
    message_params = MessageSendParams(
        message=Message(
            role=Role.user,
            message_id='msg-123',
            parts=[Part(root=TextPart(text='How are you?'))],
        ),
    )

    async def consume_stream():
        events = []
        async for event in request_handler.on_message_send_stream(
            message_params
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

    texts = [p.root.text for e in events for p in e.status.message.parts]
    assert texts == ['Event 0', 'Event 1', 'Event 2']


@pytest.mark.asyncio
async def test_list_task_push_notification_config_no_store():
    """Test on_list_task_push_notification_config when _push_config_store is None."""
    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=AsyncMock(spec=TaskStore),
        push_config_store=None,  # Explicitly None
    )
    params = ListTaskPushNotificationConfigParams(id='task1')
    from a2a.utils.errors import ServerError  # Local import

    with pytest.raises(ServerError) as exc_info:
        await request_handler.on_list_task_push_notification_config(
            params, create_server_call_context()
        )
    assert isinstance(exc_info.value.error, UnsupportedOperationError)


@pytest.mark.asyncio
async def test_list_task_push_notification_config_task_not_found():
    """Test on_list_task_push_notification_config when task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None  # Task not found
    mock_push_store = AsyncMock(spec=PushNotificationConfigStore)

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=mock_push_store,
    )
    params = ListTaskPushNotificationConfigParams(id='non_existent_task')
    from a2a.utils.errors import ServerError  # Local import

    context = create_server_call_context()
    with pytest.raises(ServerError) as exc_info:
        await request_handler.on_list_task_push_notification_config(
            params, context
        )

    assert isinstance(exc_info.value.error, TaskNotFoundError)
    mock_task_store.get.assert_awaited_once_with('non_existent_task', context)
    mock_push_store.get_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_no_task_push_notification_config_info():
    """Test on_get_task_push_notification_config when push_config_store.get_info returns []"""
    mock_task_store = AsyncMock(spec=TaskStore)

    sample_task = create_sample_task(task_id='non_existent_task')
    mock_task_store.get.return_value = sample_task

    push_store = InMemoryPushNotificationConfigStore()

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
    )
    params = ListTaskPushNotificationConfigParams(id='non_existent_task')

    result = await request_handler.on_list_task_push_notification_config(
        params, create_server_call_context()
    )
    assert result == []


@pytest.mark.asyncio
async def test_list_task_push_notification_config_info_with_config():
    """Test on_list_task_push_notification_config with push config+id"""
    mock_task_store = AsyncMock(spec=TaskStore)

    sample_task = create_sample_task(task_id='non_existent_task')
    mock_task_store.get.return_value = sample_task

    push_config1 = PushNotificationConfig(
        id='config_1', url='http://example.com'
    )
    push_config2 = PushNotificationConfig(
        id='config_2', url='http://example.com'
    )

    push_store = InMemoryPushNotificationConfigStore()
    await push_store.set_info('task_1', push_config1)
    await push_store.set_info('task_1', push_config2)

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
    )
    params = ListTaskPushNotificationConfigParams(id='task_1')

    result: list[
        TaskPushNotificationConfig
    ] = await request_handler.on_list_task_push_notification_config(
        params, create_server_call_context()
    )

    assert len(result) == 2
    assert result[0].task_id == 'task_1'
    assert result[0].push_notification_config == push_config1
    assert result[1].task_id == 'task_1'
    assert result[1].push_notification_config == push_config2


@pytest.mark.asyncio
async def test_list_task_push_notification_config_info_with_config_and_no_id():
    """Test on_list_task_push_notification_config with no push config id"""
    mock_task_store = AsyncMock(spec=TaskStore)

    push_store = InMemoryPushNotificationConfigStore()

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
    )

    # multiple calls without config id should replace the existing
    set_config_params1 = TaskPushNotificationConfig(
        task_id='task_1',
        push_notification_config=PushNotificationConfig(
            url='http://1.example.com'
        ),
    )
    await request_handler.on_set_task_push_notification_config(
        set_config_params1, create_server_call_context()
    )

    set_config_params2 = TaskPushNotificationConfig(
        task_id='task_1',
        push_notification_config=PushNotificationConfig(
            url='http://2.example.com'
        ),
    )
    await request_handler.on_set_task_push_notification_config(
        set_config_params2, create_server_call_context()
    )

    params = ListTaskPushNotificationConfigParams(id='task_1')

    result: list[
        TaskPushNotificationConfig
    ] = await request_handler.on_list_task_push_notification_config(
        params, create_server_call_context()
    )

    assert len(result) == 1
    assert result[0].task_id == 'task_1'
    assert (
        result[0].push_notification_config.url
        == set_config_params2.push_notification_config.url
    )
    assert result[0].push_notification_config.id == 'task_1'


@pytest.mark.asyncio
async def test_delete_task_push_notification_config_no_store():
    """Test on_delete_task_push_notification_config when _push_config_store is None."""
    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=AsyncMock(spec=TaskStore),
        push_config_store=None,  # Explicitly None
    )
    params = DeleteTaskPushNotificationConfigParams(
        id='task1', push_notification_config_id='config1'
    )
    from a2a.utils.errors import ServerError  # Local import

    with pytest.raises(ServerError) as exc_info:
        await request_handler.on_delete_task_push_notification_config(
            params, create_server_call_context()
        )
    assert isinstance(exc_info.value.error, UnsupportedOperationError)


@pytest.mark.asyncio
async def test_delete_task_push_notification_config_task_not_found():
    """Test on_delete_task_push_notification_config when task is not found."""
    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = None  # Task not found
    mock_push_store = AsyncMock(spec=PushNotificationConfigStore)

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=mock_push_store,
    )
    params = DeleteTaskPushNotificationConfigParams(
        id='non_existent_task', push_notification_config_id='config1'
    )
    from a2a.utils.errors import ServerError  # Local import

    context = create_server_call_context()
    with pytest.raises(ServerError) as exc_info:
        await request_handler.on_delete_task_push_notification_config(
            params, context
        )

    assert isinstance(exc_info.value.error, TaskNotFoundError)
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
        PushNotificationConfig(id='config_1', url='http://example.com'),
    )

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
    )
    params = DeleteTaskPushNotificationConfigParams(
        id='task1', push_notification_config_id='config_non_existant'
    )

    result = await request_handler.on_delete_task_push_notification_config(
        params, create_server_call_context()
    )
    assert result is None

    params = DeleteTaskPushNotificationConfigParams(
        id='task2', push_notification_config_id='config_non_existant'
    )

    result = await request_handler.on_delete_task_push_notification_config(
        params, create_server_call_context()
    )
    assert result is None


@pytest.mark.asyncio
async def test_delete_task_push_notification_config_info_with_config():
    """Test on_list_task_push_notification_config with push config+id"""
    mock_task_store = AsyncMock(spec=TaskStore)

    sample_task = create_sample_task(task_id='non_existent_task')
    mock_task_store.get.return_value = sample_task

    push_config1 = PushNotificationConfig(
        id='config_1', url='http://example.com'
    )
    push_config2 = PushNotificationConfig(
        id='config_2', url='http://example.com'
    )

    push_store = InMemoryPushNotificationConfigStore()
    await push_store.set_info('task_1', push_config1)
    await push_store.set_info('task_1', push_config2)
    await push_store.set_info('task_2', push_config1)

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
    )
    params = DeleteTaskPushNotificationConfigParams(
        id='task_1', push_notification_config_id='config_1'
    )

    result1 = await request_handler.on_delete_task_push_notification_config(
        params, create_server_call_context()
    )

    assert result1 is None

    result2 = await request_handler.on_list_task_push_notification_config(
        ListTaskPushNotificationConfigParams(id='task_1'),
        create_server_call_context(),
    )

    assert len(result2) == 1
    assert result2[0].task_id == 'task_1'
    assert result2[0].push_notification_config == push_config2


@pytest.mark.asyncio
async def test_delete_task_push_notification_config_info_with_config_and_no_id():
    """Test on_list_task_push_notification_config with no push config id"""
    mock_task_store = AsyncMock(spec=TaskStore)

    sample_task = create_sample_task(task_id='non_existent_task')
    mock_task_store.get.return_value = sample_task

    push_config = PushNotificationConfig(url='http://example.com')

    # insertion without id should replace the existing config
    push_store = InMemoryPushNotificationConfigStore()
    await push_store.set_info('task_1', push_config)
    await push_store.set_info('task_1', push_config)

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        push_config_store=push_store,
    )
    params = DeleteTaskPushNotificationConfigParams(
        id='task_1', push_notification_config_id='task_1'
    )

    result = await request_handler.on_delete_task_push_notification_config(
        params, create_server_call_context()
    )

    assert result is None

    result2 = await request_handler.on_list_task_push_notification_config(
        ListTaskPushNotificationConfigParams(id='task_1'),
        create_server_call_context(),
    )

    assert len(result2) == 0


TERMINAL_TASK_STATES = {
    TaskState.completed,
    TaskState.canceled,
    TaskState.failed,
    TaskState.rejected,
}


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal_state', TERMINAL_TASK_STATES)
async def test_on_message_send_task_in_terminal_state(terminal_state):
    """Test on_message_send when task is already in a terminal state."""
    task_id = f'terminal_task_{terminal_state.value}'
    terminal_task = create_sample_task(
        task_id=task_id, status_state=terminal_state
    )

    mock_task_store = AsyncMock(spec=TaskStore)
    # The get method of TaskManager calls task_store.get.
    # We mock TaskManager.get_task which is an async method.
    # So we should patch that instead.

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(), task_store=mock_task_store
    )

    params = MessageSendParams(
        message=Message(
            role=Role.user,
            message_id='msg_terminal',
            parts=[],
            task_id=task_id,
        )
    )

    from a2a.utils.errors import ServerError

    # Patch the TaskManager's get_task method to return our terminal task
    with patch(
        'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
        return_value=terminal_task,
    ):
        with pytest.raises(ServerError) as exc_info:
            await request_handler.on_message_send(
                params, create_server_call_context()
            )

    assert isinstance(exc_info.value.error, InvalidParamsError)
    assert exc_info.value.error.message
    assert (
        f'Task {task_id} is in terminal state: {terminal_state.value}'
        in exc_info.value.error.message
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal_state', TERMINAL_TASK_STATES)
async def test_on_message_send_stream_task_in_terminal_state(terminal_state):
    """Test on_message_send_stream when task is already in a terminal state."""
    task_id = f'terminal_stream_task_{terminal_state.value}'
    terminal_task = create_sample_task(
        task_id=task_id, status_state=terminal_state
    )

    mock_task_store = AsyncMock(spec=TaskStore)

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(), task_store=mock_task_store
    )

    params = MessageSendParams(
        message=Message(
            role=Role.user,
            message_id='msg_terminal_stream',
            parts=[],
            task_id=task_id,
        )
    )

    from a2a.utils.errors import ServerError

    with patch(
        'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
        return_value=terminal_task,
    ):
        with pytest.raises(ServerError) as exc_info:
            async for _ in request_handler.on_message_send_stream(
                params, create_server_call_context()
            ):
                pass  # pragma: no cover

    assert isinstance(exc_info.value.error, InvalidParamsError)
    assert exc_info.value.error.message
    assert (
        f'Task {task_id} is in terminal state: {terminal_state.value}'
        in exc_info.value.error.message
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal_state', TERMINAL_TASK_STATES)
async def test_on_resubscribe_to_task_in_terminal_state(terminal_state):
    """Test on_resubscribe_to_task when task is in a terminal state."""
    task_id = f'resub_terminal_task_{terminal_state.value}'
    terminal_task = create_sample_task(
        task_id=task_id, status_state=terminal_state
    )

    mock_task_store = AsyncMock(spec=TaskStore)
    mock_task_store.get.return_value = terminal_task

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(),
        task_store=mock_task_store,
        queue_manager=AsyncMock(spec=QueueManager),
    )
    params = TaskIdParams(id=task_id)

    from a2a.utils.errors import ServerError

    context = create_server_call_context()
    with pytest.raises(ServerError) as exc_info:
        async for _ in request_handler.on_resubscribe_to_task(params, context):
            pass  # pragma: no cover

    assert isinstance(exc_info.value.error, InvalidParamsError)
    assert exc_info.value.error.message
    assert (
        f'Task {task_id} is in terminal state: {terminal_state.value}'
        in exc_info.value.error.message
    )
    mock_task_store.get.assert_awaited_once_with(task_id, context)


@pytest.mark.asyncio
async def test_on_message_send_task_id_provided_but_task_not_found():
    """Test on_message_send when task_id is provided but task doesn't exist."""
    task_id = 'nonexistent_task'
    mock_task_store = AsyncMock(spec=TaskStore)

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(), task_store=mock_task_store
    )

    params = MessageSendParams(
        message=Message(
            role=Role.user,
            message_id='msg_nonexistent',
            parts=[Part(root=TextPart(text='Hello'))],
            task_id=task_id,
            context_id='ctx1',
        )
    )

    from a2a.utils.errors import ServerError

    # Mock TaskManager.get_task to return None (task not found)
    with patch(
        'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
        return_value=None,
    ):
        with pytest.raises(ServerError) as exc_info:
            await request_handler.on_message_send(
                params, create_server_call_context()
            )

    assert isinstance(exc_info.value.error, TaskNotFoundError)
    assert exc_info.value.error.message
    assert (
        f'Task {task_id} was specified but does not exist'
        in exc_info.value.error.message
    )


@pytest.mark.asyncio
async def test_on_message_send_stream_task_id_provided_but_task_not_found():
    """Test on_message_send_stream when task_id is provided but task doesn't exist."""
    task_id = 'nonexistent_stream_task'
    mock_task_store = AsyncMock(spec=TaskStore)

    request_handler = DefaultRequestHandler(
        agent_executor=DummyAgentExecutor(), task_store=mock_task_store
    )

    params = MessageSendParams(
        message=Message(
            role=Role.user,
            message_id='msg_nonexistent_stream',
            parts=[Part(root=TextPart(text='Hello'))],
            task_id=task_id,
            context_id='ctx1',
        )
    )

    from a2a.utils.errors import ServerError

    # Mock TaskManager.get_task to return None (task not found)
    with patch(
        'a2a.server.request_handlers.default_request_handler.TaskManager.get_task',
        return_value=None,
    ):
        with pytest.raises(ServerError) as exc_info:
            # Need to consume the async generator to trigger the error
            async for _ in request_handler.on_message_send_stream(
                params, create_server_call_context()
            ):
                pass

    assert isinstance(exc_info.value.error, TaskNotFoundError)
    assert exc_info.value.error.message
    assert (
        f'Task {task_id} was specified but does not exist'
        in exc_info.value.error.message
    )


def test_init_with_default_queue_manager_issues_deprecation_warning():
    """Test that initializing with default queue_manager issues deprecation warning."""
    import warnings
    from unittest.mock import MagicMock

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter('always')

        handler = DefaultRequestHandler(
            agent_executor=MagicMock(), task_store=MagicMock()
        )

        assert len(w) == 1
        assert issubclass(w[0].category, DeprecationWarning)
        assert 'Using default InMemoryQueueManager' in str(w[0].message)
        assert 'will be removed in a future version' in str(w[0].message)


def test_init_with_explicit_queue_manager_no_warning():
    """Test that initializing with explicit queue_manager does not issue warning."""
    import warnings
    from unittest.mock import MagicMock

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter('always')

        handler = DefaultRequestHandler(
            agent_executor=MagicMock(),
            task_store=MagicMock(),
            queue_manager=InMemoryQueueManager(),
        )

        # Should not have any deprecation warnings
        deprecation_warnings = [
            warning
            for warning in w
            if issubclass(warning.category, DeprecationWarning)
        ]
        assert len(deprecation_warnings) == 0


@pytest.mark.asyncio
async def test_init_with_disabled_fallback_raises_error():
    """Test that disabling fallback raises ValueError when queue_manager is None."""
    import os
    from unittest.mock import MagicMock

    # Set environment variable to disable fallback
    old_value = os.environ.get('A2A_DISABLE_QUEUE_MANAGER_FALLBACK')
    os.environ['A2A_DISABLE_QUEUE_MANAGER_FALLBACK'] = 'true'

    try:
        with pytest.raises(ValueError, match='queue_manager is required'):
            DefaultRequestHandler(
                agent_executor=MagicMock(), task_store=MagicMock()
            )
    finally:
        # Restore environment variable
        if old_value is None:
            os.environ.pop('A2A_DISABLE_QUEUE_MANAGER_FALLBACK', None)
        else:
            os.environ['A2A_DISABLE_QUEUE_MANAGER_FALLBACK'] = old_value


@pytest.mark.asyncio
async def test_init_with_disabled_fallback_false_allows_default():
    """Test that setting A2A_DISABLE_QUEUE_MANAGER_FALLBACK=false allows default behavior."""
    import os
    import warnings
    from unittest.mock import MagicMock

    # Set environment variable to explicitly allow fallback
    old_value = os.environ.get('A2A_DISABLE_QUEUE_MANAGER_FALLBACK')
    os.environ['A2A_DISABLE_QUEUE_MANAGER_FALLBACK'] = 'false'

    try:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter('always')

            handler = DefaultRequestHandler(
                agent_executor=MagicMock(), task_store=MagicMock()
            )

            # Should still get deprecation warning
            assert len(w) == 1
            assert issubclass(w[0].category, DeprecationWarning)
    finally:
        # Restore environment variable
        if old_value is None:
            os.environ.pop('A2A_DISABLE_QUEUE_MANAGER_FALLBACK', None)
        else:
            os.environ['A2A_DISABLE_QUEUE_MANAGER_FALLBACK'] = old_value


def test_environment_variable_parsing():
    """Test that environment variable accepts various true/false values."""
    import os
    from unittest.mock import MagicMock

    test_cases = [
        ('true', True),
        ('True', True),
        ('TRUE', True),
        ('1', True),
        ('yes', True),
        ('Yes', True),
        ('false', False),
        ('False', False),
        ('FALSE', False),
        ('0', False),
        ('no', False),
        ('No', False),
        ('invalid', False),  # Invalid values should default to False
        ('', False),  # Empty string should default to False
    ]

    for env_value, expected_disable in test_cases:
        old_value = os.environ.get('A2A_DISABLE_QUEUE_MANAGER_FALLBACK')
        os.environ['A2A_DISABLE_QUEUE_MANAGER_FALLBACK'] = env_value

        try:
            if expected_disable:
                with pytest.raises(
                    ValueError, match='queue_manager is required'
                ):
                    DefaultRequestHandler(
                        agent_executor=MagicMock(), task_store=MagicMock()
                    )
            else:
                # Should work without error (may issue warning)
                handler = DefaultRequestHandler(
                    agent_executor=MagicMock(), task_store=MagicMock()
                )
                assert handler is not None
        finally:
            # Restore environment variable
            if old_value is None:
                os.environ.pop('A2A_DISABLE_QUEUE_MANAGER_FALLBACK', None)
            else:
                os.environ['A2A_DISABLE_QUEUE_MANAGER_FALLBACK'] = old_value
