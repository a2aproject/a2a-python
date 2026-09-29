"""Tests for `VersionedDatabaseTaskStore` (CAS over SQLAlchemy)."""

import asyncio
import os

from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
import pytest_asyncio

from _pytest.mark.structures import ParameterSet


pytest.importorskip('sqlalchemy', reason='Database tests require SQLAlchemy')

from a2a.auth.user import User
from a2a.server.cluster import (
    ConcurrentTaskModificationError,
    TaskVersion,
    VersionedTaskStore,
)
from a2a.server.cluster.database_task_store import VersionedDatabaseTaskStore
from a2a.server.context import ServerCallContext
from a2a.server.models import Base
from a2a.types.a2a_pb2 import (
    ListTasksRequest,
    Task,
    TaskState,
    TaskStatus,
)
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)


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
    """A test implementation of the User interface."""

    def __init__(self, user_name: str):
        self._user_name = user_name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._user_name


TEST_CONTEXT = ServerCallContext(user=SampleUser('test_user'))


SQLITE_TEST_DSN = 'sqlite+aiosqlite:///file:testdb_versioned?mode=memory&cache=shared&uri=true'
POSTGRES_TEST_DSN = os.environ.get('POSTGRES_TEST_DSN')
MYSQL_TEST_DSN = os.environ.get('MYSQL_TEST_DSN')

DB_CONFIGS: list[ParameterSet | tuple[str | None, str]] = [
    pytest.param((SQLITE_TEST_DSN, 'sqlite'), id='sqlite')
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


def create_task(
    task_id: str = 'task-abc',
    context_id: str = 'session-xyz',
    state: TaskState = TaskState.TASK_STATE_SUBMITTED,
) -> Task:
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=state),
    )


@pytest_asyncio.fixture(params=DB_CONFIGS)
async def versioned_store(
    request,
) -> AsyncGenerator[VersionedDatabaseTaskStore, None]:
    db_url, dialect_name = request.param
    if db_url is None:
        pytest.skip(f'DSN for {dialect_name} not set in environment variables.')

    engine = create_async_engine(db_url)
    _enforce_sqlite_foreign_keys(engine)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store = VersionedDatabaseTaskStore(engine=engine, create_table=False)
        await store.initialize()
        yield store
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.mark.asyncio
async def test_is_a_versioned_task_store(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    assert isinstance(versioned_store, VersionedTaskStore)


@pytest.mark.asyncio
async def test_as_task_store_exposes_underlying_store(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    from a2a.server.tasks.task_store import TaskStore

    assert isinstance(versioned_store.as_task_store, TaskStore)


@pytest.mark.asyncio
async def test_non_integer_version_on_update_raises_type_error(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    await versioned_store.save(
        create_task(),
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    # A string-valued version can't back the integer `version` column.
    with pytest.raises(TypeError, match='integer versions'):
        await versioned_store.save(
            create_task(state=TaskState.TASK_STATE_WORKING),
            event=None,
            prev=None,
            prev_version=TaskVersion('not-an-int'),
            context=TEST_CONTEXT,
        )


@pytest.mark.asyncio
async def test_get_missing_returns_none(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    assert await versioned_store.get('nope', TEST_CONTEXT) is None


@pytest.mark.asyncio
async def test_first_save_then_get(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    task = create_task()
    v1 = await versioned_store.save(
        task,
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    assert not v1.is_missing

    stored = await versioned_store.get('task-abc', TEST_CONTEXT)
    assert stored is not None
    assert stored.task.id == 'task-abc'
    assert stored.version == v1


@pytest.mark.asyncio
async def test_update_with_matching_version_succeeds(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    v1 = await versioned_store.save(
        create_task(),
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    v2 = await versioned_store.save(
        create_task(state=TaskState.TASK_STATE_WORKING),
        event=None,
        prev=None,
        prev_version=v1,
        context=TEST_CONTEXT,
    )
    assert v2.is_after(v1)
    stored = await versioned_store.get('task-abc', TEST_CONTEXT)
    assert stored is not None
    assert stored.task.status.state == TaskState.TASK_STATE_WORKING
    assert stored.version == v2


@pytest.mark.asyncio
async def test_stale_update_raises_conflict(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    v1 = await versioned_store.save(
        create_task(),
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    # Winner advances the task.
    await versioned_store.save(
        create_task(state=TaskState.TASK_STATE_WORKING),
        event=None,
        prev=None,
        prev_version=v1,
        context=TEST_CONTEXT,
    )
    # Loser still holds v1 -> CAS fails.
    with pytest.raises(ConcurrentTaskModificationError):
        await versioned_store.save(
            create_task(state=TaskState.TASK_STATE_COMPLETED),
            event=None,
            prev=None,
            prev_version=v1,
            context=TEST_CONTEXT,
        )
    # Store still reflects the winner's write.
    stored = await versioned_store.get('task-abc', TEST_CONTEXT)
    assert stored is not None
    assert stored.task.status.state == TaskState.TASK_STATE_WORKING


@pytest.mark.asyncio
async def test_concurrent_first_insert_one_wins(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    # First insert succeeds.
    await versioned_store.save(
        create_task(),
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    # A second "first write" (MISSING) for the same id must not silently
    # clobber; it collides on the primary key -> conflict.
    with pytest.raises(ConcurrentTaskModificationError):
        await versioned_store.save(
            create_task(state=TaskState.TASK_STATE_WORKING),
            event=None,
            prev=None,
            prev_version=TaskVersion.MISSING,
            context=TEST_CONTEXT,
        )


@pytest.mark.asyncio
async def test_delete_removes_task(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    v1 = await versioned_store.save(
        create_task(),
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    assert not v1.is_missing
    await versioned_store.delete('task-abc', TEST_CONTEXT)
    assert await versioned_store.get('task-abc', TEST_CONTEXT) is None


@pytest.mark.asyncio
async def test_list_returns_saved_tasks(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    for i in range(3):
        await versioned_store.save(
            create_task(task_id=f'task-{i}'),
            event=None,
            prev=None,
            prev_version=TaskVersion.MISSING,
            context=TEST_CONTEXT,
        )
    resp = await versioned_store.list(ListTasksRequest(), TEST_CONTEXT)
    assert {t.id for t in resp.tasks} == {'task-0', 'task-1', 'task-2'}


@pytest.mark.asyncio
async def test_save_retries_transient_operational_error(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    """A transient OperationalError on write is retried, not surfaced."""
    from unittest import mock

    from sqlalchemy.exc import OperationalError

    calls = {'n': 0}
    real_save_once = versioned_store._save_once  # noqa: SLF001

    async def flaky_save_once(*args, **kwargs):  # noqa: ANN002, ANN003
        calls['n'] += 1
        if calls['n'] == 1:
            raise OperationalError('stmt', {}, Exception('database is locked'))
        return await real_save_once(*args, **kwargs)

    with mock.patch.object(
        versioned_store, '_save_once', side_effect=flaky_save_once
    ):
        version = await versioned_store.save(
            create_task(),
            event=None,
            prev=None,
            prev_version=TaskVersion.MISSING,
            context=TEST_CONTEXT,
        )
    assert calls['n'] == 2  # first failed, second succeeded
    assert not version.is_missing


class _DriverError(Exception):
    """A driver error carrying a SQLSTATE, as asyncpg errors do."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(sqlstate)
        self.sqlstate = sqlstate


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('sqlstate', 'expected_calls'),
    [('40P01', 2), ('40001', 2), ('42P01', 1)],
    ids=['deadlock', 'serialization', 'other'],
)
async def test_save_retries_only_transient_postgres_errors(
    versioned_store: VersionedDatabaseTaskStore,
    sqlstate: str,
    expected_calls: int,
) -> None:
    """Postgres deadlocks and serialization failures arrive as a plain
    DBAPIError; they are retried, other DBAPIErrors are not."""
    from unittest import mock

    from sqlalchemy.exc import DBAPIError

    calls = {'n': 0}
    real_save_once = versioned_store._save_once  # noqa: SLF001

    async def flaky_save_once(*args, **kwargs):  # noqa: ANN002, ANN003
        calls['n'] += 1
        if calls['n'] == 1:
            raise DBAPIError('stmt', {}, _DriverError(sqlstate))
        return await real_save_once(*args, **kwargs)

    def save():  # noqa: ANN202
        return versioned_store.save(
            create_task(),
            event=None,
            prev=None,
            prev_version=TaskVersion.MISSING,
            context=TEST_CONTEXT,
        )

    with mock.patch.object(
        versioned_store, '_save_once', side_effect=flaky_save_once
    ):
        if expected_calls == 1:
            with pytest.raises(DBAPIError):
                await save()
        else:
            await save()
    assert calls['n'] == expected_calls


@pytest.mark.asyncio
async def test_save_raises_after_exhausting_retries(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    """Persistent OperationalError surfaces after exactly `max_attempts` tries."""
    from unittest import mock

    from sqlalchemy.exc import OperationalError

    # A store tuned to two attempts with no backoff, so the loop is bounded by
    # max_attempts (not retries after the first) and the test does not sleep.
    store = VersionedDatabaseTaskStore(
        engine=versioned_store._db.engine,  # noqa: SLF001
        create_table=False,
        max_attempts=2,
        retry_delay_s=0.0,
    )

    calls = {'n': 0}

    async def always_locked(*args, **kwargs):  # noqa: ANN002, ANN003
        calls['n'] += 1
        raise OperationalError('stmt', {}, Exception('database is locked'))

    with (
        mock.patch.object(store, '_save_once', side_effect=always_locked),
        pytest.raises(OperationalError),
    ):
        await store.save(
            create_task(),
            event=None,
            prev=None,
            prev_version=TaskVersion.MISSING,
            context=TEST_CONTEXT,
        )
    assert calls['n'] == 2  # initial try + one retry, then surfaced


@pytest.mark.asyncio
async def test_get_retries_transient_operational_error(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    """A transient OperationalError on read is retried, not surfaced."""
    from unittest import mock

    from sqlalchemy.exc import OperationalError

    await versioned_store.save(
        create_task(),
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )

    calls = {'n': 0}
    real_get_once = versioned_store._get_once  # noqa: SLF001

    async def flaky_get_once(*args, **kwargs):  # noqa: ANN002, ANN003
        calls['n'] += 1
        if calls['n'] == 1:
            raise OperationalError('stmt', {}, Exception('database is locked'))
        return await real_get_once(*args, **kwargs)

    with mock.patch.object(
        versioned_store, '_get_once', side_effect=flaky_get_once
    ):
        stored = await versioned_store.get('task-abc', TEST_CONTEXT)
    assert calls['n'] == 2
    assert stored is not None
    assert not stored.version.is_missing


@pytest.mark.asyncio
async def test_get_raises_after_exhausting_retries(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    """Read surfaces the error after exactly `max_attempts` tries."""
    from unittest import mock

    from sqlalchemy.exc import OperationalError

    # As with the write path, bound the read loop to two attempts with no
    # backoff so the count is asserted and no real sleep occurs.
    store = VersionedDatabaseTaskStore(
        engine=versioned_store._db.engine,  # noqa: SLF001
        create_table=False,
        max_attempts=2,
        retry_delay_s=0.0,
    )

    calls = {'n': 0}

    async def always_locked(*args, **kwargs):  # noqa: ANN002, ANN003
        calls['n'] += 1
        raise OperationalError('stmt', {}, Exception('database is locked'))

    with (
        mock.patch.object(store, '_get_once', side_effect=always_locked),
        pytest.raises(OperationalError),
    ):
        await store.get('task-abc', TEST_CONTEXT)
    assert calls['n'] == 2  # initial try + one retry, then surfaced


@pytest.mark.asyncio
async def test_retry_defaults(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    """The shipped retry budget defaults are 5 attempts / 0.02s backoff."""
    assert versioned_store._max_attempts == 5  # noqa: SLF001
    assert versioned_store._retry_delay_s == 0.02  # noqa: SLF001


@pytest.mark.asyncio
async def test_cancel_overwrites_without_version_match(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    v1 = await versioned_store.save(
        create_task(state=TaskState.TASK_STATE_SUBMITTED),
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    await versioned_store.save(
        create_task(state=TaskState.TASK_STATE_WORKING),
        event=None,
        prev=None,
        prev_version=v1,
        context=TEST_CONTEXT,
    )
    await versioned_store.save(
        create_task(state=TaskState.TASK_STATE_CANCELED),
        event=None,
        prev=None,
        prev_version=v1,
        context=TEST_CONTEXT,
    )
    stored = await versioned_store.get('task-abc', TEST_CONTEXT)
    assert stored is not None
    assert stored.task.status.state == TaskState.TASK_STATE_CANCELED


@pytest.mark.asyncio
async def test_cancel_on_terminal_task_raises(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    v1 = await versioned_store.save(
        create_task(state=TaskState.TASK_STATE_COMPLETED),
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    with pytest.raises(ConcurrentTaskModificationError):
        await versioned_store.save(
            create_task(state=TaskState.TASK_STATE_CANCELED),
            event=None,
            prev=None,
            prev_version=v1,
            context=TEST_CONTEXT,
        )


@pytest.mark.asyncio
async def test_cancel_absent_task_raises(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    with pytest.raises(ConcurrentTaskModificationError):
        await versioned_store.save(
            create_task(state=TaskState.TASK_STATE_CANCELED),
            event=None,
            prev=None,
            prev_version=TaskVersion(123),
            context=TEST_CONTEXT,
        )


@pytest.mark.asyncio
async def test_adopts_task_written_by_plain_store(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    # A task row with no version side-table row (e.g. written by the plain
    # store) reads back as MISSING and is adopted on the next versioned save.
    await versioned_store.as_task_store.save(
        create_task(state=TaskState.TASK_STATE_SUBMITTED), TEST_CONTEXT
    )
    stored = await versioned_store.get('task-abc', TEST_CONTEXT)
    assert stored is not None
    assert stored.version.is_missing

    v1 = await versioned_store.save(
        create_task(state=TaskState.TASK_STATE_WORKING),
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    adopted = await versioned_store.get('task-abc', TEST_CONTEXT)
    assert adopted is not None
    assert not adopted.version.is_missing
    assert adopted.version == v1


@pytest.mark.asyncio
async def test_concurrent_first_writer_conflicts(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    await versioned_store.save(
        create_task(state=TaskState.TASK_STATE_SUBMITTED),
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    with pytest.raises(ConcurrentTaskModificationError):
        await versioned_store.save(
            create_task(state=TaskState.TASK_STATE_SUBMITTED),
            event=None,
            prev=None,
            prev_version=TaskVersion.MISSING,
            context=TEST_CONTEXT,
        )


@pytest.mark.asyncio
async def test_delete_removes_version_row(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    await versioned_store.save(
        create_task(state=TaskState.TASK_STATE_SUBMITTED),
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    await versioned_store.delete('task-abc', TEST_CONTEXT)
    assert await versioned_store.get('task-abc', TEST_CONTEXT) is None

    # A fresh first write succeeds only if the old version row is gone.
    await versioned_store.save(
        create_task(state=TaskState.TASK_STATE_SUBMITTED),
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    stored = await versioned_store.get('task-abc', TEST_CONTEXT)
    assert stored is not None


@pytest.mark.asyncio
async def test_versions_count_writes(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    v1 = await versioned_store.save(
        create_task(state=TaskState.TASK_STATE_SUBMITTED),
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    v2 = await versioned_store.save(
        create_task(state=TaskState.TASK_STATE_WORKING),
        event=None,
        prev=None,
        prev_version=v1,
        context=TEST_CONTEXT,
    )
    # Cancel skips the version check but still counts as a write.
    v3 = await versioned_store.save(
        create_task(state=TaskState.TASK_STATE_CANCELED),
        event=None,
        prev=None,
        prev_version=v1,
        context=TEST_CONTEXT,
    )
    assert (v1, v2, v3) == (TaskVersion(1), TaskVersion(2), TaskVersion(3))
    stored = await versioned_store.get('task-abc', TEST_CONTEXT)
    assert stored is not None
    assert stored.version == v3


@pytest.mark.asyncio
async def test_cancel_of_adopted_task_starts_at_version_one(
    versioned_store: VersionedDatabaseTaskStore,
) -> None:
    await versioned_store.as_task_store.save(
        create_task(state=TaskState.TASK_STATE_WORKING), TEST_CONTEXT
    )
    version = await versioned_store.save(
        create_task(state=TaskState.TASK_STATE_CANCELED),
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    assert version == TaskVersion(1)


@pytest.mark.asyncio
@pytest.mark.timeout(20)
@pytest.mark.parametrize('db_config', DB_CONFIGS)
async def test_cancel_racing_save_does_not_deadlock(
    db_config: tuple[str | None, str],
) -> None:
    """A cancel that starts while another replica's save holds the version
    row waits for it instead of deadlocking on the tasks row."""
    db_url, dialect_name = db_config
    if db_url is None:
        pytest.skip(f'DSN for {dialect_name} not set in environment variables.')
    if dialect_name == 'sqlite':
        pytest.skip('SQLite has no row locks.')

    engine_a = create_async_engine(db_url)
    engine_b = create_async_engine(db_url)
    try:
        async with engine_a.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        store_a = VersionedDatabaseTaskStore(
            engine=engine_a, create_table=False
        )
        store_b = VersionedDatabaseTaskStore(
            engine=engine_b, create_table=False
        )
        v1 = await store_a.save(
            create_task(state=TaskState.TASK_STATE_WORKING),
            event=None,
            prev=None,
            prev_version=TaskVersion.MISSING,
            context=TEST_CONTEXT,
        )

        # Pause A's save after it has locked the version row and before it
        # writes the tasks row; B's cancel starts in that window.
        a_holds_version = asyncio.Event()
        resume_a = asyncio.Event()
        real_merge = AsyncSession.merge

        async def paused_merge(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
            if self.bind is engine_a and not a_holds_version.is_set():
                a_holds_version.set()
                await resume_a.wait()
            return await real_merge(self, *args, **kwargs)

        async def complete_on_a() -> TaskVersion:
            return await store_a.save(
                create_task(state=TaskState.TASK_STATE_COMPLETED),
                event=None,
                prev=None,
                prev_version=v1,
                context=TEST_CONTEXT,
            )

        async def cancel_on_b() -> TaskVersion:
            await a_holds_version.wait()
            return await store_b.save(
                create_task(state=TaskState.TASK_STATE_CANCELED),
                event=None,
                prev=None,
                prev_version=v1,
                context=TEST_CONTEXT,
            )

        with patch.object(AsyncSession, 'merge', paused_merge):
            both = asyncio.gather(
                complete_on_a(), cancel_on_b(), return_exceptions=True
            )
            await a_holds_version.wait()
            await asyncio.sleep(0.3)  # let B reach its first lock
            resume_a.set()
            save_result, cancel_result = await both

        assert save_result == TaskVersion(2)
        # B waited for A, then found the task already terminal.
        assert isinstance(cancel_result, ConcurrentTaskModificationError)
        final = await store_a.get('task-abc', TEST_CONTEXT)
        assert final is not None
        assert final.task.status.state == TaskState.TASK_STATE_COMPLETED
    finally:
        async with engine_a.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine_a.dispose()
        await engine_b.dispose()
