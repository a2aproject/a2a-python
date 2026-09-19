import asyncio
import logging
import time

from collections.abc import Callable


try:
    from sqlalchemy import Table, and_, insert, inspect, select, update
    from sqlalchemy.exc import IntegrityError, OperationalError
    from sqlalchemy.ext.asyncio import AsyncEngine
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
from a2a.server.events.event_queue import Event
from a2a.server.models import (
    Base,
    TaskEventModel,
    TaskModel,
    create_task_event_model,
)
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.server.tasks.database_task_store import DatabaseTaskStore
from a2a.server.tasks.task_store import TaskStore
from a2a.types.a2a_pb2 import (
    ListTasksRequest,
    ListTasksResponse,
    Task,
    TaskState,
)
from a2a.utils.proto_utils import to_stream_response


logger = logging.getLogger(__name__)

_TERMINAL_STATES = frozenset(
    {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_REJECTED,
    }
)

# DB contention retry
_MAX_ATTEMPTS = 5
_RETRY_DELAY_S = 0.02


class VersionedDatabaseTaskStore(VersionedTaskStore):
    """`VersionedTaskStore` backed by SQLAlchemy."""

    _event_model: type[TaskEventModel]

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
        self._event_table_ready = False

    @property
    def as_task_store(self) -> TaskStore:
        """The underlying non-versioned `TaskStore`."""
        return self._db

    async def initialize(self) -> None:
        """Initializes the database schema (task table and event log)."""
        await self._db.initialize()
        await self._ensure_event_table()

    async def _ensure_event_table(self) -> None:
        if self._event_table_ready:
            return
        if self._create_table:
            async with self._db.engine.begin() as conn:
                mapper = class_mapper(self._event_model)
                tables = [t for t in mapper.tables if isinstance(t, Table)]
                await conn.run_sync(Base.metadata.create_all, tables=tables)
        self._event_table_ready = True

    async def save(
        self,
        task: Task,
        *,
        event: Event | None = None,
        prev: Task | None = None,
        prev_version: TaskVersion,
        context: ServerCallContext,
    ) -> TaskVersion:
        """Persists `task` with a compare-and-swap on the version column.

        On the first write (``prev_version`` MISSING) this INSERTs and lets a
        primary-key collision surface as a conflict. On updates it runs a
        conditional UPDATE keyed on the previous version and treats zero
        affected rows as a concurrent modification.

        When `event` is provided it is appended to the ``task_events`` log in the
        same transaction, so the log is a consistent source for cross-replica
        replay via `DatabaseTaskEventStream`.
        """
        del prev
        await self._db._ensure_initialized()  # noqa: SLF001
        await self._ensure_event_table()
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
            except OperationalError:
                if attempts >= self._max_attempts:
                    raise
                await asyncio.sleep(self._retry_delay_s * attempts)

    async def _save_once(
        self,
        task: Task,
        *,
        event: Event | None,
        prev_version: TaskVersion,
        owner: str,
    ) -> TaskVersion:
        new_version = time.time_ns()
        model = self._db._to_orm(task, owner)  # noqa: SLF001
        model.version = new_version
        task_model = self._db.task_model
        values = _column_values(model, task_model)

        async with self._db.async_session_maker.begin() as session:
            if prev_version.is_missing:
                try:
                    await session.execute(insert(task_model).values(**values))
                except IntegrityError as e:
                    raise ConcurrentTaskModificationError(task.id) from e
            elif task.status.state == TaskState.TASK_STATE_CANCELED:
                row = (
                    await session.execute(
                        select(task_model)
                        .where(
                            and_(
                                task_model.id == task.id,
                                task_model.owner == owner,
                            )
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                current = (
                    self._db._from_orm(row)  # noqa: SLF001
                    if row is not None
                    else None
                )
                if current is None or current.status.state in _TERMINAL_STATES:
                    raise ConcurrentTaskModificationError(task.id)
                await session.execute(
                    update(task_model)
                    .where(
                        and_(
                            task_model.id == task.id,
                            task_model.owner == owner,
                        )
                    )
                    .values(**values)
                )
            else:
                result = await session.execute(
                    update(task_model)
                    .where(
                        and_(
                            task_model.id == task.id,
                            task_model.owner == owner,
                            task_model.version == _as_int(prev_version),
                        )
                    )
                    .values(**values)
                )
                if result.rowcount == 0:  # ty:ignore[unresolved-attribute]
                    raise ConcurrentTaskModificationError(task.id)

            if event is not None:
                await session.execute(
                    insert(self._event_model).values(
                        task_id=task.id,
                        owner=owner,
                        task_version=new_version,
                        event_data=to_stream_response(
                            event
                        ).SerializeToString(),
                    )
                )

        return TaskVersion(new_version)

    async def get(
        self, task_id: str, context: ServerCallContext
    ) -> StoredTask | None:
        """Returns the task with its stored version, or None if absent.

        Retries transient contention (e.g. a lock held while another writer
        commits) rather than failing the read.
        """
        await self._db._ensure_initialized()  # noqa: SLF001
        owner = self._db.owner_resolver(context)
        attempts = 0
        while True:
            attempts += 1
            try:
                return await self._get_once(task_id, owner)
            except OperationalError:
                if attempts >= self._max_attempts:
                    raise
                await asyncio.sleep(self._retry_delay_s * attempts)

    async def _get_once(self, task_id: str, owner: str) -> StoredTask | None:
        task_model = self._db.task_model
        async with self._db.async_session_maker() as session:
            stmt = select(task_model).where(
                and_(
                    task_model.id == task_id,
                    task_model.owner == owner,
                )
            )
            row = (await session.execute(stmt)).scalar_one_or_none()
            if row is None:
                return None
            task = self._db._from_orm(row)  # noqa: SLF001
            version = (
                TaskVersion(row.version)
                if row.version is not None
                else TaskVersion.MISSING
            )
            return StoredTask(task, version)

    async def list(
        self,
        params: ListTasksRequest,
        context: ServerCallContext,
    ) -> ListTasksResponse:
        """Lists tasks via the underlying store."""
        return await self._db.list(params, context)

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        """Deletes a task via the underlying store."""
        await self._db.delete(task_id, context)


def _as_int(version: TaskVersion) -> int:
    """Extracts the integer value of a version, or raises if not an int."""
    value = version._value  # noqa: SLF001
    if not isinstance(value, int):
        raise TypeError(
            'VersionedDatabaseTaskStore requires integer versions, got '
            f'{type(value).__name__}'
        )
    return value


def _column_values(model: object, task_model: type) -> dict[str, object]:
    """Extracts mapped column values from an ORM instance as a dict."""
    mapper = inspect(task_model)
    return {col.key: getattr(model, col.key) for col in mapper.column_attrs}
