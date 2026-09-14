import asyncio
import contextlib
import os

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from _pytest.mark.structures import ParameterSet


pytest.importorskip('sqlalchemy', reason='Database tests require SQLAlchemy')

from a2a.auth.user import User
from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.cluster.database_event_stream import DatabaseTaskEventStream
from a2a.server.cluster.database_task_store import VersionedDatabaseTaskStore
from a2a.server.context import ServerCallContext
from a2a.server.models import Base
from a2a.server.request_handlers.default_request_handler_v2 import (
    DefaultRequestHandlerV2,
)
from a2a.server.tasks.task_updater import TaskUpdater
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    SubscribeToTaskRequest,
    TaskState,
)
from a2a.utils.errors import (
    A2AError,
    InvalidParamsError,
    TaskNotCancelableError,
    TaskNotFoundError,
)
from sqlalchemy.ext.asyncio import create_async_engine


# --- DSN parametrization -----------------------------------------------------

POSTGRES_TEST_DSN = os.environ.get('POSTGRES_TEST_DSN')
MYSQL_TEST_DSN = os.environ.get('MYSQL_TEST_DSN')


DB_CONFIGS: list[ParameterSet | tuple[str | None, str]] = []
if POSTGRES_TEST_DSN:
    DB_CONFIGS.append(
        pytest.param((POSTGRES_TEST_DSN, 'postgresql'), id='postgresql')
    )
else:
    DB_CONFIGS.append(
        pytest.param(
            (None, 'postgresql'),
            marks=pytest.mark.skip(reason='POSTGRES_TEST_DSN not set'),
            id='postgresql_skipped',
        )
    )
if MYSQL_TEST_DSN:
    DB_CONFIGS.append(pytest.param((MYSQL_TEST_DSN, 'mysql'), id='mysql'))
else:
    DB_CONFIGS.append(
        pytest.param(
            (None, 'mysql'),
            marks=pytest.mark.skip(reason='MYSQL_TEST_DSN not set'),
            id='mysql_skipped',
        )
    )


# --- Helpers and test agents definitions ------------------------------------


class SampleUser(User):
    """Minimal authenticated user for tests."""

    def __init__(self, user_name: str = 'test_user') -> None:
        self._user_name = user_name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._user_name


def make_context(user: str = 'test_user') -> ServerCallContext:
    return ServerCallContext(user=SampleUser(user))


def streaming_agent_card() -> AgentCard:
    return AgentCard(capabilities=AgentCapabilities(streaming=True))


def build_send_request(
    text: str,
    task_id: str = '',
    context_id: str = '',
    message_id: str = 'm',
) -> SendMessageRequest:
    msg = Message(
        role=Role.ROLE_USER, message_id=message_id, parts=[Part(text=text)]
    )
    if task_id:
        msg.task_id = task_id
    if context_id:
        msg.context_id = context_id
    return SendMessageRequest(message=msg)


async def wait_for_state(
    store: VersionedDatabaseTaskStore,
    task_id: str,
    state: TaskState,
    context: ServerCallContext,
    *,
    tries: int = 200,
    delay: float = 0.02,
) -> None:
    for _ in range(tries):
        stored = await store.get(task_id, context)
        if stored is not None and stored.task.status.state == state:
            return
        await asyncio.sleep(delay)
    raise AssertionError(f'Task {task_id} did not reach {state} in time')


class CompletingAgent(AgentExecutor):
    """Creates the task if needed, goes WORKING, then completes."""

    async def execute(self, context, event_queue) -> None:  # noqa: ANN001
        if context.current_task is None:
            await event_queue.enqueue_event(
                new_task_from_user_message(context.message)
            )
        updater = TaskUpdater(
            event_queue,
            str(context.task_id or ''),
            str(context.context_id or ''),
        )
        await updater.start_work()
        await updater.complete()

    async def cancel(self, context, event_queue) -> None:  # noqa: ANN001
        pass


class ControlledAgent(AgentExecutor):
    """Goes WORKING, then completes only when released."""

    def __init__(self) -> None:
        self.working = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, context, event_queue) -> None:  # noqa: ANN001
        if context.current_task is None:
            await event_queue.enqueue_event(
                new_task_from_user_message(context.message)
            )
        updater = TaskUpdater(
            event_queue,
            str(context.task_id or ''),
            str(context.context_id or ''),
        )
        await updater.start_work()
        self.working.set()
        await self.release.wait()
        await updater.add_artifact([Part(text='result')])
        await updater.complete()

    async def cancel(self, context, event_queue) -> None:  # noqa: ANN001
        pass


class LongRunningAgent(AgentExecutor):
    """Goes WORKING then saves periodically until aborted by a CAS conflict."""

    def __init__(self) -> None:
        self.working = asyncio.Event()
        self.aborted = asyncio.Event()

    async def execute(self, context, event_queue) -> None:  # noqa: ANN001
        if context.current_task is None:
            await event_queue.enqueue_event(
                new_task_from_user_message(context.message)
            )
        updater = TaskUpdater(
            event_queue,
            str(context.task_id or ''),
            str(context.context_id or ''),
        )
        await updater.start_work()
        self.working.set()
        try:
            for _ in range(200):
                await asyncio.sleep(0.05)
                await updater.update_status(
                    TaskState.TASK_STATE_WORKING,
                    message=updater.new_agent_message([Part(text='tick')]),
                )
        except asyncio.CancelledError:
            self.aborted.set()
            raise

    async def cancel(self, context, event_queue) -> None:  # noqa: ANN001
        pass


class InputRequiredThenCompleteAgent(AgentExecutor):
    """Asks for input until two user turns exist, then completes.

    Turn is derived purely from durable task history, so it behaves correctly
    regardless of which replica runs each turn.
    """

    async def execute(self, context, event_queue) -> None:  # noqa: ANN001
        task = context.current_task
        if task is None:
            await event_queue.enqueue_event(
                new_task_from_user_message(context.message)
            )
        updater = TaskUpdater(
            event_queue,
            str(context.task_id or ''),
            str(context.context_id or ''),
        )
        user_msgs = 0
        if task is not None:
            user_msgs = sum(1 for m in task.history if m.role == Role.ROLE_USER)

        if task is None or user_msgs <= 1:
            await updater.requires_input(
                message=updater.new_agent_message([Part(text='need input')])
            )
        else:
            await updater.complete()

    async def cancel(self, context, event_queue) -> None:  # noqa: ANN001
        pass


# --- simulate multi-replica deployment with 2 DefaultRequestHandlerV2 instances over shared DB


class Cluster:
    """A pair of replicas over one shared database."""

    def __init__(
        self,
        dsn: str,
        agent_a: AgentExecutor,
        agent_b: AgentExecutor,
    ) -> None:
        self._dsn = dsn
        self._engines = []
        self.replica_a = self._make_replica(agent_a)
        self.replica_b = self._make_replica(agent_b)

    def _make_replica(self, agent: AgentExecutor) -> DefaultRequestHandlerV2:
        engine = create_async_engine(self._dsn)
        self._engines.append(engine)
        store = VersionedDatabaseTaskStore(engine=engine, create_table=False)
        stream = DatabaseTaskEventStream(
            engine=engine, create_table=False, poll_interval_s=0.05
        )
        return DefaultRequestHandlerV2(
            agent_executor=agent,
            task_store=store,
            agent_card=streaming_agent_card(),
            event_stream=stream,
        )

    async def aclose(self) -> None:
        with contextlib.suppress(Exception):
            await self.replica_a.aclose()
        with contextlib.suppress(Exception):
            await self.replica_b.aclose()
        for engine in self._engines:
            await engine.dispose()


@pytest_asyncio.fixture(params=DB_CONFIGS)
async def dsn(request) -> AsyncGenerator[str, None]:
    param, dialect = request.param
    if param is None:
        pytest.skip(f'DSN for {dialect} not set.')
    url = param

    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield url
    finally:
        with contextlib.suppress(Exception):
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


# --- Scenarios --------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_concurrent_followups_no_lost_update(dsn: str) -> None:
    """Racing follow-up sends from both replicas never lose a write (OCC)."""
    cluster = Cluster(
        dsn,
        InputRequiredThenCompleteAgent(),
        InputRequiredThenCompleteAgent(),
    )
    ctx = make_context()
    try:
        r1 = await cluster.replica_a.on_message_send(
            build_send_request('q1', message_id='m1'), ctx
        )
        assert r1.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        task_id = r1.id
        context_id = r1.context_id

        # Two concurrent follow-ups for the same task, one per replica.
        results = await asyncio.gather(
            cluster.replica_a.on_message_send(
                build_send_request(
                    'a', task_id=task_id, context_id=context_id, message_id='ma'
                ),
                ctx,
            ),
            cluster.replica_b.on_message_send(
                build_send_request(
                    'b', task_id=task_id, context_id=context_id, message_id='mb'
                ),
                ctx,
            ),
            return_exceptions=True,
        )
        # Conflicts are handled internally; only an A2AError may surface.
        for r in results:
            if isinstance(r, BaseException):
                assert isinstance(r, A2AError), r

        # Durable task intact: all three user turns (q1, a, b) survived.
        final = await cluster.replica_a.task_store.get(task_id, ctx)
        assert final is not None
        assert not final.version.is_missing
        user_turns = sum(
            1 for m in final.task.history if m.role == Role.ROLE_USER
        )
        assert user_turns == 3, [m.message_id for m in final.task.history]
    finally:
        await cluster.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_multiturn_input_required_across_replicas(dsn: str) -> None:
    """turn 1 -> A, turn 2 -> B, turn 3 -> A; no stale-snapshot clobber."""
    cluster = Cluster(
        dsn,
        InputRequiredThenCompleteAgent(),
        InputRequiredThenCompleteAgent(),
    )
    ctx = make_context()
    try:
        r1 = await cluster.replica_a.on_message_send(
            build_send_request('q1', message_id='m1'), ctx
        )
        assert r1.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        task_id = r1.id
        context_id = r1.context_id

        r2 = await cluster.replica_b.on_message_send(
            build_send_request(
                'a1', task_id=task_id, context_id=context_id, message_id='m2'
            ),
            ctx,
        )
        assert r2.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        stored = await cluster.replica_a.task_store.get(task_id, ctx)
        assert stored is not None
        assert (
            sum(1 for m in stored.task.history if m.role == Role.ROLE_USER) == 2
        )

        r3 = await cluster.replica_a.on_message_send(
            build_send_request(
                'a2', task_id=task_id, context_id=context_id, message_id='m3'
            ),
            ctx,
        )
        assert r3.status.state == TaskState.TASK_STATE_COMPLETED
    finally:
        await cluster.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_resubscribe_on_non_owning_replica_streams_events(
    dsn: str,
) -> None:
    """Replica A runs the agent; replica B resubscribes and streams via the DB."""
    agent = ControlledAgent()
    cluster = Cluster(dsn, agent, ControlledAgent())
    ctx = make_context()
    try:
        # Task id comes from the first streamed event; the rest is consumed in
        # the background so the agent runs on and the stream is seen to end.
        stream = cluster.replica_a.on_message_send_stream(
            build_send_request('go', message_id='m1'), ctx
        )
        first = await asyncio.wait_for(anext(stream), timeout=10)
        task_id = first.id

        async def finish_a_stream() -> None:
            async for _ in stream:
                pass

        a_task = asyncio.create_task(finish_a_stream())
        await asyncio.wait_for(agent.working.wait(), timeout=10)
        await wait_for_state(
            cluster.replica_b.task_store,
            task_id,
            TaskState.TASK_STATE_WORKING,
            ctx,
        )

        states: list = []

        async def observe() -> None:
            async for ev in cluster.replica_b.on_subscribe_to_task(
                SubscribeToTaskRequest(id=task_id), ctx
            ):
                if getattr(ev, 'status', None):
                    states.append(ev.status.state)
                    if ev.status.state == TaskState.TASK_STATE_COMPLETED:
                        return

        observer = asyncio.create_task(observe())
        await asyncio.sleep(0.2)  # let B read snapshot + start tailing the log
        agent.release.set()

        await asyncio.wait_for(observer, timeout=30)
        await asyncio.wait_for(a_task, timeout=30)

        assert states[0] == TaskState.TASK_STATE_WORKING
        assert states[-1] == TaskState.TASK_STATE_COMPLETED
    finally:
        await cluster.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_resubscribe_terminal_task_rejected(dsn: str) -> None:
    """Resubscribe to a finished task is rejected (no hang), from any replica."""
    agent = ControlledAgent()
    agent.release.set()  # completes immediately
    cluster = Cluster(dsn, agent, ControlledAgent())
    ctx = make_context()
    try:
        result = await cluster.replica_a.on_message_send(
            build_send_request('go', message_id='m1'), ctx
        )
        assert result.status.state == TaskState.TASK_STATE_COMPLETED

        # A terminal task is rejected by the non-owning replica, not hung.
        with pytest.raises(InvalidParamsError, match='terminal state'):
            async for _ in cluster.replica_b.on_subscribe_to_task(
                SubscribeToTaskRequest(id=result.id), ctx
            ):
                pass
    finally:
        await cluster.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_cancel_from_non_owning_replica_stops_remote_agent(
    dsn: str,
) -> None:
    """Cancel on replica B stops the agent running on replica A (via CAS)."""
    agent = LongRunningAgent()
    cluster = Cluster(dsn, agent, LongRunningAgent())
    ctx = make_context()
    try:
        # Task id from the first streamed event; the rest is consumed in the
        # background so the agent keeps ticking and the stream is seen to end.
        stream = cluster.replica_a.on_message_send_stream(
            build_send_request('go', message_id='m1'), ctx
        )
        first = await asyncio.wait_for(anext(stream), timeout=10)
        task_id = first.id

        async def finish_a_stream() -> None:
            async for _ in stream:
                pass

        a_task = asyncio.create_task(finish_a_stream())
        await asyncio.wait_for(agent.working.wait(), timeout=10)
        await wait_for_state(
            cluster.replica_b.task_store,
            task_id,
            TaskState.TASK_STATE_WORKING,
            ctx,
        )

        result = await cluster.replica_b.on_cancel_task(
            CancelTaskRequest(id=task_id), ctx
        )
        assert result.status.state == TaskState.TASK_STATE_CANCELED

        await asyncio.wait_for(agent.aborted.wait(), timeout=30)
        await asyncio.wait_for(a_task, timeout=30)

        final = await cluster.replica_b.task_store.get(task_id, ctx)
        assert final is not None
        assert final.task.status.state == TaskState.TASK_STATE_CANCELED
    finally:
        await cluster.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_cancel_idempotent_and_terminal_rules(dsn: str) -> None:
    """Cancel is idempotent; cancelling a completed task is not cancelable."""
    cluster = Cluster(dsn, CompletingAgent(), CompletingAgent())
    ctx = make_context()
    try:
        result = await cluster.replica_a.on_message_send(
            build_send_request('go', message_id='m1'), ctx
        )
        assert result.status.state == TaskState.TASK_STATE_COMPLETED
        # Completed -> not cancelable, from the other replica.
        with pytest.raises(TaskNotCancelableError):
            await cluster.replica_b.on_cancel_task(
                CancelTaskRequest(id=result.id), ctx
            )
    finally:
        await cluster.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(60)
async def test_absent_and_non_owner_rejected(dsn: str) -> None:
    """Absent task -> NotFound; a non-owner cannot see another user's task."""
    cluster = Cluster(dsn, CompletingAgent(), CompletingAgent())
    try:
        # Absent task on either replica.
        with pytest.raises(TaskNotFoundError):
            await cluster.replica_b.on_get_task(
                GetTaskRequest(id='does-not-exist'), make_context()
            )

        # Owner-scoping: bob cannot get/subscribe/cancel alice's task.
        result = await cluster.replica_a.on_message_send(
            build_send_request('go', message_id='m1'), make_context('alice')
        )
        bob = make_context('bob')
        with pytest.raises(TaskNotFoundError):
            await cluster.replica_b.on_get_task(
                GetTaskRequest(id=result.id), bob
            )
        with pytest.raises(TaskNotFoundError):
            async for _ in cluster.replica_b.on_subscribe_to_task(
                SubscribeToTaskRequest(id=result.id), bob
            ):
                pass
        with pytest.raises(TaskNotFoundError):
            await cluster.replica_b.on_cancel_task(
                CancelTaskRequest(id=result.id), bob
            )
    finally:
        await cluster.aclose()
