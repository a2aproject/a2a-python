import asyncio
import unittest

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

from typing_extensions import override

from a2a.server.events.event_consumer import EventConsumer
from a2a.server.tasks.result_aggregator import ResultAggregator
from a2a.server.tasks.task_manager import TaskManager
from a2a.types import (
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
)


# Helper to create a simple message
def create_sample_message(
    content: str = 'test message', msg_id: str = 'msg1', role: Role = Role.user
) -> Message:
    return Message(
        message_id=msg_id,
        role=role,
        parts=[Part(root=TextPart(text=content))],
    )


# Helper to create a simple task
def create_sample_task(
    task_id: str = 'task1',
    status_state: TaskState = TaskState.submitted,
    context_id: str = 'ctx1',
) -> Task:
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=status_state),
    )


# Helper to create a TaskStatusUpdateEvent
def create_sample_status_update(
    task_id: str = 'task1',
    status_state: TaskState = TaskState.working,
    context_id: str = 'ctx1',
) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id=task_id,
        context_id=context_id,
        status=TaskStatus(state=status_state),
        final=False,  # Typically false unless it's the very last update
    )


class TestResultAggregator(unittest.IsolatedAsyncioTestCase):
    @override
    def setUp(self) -> None:
        self.mock_task_manager = AsyncMock(spec=TaskManager)
        self.mock_event_consumer = AsyncMock(spec=EventConsumer)
        self.aggregator = ResultAggregator(
            task_manager=self.mock_task_manager
            # event_consumer is not passed to constructor
        )

    def test_init_stores_task_manager(self) -> None:
        self.assertEqual(self.aggregator.task_manager, self.mock_task_manager)
        # event_consumer is also stored, can be tested if needed, but focus is on task_manager per req.

    async def test_current_result_property_with_message_set(self) -> None:
        sample_message = create_sample_message(content='hola')
        self.aggregator._message = sample_message
        self.assertEqual(await self.aggregator.current_result, sample_message)
        self.mock_task_manager.get_task.assert_not_called()

    async def test_current_result_property_with_message_none(self) -> None:
        expected_task = create_sample_task(task_id='task_from_tm')
        self.mock_task_manager.get_task.return_value = expected_task
        self.aggregator._message = None

        current_res = await self.aggregator.current_result

        self.assertEqual(current_res, expected_task)
        self.mock_task_manager.get_task.assert_called_once()

    async def test_consume_and_emit(self) -> None:
        event1 = create_sample_message(content='event one', msg_id='e1')
        event2 = create_sample_task(
            task_id='task_event', status_state=TaskState.working
        )
        event3 = create_sample_status_update(
            task_id='task_event', status_state=TaskState.completed
        )

        # Mock event_consumer.consume() to be an async generator
        async def mock_consume_generator():
            yield event1
            yield event2
            yield event3

        self.mock_event_consumer.consume_all.return_value = (
            mock_consume_generator()
        )

        # To store yielded events
        yielded_events = []
        async for event in self.aggregator.consume_and_emit(
            self.mock_event_consumer
        ):
            yielded_events.append(event)

        # Assert that all events were yielded
        self.assertEqual(len(yielded_events), 3)
        self.assertIn(event1, yielded_events)
        self.assertIn(event2, yielded_events)
        self.assertIn(event3, yielded_events)

        # Assert that task_manager.process was called for each event
        self.assertEqual(self.mock_task_manager.process.call_count, 3)
        self.mock_task_manager.process.assert_any_call(event1)
        self.mock_task_manager.process.assert_any_call(event2)
        self.mock_task_manager.process.assert_any_call(event3)

    async def test_consume_all_only_message_event(self) -> None:
        sample_message = create_sample_message(content='final message')

        async def mock_consume_generator():
            yield sample_message

        self.mock_event_consumer.consume_all.return_value = (
            mock_consume_generator()
        )

        result = await self.aggregator.consume_all(self.mock_event_consumer)

        self.assertEqual(result, sample_message)
        self.mock_task_manager.process.assert_not_called()  # Process is not called if message is returned directly
        self.mock_task_manager.get_task.assert_not_called()  # Should not be called if message is returned

    async def test_consume_all_other_event_types(self) -> None:
        task_event = create_sample_task(task_id='task_other_event')
        status_update_event = create_sample_status_update(
            task_id='task_other_event', status_state=TaskState.completed
        )
        final_task_state = create_sample_task(
            task_id='task_other_event', status_state=TaskState.completed
        )

        async def mock_consume_generator():
            yield task_event
            yield status_update_event

        self.mock_event_consumer.consume_all.return_value = (
            mock_consume_generator()
        )
        self.mock_task_manager.get_task.return_value = final_task_state

        result = await self.aggregator.consume_all(self.mock_event_consumer)

        self.assertEqual(result, final_task_state)
        self.assertEqual(self.mock_task_manager.process.call_count, 2)
        self.mock_task_manager.process.assert_any_call(task_event)
        self.mock_task_manager.process.assert_any_call(status_update_event)
        self.mock_task_manager.get_task.assert_called_once()

    async def test_consume_all_empty_stream(self) -> None:
        empty_task_state = create_sample_task(task_id='empty_stream_task')

        async def mock_consume_generator():
            if False:  # Will not yield anything
                yield

        self.mock_event_consumer.consume_all.return_value = (
            mock_consume_generator()
        )
        self.mock_task_manager.get_task.return_value = empty_task_state

        result = await self.aggregator.consume_all(self.mock_event_consumer)

        self.assertEqual(result, empty_task_state)
        self.mock_task_manager.process.assert_not_called()
        self.mock_task_manager.get_task.assert_called_once()

    async def test_consume_all_event_consumer_exception(self) -> None:
        class TestException(Exception):
            pass

        self.mock_event_consumer.consume_all = (
            AsyncMock()
        )  # Re-mock to make it an async generator that raises

        async def raiser_gen():
            # Yield a non-Message event first to ensure process is called
            yield create_sample_task('task_before_error_consume_all')
            raise TestException('Consumer error')

        self.mock_event_consumer.consume_all = MagicMock(
            return_value=raiser_gen()
        )

        with self.assertRaises(TestException):
            await self.aggregator.consume_all(self.mock_event_consumer)

        # Ensure process was called for the event before the exception
        self.mock_task_manager.process.assert_called_once_with(
            unittest.mock.ANY  # Check it was called, arg is the task
        )
        self.mock_task_manager.get_task.assert_not_called()

    async def test_consume_and_break_on_message(self) -> None:
        sample_message = create_sample_message(content='interrupt message')
        event_after = create_sample_task('task_after_msg')

        async def mock_consume_generator():
            yield sample_message
            yield event_after  # This should not be processed by task_manager in this call

        self.mock_event_consumer.consume_all.return_value = (
            mock_consume_generator()
        )

        (
            result,
            interrupted,
        ) = await self.aggregator.consume_and_break_on_interrupt(
            self.mock_event_consumer
        )

        self.assertEqual(result, sample_message)
        self.assertFalse(interrupted)
        self.mock_task_manager.process.assert_not_called()  # Process is not called for the Message if returned directly
        # _continue_consuming should not be called if it's a message interrupt
        # and no auth_required state.

    @patch('asyncio.create_task')
    async def test_consume_and_break_on_auth_required_task_event(
        self, mock_create_task: MagicMock
    ) -> None:
        auth_task = create_sample_task(
            task_id='auth_task', status_state=TaskState.auth_required
        )
        event_after_auth = create_sample_message('after auth')

        async def mock_consume_generator():
            yield auth_task
            yield event_after_auth  # This event will be handled by _continue_consuming

        self.mock_event_consumer.consume_all.return_value = (
            mock_consume_generator()
        )
        self.mock_task_manager.get_task.return_value = (
            auth_task  # current_result after auth_task processing
        )

        # Mock _continue_consuming to check if it's called by create_task
        self.aggregator._continue_consuming = AsyncMock()
        mock_create_task.side_effect = lambda coro: asyncio.ensure_future(coro)

        (
            result,
            interrupted,
        ) = await self.aggregator.consume_and_break_on_interrupt(
            self.mock_event_consumer
        )

        self.assertEqual(result, auth_task)
        self.assertTrue(interrupted)
        self.mock_task_manager.process.assert_called_once_with(auth_task)
        mock_create_task.assert_called_once()  # Check that create_task was called
        # self.aggregator._continue_consuming is an AsyncMock.
        # The actual call in product code is create_task(self._continue_consuming(event_stream_arg))
        # So, we check that our mock _continue_consuming was called with an AsyncIterator arg.
        self.aggregator._continue_consuming.assert_called_once()
        self.assertIsInstance(
            self.aggregator._continue_consuming.call_args[0][0], AsyncIterator
        )

        # Manually run the mocked _continue_consuming to check its behavior
        # This requires the generator to be re-setup or passed if stateful.
        # For simplicity, let's assume _continue_consuming uses the same generator instance.
        # In a real scenario, the generator's state would be an issue.
        # However, ResultAggregator re-assigns self.mock_event_consumer.consume()
        # to self.aggregator._event_stream in the actual code.
        # The test setup for _continue_consuming needs to be more robust if we want to test its internal loop.
        # For now, we've verified it's called.

    @patch('asyncio.create_task')
    async def test_consume_and_break_on_auth_required_status_update_event(
        self, mock_create_task: MagicMock
    ) -> None:
        auth_status_update = create_sample_status_update(
            task_id='auth_status_task', status_state=TaskState.auth_required
        )
        current_task_state_after_update = create_sample_task(
            task_id='auth_status_task', status_state=TaskState.auth_required
        )

        async def mock_consume_generator():
            yield auth_status_update

        self.mock_event_consumer.consume_all.return_value = (
            mock_consume_generator()
        )
        # When current_result is called after processing auth_status_update
        self.mock_task_manager.get_task.return_value = (
            current_task_state_after_update
        )
        self.aggregator._continue_consuming = AsyncMock()
        mock_create_task.side_effect = lambda coro: asyncio.ensure_future(coro)

        (
            result,
            interrupted,
        ) = await self.aggregator.consume_and_break_on_interrupt(
            self.mock_event_consumer
        )

        self.assertEqual(result, current_task_state_after_update)
        self.assertTrue(interrupted)
        self.mock_task_manager.process.assert_called_once_with(
            auth_status_update
        )
        mock_create_task.assert_called_once()
        self.aggregator._continue_consuming.assert_called_once()
        self.assertIsInstance(
            self.aggregator._continue_consuming.call_args[0][0], AsyncIterator
        )

    async def test_consume_and_break_completes_normally(self) -> None:
        event1 = create_sample_message('event one normal', msg_id='n1')
        event2 = create_sample_task('normal_task')
        final_task_state = create_sample_task(
            'normal_task', status_state=TaskState.completed
        )

        async def mock_consume_generator():
            yield event1
            yield event2

        self.mock_event_consumer.consume_all.return_value = (
            mock_consume_generator()
        )
        self.mock_task_manager.get_task.return_value = (
            final_task_state  # For the end of stream
        )

        (
            result,
            interrupted,
        ) = await self.aggregator.consume_and_break_on_interrupt(
            self.mock_event_consumer
        )

        # If the first event is a Message, it's returned directly.
        self.assertEqual(result, event1)
        self.assertFalse(interrupted)
        # process() is NOT called for the Message if it's the one causing the return
        self.mock_task_manager.process.assert_not_called()
        self.mock_task_manager.get_task.assert_not_called()

    async def test_consume_and_break_event_consumer_exception(self) -> None:
        class TestInterruptException(Exception):
            pass

        self.mock_event_consumer.consume_all = AsyncMock()

        async def raiser_gen_interrupt():
            # Yield a non-Message event first
            yield create_sample_task('task_before_error_interrupt')
            raise TestInterruptException(
                'Consumer error during interrupt check'
            )

        self.mock_event_consumer.consume_all = MagicMock(
            return_value=raiser_gen_interrupt()
        )

        with self.assertRaises(TestInterruptException):
            await self.aggregator.consume_and_break_on_interrupt(
                self.mock_event_consumer
            )

        self.mock_task_manager.process.assert_called_once_with(
            unittest.mock.ANY  # Check it was called, arg is the task
        )
        self.mock_task_manager.get_task.assert_not_called()

    @patch('asyncio.create_task')
    async def test_consume_and_break_non_blocking(
        self, mock_create_task: MagicMock
    ) -> None:
        """Test that with blocking=False, the method returns after the first event."""
        first_event = create_sample_task('non_blocking_task')
        event_after = create_sample_message('should be consumed later')

        async def mock_consume_generator():
            yield first_event
            yield event_after

        self.mock_event_consumer.consume_all.return_value = (
            mock_consume_generator()
        )
        # After processing `first_event`, the current result will be that task.
        self.aggregator.task_manager.get_task.return_value = first_event

        self.aggregator._continue_consuming = AsyncMock()
        mock_create_task.side_effect = lambda coro: asyncio.ensure_future(coro)

        (
            result,
            interrupted,
        ) = await self.aggregator.consume_and_break_on_interrupt(
            self.mock_event_consumer, blocking=False
        )

        self.assertEqual(result, first_event)
        self.assertTrue(interrupted)
        self.mock_task_manager.process.assert_called_once_with(first_event)
        mock_create_task.assert_called_once()
        # The background task should be created with the remaining stream
        self.aggregator._continue_consuming.assert_called_once()
        self.assertIsInstance(
            self.aggregator._continue_consuming.call_args[0][0], AsyncIterator
        )

    @patch('asyncio.create_task')  # To verify _continue_consuming is called
    async def test_continue_consuming_processes_remaining_events(
        self, mock_create_task: MagicMock
    ) -> None:
        # This test focuses on verifying that if an interrupt occurs,
        # the events *after* the interrupting one are processed by _continue_consuming.

        auth_event = create_sample_task(
            'task_auth_for_continue', status_state=TaskState.auth_required
        )
        event_after_auth1 = create_sample_message(
            'after auth 1', msg_id='cont1'
        )
        event_after_auth2 = create_sample_task('task_after_auth_2')

        # This generator will be iterated first by consume_and_break_on_interrupt,
        # then by _continue_consuming.
        # We need a way to simulate this shared iterator state or provide a new one for _continue_consuming.
        # The actual implementation uses self.aggregator._event_stream

        # Let's simulate the state after consume_and_break_on_interrupt has consumed auth_event
        # and _event_stream is now the rest of the generator.

        # Initial stream for consume_and_break_on_interrupt
        async def initial_consume_generator():
            yield auth_event
            # These should be consumed by _continue_consuming
            yield event_after_auth1
            yield event_after_auth2

        self.mock_event_consumer.consume_all.return_value = (
            initial_consume_generator()
        )
        self.mock_task_manager.get_task.return_value = (
            auth_event  # Task state at interrupt
        )
        mock_create_task.side_effect = lambda coro: asyncio.ensure_future(coro)

        # Call the main method that triggers _continue_consuming via create_task
        _, _ = await self.aggregator.consume_and_break_on_interrupt(
            self.mock_event_consumer
        )

        mock_create_task.assert_called_once()
        # Now, we need to actually execute the coroutine passed to create_task
        # to test the behavior of _continue_consuming
        continue_consuming_coro = mock_create_task.call_args[0][0]

        # Reset process mock to only count calls from _continue_consuming
        self.mock_task_manager.process.reset_mock()

        await continue_consuming_coro

        # Verify process was called for events after the interrupt
        self.assertEqual(self.mock_task_manager.process.call_count, 2)
        self.mock_task_manager.process.assert_any_call(event_after_auth1)
        self.mock_task_manager.process.assert_any_call(event_after_auth2)


if __name__ == '__main__':
    unittest.main()
