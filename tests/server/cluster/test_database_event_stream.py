"""Tests for `DatabaseTaskEventStream` including a multi-replica simulation."""

import asyncio
import contextlib
import os
import uuid

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from _pytest.mark.structures import ParameterSet


pytest.importorskip('sqlalchemy', reason='Database tests require SQLAlchemy')

from a2a.auth.user import User
from a2a.server.cluster import TaskVersion, VersionedEvent
from a2a.server.cluster.database_event_stream import (
    DatabaseTaskEventStream,
    stream_response_to_event,
)
from a2a.server.cluster.database_task_store import VersionedDatabaseTaskStore
from a2a.server.context import ServerCallContext
from a2a.server.models import Base
from a2a.types.a2a_pb2 import (
    Message,
    Part,
    Role,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def _enforce_sqlite_foreign_keys(engine: AsyncEngine) -> None:
    """Make SQLite enforce foreign keys, matching Postgres and MySQL.

    SQLite ignores foreign keys unless asked, so without this a FK bug passes
    the SQLite tests and only fails against the real engines in CI.
    """
    if engine.dialect.name != 'sqlite':
        return

    @event.listens_for(engine.sync_engine, 'connect')
    def _set_sqlite_pragma(dbapi_conn, _):  # noqa: ANN001, ANN202
        dbapi_conn.execute('PRAGMA foreign_keys=ON')


class SampleUser(User):
    def __init__(self, user_name: str):
        self._user_name = user_name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._user_name


TEST_CONTEXT = ServerCallContext(user=SampleUser('test_user'))

POSTGRES_TEST_DSN = os.environ.get('POSTGRES_TEST_DSN')
MYSQL_TEST_DSN = os.environ.get('MYSQL_TEST_DSN')


def _sqlite_dsn() -> str:
    # Unique shared-cache in-memory DB per test so multiple engines see the
    # same data while different tests never collide.
    name = f'eventstream_{uuid.uuid4().hex}'
    return f'sqlite+aiosqlite:///file:{name}?mode=memory&cache=shared&uri=true'


DB_CONFIGS: list[ParameterSet | tuple[str | None, str]] = [
    pytest.param(('sqlite', 'sqlite'), id='sqlite')
]
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


def create_task(task_id: str = 'task-abc') -> Task:
    return Task(
        id=task_id,
        context_id='ctx',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )


def status_event(
    task_id: str = 'task-abc',
    state: TaskState = TaskState.TASK_STATE_WORKING,
) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id=task_id,
        context_id='ctx',
        status=TaskStatus(state=state),
    )


@pytest_asyncio.fixture(params=DB_CONFIGS)
async def db_url(request) -> AsyncGenerator[str, None]:
    param, dialect = request.param
    if param is None:
        pytest.skip(f'DSN for {dialect} not set.')
    url = _sqlite_dsn() if param == 'sqlite' else param

    engine = create_async_engine(url)
    _enforce_sqlite_foreign_keys(engine)
    # Keep one connection open for the whole test so a shared-cache in-memory
    # SQLite database is not torn down while other engines connect to it.
    keepalive = await engine.connect()
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield url
    finally:
        await keepalive.close()
        await engine.dispose()


# --- Pure serialization helper tests (no DB needed) ---


def test_stream_response_to_event_roundtrips_all_kinds() -> None:
    task = create_task()
    msg = Message(
        message_id='m1', role=Role.ROLE_AGENT, parts=[Part(text='hi')]
    )
    status = status_event()
    artifact = TaskArtifactUpdateEvent(task_id='task-abc', context_id='ctx')

    for event in (task, msg, status, artifact):
        response = StreamResponse()
        # Round-trip through serialize to mimic DB storage.
        from a2a.utils.proto_utils import to_stream_response

        response = to_stream_response(event)
        raw = response.SerializeToString()
        parsed = StreamResponse()
        parsed.ParseFromString(raw)
        assert stream_response_to_event(parsed) == event


def test_stream_response_to_event_raises_on_empty() -> None:
    with pytest.raises(ValueError, match='no known payload'):
        stream_response_to_event(StreamResponse())


def test_as_int_rejects_non_integer_version() -> None:
    from a2a.server.cluster.database_event_stream import _as_int

    with pytest.raises(TypeError, match='integer versions'):
        _as_int(TaskVersion('etag'))


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_initialize_creates_table_and_is_idempotent(
    tmp_path,
) -> None:
    # A dedicated file-backed SQLite DB (not shared-cache in-memory) so
    # create_table=True owns the schema with no cross-engine contention.
    url = f'sqlite+aiosqlite:///{tmp_path / "events.db"}'
    engine = create_async_engine(url)
    try:
        stream = DatabaseTaskEventStream(engine=engine, create_table=True)
        await stream.initialize()
        # Second call is a no-op (already initialized) -> early-return path.
        await stream.initialize()
        # Subscribing seeds the cursor from an empty table (0) without error.
        agen = stream.subscribe('task-abc', after=TaskVersion.MISSING)
        consumer = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.05)
        consumer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer
        await agen.aclose()
    finally:
        await engine.dispose()


# --- Integration tests (shared DB across store + stream) ---


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_stream_delivers_events_written_by_store(db_url: str) -> None:
    """A store writes events; a stream on a SEPARATE engine reads them.

    This simulates the producing replica (store) and a subscribing replica
    (stream) sharing one database.
    """
    store_engine = create_async_engine(db_url)
    stream_engine = create_async_engine(db_url)
    _enforce_sqlite_foreign_keys(store_engine)
    _enforce_sqlite_foreign_keys(stream_engine)
    try:
        store = VersionedDatabaseTaskStore(
            engine=store_engine, create_table=False
        )
        stream = DatabaseTaskEventStream(
            engine=stream_engine, create_table=False, poll_interval_s=0.05
        )
        await store.initialize()
        await stream.initialize()

        # Snapshot version (as a subscriber would have after reading the task).
        v1 = await store.save(
            create_task(),
            event=None,
            prev=None,
            prev_version=TaskVersion.MISSING,
            context=TEST_CONTEXT,
        )

        received: list[TaskState] = []

        async def consume() -> None:
            async for ve in stream.subscribe('task-abc', after=v1):
                received.append(ve.event.status.state)
                if ve.event.status.state == TaskState.TASK_STATE_COMPLETED:
                    return

        consumer = asyncio.create_task(consume())
        await asyncio.sleep(0.1)  # subscriber captures the tail cursor

        # Producer writes two events with the task, in transaction.
        v2 = await store.save(
            create_task(),
            event=status_event(state=TaskState.TASK_STATE_WORKING),
            prev=None,
            prev_version=v1,
            context=TEST_CONTEXT,
        )
        await store.save(
            create_task(),
            event=status_event(state=TaskState.TASK_STATE_COMPLETED),
            prev=None,
            prev_version=v2,
            context=TEST_CONTEXT,
        )

        await asyncio.wait_for(consumer, timeout=8)
        assert received == [
            TaskState.TASK_STATE_WORKING,
            TaskState.TASK_STATE_COMPLETED,
        ]
    finally:
        await store_engine.dispose()
        await stream_engine.dispose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_publish_is_noop(db_url: str) -> None:
    stream_engine = create_async_engine(db_url)
    try:
        stream = DatabaseTaskEventStream(
            engine=stream_engine, create_table=False
        )
        await stream.initialize()
        # publish is a no-op for the DB stream; must not raise.
        await stream.publish(
            'task-abc', VersionedEvent(create_task(), TaskVersion(1))
        )
        await stream.destroy('task-abc')  # also a no-op
    finally:
        await stream_engine.dispose()
