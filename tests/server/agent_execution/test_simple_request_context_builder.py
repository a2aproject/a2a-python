import unittest

from unittest.mock import AsyncMock

from a2a.auth.user import UnauthenticatedUser  # Import User types
from a2a.server.agent_execution.context import (
    RequestContext,  # Corrected import path
)
from a2a.server.agent_execution.simple_request_context_builder import (
    SimpleRequestContextBuilder,
)
from a2a.server.context import ServerCallContext
from a2a.server.id_generator import IDGenerator
from a2a.server.tasks.task_store import TaskStore
from a2a.types import (
    Message,
    MessageSendParams,
    Part,
    # ServerCallContext, # Removed from a2a.types
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)


# Helper to create a simple message
def create_sample_message(
    content: str = 'test message',
    msg_id: str = 'msg1',
    role: Role = Role.user,
    reference_task_ids: list[str] | None = None,
) -> Message:
    return Message(
        message_id=msg_id,
        role=role,
        parts=[Part(root=TextPart(text=content))],
        reference_task_ids=reference_task_ids if reference_task_ids else [],
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


class TestSimpleRequestContextBuilder(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.mock_task_store = AsyncMock(spec=TaskStore)

    def test_init_with_populate_true_and_task_store(self) -> None:
        builder = SimpleRequestContextBuilder(
            should_populate_referred_tasks=True, task_store=self.mock_task_store
        )
        self.assertTrue(builder._should_populate_referred_tasks)
        self.assertEqual(builder._task_store, self.mock_task_store)

    def test_init_with_populate_false_task_store_none(self) -> None:
        builder = SimpleRequestContextBuilder(
            should_populate_referred_tasks=False, task_store=None
        )
        self.assertFalse(builder._should_populate_referred_tasks)
        self.assertIsNone(builder._task_store)

    def test_init_with_populate_false_task_store_provided(self) -> None:
        # Even if populate is false, task_store might still be provided (though not used by build for related_tasks)
        builder = SimpleRequestContextBuilder(
            should_populate_referred_tasks=False,
            task_store=self.mock_task_store,
        )
        self.assertFalse(builder._should_populate_referred_tasks)
        self.assertEqual(builder._task_store, self.mock_task_store)

    async def test_build_basic_context_no_populate(self) -> None:
        builder = SimpleRequestContextBuilder(
            should_populate_referred_tasks=False,
            task_store=self.mock_task_store,
        )

        params = MessageSendParams(message=create_sample_message())
        task_id = 'test_task_id_1'
        context_id = 'test_context_id_1'
        current_task = create_sample_task(
            task_id=task_id, context_id=context_id
        )
        # Pass a valid User instance, e.g., UnauthenticatedUser or a mock spec'd as User
        server_call_context = ServerCallContext(
            user=UnauthenticatedUser(), auth_token='dummy_token'
        )

        request_context = await builder.build(
            params=params,
            task_id=task_id,
            context_id=context_id,
            task=current_task,
            context=server_call_context,
        )

        self.assertIsInstance(request_context, RequestContext)
        # Access params via its properties message and configuration
        self.assertEqual(request_context.message, params.message)
        self.assertEqual(request_context.configuration, params.configuration)
        self.assertEqual(request_context.task_id, task_id)
        self.assertEqual(request_context.context_id, context_id)
        self.assertEqual(
            request_context.current_task, current_task
        )  # Property is current_task
        self.assertEqual(
            request_context.call_context, server_call_context
        )  # Property is call_context
        self.assertEqual(request_context.related_tasks, [])  # Initialized to []
        self.mock_task_store.get.assert_not_called()

    async def test_build_populate_true_with_reference_task_ids(self) -> None:
        builder = SimpleRequestContextBuilder(
            should_populate_referred_tasks=True, task_store=self.mock_task_store
        )
        ref_task_id1 = 'ref_task1'
        ref_task_id2 = 'ref_task2_missing'
        ref_task_id3 = 'ref_task3'

        mock_ref_task1 = create_sample_task(task_id=ref_task_id1)
        mock_ref_task3 = create_sample_task(task_id=ref_task_id3)

        # Configure task_store.get mock
        # Note: AsyncMock side_effect needs to handle multiple calls if they have different args.
        # A simple way is a list of return values, or a function.
        async def get_side_effect(task_id):
            if task_id == ref_task_id1:
                return mock_ref_task1
            if task_id == ref_task_id3:
                return mock_ref_task3
            return None

        self.mock_task_store.get = AsyncMock(side_effect=get_side_effect)

        params = MessageSendParams(
            message=create_sample_message(
                reference_task_ids=[ref_task_id1, ref_task_id2, ref_task_id3]
            )
        )
        server_call_context = ServerCallContext(user=UnauthenticatedUser())

        request_context = await builder.build(
            params=params,
            task_id='t1',
            context_id='c1',
            task=None,
            context=server_call_context,
        )

        self.assertEqual(self.mock_task_store.get.call_count, 3)
        self.mock_task_store.get.assert_any_call(ref_task_id1)
        self.mock_task_store.get.assert_any_call(ref_task_id2)
        self.mock_task_store.get.assert_any_call(ref_task_id3)

        self.assertIsNotNone(request_context.related_tasks)
        self.assertEqual(
            len(request_context.related_tasks), 2
        )  # Only non-None tasks
        self.assertIn(mock_ref_task1, request_context.related_tasks)
        self.assertIn(mock_ref_task3, request_context.related_tasks)

    async def test_build_populate_true_params_none(self) -> None:
        builder = SimpleRequestContextBuilder(
            should_populate_referred_tasks=True, task_store=self.mock_task_store
        )
        server_call_context = ServerCallContext(user=UnauthenticatedUser())
        request_context = await builder.build(
            params=None,
            task_id='t1',
            context_id='c1',
            task=None,
            context=server_call_context,
        )
        self.assertEqual(request_context.related_tasks, [])
        self.mock_task_store.get.assert_not_called()

    async def test_build_populate_true_reference_ids_empty_or_none(
        self,
    ) -> None:
        builder = SimpleRequestContextBuilder(
            should_populate_referred_tasks=True, task_store=self.mock_task_store
        )
        server_call_context = ServerCallContext(user=UnauthenticatedUser())

        # Test with empty list
        params_empty_refs = MessageSendParams(
            message=create_sample_message(reference_task_ids=[])
        )
        request_context_empty = await builder.build(
            params=params_empty_refs,
            task_id='t1',
            context_id='c1',
            task=None,
            context=server_call_context,
        )
        self.assertEqual(
            request_context_empty.related_tasks, []
        )  # Should be [] if list is empty
        self.mock_task_store.get.assert_not_called()

        self.mock_task_store.get.reset_mock()  # Reset for next call

        # Test with referenceTaskIds=None (Pydantic model might default it to empty list or handle it)
        # create_sample_message defaults to [] if None is passed, so this tests the same as above.
        # To explicitly test None in Message, we'd have to bypass Pydantic default or modify helper.
        # For now, this covers the "no IDs to process" case.
        msg_with_no_refs = Message(
            message_id='m2', role=Role.user, parts=[], referenceTaskIds=None
        )
        params_none_refs = MessageSendParams(message=msg_with_no_refs)
        request_context_none = await builder.build(
            params=params_none_refs,
            task_id='t2',
            context_id='c2',
            task=None,
            context=server_call_context,
        )
        self.assertEqual(request_context_none.related_tasks, [])
        self.mock_task_store.get.assert_not_called()

    async def test_build_populate_true_task_store_none(self) -> None:
        # This scenario might be prevented by constructor logic if should_populate_referred_tasks is True,
        # but testing defensively. The builder might allow task_store=None if it's set post-init,
        # or if constructor logic changes. Current SimpleRequestContextBuilder takes it at init.
        # If task_store is None, it should not attempt to call get.
        builder = SimpleRequestContextBuilder(
            should_populate_referred_tasks=True,
            task_store=None,  # Explicitly None
        )
        params = MessageSendParams(
            message=create_sample_message(reference_task_ids=['ref1'])
        )
        server_call_context = ServerCallContext(user=UnauthenticatedUser())

        request_context = await builder.build(
            params=params,
            task_id='t1',
            context_id='c1',
            task=None,
            context=server_call_context,
        )
        # Expect related_tasks to be an empty list as task_store is None
        self.assertEqual(request_context.related_tasks, [])
        # No mock_task_store to check calls on, this test is mostly for graceful handling.

    async def test_build_populate_false_with_reference_task_ids(self) -> None:
        builder = SimpleRequestContextBuilder(
            should_populate_referred_tasks=False,
            task_store=self.mock_task_store,
        )
        params = MessageSendParams(
            message=create_sample_message(
                reference_task_ids=['ref_task_should_not_be_fetched']
            )
        )
        server_call_context = ServerCallContext(user=UnauthenticatedUser())

        request_context = await builder.build(
            params=params,
            task_id='t1',
            context_id='c1',
            task=None,
            context=server_call_context,
        )
        self.assertEqual(request_context.related_tasks, [])
        self.mock_task_store.get.assert_not_called()

    async def test_build_with_custom_id_generators(self) -> None:
        mock_task_id_generator = AsyncMock(spec=IDGenerator)
        mock_context_id_generator = AsyncMock(spec=IDGenerator)
        mock_task_id_generator.generate.return_value = 'custom_task_id'
        mock_context_id_generator.generate.return_value = 'custom_context_id'

        builder = SimpleRequestContextBuilder(
            should_populate_referred_tasks=False,
            task_store=self.mock_task_store,
            task_id_generator=mock_task_id_generator,
            context_id_generator=mock_context_id_generator,
        )
        params = MessageSendParams(message=create_sample_message())
        server_call_context = ServerCallContext(user=UnauthenticatedUser())

        request_context = await builder.build(
            params=params,
            task_id=None,
            context_id=None,
            task=None,
            context=server_call_context,
        )

        mock_task_id_generator.generate.assert_called_once()
        mock_context_id_generator.generate.assert_called_once()
        self.assertEqual(request_context.task_id, 'custom_task_id')
        self.assertEqual(request_context.context_id, 'custom_context_id')

    async def test_build_with_provided_ids_and_custom_id_generators(
        self,
    ) -> None:
        mock_task_id_generator = AsyncMock(spec=IDGenerator)
        mock_context_id_generator = AsyncMock(spec=IDGenerator)

        builder = SimpleRequestContextBuilder(
            should_populate_referred_tasks=False,
            task_store=self.mock_task_store,
            task_id_generator=mock_task_id_generator,
            context_id_generator=mock_context_id_generator,
        )
        params = MessageSendParams(message=create_sample_message())
        server_call_context = ServerCallContext(user=UnauthenticatedUser())

        provided_task_id = 'provided_task_id'
        provided_context_id = 'provided_context_id'

        request_context = await builder.build(
            params=params,
            task_id=provided_task_id,
            context_id=provided_context_id,
            task=None,
            context=server_call_context,
        )

        mock_task_id_generator.generate.assert_not_called()
        mock_context_id_generator.generate.assert_not_called()
        self.assertEqual(request_context.task_id, provided_task_id)
        self.assertEqual(request_context.context_id, provided_context_id)


if __name__ == '__main__':
    unittest.main()
