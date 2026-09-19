import asyncio

import pytest

from a2a.types.a2a_pb2 import SubscribeToTaskRequest, TaskState
from a2a.utils.errors import InvalidParamsError, TaskNotFoundError

from .conftest import (
    ControlledAgent,
    build_send_request,
    make_context,
    make_replica,
    wait_for_state,
)


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_resubscribe_on_non_owning_replica_streams_events(
    shared_store, shared_stream, context
) -> None:
    """Replica A runs the agent; a resubscribe on replica B streams its events.

    Pre-fix, B would build a local ActiveTask, emit the snapshot, then hang.
    """
    agent = ControlledAgent()
    replica_a = make_replica(shared_store, shared_stream, agent)
    replica_b = make_replica(shared_store, shared_stream, agent)
    try:
        a_events: list = []

        async def run_a() -> None:
            async for ev in replica_a.on_message_send_stream(
                build_send_request('go', message_id='m1'), context
            ):
                a_events.append(ev)

        a_task = asyncio.create_task(run_a())
        await asyncio.wait_for(agent.working.wait(), timeout=5)

        task_id = next(
            iter(replica_a._active_task_registry._active_tasks)  # noqa: SLF001
        )
        await wait_for_state(
            shared_store, task_id, TaskState.TASK_STATE_WORKING, context
        )

        # Resubscribe on B (not running the agent). Collect until COMPLETED.
        b_states: list = []

        async def run_b() -> None:
            async for ev in replica_b.on_subscribe_to_task(
                SubscribeToTaskRequest(id=task_id), context
            ):
                if getattr(ev, 'status', None):
                    b_states.append(ev.status.state)
                    if ev.status.state == TaskState.TASK_STATE_COMPLETED:
                        return

        b_task = asyncio.create_task(run_b())
        await asyncio.sleep(0.1)  # let B read snapshot + start tailing

        # Release the agent on A -> it completes; B must observe via the stream.
        agent.release.set()

        await asyncio.wait_for(b_task, timeout=8)
        await asyncio.wait_for(a_task, timeout=8)

        assert b_states[0] == TaskState.TASK_STATE_WORKING
        assert b_states[-1] == TaskState.TASK_STATE_COMPLETED
    finally:
        await replica_a.aclose()
        await replica_b.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_resubscribe_to_terminal_task_rejected(
    shared_store, shared_stream, context
) -> None:
    """A resubscribe to an already-finished task is rejected (no hang)."""
    agent = ControlledAgent()
    agent.release.set()  # completes immediately
    handler = make_replica(shared_store, shared_stream, agent)
    other = make_replica(shared_store, shared_stream, agent)
    try:
        result = await handler.on_message_send(
            build_send_request('go', message_id='m1'), context
        )
        assert result.status.state == TaskState.TASK_STATE_COMPLETED
        task_id = result.id

        # Terminal task: the non-owning replica rejects rather than hanging.
        with pytest.raises(InvalidParamsError, match='terminal state'):
            async for _ in other.on_subscribe_to_task(
                SubscribeToTaskRequest(id=task_id), context
            ):
                pass
    finally:
        await handler.aclose()
        await other.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_resubscribe_absent_task_raises_not_found(
    shared_store, shared_stream, context
) -> None:
    handler = make_replica(shared_store, shared_stream, ControlledAgent())
    try:
        with pytest.raises(TaskNotFoundError):
            async for _ in handler.on_subscribe_to_task(
                SubscribeToTaskRequest(id='nope'), context
            ):
                pass
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_resubscribe_non_owner_rejected(
    shared_store, shared_stream
) -> None:
    """Issue #1159: a non-owner cannot resubscribe to another user's task."""
    agent = ControlledAgent()
    agent.release.set()
    handler = make_replica(shared_store, shared_stream, agent)
    try:
        result = await handler.on_message_send(
            build_send_request('go', message_id='m1'), make_context('alice')
        )
        with pytest.raises(TaskNotFoundError):
            async for _ in handler.on_subscribe_to_task(
                SubscribeToTaskRequest(id=result.id), make_context('bob')
            ):
                pass
    finally:
        await handler.aclose()
