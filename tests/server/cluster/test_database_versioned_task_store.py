"""Tests for `VersionedDatabaseTaskStore` (CAS over SQLAlchemy)."""

import os

from collections.abc import AsyncGenerator

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
from sqlalchemy.ext.asyncio import create_async_engine


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
