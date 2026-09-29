import asyncio

import pytest

from a2a.server.cluster import ConcurrentTaskModificationError, TaskVersion
from a2a.types.a2a_pb2 import Role, Task, TaskState
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
    handler = make_replica(shared_store, shared_stream, CompletingAgent())
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
    """Rejects every save while `contended` is set, as if other replicas kept
    writing the task."""

    def __init__(self) -> None:
        super().__init__()
        self.contended = False

    async def save(self, task: Task, **kwargs) -> TaskVersion:  # noqa: ANN003
        if self.contended:
            raise ConcurrentTaskModificationError(task.id)
        return await super().save(task, **kwargs)


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
