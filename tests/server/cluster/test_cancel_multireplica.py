import asyncio
import contextlib

import pytest

from a2a.types.a2a_pb2 import CancelTaskRequest, TaskState
from a2a.utils.errors import (
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)

from .conftest import (
    CompletingAgent,
    InputRequiredThenCompleteAgent,
    LongRunningAgent,
    build_send_request,
    drain,
    make_context,
    make_replica,
    wait_for_state,
)


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_cancel_on_non_owning_replica_stops_remote_agent(
    shared_store, shared_stream, context
) -> None:
    agent = LongRunningAgent()
    replica_a = make_replica(shared_store, shared_stream, agent)
    replica_b = make_replica(shared_store, shared_stream, agent)
    try:
        a_task = asyncio.create_task(
            drain(
                replica_a.on_message_send_stream(
                    build_send_request('go', message_id='m1'), context
                )
            )
        )
        await asyncio.wait_for(agent.working.wait(), timeout=5)

        task_id = next(
            iter(replica_a._active_task_registry._active_tasks)  # noqa: SLF001
        )
        await wait_for_state(
            shared_store, task_id, TaskState.TASK_STATE_WORKING, context
        )

        # Cancel from B (not running the agent).
        result = await replica_b.on_cancel_task(
            CancelTaskRequest(id=task_id), context
        )
        assert result.status.state == TaskState.TASK_STATE_CANCELED

        # A's agent observes the CAS conflict and aborts; its stream ends.
        await asyncio.wait_for(agent.aborted.wait(), timeout=8)
        await asyncio.wait_for(a_task, timeout=8)

        final = await shared_store.get(task_id, context)
        assert final is not None
        assert final.task.status.state == TaskState.TASK_STATE_CANCELED
    finally:
        await replica_a.aclose()
        await replica_b.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_second_cancel_is_not_cancelable(
    shared_store, shared_stream, context
) -> None:
    agent = LongRunningAgent()
    handler = make_replica(shared_store, shared_stream, agent)
    other = make_replica(shared_store, shared_stream, agent)
    try:
        a_task = asyncio.create_task(
            drain(
                handler.on_message_send_stream(
                    build_send_request('go', message_id='m1'), context
                )
            )
        )
        await asyncio.wait_for(agent.working.wait(), timeout=5)
        task_id = next(
            iter(handler._active_task_registry._active_tasks)  # noqa: SLF001
        )
        await wait_for_state(
            shared_store, task_id, TaskState.TASK_STATE_WORKING, context
        )

        r1 = await other.on_cancel_task(CancelTaskRequest(id=task_id), context)
        assert r1.status.state == TaskState.TASK_STATE_CANCELED
        # A cancelled task is terminal, so a second cancel is rejected.
        with pytest.raises(TaskNotCancelableError):
            await other.on_cancel_task(CancelTaskRequest(id=task_id), context)
        stored = await shared_store.get(task_id, context)
        assert stored is not None
        assert stored.task.status.state == TaskState.TASK_STATE_CANCELED
        with contextlib.suppress(Exception):
            await asyncio.wait_for(a_task, timeout=8)
    finally:
        await handler.aclose()
        await other.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_cancel_completed_task_raises_not_cancelable(
    shared_store, shared_stream, context
) -> None:
    handler = make_replica(shared_store, shared_stream, CompletingAgent())
    try:
        result = await handler.on_message_send(
            build_send_request('go', message_id='m1'), context
        )
        assert result.status.state == TaskState.TASK_STATE_COMPLETED
        with pytest.raises(TaskNotCancelableError):
            await handler.on_cancel_task(
                CancelTaskRequest(id=result.id), context
            )
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_cancel_absent_task_raises_not_found(
    shared_store, shared_stream, context
) -> None:
    handler = make_replica(shared_store, shared_stream, CompletingAgent())
    try:
        with pytest.raises(TaskNotFoundError):
            await handler.on_cancel_task(CancelTaskRequest(id='nope'), context)
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_cancel_non_owner_rejected(shared_store, shared_stream) -> None:
    handler = make_replica(shared_store, shared_stream, CompletingAgent())
    try:
        result = await handler.on_message_send(
            build_send_request('go', message_id='m1'), make_context('alice')
        )
        with pytest.raises(TaskNotFoundError):
            await handler.on_cancel_task(
                CancelTaskRequest(id=result.id), make_context('bob')
            )
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_follow_up_after_remote_cancel_of_waiting_task_is_rejected(
    shared_store, shared_stream, context
) -> None:
    """A task waits for input on A and is cancelled from B. A follow-up routed
    to A is rejected rather than reviving the task, and A drops the task."""
    agent = InputRequiredThenCompleteAgent()
    replica_a = make_replica(shared_store, shared_stream, agent)
    replica_b = make_replica(shared_store, shared_stream, agent)
    try:
        r1 = await replica_a.on_message_send(
            build_send_request('q1', message_id='m1'), context
        )
        assert r1.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        registry_a = replica_a._active_task_registry  # noqa: SLF001
        assert await registry_a.get(r1.id) is not None

        cancelled = await replica_b.on_cancel_task(
            CancelTaskRequest(id=r1.id), context
        )
        assert cancelled.status.state == TaskState.TASK_STATE_CANCELED

        with pytest.raises(UnsupportedOperationError, match='terminal state'):
            await replica_a.on_message_send(
                build_send_request(
                    'a',
                    task_id=r1.id,
                    context_id=r1.context_id,
                    message_id='m2',
                ),
                context,
            )

        final = await shared_store.get(r1.id, context)
        assert final is not None
        assert final.task.status.state == TaskState.TASK_STATE_CANCELED
        for _ in range(100):
            if await registry_a.get(r1.id) is None:
                break
            await asyncio.sleep(0.01)
        assert await registry_a.get(r1.id) is None
    finally:
        await replica_a.aclose()
        await replica_b.aclose()
