"""Tests for `DatabaseTaskLeaseStore`."""

import asyncio
import os

from collections.abc import AsyncGenerator
from pathlib import Path

import pytest
import pytest_asyncio

from _pytest.mark.structures import ParameterSet


pytest.importorskip('sqlalchemy', reason='Database tests require SQLAlchemy')

from a2a.auth.user import User
from a2a.server.cluster import DatabaseTaskLeaseStore, TaskLease
from a2a.server.context import ServerCallContext
from a2a.server.models import Base
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


class _User(User):
    def __init__(self, user_name: str) -> None:
        self._user_name = user_name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._user_name


ALICE = ServerCallContext(user=_User('alice'))

SQLITE_TEST_DSN = (
    'sqlite+aiosqlite:///file:testdb_leases?mode=memory&cache=shared&uri=true'
)
POSTGRES_TEST_DSN = os.environ.get('POSTGRES_TEST_DSN')
MYSQL_TEST_DSN = os.environ.get('MYSQL_TEST_DSN')

DB_CONFIGS: list[ParameterSet | tuple[str | None, str]] = [
    pytest.param((SQLITE_TEST_DSN, 'sqlite'), id='sqlite')
]
for _dsn, _name, _var in (
    (POSTGRES_TEST_DSN, 'postgresql', 'POSTGRES_TEST_DSN'),
    (MYSQL_TEST_DSN, 'mysql', 'MYSQL_TEST_DSN'),
):
    if _dsn:
        DB_CONFIGS.append(pytest.param((_dsn, _name), id=_name))
    else:
        DB_CONFIGS.append(
            pytest.param(
                (None, _name),
                marks=pytest.mark.skip(reason=f'{_var} not set'),
                id=f'{_name}_skipped',
            )
        )


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest_asyncio.fixture(params=DB_CONFIGS)
async def engine(request) -> AsyncGenerator[AsyncEngine, None]:  # noqa: ANN001
    db_url, dialect = request.param
    if db_url is None:
        pytest.skip(f'DSN for {dialect} not set.')
    engine = create_async_engine(db_url)
    # Holding a connection keeps a shared in-memory SQLite database alive.
    keeper = await engine.connect()
    try:
        yield engine
    finally:
        await keeper.close()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest_asyncio.fixture
async def leases(engine: AsyncEngine, clock: _Clock) -> DatabaseTaskLeaseStore:
    store = DatabaseTaskLeaseStore(engine, clock=clock)
    await store.initialize()
    return store


@pytest.mark.asyncio
async def test_acquire_records_owner_and_blocks_a_second_holder(
    leases: DatabaseTaskLeaseStore,
) -> None:
    lease = await leases.acquire('task-1', ALICE, ttl=30)

    assert lease is not None
    assert lease.task_id == 'task-1'
    assert lease.owner == 'alice'
    assert await leases.acquire('task-1', ALICE, ttl=30) is None
    assert await leases.acquire('task-2', ALICE, ttl=30) is not None


@pytest.mark.asyncio
async def test_expired_lease_is_taken_over(
    leases: DatabaseTaskLeaseStore, clock: _Clock
) -> None:
    first = await leases.acquire('task-1', ALICE, ttl=30)
    assert first is not None
    clock.now += 31

    second = await leases.acquire('task-1', ALICE, ttl=30)

    assert second is not None
    assert second.token != first.token
    assert await leases.renew(first, ttl=30) is False
    assert await leases.renew(second, ttl=30) is True


@pytest.mark.asyncio
async def test_renew_extends_the_lease(
    leases: DatabaseTaskLeaseStore, clock: _Clock
) -> None:
    lease = await leases.acquire('task-1', ALICE, ttl=30)
    assert lease is not None
    clock.now += 20
    assert await leases.renew(lease, ttl=30) is True
    clock.now += 20

    assert await leases.acquire('task-1', ALICE, ttl=30) is None
    assert await leases.claim_expired(ttl=60, limit=10) == []


@pytest.mark.asyncio
async def test_renew_after_release_does_not_recreate_the_lease(
    leases: DatabaseTaskLeaseStore, clock: _Clock
) -> None:
    lease = await leases.acquire('task-1', ALICE, ttl=30)
    assert lease is not None
    await leases.release(lease)

    assert await leases.renew(lease, ttl=30) is False
    clock.now += 31
    assert await leases.claim_expired(ttl=60, limit=10) == []


@pytest.mark.asyncio
async def test_release_with_a_stale_token_keeps_the_current_lease(
    leases: DatabaseTaskLeaseStore,
) -> None:
    lease = await leases.acquire('task-1', ALICE, ttl=30)
    assert lease is not None

    await leases.release(TaskLease('task-1', 'alice', 'not-the-token'))

    assert await leases.renew(lease, ttl=30) is True


@pytest.mark.asyncio
async def test_claim_expired_claims_each_expired_lease_once(
    leases: DatabaseTaskLeaseStore, clock: _Clock
) -> None:
    stale = await leases.acquire('stale', ALICE, ttl=10)
    assert stale is not None
    assert await leases.acquire('live', ALICE, ttl=60) is not None
    clock.now += 11

    claimed = await leases.claim_expired(ttl=60, limit=10)

    assert [(c.task_id, c.owner) for c in claimed] == [('stale', 'alice')]
    assert claimed[0].token != stale.token
    assert await leases.renew(stale, ttl=10) is False
    assert await leases.claim_expired(ttl=60, limit=10) == []


@pytest.mark.asyncio
async def test_claim_expired_respects_the_limit(
    leases: DatabaseTaskLeaseStore, clock: _Clock
) -> None:
    for task_id in ('a', 'b', 'c'):
        assert await leases.acquire(task_id, ALICE, ttl=10) is not None
    clock.now += 11

    first = await leases.claim_expired(ttl=60, limit=2)
    rest = await leases.claim_expired(ttl=60, limit=2)

    assert len(first) == 2
    assert len(rest) == 1
    assert {c.task_id for c in first + rest} == {'a', 'b', 'c'}


@pytest.mark.asyncio
async def test_concurrent_acquire_has_one_winner(
    engine: AsyncEngine, clock: _Clock, tmp_path: Path
) -> None:
    # One engine per replica. In-memory SQLite engines share one connection,
    # which would run the racing transactions as one, so use a file instead.
    url = engine.url
    if url.get_backend_name() == 'sqlite':
        url = make_url(f'sqlite+aiosqlite:///{tmp_path / "leases.db"}')
    engines = [create_async_engine(url) for _ in range(5)]
    replicas = [DatabaseTaskLeaseStore(e, clock=clock) for e in engines]
    try:
        await replicas[0].initialize()

        results = await asyncio.gather(
            *(r.acquire('task-1', ALICE, ttl=30) for r in replicas),
            return_exceptions=True,
        )

        assert sum(isinstance(r, TaskLease) for r in results) == 1
    finally:
        for e in engines:
            await e.dispose()


@pytest.mark.asyncio
async def test_custom_table_name_and_owner_resolver(
    engine: AsyncEngine, clock: _Clock
) -> None:
    store = DatabaseTaskLeaseStore(
        engine,
        # A model can be defined once per table name in a process.
        table_name=f'custom_leases_{engine.url.get_backend_name()}',
        owner_resolver=lambda _: 'tenant-7',
        clock=clock,
    )
    await store.initialize()

    lease = await store.acquire('task-1', ALICE, ttl=10)

    assert lease is not None
    assert lease.owner == 'tenant-7'
    clock.now += 11
    claimed = await store.claim_expired(ttl=60, limit=10)
    assert [c.owner for c in claimed] == ['tenant-7']
