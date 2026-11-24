import asyncio
import logging

from collections.abc import Callable


try:
    from sqlalchemy import Table, and_, delete, insert, select, update
    from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
    from sqlalchemy.orm import class_mapper
except ImportError as e:
    raise ImportError(
        'VersionedDatabaseTaskStore requires SQLAlchemy and a database driver. '
        'Install with one of: '
        "'pip install a2a-sdk[postgresql]', "
        "'pip install a2a-sdk[mysql]', "
        "'pip install a2a-sdk[sqlite]', "
        "or 'pip install a2a-sdk[sql]'"
    ) from e

from a2a.server.cluster.task_store import (
    ConcurrentTaskModificationError,
    StoredTask,
    VersionedTaskStore,
)
from a2a.server.cluster.version import TaskVersion
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import StoredEvent
from a2a.server.models import (
    Base,
    TaskEventModel,
    TaskModel,
    TaskVersionModel,
    create_task_event_model,
    create_task_version_model,
)
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.server.tasks.database_task_store import DatabaseTaskStore
from a2a.server.tasks.task_store import TaskStore
from a2a.types.a2a_pb2 import (
    ListTasksRequest,
    ListTasksResponse,
    Task,
)
from a2a.utils.proto_utils import to_stream_response


logger = logging.getLogger(__name__)

# DB contention retry
_MAX_ATTEMPTS = 5
_RETRY_DELAY_S = 0.02
# Postgres deadlock and serialization failure. asyncpg reports these as a
# generic DBAPIError rather than OperationalError.
_TRANSIENT_ERROR_CODES = frozenset({'40P01', '40001'})


def _is_transient(error: DBAPIError) -> bool:
    """Whether `error` is DB contention that is safe to retry."""
    return (
        isinstance(error, OperationalError)
        or getattr(error.orig, 'sqlstate', None) in _TRANSIENT_ERROR_CODES
    )


class VersionedDatabaseTaskStore(VersionedTaskStore):
    """`VersionedTaskStore` backed by SQLAlchemy."""

    _event_model: type[TaskEventModel]
    _version_model: type[TaskVersionModel]

    def __init__(  # noqa: PLR0913
        self,
        engine: AsyncEngine,
        create_table: bool = True,
        table_name: str = 'tasks',
        owner_resolver: OwnerResolver = resolve_user_scope,
        core_to_model_conversion: Callable[[Task, str], TaskModel]
        | None = None,
        model_to_core_conversion: Callable[[TaskModel], Task] | None = None,
        event_table_name: str = 'task_events',
        version_table_name: str = 'task_versions',
        max_attempts: int = _MAX_ATTEMPTS,
        retry_delay_s: float = _RETRY_DELAY_S,
    ) -> None:
        """Initializes the store, delegating schema to `DatabaseTaskStore`."""
        self._db = DatabaseTaskStore(
            engine=engine,
            create_table=create_table,
            table_name=table_name,
            owner_resolver=owner_resolver,
            core_to_model_conversion=core_to_model_conversion,
            model_to_core_conversion=model_to_core_conversion,
        )
        self._create_table = create_table
        self._max_attempts = max_attempts
        self._retry_delay_s = retry_delay_s
        self._event_model = (  # ty:ignore[invalid-assignment]
            TaskEventModel
            if event_table_name == 'task_events'
            else create_task_event_model(event_table_name)
        )
        self._version_model = (  # ty:ignore[invalid-assignment]
            TaskVersionModel
            if version_table_name == 'task_versions'
            else create_task_version_model(version_table_name)
        )
        self._cluster_tables_ready = False

    @property
    def as_task_store(self) -> TaskStore:
        """The underlying non-versioned `TaskStore`."""
        return self._db

    async def initialize(self) -> None:
        """Initializes the database schema (task, version and event tables)."""
        await self._db.initialize()
        await self._ensure_cluster_tables()

    async def _ensure_cluster_tables(self) -> None:
        if self._cluster_tables_ready:
            return
        if self._create_table:
            async with self._db.engine.begin() as conn:
                tables = [
                    t
                    for model in (self._version_model, self._event_model)
                    for t in class_mapper(model).tables
                    if isinstance(t, Table)
                ]
                await conn.run_sync(Base.metadata.create_all, tables=tables)
        self._cluster_tables_ready = True

    async def save(
        self,
        task: Task,
        *,
        event: StoredEvent | None = None,
        prev: Task | None = None,
        prev_version: TaskVersion,
        context: ServerCallContext,
    ) -> TaskVersion:
        """Persists `task` with a compare-and-swap on the version side table.

        The version lives in ``task_versions``, not on the tasks row, and
        counts writes to the task: the first write sets 1 and every later
        write adds 1. When `event` is provided it is appended to
        ``task_events`` in the same transaction, with the task's generation,
        for cross-replica replay.
        """
        del prev
        await self._db._ensure_initialized()  # noqa: SLF001
        await self._ensure_cluster_tables()
        owner = self._db.owner_resolver(context)

        # Retry only transient DB contention (e.g. lock/serialization errors).
        # A ConcurrentTaskModificationError is the real signal and is never
        # retried - it propagates so the caller can reload and decide.
        attempts = 0
        while True:
            attempts += 1
            try:
                return await self._save_once(
                    task, event=event, prev_version=prev_version, owner=owner
                )
            except DBAPIError as e:
                if not _is_transient(e) or attempts >= self._max_attempts:
                    raise
                await asyncio.sleep(self._retry_delay_s * attempts)

    async def _save_once(
        self,
        task: Task,
        *,
        event: StoredEvent | None,
        prev_version: TaskVersion,
        owner: str,
    ) -> TaskVersion:
        model = self._db._to_orm(task, owner)  # noqa: SLF001

        # The task_versions row is written before the tasks row, so
        # concurrent writers cannot deadlock.
        async with self._db.async_session_maker.begin() as session:
            if prev_version.is_missing:
                new_version = 1
                await self._insert_version(session, task.id, owner, new_version)
            else:
                new_version = _as_int(prev_version) + 1
                result = await session.execute(
                    update(self._version_model)
                    .where(
                        and_(
                            self._version_model.task_id == task.id,
                            self._version_model.owner == owner,
                            self._version_model.version
                            == _as_int(prev_version),
                        )
                    )
                    .values(version=new_version)
                )
                if result.rowcount == 0:  # ty:ignore[unresolved-attribute]
                    raise ConcurrentTaskModificationError(task.id)

            await session.merge(model)

            if event is not None:
                await session.execute(
                    insert(self._event_model).values(
                        task_id=task.id,
                        owner=owner,
                        task_version=new_version,
                        generation=task.generation,
                        event_data=to_stream_response(
                            event
                        ).SerializeToString(),
                    )
                )

        return TaskVersion(new_version)

    async def update(
        self,
        task_id: str,
        change: Callable[[Task], StoredEvent | None],
        *,
        context: ServerCallContext,
    ) -> StoredTask | None:
        """Applies `change` to the current task in one locked transaction.

        The version row is locked before the task is read, so writers that
        compare-and-swap on it wait and `change` applies on the first
        attempt.
        """
        await self._db._ensure_initialized()  # noqa: SLF001
        await self._ensure_cluster_tables()
        owner = self._db.owner_resolver(context)
        attempts = 0
        while True:
            attempts += 1
            try:
                return await self._update_once(task_id, change, owner)
            except ConcurrentTaskModificationError:
                # Only a version row created concurrently gets here.
                if attempts >= self._max_attempts:
                    raise
            except DBAPIError as e:
                if not _is_transient(e) or attempts >= self._max_attempts:
                    raise
                await asyncio.sleep(self._retry_delay_s * attempts)

    async def _update_once(
        self,
        task_id: str,
        change: Callable[[Task], StoredEvent | None],
        owner: str,
    ) -> StoredTask | None:
        async with self._db.async_session_maker.begin() as session:
            stored_version = await self._lock_version(session, task_id, owner)
            row = (
                await session.execute(
                    select(self._db.task_model).where(
                        and_(
                            self._db.task_model.id == task_id,
                            self._db.task_model.owner == owner,
                        )
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            task = self._db._from_orm(row)  # noqa: SLF001
            event = change(task)
            version = (
                TaskVersion(stored_version)
                if stored_version is not None
                else TaskVersion.MISSING
            )
            if event is None:
                return StoredTask(task, version)
            new_version = (stored_version or 0) + 1
            if stored_version is None:
                await self._insert_version(session, task_id, owner, new_version)
            else:
                result = await session.execute(
                    update(self._version_model)
                    .where(
                        and_(
                            self._version_model.task_id == task_id,
                            self._version_model.owner == owner,
                            self._version_model.version == stored_version,
                        )
                    )
                    .values(version=new_version)
                )
                if result.rowcount == 0:  # ty:ignore[unresolved-attribute]
                    raise ConcurrentTaskModificationError(task_id)
            await session.merge(self._db._to_orm(task, owner))  # noqa: SLF001
            await session.execute(
                insert(self._event_model).values(
                    task_id=task_id,
                    owner=owner,
                    task_version=new_version,
                    generation=task.generation,
                    event_data=to_stream_response(event).SerializeToString(),
                )
            )
        return StoredTask(task, TaskVersion(new_version))

    async def _lock_version(
        self, session: AsyncSession, task_id: str, owner: str
    ) -> int | None:
        """Locks the task's version row and returns its version.

        A no-op update rather than SELECT ... FOR UPDATE: it takes the same
        row lock on PostgreSQL and MySQL, and on SQLite, which has no row
        locks, it takes the database write lock up front.
        """
        where = and_(
            self._version_model.task_id == task_id,
            self._version_model.owner == owner,
        )
        await session.execute(
            update(self._version_model)
            .where(where)
            .values(version=self._version_model.version)
        )
        return (
            await session.execute(
                select(self._version_model.version).where(where)
            )
        ).scalar_one_or_none()

    async def get(
        self, task_id: str, context: ServerCallContext
    ) -> StoredTask | None:
        """Returns the task with its stored version, or None if absent.

        Retries transient contention (e.g. a lock held while another writer
        commits) rather than failing the read.
        """
        await self._db._ensure_initialized()  # noqa: SLF001
        await self._ensure_cluster_tables()
        owner = self._db.owner_resolver(context)
        attempts = 0
        while True:
            attempts += 1
            try:
                return await self._get_once(task_id, owner)
            except DBAPIError as e:
                if not _is_transient(e) or attempts >= self._max_attempts:
                    raise
                await asyncio.sleep(self._retry_delay_s * attempts)

    async def _get_once(self, task_id: str, owner: str) -> StoredTask | None:
        task_model = self._db.task_model
        version_model = self._version_model
        async with self._db.async_session_maker() as session:
            stmt = (
                select(task_model, version_model.version)
                .outerjoin(
                    version_model,
                    and_(
                        version_model.task_id == task_model.id,
                        version_model.owner == task_model.owner,
                    ),
                )
                .where(
                    and_(
                        task_model.id == task_id,
                        task_model.owner == owner,
                    )
                )
            )
            result = (await session.execute(stmt)).one_or_none()
            if result is None:
                return None
            row, version_value = result
            task = self._db._from_orm(row)  # noqa: SLF001
            version = (
                TaskVersion(version_value)
                if version_value is not None
                else TaskVersion.MISSING
            )
            return StoredTask(task, version)

    async def _insert_version(
        self, session: AsyncSession, task_id: str, owner: str, version: int
    ) -> None:
        # The insert is what makes one of two concurrent first writers lose.
        try:
            await session.execute(
                insert(self._version_model).values(
                    task_id=task_id, owner=owner, version=version
                )
            )
        except IntegrityError as e:
            raise ConcurrentTaskModificationError(task_id) from e

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        """Lists tasks via the underlying store."""
        return await self._db.list(params, context)

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        """Deletes a task and its version row via the underlying store."""
        await self._db._ensure_initialized()  # noqa: SLF001
        await self._ensure_cluster_tables()
        owner = self._db.owner_resolver(context)
        await self._db.delete(task_id, context)
        async with self._db.async_session_maker.begin() as session:
            await session.execute(
                delete(self._version_model).where(
                    and_(
                        self._version_model.task_id == task_id,
                        self._version_model.owner == owner,
                    )
                )
            )


def _as_int(version: TaskVersion) -> int:
    """Extracts the integer value of a version, or raises if not an int."""
    value = version._value  # noqa: SLF001
    if not isinstance(value, int):
        raise TypeError(
            'VersionedDatabaseTaskStore requires integer versions, got '
            f'{type(value).__name__}'
        )
    return value
