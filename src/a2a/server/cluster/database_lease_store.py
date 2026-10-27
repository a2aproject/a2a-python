import time
import uuid

from collections.abc import Callable


try:
    from sqlalchemy import Table, and_, delete, insert, select, update
    from sqlalchemy.exc import IntegrityError
    from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
    from sqlalchemy.orm import class_mapper
except ImportError as e:
    raise ImportError(
        'DatabaseTaskLeaseStore requires SQLAlchemy and a database driver. '
        'Install with one of: '
        "'pip install a2a-sdk[postgresql]', "
        "'pip install a2a-sdk[mysql]', "
        "'pip install a2a-sdk[sqlite]', "
        "or 'pip install a2a-sdk[sql]'"
    ) from e

from a2a.server.cluster.lease import TaskLease, TaskLeaseStore
from a2a.server.context import ServerCallContext
from a2a.server.models import Base, TaskLeaseModel, create_task_lease_model
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope


class DatabaseTaskLeaseStore(TaskLeaseStore):
    """`TaskLeaseStore` backed by a ``task_leases`` table.

    Uses conditional UPDATEs and the primary key for mutual exclusion, so it
    works the same on PostgreSQL, MySQL and SQLite. Expiry times come from
    each replica's `clock`, so replica clocks must agree to well within the
    lease TTL minus the heartbeat interval.
    """

    _lease_model: type[TaskLeaseModel]

    def __init__(
        self,
        engine: AsyncEngine,
        create_table: bool = True,
        table_name: str = 'task_leases',
        owner_resolver: OwnerResolver = resolve_user_scope,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Initializes the store over an existing SQLAlchemy AsyncEngine.

        `owner_resolver` must match the one used by the task store, so that
        a lease records the owner its task is stored under.
        """
        self._engine = engine
        self._session_maker = async_sessionmaker(engine, expire_on_commit=False)
        self._create_table = create_table
        self._owner_resolver = owner_resolver
        self._clock = clock
        self._initialized = False
        self._lease_model = (  # ty:ignore[invalid-assignment]
            TaskLeaseModel
            if table_name == 'task_leases'
            else create_task_lease_model(table_name)
        )

    async def initialize(self) -> None:
        """Creates the ``task_leases`` table if requested."""
        if self._initialized:
            return
        if self._create_table:
            async with self._engine.begin() as conn:
                mapper = class_mapper(self._lease_model)
                tables = [t for t in mapper.tables if isinstance(t, Table)]
                await conn.run_sync(Base.metadata.create_all, tables=tables)
        self._initialized = True

    def _now_ms(self) -> int:
        return int(self._clock() * 1000)

    async def acquire(
        self, task_id: str, context: ServerCallContext, ttl: float
    ) -> TaskLease | None:
        """Takes the lease, or returns None while another holder's is live."""
        await self.initialize()
        model = self._lease_model
        owner = self._owner_resolver(context)
        now = self._now_ms()
        token = uuid.uuid4().hex
        values = {
            'owner': owner,
            'token': token,
            'expires_at_ms': now + int(ttl * 1000),
        }
        async with self._session_maker.begin() as session:
            # Take over a lease whose holder stopped renewing it.
            result = await session.execute(
                update(model)
                .where(
                    and_(model.task_id == task_id, model.expires_at_ms < now)
                )
                .values(**values)
            )
            if result.rowcount == 1:  # ty:ignore[unresolved-attribute]
                return TaskLease(task_id, owner, token)
        try:
            # The primary key lets only one of two racing acquirers in.
            async with self._session_maker.begin() as session:
                await session.execute(
                    insert(model).values(task_id=task_id, **values)
                )
        except IntegrityError:
            return None
        return TaskLease(task_id, owner, token)

    async def renew(self, lease: TaskLease, ttl: float) -> bool:
        """Extends the lease; False if it was released or taken over."""
        model = self._lease_model
        async with self._session_maker.begin() as session:
            result = await session.execute(
                update(model)
                .where(
                    and_(
                        model.task_id == lease.task_id,
                        model.token == lease.token,
                    )
                )
                .values(expires_at_ms=self._now_ms() + int(ttl * 1000))
            )
        return result.rowcount == 1  # ty:ignore[unresolved-attribute]

    async def release(self, lease: TaskLease) -> None:
        """Drops the lease if it is still held with this token."""
        model = self._lease_model
        async with self._session_maker.begin() as session:
            await session.execute(
                delete(model).where(
                    and_(
                        model.task_id == lease.task_id,
                        model.token == lease.token,
                    )
                )
            )

    async def claim_expired(self, ttl: float, limit: int) -> list[TaskLease]:
        """Claims expired leases, each for exactly one caller."""
        await self.initialize()
        model = self._lease_model
        now = self._now_ms()
        async with self._session_maker() as session:
            rows = (
                await session.execute(
                    select(model.task_id, model.owner)
                    .where(model.expires_at_ms < now)
                    .order_by(model.expires_at_ms)
                    .limit(limit)
                )
            ).all()
        claimed: list[TaskLease] = []
        for task_id, owner in rows:
            token = uuid.uuid4().hex
            async with self._session_maker.begin() as session:
                # Re-checked here: another sweeper or a late renewal may have
                # won since the SELECT.
                result = await session.execute(
                    update(model)
                    .where(
                        and_(
                            model.task_id == task_id,
                            model.expires_at_ms < now,
                        )
                    )
                    .values(token=token, expires_at_ms=now + int(ttl * 1000))
                )
            if result.rowcount == 1:  # ty:ignore[unresolved-attribute]
                claimed.append(TaskLease(task_id, owner or '', token))
        return claimed
