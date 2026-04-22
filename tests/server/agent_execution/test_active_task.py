import asyncio
import logging

from unittest.mock import AsyncMock, Mock, patch

import pytest
import pytest_asyncio

from a2a.server.agent_execution.active_task import ActiveTask
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue_v2 import EventQueueSource as EventQueue
from a2a.server.tasks.push_notification_sender import PushNotificationSender
from a2a.server.tasks.task_manager import TaskManager
from a2a.types.a2a_pb2 import (
    Message,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    Role,
    Part,
)
from a2a.utils.errors import InvalidParamsError


logger = logging.getLogger(__name__)


class TestActiveTask:
    """Tests for the ActiveTask class."""

    @pytest.fixture
    def agent_executor(self) -> Mock:
        return Mock(spec=AgentExecutor)

    @pytest.fixture
    def task_manager(self) -> Mock:
        tm = Mock(spec=TaskManager)
        tm.process = AsyncMock(side_effect=lambda x: x)
        tm.get_task = AsyncMock(return_value=None)
        tm.context_id = 'test-context-id'
        tm._init_task_obj = Mock(return_value=Task(id='test-task-id'))
        tm.save_task_event = AsyncMock()
        return tm

    @pytest_asyncio.fixture
    async def event_queue(self) -> EventQueue:
        return EventQueue()

    @pytest.fixture
    def push_sender(self) -> Mock:
        ps = Mock(spec=PushNotificationSender)
        ps.send_notification = AsyncMock()
        return ps

    @pytest.fixture
    def request_context(self) -> Mock:
        return Mock(spec=RequestContext)

    @pytest_asyncio.fixture
    async def active_task(
        self,
        agent_executor: Mock,
        task_manager: Mock,
        push_sender: Mock,
    ) -> ActiveTask:
        return ActiveTask(
            agent_executor=agent_executor,
            task_id='test-task-id',
            task_manager=task_manager,
            push_sender=push_sender,
        )

    @pytest.mark.asyncio
    async def test_active_task_already_started(
        self, active_task: ActiveTask, request_context: Mock
    ) -> None:
        """Test starting a task that is already started."""
        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )
        # Enqueuing and starting again should not raise errors
        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )
        assert active_task._producer_task is not None

    @pytest.mark.asyncio
    async def test_active_task_cancel(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        task_manager: Mock,
    ) -> None:
        """Test canceling an ActiveTask."""
        stop_event = asyncio.Event()

        async def execute_mock(req, q):
            await stop_event.wait()

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        agent_executor.cancel = AsyncMock()
        task_manager.get_task.side_effect = [
            Task(
                id='test-task-id',
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        ] + [
            Task(
                id='test-task-id',
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        ] * 10

        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        # Give it a moment to start
        await asyncio.sleep(0.1)

        await active_task.cancel(request_context)

        agent_executor.cancel.assert_called_once()
        stop_event.set()

    @pytest.mark.asyncio
    async def test_active_task_interrupted_auth(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        task_manager: Mock,
    ) -> None:
        """Test task interruption due to AUTH_REQUIRED."""
        task_obj = Task(
            id='test-task-id',
            status=TaskStatus(state=TaskState.TASK_STATE_AUTH_REQUIRED),
        )

        async def execute_mock(req, q):
            await q.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id='test-task-id',
                    status=TaskStatus(state=TaskState.TASK_STATE_AUTH_REQUIRED),
                )
            )

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        task_manager.get_task.side_effect = [
            Task(
                id='test-task-id',
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        ] + [task_obj] * 10

        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        events = [
            e async for e in active_task.subscribe(request=request_context)
        ]

        result = events[0] if events else None
        assert (
            getattr(result, 'id', getattr(result, 'task_id', None))
            == 'test-task-id'
        )
        assert result.status.state == TaskState.TASK_STATE_AUTH_REQUIRED

    @pytest.mark.asyncio
    async def test_active_task_interrupted_input(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        task_manager: Mock,
    ) -> None:
        """Test task interruption due to INPUT_REQUIRED."""
        task_obj = Task(
            id='test-task-id',
            status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
        )

        async def execute_mock(req, q):
            await q.enqueue_event(
                Task(
                    id='test-task-id',
                    status=TaskStatus(
                        state=TaskState.TASK_STATE_INPUT_REQUIRED
                    ),
                )
            )

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        task_manager.get_task.side_effect = [
            Task(
                id='test-task-id',
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        ] + [task_obj] * 10

        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        events = [
            e async for e in active_task.subscribe(request=request_context)
        ]

        result = events[-1] if events else None
        assert result.id == 'test-task-id'
        assert result.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

    @pytest.mark.asyncio
    async def test_active_task_producer_failure(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
    ) -> None:
        """Test ActiveTask behavior when the producer fails."""
        agent_executor.execute = AsyncMock(
            side_effect=ValueError('Producer crashed')
        )

        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        # We need to wait a bit for the producer to fail and set the exception
        for _ in range(10):
            try:
                async for _ in active_task.subscribe():
                    pass
            except ValueError:
                return
            await asyncio.sleep(0.05)

        pytest.fail('Producer failure was not raised')

    @pytest.mark.asyncio
    async def test_active_task_push_notification(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        push_sender: Mock,
        task_manager: Mock,
    ) -> None:
        """Test push notification sending."""
        task_obj = Task(
            id='test-task-id',
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )

        async def execute_mock(req, q):
            await q.enqueue_event(task_obj)

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        task_manager.get_task.side_effect = [
            Task(
                id='test-task-id',
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        ] + [task_obj] * 10

        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        async for _ in active_task.subscribe(request=request_context):
            pass

        push_sender.send_notification.assert_called()

    @pytest.mark.asyncio
    async def test_active_task_consumer_failure(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
    ) -> None:
        """Test behavior when the consumer task fails."""
        # Mock dequeue_event to raise exception
        active_task._event_queue_agent.dequeue_event = AsyncMock(
            side_effect=RuntimeError('Consumer crash')
        )

        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        # We need to wait for the consumer to fail
        for _ in range(10):
            try:
                async for _ in active_task.subscribe():
                    pass
            except RuntimeError as e:
                if str(e) == 'Consumer crash':
                    return
            await asyncio.sleep(0.05)

        pytest.fail('Consumer failure was not raised')

    @pytest.mark.asyncio
    async def test_active_task_subscribe_exception_handling(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
    ) -> None:
        """Test exception handling in subscribe."""
        agent_executor.execute = AsyncMock(
            side_effect=ValueError('Producer failure')
        )

        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        # Give it a moment to fail
        for _ in range(10):
            if active_task._exception:
                break
            await asyncio.sleep(0.05)

        with pytest.raises(ValueError, match='Producer failure'):
            async for _ in active_task.subscribe():
                pass

    @pytest.mark.asyncio
    async def test_active_task_cancel_not_started(
        self, active_task: ActiveTask, request_context: Mock
    ) -> None:
        """Test canceling a task that was never started."""
        # TODO: Implement this test

    @pytest.mark.asyncio
    async def test_active_task_cancel_already_finished(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        task_manager: Mock,
    ) -> None:
        """Test canceling a task that is already finished."""
        task_obj = Task(
            id='test-task-id',
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )

        async def execute_mock(req, q):
            active_task._request_queue.shutdown(immediate=True)

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        task_manager.get_task.side_effect = [
            Task(
                id='test-task-id',
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        ] + [task_obj] * 10

        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        async for _ in active_task.subscribe(request=request_context):
            pass

        await active_task._is_finished.wait()

        # Now it is finished
        await active_task.cancel(request_context)

        # agent_executor.cancel should NOT be called
        agent_executor.cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_active_task_subscribe_cancelled_during_wait(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
    ) -> None:
        """Test subscribe when it is cancelled while waiting for events."""

        async def slow_execute(req, q):
            await asyncio.sleep(10)

        agent_executor.execute = AsyncMock(side_effect=slow_execute)

        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        it = active_task.subscribe()
        it_obj = it.__aiter__()

        # This task will be waiting inside the loop in subscribe()
        task = asyncio.create_task(it_obj.__anext__())
        await asyncio.sleep(0.2)

        task.cancel()

        # In python 3.10+ cancelling an async generator next() might raise StopAsyncIteration
        # if the generator handles the cancellation by closing.
        with pytest.raises((asyncio.CancelledError, StopAsyncIteration)):
            await task

        await it.aclose()

    @pytest.mark.asyncio
    async def test_active_task_subscribe_queue_shutdown(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
    ) -> None:
        """Test subscribe when the queue is shut down."""

        async def long_execute(*args, **kwargs):
            await asyncio.sleep(10)

        agent_executor.execute = AsyncMock(side_effect=long_execute)
        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        tapped = await active_task._event_queue_subscribers.tap()

        with patch.object(
            active_task._event_queue_subscribers, 'tap', return_value=tapped
        ):
            # Close the queue while subscribe is waiting
            async def close_later():
                await asyncio.sleep(0.2)
                await tapped.close()

            _ = asyncio.create_task(close_later())

            async for _ in active_task.subscribe():
                pass

        # Should finish normally after QueueShutDown

    @pytest.mark.asyncio
    async def test_active_task_subscribe_yield_then_shutdown(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
    ) -> None:
        """Test subscribe when an event is yielded and then the queue is shut down."""
        msg = Message(message_id='m1')

        async def execute_mock(req, q):
            await q.enqueue_event(msg)
            await asyncio.sleep(0.5)
            # Finish producer
            active_task._request_queue.shutdown(immediate=True)

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        events = [event async for event in active_task.subscribe()]
        assert len(events) == 1
        assert events[0] == msg

    @pytest.mark.asyncio
    async def test_active_task_task_sets_result_first(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        task_manager: Mock,
    ) -> None:
        """Test that enqueuing a Task sets result_available when no result yet."""
        task_obj = Task(
            id='test-task-id',
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        )

        async def execute_mock(req, q):
            # No result available yet
            await q.enqueue_event(task_obj)

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        task_manager.get_task.side_effect = [
            Task(
                id='test-task-id',
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        ] + [task_obj] * 10

        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        events = [
            e async for e in active_task.subscribe(request=request_context)
        ]

        result = events[-1] if events else None
        assert result == task_obj

    @pytest.mark.asyncio
    async def test_active_task_subscribe_cancelled_during_yield(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
    ) -> None:
        """Test subscribe cancellation while yielding (GeneratorExit)."""
        msg = Message(message_id='m1')

        async def execute_mock(req, q):
            await q.enqueue_event(msg)
            await asyncio.sleep(10)

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        it = active_task.subscribe()
        async for event in it:
            assert event == msg
            # Cancel while we have the event (inside the loop)
            await it.aclose()
            break

    @pytest.mark.asyncio
    async def test_active_task_cancel_when_already_closed(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        task_manager: Mock,
    ) -> None:
        """Test cancel when the event queue is already closed."""

        async def execute_mock(req, q):
            active_task._request_queue.shutdown(immediate=True)

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        task_manager.get_task.return_value = Task(id='test')
        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        # Forced queue close.
        await active_task._event_queue_agent.close()
        await active_task._event_queue_subscribers.close()

        # Now cancel the task itself.
        await active_task.cancel(request_context)
        # wait() was removed, no need to wait here.

        # Cancel again should not do anything.
        await active_task.cancel(request_context)
        # wait() was removed, no need to wait here.

    @pytest.mark.asyncio
    async def test_active_task_subscribe_dequeue_failure(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
    ) -> None:
        """Test subscribe when dequeue_event fails on the tapped queue."""

        async def slow_execute(req, q):
            await asyncio.sleep(10)

        agent_executor.execute = AsyncMock(side_effect=slow_execute)
        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        mock_tapped_queue = Mock(spec=EventQueue)
        mock_tapped_queue.dequeue_event = AsyncMock(
            side_effect=RuntimeError('Tapped queue crash')
        )
        mock_tapped_queue.close = AsyncMock()

        with (
            patch.object(
                active_task._event_queue_subscribers,
                'tap',
                return_value=mock_tapped_queue,
            ),
            pytest.raises(RuntimeError, match='Tapped queue crash'),
        ):
            async for _ in active_task.subscribe():
                pass

        mock_tapped_queue.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_active_task_consumer_interrupted_multiple_times(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        task_manager: Mock,
    ) -> None:
        """Test consumer receiving multiple interrupting events."""
        task_obj = Task(
            id='test-task-id',
            status=TaskStatus(state=TaskState.TASK_STATE_AUTH_REQUIRED),
        )

        async def execute_mock(req, q):
            await q.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id='test-task-id',
                    status=TaskStatus(state=TaskState.TASK_STATE_AUTH_REQUIRED),
                )
            )
            await q.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id='test-task-id',
                    status=TaskStatus(
                        state=TaskState.TASK_STATE_INPUT_REQUIRED
                    ),
                )
            )

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        task_manager.get_task.side_effect = [
            Task(
                id='test-task-id',
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        ] + [task_obj] * 10

        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        events = [
            e async for e in active_task.subscribe(request=request_context)
        ]

        result = events[0] if events else None
        assert result.status.state == TaskState.TASK_STATE_AUTH_REQUIRED

    @pytest.mark.asyncio
    async def test_active_task_subscribe_immediate_finish(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
    ) -> None:
        """Test subscribe when the task finishes immediately."""

        async def execute_mock(req, q):
            active_task._request_queue.shutdown(immediate=True)

        agent_executor.execute = AsyncMock(side_effect=execute_mock)

        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        # Wait for it to finish
        await active_task._is_finished.wait()

        with pytest.raises(
            InvalidParamsError, match=r'Task .* is already completed'
        ):
            async for _ in active_task.subscribe():
                pass

    @pytest.mark.asyncio
    async def test_active_task_start_producer_immediate_error(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
    ) -> None:
        """Test start when producer fails immediately."""
        agent_executor.execute = AsyncMock(
            side_effect=ValueError('Quick failure')
        )

        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        # Consumer should also finish
        with pytest.raises(ValueError, match='Quick failure'):
            async for _ in active_task.subscribe():
                pass

    @pytest.mark.asyncio
    async def test_active_task_subscribe_finished_during_wait(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
    ) -> None:
        """Test subscribe when the task finishes while waiting for an event."""

        async def slow_execute(req, q):
            # Do nothing and just finish
            await asyncio.sleep(0.5)
            active_task._request_queue.shutdown(immediate=True)

        agent_executor.execute = AsyncMock(side_effect=slow_execute)

        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        async def consume():
            async for _ in active_task.subscribe():
                pass

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.2)

        # Task is still running, subscribe is waiting.
        # Now it finishes.
        await asyncio.sleep(0.5)
        await task  # Should finish normally

    @pytest.mark.asyncio
    async def test_active_task_maybe_cleanup_not_finished(
        self,
        agent_executor: Mock,
        task_manager: Mock,
        push_sender: Mock,
    ) -> None:
        """Test that cleanup is not called if task is not finished."""
        on_cleanup = Mock()
        active_task = ActiveTask(
            agent_executor=agent_executor,
            task_id='test-task-id',
            task_manager=task_manager,
            push_sender=push_sender,
            on_cleanup=on_cleanup,
        )

        # Explicitly call private _maybe_cleanup to verify it respects finished state
        await active_task._maybe_cleanup()
        on_cleanup.assert_not_called()

    @pytest.mark.asyncio
    async def test_active_task_subscribe_exception_already_set(
        self, active_task: ActiveTask
    ) -> None:
        """Test subscribe when exception is already set."""
        active_task._exception = ValueError('Pre-existing error')
        with pytest.raises(ValueError, match='Pre-existing error'):
            async for _ in active_task.subscribe():
                pass

    @pytest.mark.asyncio
    async def test_active_task_subscribe_inner_exception(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
    ) -> None:
        """Test the generic exception block in subscribe."""

        async def slow_execute(req, q):
            await asyncio.sleep(10)

        agent_executor.execute = AsyncMock(side_effect=slow_execute)
        await active_task.enqueue_request(request_context)
        await active_task.start(
            call_context=ServerCallContext(), create_task_if_missing=True
        )

        mock_tapped_queue = Mock(spec=EventQueue)
        # dequeue_event returns a task that fails
        mock_tapped_queue.dequeue_event = AsyncMock(
            side_effect=Exception('Inner error')
        )
        mock_tapped_queue.close = AsyncMock()

        with (
            patch.object(
                active_task._event_queue_subscribers,
                'tap',
                return_value=mock_tapped_queue,
            ),
            pytest.raises(Exception, match='Inner error'),
        ):
            async for _ in active_task.subscribe():
                pass


@pytest.mark.asyncio
async def test_active_task_subscribe_include_initial_task():
    agent_executor = Mock()
    task_manager = Mock()
    request_context = Mock(spec=RequestContext)

    active_task = ActiveTask(
        agent_executor=agent_executor,
        task_id='test-task-id',
        task_manager=task_manager,
        push_sender=Mock(),
    )

    initial_task = Task(
        id='test-task-id', status=TaskStatus(state=TaskState.TASK_STATE_WORKING)
    )

    async def execute_mock(req, q):
        active_task._request_queue.shutdown(immediate=True)

    agent_executor.execute = AsyncMock(side_effect=execute_mock)
    task_manager.get_task = AsyncMock(return_value=initial_task)
    task_manager.save_task_event = AsyncMock()

    await active_task.enqueue_request(request_context)
    await active_task.start(
        call_context=ServerCallContext(), create_task_if_missing=True
    )

    events = [e async for e in active_task.subscribe(include_initial_task=True)]

    # Verify that the first yielded event is the initial task
    assert len(events) >= 1
    assert events[0] == initial_task


@pytest.mark.timeout(1)
@pytest.mark.asyncio
async def test_active_task_subscribe_request_parameter():
    agent_executor = Mock()
    task_manager = Mock()
    request_context = Mock(spec=RequestContext)

    active_task = ActiveTask(
        agent_executor=agent_executor,
        task_id='test-task-id',
        task_manager=task_manager,
        push_sender=Mock(),
    )

    async def execute_mock(req, q):
        # We simulate the task finishing successfully, so it will emit _RequestCompleted
        pass

    agent_executor.execute = AsyncMock(side_effect=execute_mock)
    agent_executor.cancel = AsyncMock()
    task_manager.get_task = AsyncMock(
        return_value=Task(
            id='test-task-id',
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )
    )
    task_manager.save_task_event = AsyncMock()
    task_manager.process = AsyncMock(side_effect=lambda x: x)

    await active_task.start(
        call_context=ServerCallContext(), create_task_if_missing=True
    )

    # Pass request_context directly to subscribe without enqueuing manually
    events = [e async for e in active_task.subscribe(request=request_context)]

    # Should complete without error, and yield no events (just _RequestCompleted which is hidden)
    assert len(events) == 0

    await active_task.cancel(request_context)
