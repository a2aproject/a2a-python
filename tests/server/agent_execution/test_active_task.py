import asyncio
import logging

from unittest.mock import AsyncMock, Mock, patch

import pytest
import pytest_asyncio

from a2a.server.agent_execution.active_task import ActiveTask
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.tasks.push_notification_sender import PushNotificationSender
from a2a.server.tasks.task_manager import TaskManager
from a2a.types.a2a_pb2 import (
    Message,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import TaskAlreadyStartedError


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

    @pytest.fixture
    def active_task(
        self,
        agent_executor: Mock,
        event_queue: EventQueue,
        task_manager: Mock,
        push_sender: Mock,
    ) -> ActiveTask:
        return ActiveTask(
            agent_executor=agent_executor,
            task_id='test-task-id',
            event_queue=event_queue,
            task_manager=task_manager,
            push_sender=push_sender,
        )

    @pytest.mark.asyncio
    async def test_active_task_lifecycle(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        task_manager: Mock,
    ) -> None:
        """Test the basic lifecycle of an ActiveTask."""

        async def execute_mock(req, q):
            await q.enqueue_event(Message(message_id='m1'))
            await q.enqueue_event(Task(id='test-task-id', status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)))

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        task_manager.get_task.return_value = Task(id='test-task-id', status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))

        await active_task.start(request_context)

        # Wait for the task to finish
        result = await active_task.wait()

        assert isinstance(result, Message)
        assert result.message_id == 'm1'
        assert active_task.task_id == 'test-task-id'

    @pytest.mark.asyncio
    async def test_active_task_already_started(
        self, active_task: ActiveTask, request_context: Mock
    ) -> None:
        """Test starting a task that is already started."""
        await active_task.start(request_context)
        with pytest.raises(TaskAlreadyStartedError):
            await active_task.start(request_context)

    @pytest.mark.asyncio
    async def test_active_task_subscribe(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
    ) -> None:
        """Test subscribing to events from an ActiveTask."""

        async def execute_mock(req, q):
            await q.enqueue_event(Message(message_id='m1'))
            await q.enqueue_event(Message(message_id='m2'))

        agent_executor.execute = AsyncMock(side_effect=execute_mock)

        await active_task.start(request_context)

        events = []
        async for event in active_task.subscribe():
            events.append(event)
            if len(events) == 2:
                break

        assert len(events) == 2
        assert events[0].message_id == 'm1'
        assert events[1].message_id == 'm2'

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
        task_manager.get_task.return_value = Task(
            id='test-task-id',
            status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
        )

        await active_task.start(request_context)

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
        task_manager.get_task.return_value = task_obj

        await active_task.start(request_context)

        result = await active_task.wait()
        assert result.id == 'test-task-id'
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
        task_manager.get_task.return_value = task_obj

        await active_task.start(request_context)

        result = await active_task.wait()
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

        await active_task.start(request_context)

        # We need to wait a bit for the producer to fail and set the exception
        for _ in range(10):
            try:
                await active_task.wait()
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
        task_obj = Task(id='test-task-id', status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))

        async def execute_mock(req, q):
            await q.enqueue_event(task_obj)

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        task_manager.get_task.return_value = task_obj

        await active_task.start(request_context)
        await active_task.wait()

        push_sender.send_notification.assert_called()

    @pytest.mark.asyncio
    async def test_active_task_cleanup(
        self,
        agent_executor: Mock,
        event_queue: EventQueue,
        task_manager: Mock,
        request_context: Mock,
    ) -> None:
        """Test that the cleanup callback is called."""
        on_cleanup = Mock()
        active_task = ActiveTask(
            agent_executor=agent_executor,
            task_id='test-task-id',
            event_queue=event_queue,
            task_manager=task_manager,
            on_cleanup=on_cleanup,
        )

        async def execute_mock(req, q):
            await q.enqueue_event(Message(message_id='m1'))
            await q.enqueue_event(Task(id='test-task-id', status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)))

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        task_manager.get_task.return_value = Task(id='test-task-id', status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))

        await active_task.start(request_context)
        await active_task.wait()

        # Wait for consumer thread to finish and call cleanup
        for _ in range(20):
            if on_cleanup.called:
                break
            await asyncio.sleep(0.05)

        on_cleanup.assert_called_once_with(active_task)

    @pytest.mark.asyncio
    async def test_active_task_wait_no_result_fallback(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        task_manager: Mock,
    ) -> None:
        """Test wait fallback when no first_result is set but task finishes."""
        task_obj = Task(id='test-task-id', status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
        task_manager.get_task.return_value = task_obj

        async def execute_mock(req, q):
            await q.enqueue_event(task_obj)

        agent_executor.execute = AsyncMock(side_effect=execute_mock)

        await active_task.start(request_context)

        # Wait for task to finish
        for _ in range(10):
            if active_task._is_finished.is_set():
                break
            await asyncio.sleep(0.05)

        result = await active_task.wait()
        assert result == task_obj

    @pytest.mark.asyncio
    async def test_active_task_wait_fails_no_result_at_all(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        task_manager: Mock,
    ) -> None:
        """Test wait raising error when no result/message available after finish."""
        async def execute_mock(req, q):
            active_task._request_queue.shutdown(immediate=True)

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        task_manager.get_task.return_value = None

        await active_task.start(request_context)

        # Wait for task to finish
        for _ in range(10):
            if active_task._is_finished.is_set():
                break
            await asyncio.sleep(0.05)

        with pytest.raises(
            RuntimeError, match='Task finished without result or message'
        ):
            await active_task.wait()

    @pytest.mark.asyncio
    async def test_active_task_consumer_failure(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        event_queue: EventQueue,
    ) -> None:
        """Test behavior when the consumer task fails."""
        # Mock dequeue_event to raise exception
        event_queue.dequeue_event = AsyncMock(
            side_effect=RuntimeError('Consumer crash')
        )

        await active_task.start(request_context)

        # We need to wait for the consumer to fail
        for _ in range(10):
            try:
                await active_task.wait()
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

        await active_task.start(request_context)

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
        # Manually set finished to avoid hanging in wait()
        active_task._is_finished.set()
        async with active_task._state_changed:
            active_task._state_changed.notify_all()

        # This will call wait() which will fail because no result/message
        with pytest.raises(
            RuntimeError, match='Task finished without result or message'
        ):
            await active_task.cancel(request_context)

    @pytest.mark.asyncio
    async def test_active_task_cancel_already_finished(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        task_manager: Mock,
    ) -> None:
        """Test canceling a task that is already finished."""
        task_obj = Task(id='test-task-id', status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))
        async def execute_mock(req, q):
            active_task._request_queue.shutdown(immediate=True)

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        task_manager.get_task.return_value = task_obj

        await active_task.start(request_context)
        await active_task.wait()

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

        await active_task.start(request_context)

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
        event_queue: EventQueue,
    ) -> None:
        """Test subscribe when the queue is shut down."""

        async def long_execute(*args, **kwargs):
            await asyncio.sleep(10)

        agent_executor.execute = AsyncMock(side_effect=long_execute)
        await active_task.start(request_context)

        tapped = await event_queue.tap()

        with patch.object(event_queue, 'tap', return_value=tapped):
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
        event_queue: EventQueue,
    ) -> None:
        """Test subscribe when an event is yielded and then the queue is shut down."""
        msg = Message(message_id='m1')

        async def execute_mock(req, q):
            await q.enqueue_event(msg)
            await asyncio.sleep(0.5)
            # Finish producer
            active_task._request_queue.shutdown(immediate=True)

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        await active_task.start(request_context)

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
        task_manager.get_task.return_value = task_obj

        await active_task.start(request_context)
        result = await active_task.wait()
        assert result == task_obj

    @pytest.mark.asyncio
    async def test_active_task_wait_fallback_to_manager(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        task_manager: Mock,
    ) -> None:
        """Test wait falling back to manager.get_task() when finished."""
        task_obj = Task(id='test')
        async def execute_mock(req, q):
            active_task._request_queue.shutdown(immediate=True)

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        task_manager.get_task.return_value = task_obj

        await active_task.start(request_context)
        # Force finish without setting _first_result
        active_task._is_finished.set()
        async with active_task._state_changed:
            active_task._state_changed.notify_all()

        result = await active_task.wait()
        assert result == task_obj

    @pytest.mark.asyncio
    async def test_active_task_subscribe_cancelled_during_yield(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        event_queue: EventQueue,
    ) -> None:
        """Test subscribe cancellation while yielding (GeneratorExit)."""
        msg = Message(message_id='m1')

        async def execute_mock(req, q):
            await q.enqueue_event(msg)
            await asyncio.sleep(10)

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        await active_task.start(request_context)

        it = active_task.subscribe()
        async for event in it:
            assert event == msg
            # Cancel while we have the event (inside the loop)
            await it.aclose()
            break

    @pytest.mark.asyncio
    async def test_active_task_wait_runtime_error_absolute(
        self, active_task: ActiveTask
    ) -> None:
        """Test the final RuntimeError in wait()."""
        active_task._is_finished.set()
        async with active_task._state_changed:
            active_task._state_changed.notify_all()

        # No first_result, no manager task, no message
        with pytest.raises(
            RuntimeError, match='Task finished without result or message'
        ):
            await active_task.wait()

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
        await active_task.start(request_context)

        # Forced queue close.
        await active_task._event_queue.close()

        # Now cancel the task itself.
        await active_task.cancel(request_context)
        await asyncio.wait_for(active_task.wait(), timeout=0.1)

        # Cancel again should not do anything.
        await active_task.cancel(request_context)
        await asyncio.wait_for(active_task.wait(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_active_task_subscribe_dequeue_failure(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        event_queue: EventQueue,
    ) -> None:
        """Test subscribe when dequeue_event fails on the tapped queue."""

        async def slow_execute(req, q):
            await asyncio.sleep(10)

        agent_executor.execute = AsyncMock(side_effect=slow_execute)
        await active_task.start(request_context)

        mock_tapped_queue = Mock(spec=EventQueue)
        mock_tapped_queue.dequeue_event = AsyncMock(
            side_effect=RuntimeError('Tapped queue crash')
        )
        mock_tapped_queue.close = AsyncMock()

        with (
            patch.object(event_queue, 'tap', return_value=mock_tapped_queue),
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
        task_manager.get_task.return_value = task_obj

        await active_task.start(request_context)

        result = await active_task.wait()
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

        await active_task.start(request_context)

        # Wait for it to finish
        await active_task._is_finished.wait()

        async for _ in active_task.subscribe():
            pytest.fail('Should not have any events')

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

        await active_task.start(request_context)

        # Consumer should also finish
        with pytest.raises(ValueError, match='Quick failure'):
            await active_task.wait()

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

        await active_task.start(request_context)

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
        event_queue: EventQueue,
        task_manager: Mock,
        push_sender: Mock,
    ) -> None:
        """Test that cleanup is not called if task is not finished."""
        on_cleanup = Mock()
        active_task = ActiveTask(
            agent_executor=agent_executor,
            task_id='test-task-id',
            event_queue=event_queue,
            task_manager=task_manager,
            push_sender=push_sender,
            on_cleanup=on_cleanup,
        )

        # Explicitly call private _maybe_cleanup to verify it respects finished state
        await active_task._maybe_cleanup()
        on_cleanup.assert_not_called()

    @pytest.mark.asyncio
    async def test_active_task_maybe_cleanup_with_subscribers(
        self,
        agent_executor: Mock,
        event_queue: EventQueue,
        task_manager: Mock,
        push_sender: Mock,
        request_context: Mock,
    ) -> None:
        """Test that cleanup is not called if there are subscribers."""
        on_cleanup = Mock()
        active_task = ActiveTask(
            agent_executor=agent_executor,
            task_id='test-task-id',
            event_queue=event_queue,
            task_manager=task_manager,
            push_sender=push_sender,
            on_cleanup=on_cleanup,
        )

        # Mock execute to finish immediately
        async def execute_mock(req, q):
            await q.enqueue_event(Message(message_id='m1'))
            await q.enqueue_event(Task(id='test-task-id', status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)))

        agent_executor.execute = AsyncMock(side_effect=execute_mock)
        task_manager.get_task.return_value = Task(id='test-task-id', status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))

        # 1. Start a subscriber before task finishes
        gen = active_task.subscribe()
        # Start the generator to increment reference count
        msg_task = asyncio.create_task(gen.__anext__())

        # 2. Start the task and wait for it to finish
        await active_task.start(request_context)
        await active_task.wait()

        # Give the consumer loop a moment to set _is_finished
        await asyncio.sleep(0.1)

        # Ensure we got the message
        assert (await msg_task).message_id == 'm1'

        # At this point, task is finished, but we still have a subscriber (gen).
        # _maybe_cleanup was called by consumer loop, but should have done nothing.
        on_cleanup.assert_not_called()

        # 3. Close the subscriber
        await gen.aclose()

        # Now cleanup should be triggered
        on_cleanup.assert_called_once_with(active_task)

    @pytest.mark.asyncio
    async def test_active_task_cancel_producer_none(
        self, active_task: ActiveTask, request_context: Mock, task_manager: Mock
    ) -> None:
        """Test cancel when producer_task is None."""
        active_task._is_finished.set()
        task_manager.get_task.return_value = Task(id='test')
        await active_task.cancel(request_context)
        # Should not raise and should finish

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
    async def test_active_task_wait_exception_already_set(
        self, active_task: ActiveTask
    ) -> None:
        """Test wait when exception is already set."""
        active_task._exception = ValueError('Pre-existing error')
        with pytest.raises(ValueError, match='Pre-existing error'):
            await active_task.wait()

    @pytest.mark.asyncio
    async def test_active_task_subscribe_inner_exception(
        self,
        active_task: ActiveTask,
        agent_executor: Mock,
        request_context: Mock,
        event_queue: EventQueue,
    ) -> None:
        """Test the generic exception block in subscribe."""

        async def slow_execute(req, q):
            await asyncio.sleep(10)

        agent_executor.execute = AsyncMock(side_effect=slow_execute)
        await active_task.start(request_context)

        mock_tapped_queue = Mock(spec=EventQueue)
        # dequeue_event returns a task that fails
        mock_tapped_queue.dequeue_event = AsyncMock(
            side_effect=Exception('Inner error')
        )
        mock_tapped_queue.close = AsyncMock()

        with (
            patch.object(event_queue, 'tap', return_value=mock_tapped_queue),
            pytest.raises(Exception, match='Inner error'),
        ):
            async for _ in active_task.subscribe():
                pass
