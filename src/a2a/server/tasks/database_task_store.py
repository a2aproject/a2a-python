import logging

from collections.abc import Callable
from datetime import datetime
from typing import Any, cast


try:
    from sqlalchemy import Table, and_, case, delete, func, or_, select
    from sqlalchemy.ext.asyncio import (
        AsyncEngine,
        AsyncSession,
        async_sessionmaker,
    )
    from sqlalchemy.orm import class_mapper
except ImportError as e:
    raise ImportError(
        'DatabaseTaskStore requires SQLAlchemy and a database driver. '
        'Install with one of: '
        "'pip install a2a-sdk[postgresql]', "
        "'pip install a2a-sdk[mysql]', "
        "'pip install a2a-sdk[sqlite]', "
        "or 'pip install a2a-sdk[sql]'"
    ) from e
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.timestamp_pb2 import Timestamp

from a2a.compat.v0_3.model_conversions import (
    compat_task_model_to_core,
)
from a2a.server.context import ServerCallContext
from a2a.server.models import Base, TaskModel, create_task_model
from a2a.server.owner_resolver import OwnerResolver, resolve_user_scope
from a2a.server.tasks.task_store import TaskStore
from a2a.types import a2a_pb2
from a2a.types.a2a_pb2 import Task
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE
from a2a.utils.errors import InvalidParamsError
from a2a.utils.task import (
    ListTasksCursor,
    decode_list_tasks_cursor,
    decode_page_token,
    encode_list_tasks_cursor,
)


logger = logging.getLogger(__name__)


def _datetime_to_ns(value: datetime) -> int:
    """Nanoseconds since the epoch for a stored (naive UTC) `last_updated`."""
    timestamp = Timestamp()
    timestamp.FromDatetime(value)
    return timestamp.ToNanoseconds()


def _ns_to_datetime(timestamp_ns: int) -> datetime:
    """Inverse of `_datetime_to_ns`, as a naive UTC datetime."""
    timestamp = Timestamp()
    timestamp.FromNanoseconds(timestamp_ns)
    return timestamp.ToDatetime()


class DatabaseTaskStore(TaskStore):
    """SQLAlchemy-based implementation of TaskStore.

    Stores task objects in a database supported by SQLAlchemy.
    """

    engine: AsyncEngine
    async_session_maker: async_sessionmaker[AsyncSession]
    create_table: bool
    _initialized: bool
    task_model: type[TaskModel]
    owner_resolver: OwnerResolver
    core_to_model_conversion: Callable[[Task, str], TaskModel] | None = None
    model_to_core_conversion: Callable[[TaskModel], Task] | None = None

    def __init__(  # noqa: PLR0913
        self,
        engine: AsyncEngine,
        create_table: bool = True,
        table_name: str = 'tasks',
        owner_resolver: OwnerResolver = resolve_user_scope,
        core_to_model_conversion: Callable[[Task, str], TaskModel]
        | None = None,
        model_to_core_conversion: Callable[[TaskModel], Task] | None = None,
    ) -> None:
        """Initializes the DatabaseTaskStore.

        Args:
            engine: An existing SQLAlchemy AsyncEngine to be used by Task Store
            create_table: If true, create tasks table on initialization.
            table_name: Name of the database table. Defaults to 'tasks'.
            owner_resolver: Function to resolve the owner from the context.
            core_to_model_conversion: Optional function to convert a Task to a TaskModel.
            model_to_core_conversion: Optional function to convert a TaskModel to a Task.
        """
        logger.debug(
            'Initializing DatabaseTaskStore with existing engine, table: %s',
            table_name,
        )
        self.engine = engine
        self.async_session_maker = async_sessionmaker(
            self.engine, expire_on_commit=False
        )
        self.create_table = create_table
        self._initialized = False
        self.owner_resolver = owner_resolver
        self.core_to_model_conversion = core_to_model_conversion
        self.model_to_core_conversion = model_to_core_conversion

        self.task_model = (  # ty:ignore[invalid-assignment]
            TaskModel
            if table_name == 'tasks'
            else create_task_model(table_name)
        )

    async def initialize(self) -> None:
        """Initialize the database and create the table if needed."""
        if self._initialized:
            return

        logger.debug('Initializing database schema...')
        if self.create_table:
            async with self.engine.begin() as conn:
                mapper = class_mapper(self.task_model)
                tables_to_create = [
                    table for table in mapper.tables if isinstance(table, Table)
                ]
                await conn.run_sync(
                    Base.metadata.create_all, tables=tables_to_create
                )
        self._initialized = True
        logger.debug('Database schema initialized.')

    async def _ensure_initialized(self) -> None:
        """Ensure the database connection is initialized."""
        if not self._initialized:
            await self.initialize()

    def _to_orm(self, task: Task, owner: str) -> TaskModel:
        """Maps a Proto Task to a SQLAlchemy TaskModel instance."""
        if self.core_to_model_conversion:
            return self.core_to_model_conversion(task, owner)

        return self.task_model(
            id=task.id,
            context_id=task.context_id,
            kind='task',  # Default kind for tasks
            owner=owner,
            last_updated=(
                task.status.timestamp.ToDatetime()
                if task.status.HasField('timestamp')
                else None
            ),
            status=MessageToDict(task.status),
            artifacts=[MessageToDict(artifact) for artifact in task.artifacts],
            history=[MessageToDict(history) for history in task.history],
            task_metadata=(
                MessageToDict(task.metadata) if task.metadata.fields else None
            ),
            protocol_version='1.0',
        )

    def _from_orm(self, task_model: TaskModel) -> Task:
        """Maps a SQLAlchemy TaskModel to a Proto Task instance."""
        if self.model_to_core_conversion:
            return self.model_to_core_conversion(task_model)

        if task_model.protocol_version == '1.0':
            task = Task(
                id=task_model.id,
                context_id=task_model.context_id,
            )
            # These JSON columns are annotated with proto types but hold plain
            # dicts at rest; ParseDict wants the dict view.
            if task_model.status:
                ParseDict(
                    cast('dict[str, Any]', task_model.status), task.status
                )
            if task_model.artifacts:
                for art_dict in task_model.artifacts:
                    art = task.artifacts.add()
                    ParseDict(cast('dict[str, Any]', art_dict), art)
            if task_model.history:
                for msg_dict in task_model.history:
                    msg = task.history.add()
                    ParseDict(cast('dict[str, Any]', msg_dict), msg)
            if task_model.task_metadata:
                task.metadata.update(task_model.task_metadata)
            return task

        # Legacy conversion
        return compat_task_model_to_core(task_model)

    async def save(self, task: Task, context: ServerCallContext) -> None:
        """Saves or updates a task in the database for the resolved owner."""
        await self._ensure_initialized()
        owner = self.owner_resolver(context)
        db_task = self._to_orm(task, owner)
        async with self.async_session_maker.begin() as session:
            await session.merge(db_task)
            logger.debug(
                'Task %s for owner %s saved/updated successfully.',
                task.id,
                owner,
            )

    async def get(
        self, task_id: str, context: ServerCallContext
    ) -> Task | None:
        """Retrieves a task from the database by ID, for the given owner."""
        await self._ensure_initialized()
        owner = self.owner_resolver(context)
        async with self.async_session_maker() as session:
            stmt = select(self.task_model).where(
                and_(
                    self.task_model.id == task_id,
                    self.task_model.owner == owner,
                )
            )
            result = await session.execute(stmt)
            task_model = result.scalar_one_or_none()
            if task_model:
                task = self._from_orm(task_model)
                logger.debug(
                    'Task %s retrieved successfully for owner %s.',
                    task_id,
                    owner,
                )
                return task

            logger.debug(
                'Task %s not found in store for owner %s.', task_id, owner
            )
            return None

    async def list(
        self,
        params: a2a_pb2.ListTasksRequest,
        context: ServerCallContext,
    ) -> a2a_pb2.ListTasksResponse:
        """Retrieves tasks from the database based on provided parameters, for the given owner."""
        await self._ensure_initialized()
        owner = self.owner_resolver(context)
        logger.debug('Listing tasks for owner %s with params %s', owner, params)

        async with self.async_session_maker() as session:
            timestamp_col = self.task_model.last_updated
            base_stmt = select(self.task_model).where(
                self.task_model.owner == owner
            )

            # Add filters
            if params.context_id:
                base_stmt = base_stmt.where(
                    self.task_model.context_id == params.context_id
                )
            if params.status:
                base_stmt = base_stmt.where(
                    self.task_model.status['state'].as_string()
                    == a2a_pb2.TaskState.Name(params.status)
                )
            if params.HasField('status_timestamp_after'):
                last_updated_after = params.status_timestamp_after.ToDatetime()
                base_stmt = base_stmt.where(timestamp_col >= last_updated_after)

            # Get total count
            count_stmt = select(func.count()).select_from(base_stmt.alias())
            total_count = (await session.execute(count_stmt)).scalar_one()

            # Sort NULL timestamps last without binding a sentinel value, which
            # may fall outside a database's supported datetime range.
            stmt = base_stmt.order_by(
                case((timestamp_col.is_(None), 1), else_=0).asc(),
                timestamp_col.desc(),
                self.task_model.id.desc(),
            )

            # Get paginated results. The page token carries the position of the
            # last task returned, so it stays valid if that task is updated or
            # deleted. A task updated mid-listing can move above the cursor and
            # be skipped.
            if params.page_token:
                cursor = decode_list_tasks_cursor(params.page_token)
                if cursor is None:
                    stmt = stmt.where(
                        await self._legacy_page_clause(
                            session, owner, params.page_token
                        )
                    )
                else:
                    stmt = stmt.where(self._after_cursor(cursor))

            page_size = params.page_size or DEFAULT_LIST_TASKS_PAGE_SIZE
            stmt = stmt.limit(page_size + 1)  # Add 1 for next page token

            result = await session.execute(stmt)
            tasks_models = result.scalars().all()
            page_models = tasks_models[:page_size]

            next_page_token = (
                encode_list_tasks_cursor(self._cursor_for(page_models[-1]))
                if len(tasks_models) == page_size + 1
                else None
            )

            return a2a_pb2.ListTasksResponse(
                tasks=[
                    self._from_orm(task_model) for task_model in page_models
                ],
                total_size=total_count,
                next_page_token=next_page_token,
                page_size=page_size,
            )

    @staticmethod
    def _cursor_for(task_model: TaskModel) -> ListTasksCursor:
        # From the stored column, not the proto, so the cursor compares equal
        # at the database's own timestamp precision.
        last_updated = task_model.last_updated
        return ListTasksCursor(
            timestamp_ns=_datetime_to_ns(last_updated)
            if last_updated is not None
            else None,
            task_id=task_model.id,
        )

    def _after_cursor(self, cursor: ListTasksCursor) -> Any:
        """Rows strictly after `cursor` in the `ListTasks` sort order."""
        timestamp_col = self.task_model.last_updated
        if cursor.timestamp_ns is None:
            return and_(
                timestamp_col.is_(None), self.task_model.id < cursor.task_id
            )
        try:
            cursor_timestamp = _ns_to_datetime(cursor.timestamp_ns)
        except OverflowError as e:
            raise InvalidParamsError('Invalid page token') from e
        return or_(
            timestamp_col < cursor_timestamp,
            and_(
                timestamp_col == cursor_timestamp,
                self.task_model.id < cursor.task_id,
            ),
            timestamp_col.is_(None),
        )

    async def _legacy_page_clause(
        self, session: AsyncSession, owner: str, page_token: str
    ) -> Any:
        """Resolves a legacy page token, which names the first task of the page."""
        timestamp_col = self.task_model.last_updated
        start_task_id = decode_page_token(page_token)
        start_task = (
            await session.execute(
                select(self.task_model).where(
                    and_(
                        self.task_model.id == start_task_id,
                        self.task_model.owner == owner,
                    )
                )
            )
        ).scalar_one_or_none()
        if not start_task:
            raise InvalidParamsError(f'Invalid page token: {page_token}')

        start_task_timestamp = start_task.last_updated
        if start_task_timestamp:
            return or_(
                and_(
                    timestamp_col == start_task_timestamp,
                    self.task_model.id <= start_task_id,
                ),
                timestamp_col < start_task_timestamp,
                timestamp_col.is_(None),
            )
        return and_(
            timestamp_col.is_(None),
            self.task_model.id <= start_task_id,
        )

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        """Deletes a task from the database by ID, for the given owner."""
        await self._ensure_initialized()
        owner = self.owner_resolver(context)

        async with self.async_session_maker.begin() as session:
            stmt = delete(self.task_model).where(
                and_(
                    self.task_model.id == task_id,
                    self.task_model.owner == owner,
                )
            )
            result = await session.execute(stmt)
            # Commit is automatic when using session.begin()

            if result.rowcount > 0:  # ty:ignore[unresolved-attribute]
                logger.info(
                    'Task %s deleted successfully for owner %s.', task_id, owner
                )
            else:
                logger.warning(
                    'Attempted to delete nonexistent task with id: %s and owner %s',
                    task_id,
                    owner,
                )
