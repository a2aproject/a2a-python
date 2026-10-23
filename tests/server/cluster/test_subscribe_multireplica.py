import asyncio
import uuid

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.cluster import DatabaseTaskEventStream
from a2a.server.cluster.database_task_store import VersionedDatabaseTaskStore
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types.a2a_pb2 import Part, Role, SubscribeToTaskRequest, TaskState
from a2a.utils.errors import TaskNotFoundError, UnsupportedOperationError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

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
        with pytest.raises(UnsupportedOperationError, match='terminal state'):
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


class _PauseThenResumeAgent(AgentExecutor):
    """Turn 1 asks for input. Turn 2 goes WORKING, waits, then completes."""

    def __init__(self) -> None:
        self.resumed = asyncio.Event()
        self.release = asyncio.Event()

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
        await updater.start_work()
        self.resumed.set()
        await self.release.wait()
        await updater.complete()

    async def cancel(self, context, event_queue) -> None:  # noqa: ANN001
        pass


@pytest_asyncio.fixture
async def sqlite_dsn() -> AsyncGenerator[str, None]:
    """A shared in-memory SQLite database, kept alive for the test."""
    dsn = (
        f'sqlite+aiosqlite:///file:resume_{uuid.uuid4().hex}'
        '?mode=memory&cache=shared&uri=true'
    )
    engine = create_async_engine(dsn)
    keeper: AsyncConnection = await engine.connect()
    await VersionedDatabaseTaskStore(engine).initialize()
    await DatabaseTaskEventStream(engine).initialize()
    yield dsn
    await keeper.close()
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_remote_subscriber_follows_resumed_turn_to_completion(
    sqlite_dsn: str, context
) -> None:
    """A task waits for input after a turn on A. C subscribes, and B runs the
    next turn. C must see that turn through to COMPLETED; the user message
    that resumed the task must not first re-save the waiting state."""
    agent = _PauseThenResumeAgent()
    engines = [create_async_engine(sqlite_dsn) for _ in range(3)]
    replica_a, replica_b, replica_c = (
        make_replica(
            VersionedDatabaseTaskStore(engine),
            DatabaseTaskEventStream(engine, poll_interval_s=0.01),
            agent,
        )
        for engine in engines
    )
    try:
        first = await replica_a.on_message_send(
            build_send_request('q1', message_id='m1'), context
        )
        assert first.status.state == TaskState.TASK_STATE_INPUT_REQUIRED

        c_states: list = []

        async def follow_on_c() -> None:
            async for event in replica_c.on_subscribe_to_task(
                SubscribeToTaskRequest(id=first.id), context
            ):
                c_states.append(event.status.state)

        c_task = asyncio.create_task(follow_on_c())
        await asyncio.sleep(0.1)

        b_task = asyncio.create_task(
            replica_b.on_message_send(
                build_send_request(
                    'q2',
                    task_id=first.id,
                    context_id=first.context_id,
                    message_id='m2',
                ),
                context,
            )
        )
        await asyncio.wait_for(agent.resumed.wait(), timeout=5)
        agent.release.set()
        await asyncio.wait_for(b_task, timeout=8)
        await asyncio.wait_for(c_task, timeout=8)

        assert c_states == [
            TaskState.TASK_STATE_INPUT_REQUIRED,
            TaskState.TASK_STATE_WORKING,
            TaskState.TASK_STATE_COMPLETED,
        ]
        stored = await VersionedDatabaseTaskStore(engines[0]).get(
            first.id, context
        )
        assert stored is not None
        assert [
            m.message_id
            for m in stored.task.history
            if m.role == Role.ROLE_USER
        ] == ['m1', 'm2']
    finally:
        for replica in (replica_a, replica_b, replica_c):
            await replica.aclose()
        for engine in engines:
            await engine.dispose()
