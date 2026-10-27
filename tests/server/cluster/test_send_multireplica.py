import asyncio
import contextlib

import pytest

from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.cluster import ConcurrentTaskModificationError, TaskVersion
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types.a2a_pb2 import Part, Role, Task, TaskState
from a2a.utils.errors import InternalError

from .conftest import (
    CompletingAgent,
    InputRequiredThenCompleteAgent,
    VersionedInMemoryTaskStore,
    build_send_request,
    make_replica,
)


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_single_send_completes_on_shared_store(
    shared_store, shared_stream, context
) -> None:
    handler = make_replica(shared_store, shared_stream, CompletingAgent())
    try:
        result = await handler.on_message_send(
            build_send_request('hi'), context
        )
        assert result.status.state == TaskState.TASK_STATE_COMPLETED
        # Task persisted with a real version.
        stored = await shared_store.get(result.id, context)
        assert stored is not None
        assert stored.task.status.state == TaskState.TASK_STATE_COMPLETED
        assert not stored.version.is_missing
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_multiturn_input_required_across_replicas(
    shared_store, shared_stream, context
) -> None:
    """turn 1 -> replica A, turn 2 -> replica B, turn 3 -> replica A.

    The pre-fix bug: replica A reuses a stale cached snapshot on turn 3 and
    clobbers replica B's turn-2 write. With request-boundary invalidation +
    CAS, A re-reads and the task advances correctly.
    """
    agent = InputRequiredThenCompleteAgent()
    replica_a = make_replica(shared_store, shared_stream, agent)
    replica_b = make_replica(shared_store, shared_stream, agent)
    try:
        # Turn 1 on A: creates task, asks for input.
        r1 = await replica_a.on_message_send(
            build_send_request('q1', message_id='m1'), context
        )
        assert r1.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        task_id = r1.id
        context_id = r1.context_id

        # Turn 2 on B: provides input; agent still needs more (2 user msgs).
        r2 = await replica_b.on_message_send(
            build_send_request(
                'a1', task_id=task_id, context_id=context_id, message_id='m2'
            ),
            context,
        )
        assert r2.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        # B's write is durable and visible.
        stored = await shared_store.get(task_id, context)
        assert stored is not None
        assert (
            sum(1 for m in stored.task.history if m.role == Role.ROLE_USER) == 2
        )

        # Turn 3 back on A: must see B's state (not the stale turn-1 snapshot)
        # and complete.
        r3 = await replica_a.on_message_send(
            build_send_request(
                'a2', task_id=task_id, context_id=context_id, message_id='m3'
            ),
            context,
        )
        assert r3.status.state == TaskState.TASK_STATE_COMPLETED
    finally:
        await replica_a.aclose()
        await replica_b.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_stale_version_save_raises_conflict_directly(
    shared_store, shared_stream, context
) -> None:
    """The store-level CAS that underpins the handler fix."""
    # A non-terminal task, so only the version check can reject the save.
    handler = make_replica(
        shared_store, shared_stream, InputRequiredThenCompleteAgent()
    )
    try:
        result = await handler.on_message_send(
            build_send_request('hi'), context
        )
        stored = await shared_store.get(result.id, context)
        assert stored is not None
        task, v = stored.task, stored.version
        # Simulate a stale writer: another write advances the version.
        await shared_store.save(
            task, event=None, prev=None, prev_version=v, context=context
        )
        # Now the original version is stale.
        with pytest.raises(ConcurrentTaskModificationError):
            await shared_store.save(
                task, event=None, prev=None, prev_version=v, context=context
            )
    finally:
        await handler.aclose()


class _ContendedStore(VersionedInMemoryTaskStore):
    """Rejects every save while `contended` is set, and the next `conflicts`
    saves, as if other replicas kept writing the task."""

    def __init__(self) -> None:
        super().__init__()
        self.contended = False
        self.conflicts = 0

    async def save(self, task: Task, **kwargs) -> TaskVersion:  # noqa: ANN003
        if self.contended:
            raise ConcurrentTaskModificationError(task.id)
        if self.conflicts > 0:
            self.conflicts -= 1
            raise ConcurrentTaskModificationError(task.id)
        return await super().save(task, **kwargs)


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_follow_up_message_survives_a_conflict_on_its_first_save(
    shared_stream, context
) -> None:
    """The follow-up's message is saved with the turn's first event. When
    that save conflicts and is retried on the reloaded task, the message
    must still be recorded."""
    store = _ContendedStore()
    handler = make_replica(
        store, shared_stream, InputRequiredThenCompleteAgent()
    )
    try:
        r1 = await handler.on_message_send(
            build_send_request('q1', message_id='m1'), context
        )
        store.conflicts = 1
        await handler.on_message_send(
            build_send_request(
                'a', task_id=r1.id, context_id=r1.context_id, message_id='m2'
            ),
            context,
        )

        stored = await store.get(r1.id, context)
        assert stored is not None
        assert [
            m.message_id
            for m in stored.task.history
            if m.role == Role.ROLE_USER
        ] == ['m1', 'm2']
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_follow_up_that_keeps_conflicting_fails_with_a2a_error(
    shared_stream, context
) -> None:
    """When the retry after a conflict conflicts again, the request fails
    with an A2A error instead of hanging, and the stored task is kept."""
    store = _ContendedStore()
    handler = make_replica(
        store, shared_stream, InputRequiredThenCompleteAgent()
    )
    try:
        r1 = await handler.on_message_send(
            build_send_request('q1', message_id='m1'), context
        )
        assert r1.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

        store.contended = True
        with pytest.raises(InternalError):
            await asyncio.wait_for(
                handler.on_message_send(
                    build_send_request(
                        'a',
                        task_id=r1.id,
                        context_id=r1.context_id,
                        message_id='m2',
                    ),
                    context,
                ),
                timeout=5,
            )
        store.contended = False

        stored = await store.get(r1.id, context)
        assert stored is not None
        assert stored.task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    finally:
        store.contended = False
        await handler.aclose()


class _SecondTurnAgent(AgentExecutor):
    """Turn 1 asks for input; turn 2 starts with the configured first event."""

    def __init__(self, second_turn: str) -> None:
        self.second_turn = second_turn

    async def execute(self, context, event_queue) -> None:  # noqa: ANN001
        updater = TaskUpdater(
            event_queue, str(context.task_id), str(context.context_id)
        )
        if context.current_task is None:
            await event_queue.enqueue_event(
                new_task_from_user_message(context.message)
            )
            await updater.requires_input(
                message=updater.new_agent_message([Part(text='need input')])
            )
            return
        if self.second_turn == 'raises':
            raise RuntimeError('agent failed')
        if self.second_turn == 'artifact_first':
            await updater.add_artifact([Part(text='result')])
        await updater.complete()

    async def cancel(self, context, event_queue) -> None:  # noqa: ANN001
        pass


@pytest.mark.asyncio
@pytest.mark.timeout(10)
@pytest.mark.parametrize(
    ('second_turn', 'final_state'),
    [
        ('status_first', TaskState.TASK_STATE_COMPLETED),
        ('artifact_first', TaskState.TASK_STATE_COMPLETED),
        ('raises', TaskState.TASK_STATE_FAILED),
    ],
)
async def test_resumed_turn_records_follow_up_message_once(
    shared_store, shared_stream, context, second_turn: str, final_state
) -> None:
    agent = _SecondTurnAgent(second_turn)
    replica_a = make_replica(shared_store, shared_stream, agent)
    replica_b = make_replica(shared_store, shared_stream, agent)
    try:
        first = await replica_a.on_message_send(
            build_send_request('q1', message_id='m1'), context
        )
        with contextlib.suppress(RuntimeError):
            await replica_b.on_message_send(
                build_send_request(
                    'a1',
                    task_id=first.id,
                    context_id=first.context_id,
                    message_id='m2',
                ),
                context,
            )
        stored = await shared_store.get(first.id, context)
        assert stored is not None
        assert stored.task.status.state == final_state
        assert [
            m.message_id
            for m in stored.task.history
            if m.role == Role.ROLE_USER
        ] == ['m1', 'm2']
    finally:
        await replica_a.aclose()
        await replica_b.aclose()
