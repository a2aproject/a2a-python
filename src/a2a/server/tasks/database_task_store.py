import logging

from collections.abc import Callable
from datetime import datetime, timezone


try:
    from sqlalchemy import Table, and_, delete, func, or_, select
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
from a2a.utils.task import decode_page_token, encode_page_token


logger = logging.getLogger(__name__)


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

        self.task_model = (
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
            if task_model.status:
                ParseDict(task_model.status, task.status)
            if task_model.artifacts:
                for art_dict in task_model.artifacts:
                    art = task.artifacts.add()
                    ParseDict(art_dict, art)
            if task_model.history:
                for msg_dict in task_model.history:
                    msg = task.history.add()
                    ParseDict(msg_dict, msg)
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

            # Use coalesce to treat NULL timestamps as datetime.min,
            # which sort last in descending order
            stmt = base_stmt.order_by(
                func.coalesce(
                    timestamp_col,
                    datetime.min.replace(tzinfo=timezone.utc),
                ).desc(),
                self.task_model.id.desc(),
            )

            # Get paginated results
            if params.page_token:
                start_task_id = decode_page_token(params.page_token)
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
                    raise InvalidParamsError(
                        f'Invalid page token: {params.page_token}'
                    )

                start_task_timestamp = start_task.last_updated
                where_clauses = []
                if start_task_timestamp:
                    where_clauses.append(
                        and_(
                            timestamp_col == start_task_timestamp,
                            self.task_model.id <= start_task_id,
                        )
                    )
                    where_clauses.append(timestamp_col < start_task_timestamp)
                    where_clauses.append(timestamp_col.is_(None))
                else:
                    where_clauses.append(
                        and_(
                            timestamp_col.is_(None),
                            self.task_model.id <= start_task_id,
                        )
                    )
                stmt = stmt.where(or_(*where_clauses))

            page_size = params.page_size or DEFAULT_LIST_TASKS_PAGE_SIZE
            stmt = stmt.limit(page_size + 1)  # Add 1 for next page token

            result = await session.execute(stmt)
            tasks_models = result.scalars().all()
            tasks = [self._from_orm(task_model) for task_model in tasks_models]

            next_page_token = (
                encode_page_token(tasks[-1].id)
                if len(tasks) == page_size + 1
                else None
            )

            return a2a_pb2.ListTasksResponse(
                tasks=tasks[:page_size],
                total_size=total_count,
                next_page_token=next_page_token,
                page_size=page_size,
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

            if result.rowcount > 0:  # type: ignore[attr-defined]
                logger.info(
                    'Task %s deleted successfully for owner %s.', task_id, owner
                )
            else:
                logger.warning(
                    'Attempted to delete nonexistent task with id: %s and owner %s',
                    task_id,
                    owner,
                )
