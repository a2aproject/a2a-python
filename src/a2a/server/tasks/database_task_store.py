import logging

from datetime import datetime, timezone


try:
    from sqlalchemy import (
        Table,
        and_,
        delete,
        func,
        or_,
        select,
    )
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

from a2a.server.context import ServerCallContext
from a2a.server.models import Base, TaskModel, create_task_model
from a2a.server.tasks.task_store import TaskStore, TasksPage
from a2a.types import ListTasksParams, Task
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE
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

    def __init__(
        self,
        engine: AsyncEngine,
        create_table: bool = True,
        table_name: str = 'tasks',
    ) -> None:
        """Initializes the DatabaseTaskStore.

        Args:
            engine: An existing SQLAlchemy AsyncEngine to be used by Task Store
            create_table: If true, create tasks table on initialization.
            table_name: Name of the database table. Defaults to 'tasks'.
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

    def _to_orm(self, task: Task) -> TaskModel:
        """Maps a Pydantic Task to a SQLAlchemy TaskModel instance."""
        return self.task_model(
            id=task.id,
            context_id=task.context_id,
            kind=task.kind,
            status=task.status,
            artifacts=task.artifacts,
            history=task.history,
            task_metadata=task.metadata,
        )

    def _from_orm(self, task_model: TaskModel) -> Task:
        """Maps a SQLAlchemy TaskModel to a Pydantic Task instance."""
        # Map database columns to Pydantic model fields
        task_data_from_db = {
            'id': task_model.id,
            'context_id': task_model.context_id,
            'kind': task_model.kind,
            'status': task_model.status,
            'artifacts': task_model.artifacts,
            'history': task_model.history,
            'metadata': task_model.task_metadata,  # Map task_metadata column to metadata field
        }
        # Pydantic's model_validate will parse the nested dicts/lists from JSON
        return Task.model_validate(task_data_from_db)

    async def save(
        self, task: Task, context: ServerCallContext | None = None
    ) -> None:
        """Saves or updates a task in the database."""
        await self._ensure_initialized()
        db_task = self._to_orm(task)
        async with self.async_session_maker.begin() as session:
            await session.merge(db_task)
            logger.debug('Task %s saved/updated successfully.', task.id)

    async def get(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> Task | None:
        """Retrieves a task from the database by ID."""
        await self._ensure_initialized()
        async with self.async_session_maker() as session:
            stmt = select(self.task_model).where(self.task_model.id == task_id)
            result = await session.execute(stmt)
            task_model = result.scalar_one_or_none()
            if task_model:
                task = self._from_orm(task_model)
                logger.debug('Task %s retrieved successfully.', task_id)
                return task

            logger.debug('Task %s not found in store.', task_id)
            return None

    async def list(
        self, params: ListTasksParams, context: ServerCallContext | None = None
    ) -> TasksPage:
        """Retrieves all tasks from the database."""
        await self._ensure_initialized()
        async with self.async_session_maker() as session:
            timestamp_col = self.task_model.status['timestamp'].as_string()
            base_stmt = select(self.task_model)

            # Add filters
            if params.context_id:
                base_stmt = base_stmt.where(
                    self.task_model.context_id == params.context_id
                )
            if params.status and params.status != 'unknown':
                base_stmt = base_stmt.where(
                    self.task_model.status['state'].as_string()
                    == params.status.value
                )
            if params.last_updated_after:
                last_updated_after_iso = datetime.fromtimestamp(
                    params.last_updated_after / 1000, tz=timezone.utc
                ).isoformat()
                base_stmt = base_stmt.where(
                    timestamp_col >= last_updated_after_iso
                )

            # Get total count
            count_stmt = select(func.count()).select_from(base_stmt.alias())
            total_count = (await session.execute(count_stmt)).scalar_one()

            stmt = base_stmt.order_by(
                timestamp_col.desc().nulls_last(),
                self.task_model.id.desc(),
            )

            # Get paginated results
            if params.page_token:
                start_task_id = decode_page_token(params.page_token)
                start_task = (
                    await session.execute(
                        select(self.task_model).where(
                            self.task_model.id == start_task_id
                        )
                    )
                ).scalar_one_or_none()
                if not start_task:
                    raise ValueError(f'Invalid page token: {params.page_token}')
                if start_task.status.timestamp:
                    stmt = stmt.where(
                        or_(
                            and_(
                                timestamp_col == start_task.status.timestamp,
                                self.task_model.id <= start_task.id,
                            ),
                            timestamp_col < start_task.status.timestamp,
                            timestamp_col.is_(None),
                        )
                    )
                else:
                    stmt = stmt.where(
                        and_(
                            timestamp_col.is_(None),
                            self.task_model.id <= start_task.id,
                        )
                    )
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

            return TasksPage(
                tasks=tasks[:page_size],
                total_size=total_count,
                next_page_token=next_page_token,
            )

    async def delete(
        self, task_id: str, context: ServerCallContext | None = None
    ) -> None:
        """Deletes a task from the database by ID."""
        await self._ensure_initialized()

        async with self.async_session_maker.begin() as session:
            stmt = delete(self.task_model).where(self.task_model.id == task_id)
            result = await session.execute(stmt)
            # Commit is automatic when using session.begin()

            if result.rowcount > 0:
                logger.info('Task %s deleted successfully.', task_id)
            else:
                logger.warning(
                    'Attempted to delete nonexistent task with id: %s', task_id
                )
