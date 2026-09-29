import asyncio
import logging

from collections.abc import AsyncGenerator


try:
    from sqlalchemy import Table, and_, select
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.ext.asyncio import (
        AsyncEngine,
        AsyncSession,
        async_sessionmaker,
    )
    from sqlalchemy.orm import class_mapper
except ImportError as e:
    raise ImportError(
        'DatabaseTaskEventStream requires SQLAlchemy and a database driver. '
        'Install with one of: '
        "'pip install a2a-sdk[postgresql]', "
        "'pip install a2a-sdk[mysql]', "
        "'pip install a2a-sdk[sqlite]', "
        "or 'pip install a2a-sdk[sql]'"
    ) from e

from a2a.server.cluster.event_stream import TaskEventStream, VersionedEvent
from a2a.server.cluster.version import TaskVersion
from a2a.server.events.event_queue import Event
from a2a.server.models import Base, TaskEventModel, create_task_event_model
from a2a.types.a2a_pb2 import StreamResponse


logger = logging.getLogger(__name__)

_DEFAULT_POLL_INTERVAL_S = 0.5


def _as_int(version: TaskVersion) -> int:
    """Extracts the integer value of a version, or raises if not an int."""
    value = version._value  # noqa: SLF001
    if not isinstance(value, int):
        raise TypeError(
            'DatabaseTaskEventStream requires integer versions, got '
            f'{type(value).__name__}'
        )
    return value


def stream_response_to_event(response: StreamResponse) -> Event:
    """Converts a `StreamResponse` proto back to an internal `Event`."""
    which = response.WhichOneof('payload')
    if which == 'task':
        return response.task
    if which == 'message':
        return response.message
    if which == 'status_update':
        return response.status_update
    if which == 'artifact_update':
        return response.artifact_update
    raise ValueError(f'StreamResponse has no known payload set: {which!r}')


class DatabaseTaskEventStream(TaskEventStream):
    """`TaskEventStream` backed by polling the shared ``task_events`` table."""

    _event_model: type[TaskEventModel]

    def __init__(
        self,
        engine: AsyncEngine,
        create_table: bool = True,
        table_name: str = 'task_events',
        poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    ) -> None:
        """Initializes the stream over an existing SQLAlchemy AsyncEngine."""
        self._engine = engine
        self._session_maker = async_sessionmaker(engine, expire_on_commit=False)
        self._create_table = create_table
        self._poll_interval_s = poll_interval_s
        self._initialized = False
        self._event_model = (  # ty:ignore[invalid-assignment]
            TaskEventModel
            if table_name == 'task_events'
            else create_task_event_model(table_name)
        )

    async def initialize(self) -> None:
        """Creates the ``task_events`` table if requested."""
        if self._initialized:
            return
        if self._create_table:
            async with self._engine.begin() as conn:
                mapper = class_mapper(self._event_model)
                tables = [t for t in mapper.tables if isinstance(t, Table)]
                await conn.run_sync(Base.metadata.create_all, tables=tables)
        self._initialized = True

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            await self.initialize()

    async def publish(self, task_id: str, event: VersionedEvent) -> None:
        """No-op: events are persisted transactionally by the task store."""
        del task_id, event

    async def _seq_at_or_before_version(
        self, session: AsyncSession, task_id: str, after: TaskVersion
    ) -> int:
        """Seq to start polling from so nothing after `after` is missed."""
        if after.is_missing:
            return 0
        stmt = (
            select(self._event_model.seq)
            .where(
                and_(
                    self._event_model.task_id == task_id,
                    self._event_model.task_version <= _as_int(after),
                )
            )
            .order_by(self._event_model.seq.desc())
            .limit(1)
        )
        result = (await session.execute(stmt)).scalar_one_or_none()
        return result or 0

    async def subscribe(  # type: ignore[override]
        self, task_id: str, *, after: TaskVersion
    ) -> AsyncGenerator[VersionedEvent, None]:
        """Polls the log for events of `task_id` newer than `after`."""
        await self._ensure_initialized()
        async with self._session_maker() as session:
            cursor = await self._seq_at_or_before_version(
                session, task_id, after
            )

        while True:
            try:
                async with self._session_maker() as session:
                    stmt = (
                        select(
                            self._event_model.seq,
                            self._event_model.task_version,
                            self._event_model.event_data,
                        )
                        .where(
                            and_(
                                self._event_model.task_id == task_id,
                                self._event_model.seq > cursor,
                            )
                        )
                        .order_by(self._event_model.seq.asc())
                    )
                    rows = (await session.execute(stmt)).all()
            except OperationalError:
                logger.debug(
                    'Transient DB error polling events for %s; retrying',
                    task_id,
                    exc_info=True,
                )
                await asyncio.sleep(self._poll_interval_s)
                continue

            for row in rows:
                seq, task_version, event_data = row[0], row[1], row[2]
                cursor = seq
                version = TaskVersion(task_version)
                if not version.is_after(after):
                    continue
                response = StreamResponse()
                response.ParseFromString(event_data)
                yield VersionedEvent(
                    event=stream_response_to_event(response),
                    version=version,
                )

            if not rows:
                await asyncio.sleep(self._poll_interval_s)

    async def destroy(self, task_id: str) -> None:
        """No-op: the append-only log is retained; subscribers stop on their own."""
        del task_id
