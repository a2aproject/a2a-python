"""Task generation across replicas sharing one database.

Runs on file-backed SQLite always, and on PostgreSQL / MySQL when their DSNs
are set.
"""

import asyncio
import contextlib
import os

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio


pytest.importorskip('sqlalchemy', reason='Database tests require SQLAlchemy')

from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.cluster import DatabaseTaskEventStream
from a2a.server.cluster.database_task_store import VersionedDatabaseTaskStore
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.models import Base
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.tasks import TaskUpdater
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import TaskGenerationMismatchError
from sqlalchemy.ext.asyncio import create_async_engine


_DSNS = [
    pytest.param(dsn, id=name)
    for name, dsn in (
        ('postgresql', os.environ.get('POSTGRES_TEST_DSN')),
        ('mysql', os.environ.get('MYSQL_TEST_DSN')),
    )
    if dsn
]


class _GatedAgent(AgentExecutor):
    """Turn 1 asks for input; later turns wait on `release`, then complete."""

    def __init__(self) -> None:
        self.working = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
            await TaskUpdater(
                event_queue, task.id, task.context_id
            ).requires_input()
            return
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        self.working.set()
        await self.release.wait()
        await updater.add_artifact([Part(text='result')])
        await updater.complete()

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        pass


class _Cluster:
    def __init__(self, dsn: str) -> None:
        self.agent_a = _GatedAgent()
        self.agent_b = _GatedAgent()
        self._engines = []
        self.a = self._replica(dsn, self.agent_a)
        self.b = self._replica(dsn, self.agent_b)

    def _replica(
        self, dsn: str, agent: AgentExecutor
    ) -> DefaultRequestHandlerV2:
        engine = create_async_engine(dsn)
        self._engines.append(engine)
        return DefaultRequestHandlerV2(
            agent_executor=agent,
            task_store=VersionedDatabaseTaskStore(
                engine=engine, create_table=False
            ),
            agent_card=AgentCard(
                capabilities=AgentCapabilities(streaming=True)
            ),
            event_stream=DatabaseTaskEventStream(
                engine=engine, create_table=False, poll_interval_s=0.02
            ),
            long_poll_timeout=5,
        )

    async def aclose(self) -> None:
        for replica in (self.a, self.b):
            with contextlib.suppress(Exception):
                await replica.aclose()
        for engine in self._engines:
            await engine.dispose()


@pytest_asyncio.fixture(params=['sqlite', *_DSNS])
async def cluster(request, tmp_path) -> AsyncGenerator[_Cluster, None]:
    dsn = (
        f'sqlite+aiosqlite:///{tmp_path / "cluster.db"}'
        if request.param == 'sqlite'
        else request.param
    )
    engine = create_async_engine(dsn)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    c = _Cluster(dsn)
    try:
        yield c
    finally:
        await c.aclose()
        with contextlib.suppress(Exception):
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


def _message(message_id: str, task: Task | None = None) -> Message:
    message = Message(
        message_id=message_id, role=Role.ROLE_USER, parts=[Part(text='hi')]
    )
    if task is not None:
        message.task_id = task.id
        message.context_id = task.context_id
    return message


async def _start(c: _Cluster) -> Task:
    task = await c.a.on_message_send(
        SendMessageRequest(message=_message('m-1')), ServerCallContext()
    )
    assert isinstance(task, Task)
    return task


def _generation(event: object) -> int:
    assert isinstance(
        event, Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent
    )
    return event.generation


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_resume_on_other_replica_replays_without_snapshot(
    cluster: _Cluster,
) -> None:
    task = await _start(cluster)
    follow_up = asyncio.create_task(
        cluster.a.on_message_send(
            SendMessageRequest(message=_message('m-2', task)),
            ServerCallContext(),
        )
    )
    await asyncio.wait_for(cluster.agent_a.working.wait(), timeout=10)

    # The client saw everything up to the first status of turn 1.
    events = cluster.b.on_subscribe_to_task(
        SubscribeToTaskRequest(id=task.id, last_seen_generation=1),
        ServerCallContext(),
    )
    received: list = []

    async def consume() -> None:
        async for event in events:
            received.append(event)

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.3)
    cluster.agent_a.release.set()
    await asyncio.wait_for(consumer, timeout=10)
    await follow_up

    # No snapshot first; the client message (generation 3) is not sent to
    # this 1.0 subscriber, which sees a one-step gap.
    assert not isinstance(received[0], Task)
    assert [_generation(e) for e in received] == [2, 4, 5, 6]
    assert received[-1].status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_resume_from_unknown_generation_falls_back_to_snapshot(
    cluster: _Cluster,
) -> None:
    task = await _start(cluster)
    events = cluster.b.on_subscribe_to_task(
        SubscribeToTaskRequest(
            id=task.id, last_seen_generation=task.generation + 5
        ),
        ServerCallContext(),
    )
    first = await asyncio.wait_for(anext(events), timeout=5)
    await events.aclose()
    assert isinstance(first, Task)
    assert first.generation == task.generation


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_long_poll_on_one_replica_wakes_on_write_from_the_other(
    cluster: _Cluster,
) -> None:
    task = await _start(cluster)
    poll = asyncio.create_task(
        cluster.b.on_get_task(
            GetTaskRequest(id=task.id, current_generation=task.generation),
            ServerCallContext(),
        )
    )
    await asyncio.sleep(0.2)
    assert not poll.done()

    send = asyncio.create_task(
        cluster.a.on_message_send(
            SendMessageRequest(message=_message('m-2', task)),
            ServerCallContext(),
        )
    )
    result = await asyncio.wait_for(poll, timeout=5)
    assert result.generation > task.generation

    cluster.agent_a.release.set()
    await send


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_conditional_send_on_other_replica_sees_the_change(
    cluster: _Cluster,
) -> None:
    task = await _start(cluster)
    follow_up = asyncio.create_task(
        cluster.a.on_message_send(
            SendMessageRequest(message=_message('m-2', task)),
            ServerCallContext(),
        )
    )
    await asyncio.wait_for(cluster.agent_a.working.wait(), timeout=10)
    try:
        with pytest.raises(TaskGenerationMismatchError) as exc_info:
            await cluster.b.on_message_send(
                SendMessageRequest(
                    message=_message('m-3', task),
                    configuration=SendMessageConfiguration(
                        if_generation_match=task.generation
                    ),
                ),
                ServerCallContext(),
            )
        assert exc_info.value.data is not None
        assert exc_info.value.data['expectedGeneration'] == str(task.generation)
    finally:
        cluster.agent_a.release.set()
        await follow_up


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_remote_cancel_never_reuses_a_generation(
    cluster: _Cluster,
) -> None:
    task = await _start(cluster)
    follow_up = asyncio.create_task(
        cluster.a.on_message_send(
            SendMessageRequest(message=_message('m-2', task)),
            ServerCallContext(),
        )
    )
    await asyncio.wait_for(cluster.agent_a.working.wait(), timeout=10)

    # B read the task before A's WORKING write: its cancel is stale and must
    # re-read instead of overwriting.
    cancelled = await cluster.b.on_cancel_task(
        CancelTaskRequest(id=task.id), ServerCallContext()
    )
    cluster.agent_a.release.set()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(follow_up, timeout=10)

    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    stored = await cluster.a.on_get_task(
        GetTaskRequest(id=task.id), ServerCallContext()
    )
    assert stored.status.state == TaskState.TASK_STATE_CANCELED
    assert stored.generation == cancelled.generation

    events = cluster.b._event_stream.subscribe_after_generation(  # type: ignore[union-attr]  # noqa: SLF001
        task.id, after_generation=0
    )
    generations: list[int] = []
    async with contextlib.aclosing(events):
        async for versioned in events:
            generations.append(versioned.event.generation)  # type: ignore[union-attr]
            if versioned.event.generation == stored.generation:  # type: ignore[union-attr]
                break
    assert generations == list(range(1, stored.generation + 1))


class _BusyAgent(AgentExecutor):
    """Writes a status update every few milliseconds until cancelled."""

    def __init__(self) -> None:
        self.working = asyncio.Event()

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        task = new_task_from_user_message(context.message)
        await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        self.working.set()
        while True:
            await updater.update_status(
                TaskState.TASK_STATE_WORKING,
                message=updater.new_agent_message([Part(text='tick')]),
            )
            await asyncio.sleep(0.002)

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        pass


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_remote_cancel_wins_against_a_busy_writer(
    cluster: _Cluster,
) -> None:
    agent = _BusyAgent()
    cluster.a._active_task_registry._agent_executor = agent  # noqa: SLF001
    stream = cluster.a.on_message_send_stream(
        SendMessageRequest(message=_message('m-1')), ServerCallContext()
    )
    first = await asyncio.wait_for(anext(stream), timeout=10)

    async def drain() -> None:
        with contextlib.suppress(Exception):
            async for _ in stream:
                pass

    drained = asyncio.create_task(drain())
    await asyncio.wait_for(agent.working.wait(), timeout=10)
    await asyncio.sleep(0.2)

    cancelled = await cluster.b.on_cancel_task(
        CancelTaskRequest(id=first.id), ServerCallContext()
    )
    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
    await asyncio.wait_for(drained, timeout=20)


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_slow_subscriber_does_not_block_writes_after_remote_finish(
    cluster: _Cluster,
) -> None:
    """When another replica finishes the task, the local consumer waits for
    its subscribers to drain without holding the task's write lock."""
    task = await _start(cluster)
    follow_up = asyncio.create_task(
        cluster.a.on_message_send(
            SendMessageRequest(message=_message('m-2', task)),
            ServerCallContext(),
        )
    )
    await asyncio.wait_for(cluster.agent_a.working.wait(), timeout=10)

    # A subscriber on A that reads one event and then stalls.
    slow = cluster.a.on_subscribe_to_task(
        SubscribeToTaskRequest(id=task.id), ServerCallContext()
    )
    await asyncio.wait_for(anext(slow), timeout=5)

    await cluster.b.on_cancel_task(
        CancelTaskRequest(id=task.id), ServerCallContext()
    )
    cluster.agent_a.release.set()  # A's next save conflicts with the cancel
    await asyncio.sleep(0.5)

    active = await cluster.a._active_task_registry.get(task.id)  # noqa: SLF001
    if active is not None:
        await asyncio.wait_for(active._write_lock.acquire(), timeout=2)  # noqa: SLF001
        active._write_lock.release()  # noqa: SLF001

    await slow.aclose()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(follow_up, timeout=10)
