import os

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from _pytest.mark.structures import ParameterSet


# Skip entire test module if SQLAlchemy is not installed
pytest.importorskip('sqlalchemy', reason='Database tests require SQLAlchemy')

# Now safe to import SQLAlchemy-dependent modules
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.inspection import inspect

from a2a.server.models import Base, TaskModel  # Important: To get Base.metadata
from a2a.server.tasks.database_task_store import DatabaseTaskStore
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
)


# DSNs for different databases
SQLITE_TEST_DSN = (
    'sqlite+aiosqlite:///file:testdb?mode=memory&cache=shared&uri=true'
)
POSTGRES_TEST_DSN = os.environ.get(
    'POSTGRES_TEST_DSN'
)  # e.g., "postgresql+asyncpg://user:pass@host:port/dbname"
MYSQL_TEST_DSN = os.environ.get(
    'MYSQL_TEST_DSN'
)  # e.g., "mysql+aiomysql://user:pass@host:port/dbname"

# Parameterization for the db_store fixture
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


# Minimal Task object for testing - remains the same
task_status_submitted = TaskStatus(
    state=TaskState.submitted, timestamp='2023-01-01T00:00:00Z'
)
MINIMAL_TASK_OBJ = Task(
    id='task-abc',
    context_id='session-xyz',
    status=task_status_submitted,
    kind='task',
    metadata={'test_key': 'test_value'},
    artifacts=[],
    history=[],
)


@pytest_asyncio.fixture(params=DB_CONFIGS)
async def db_store_parameterized(
    request,
) -> AsyncGenerator[DatabaseTaskStore, None]:
    """
    Fixture that provides a DatabaseTaskStore connected to different databases
    based on parameterization (SQLite, PostgreSQL, MySQL).
    """
    db_url, dialect_name = request.param

    if db_url is None:
        pytest.skip(f'DSN for {dialect_name} not set in environment variables.')

    engine = create_async_engine(db_url)
    store = None  # Initialize store to None for the finally block

    try:
        # Create tables
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # create_table=False as we've explicitly created tables above.
        store = DatabaseTaskStore(engine=engine, create_table=False)
        # Initialize the store (connects, etc.). Safe to call even if tables exist.
        await store.initialize()

        yield store

    finally:
        if engine:  # If engine was created for setup/teardown
            # Drop tables using the fixture's engine
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()  # Dispose the engine created in the fixture


@pytest.mark.asyncio
async def test_initialize_creates_table(
    db_store_parameterized: DatabaseTaskStore,
) -> None:
    """Test that tables are created (implicitly by fixture setup)."""
    # Ensure store is initialized (already done by fixture, but good for clarity)
    await db_store_parameterized._ensure_initialized()

    # Use the store's engine for inspection
    async with db_store_parameterized.engine.connect() as conn:

        def has_table_sync(sync_conn):
            inspector = inspect(sync_conn)
            return inspector.has_table(TaskModel.__tablename__)

        assert await conn.run_sync(has_table_sync)


@pytest.mark.asyncio
async def test_save_task(db_store_parameterized: DatabaseTaskStore) -> None:
    """Test saving a task to the DatabaseTaskStore."""
    task_to_save = MINIMAL_TASK_OBJ.model_copy(deep=True)
    # Ensure unique ID for parameterized tests if needed, or rely on table isolation
    task_to_save.id = (
        f'save-task-{db_store_parameterized.engine.url.drivername}'
    )
    await db_store_parameterized.save(task_to_save)

    retrieved_task = await db_store_parameterized.get(task_to_save.id)
    assert retrieved_task is not None
    assert retrieved_task.id == task_to_save.id
    assert retrieved_task.model_dump() == task_to_save.model_dump()
    await db_store_parameterized.delete(task_to_save.id)  # Cleanup


@pytest.mark.asyncio
async def test_get_task(db_store_parameterized: DatabaseTaskStore) -> None:
    """Test retrieving a task from the DatabaseTaskStore."""
    task_id = f'get-test-task-{db_store_parameterized.engine.url.drivername}'
    task_to_save = MINIMAL_TASK_OBJ.model_copy(update={'id': task_id})
    await db_store_parameterized.save(task_to_save)

    retrieved_task = await db_store_parameterized.get(task_to_save.id)
    assert retrieved_task is not None
    assert retrieved_task.id == task_to_save.id
    assert retrieved_task.context_id == task_to_save.context_id
    assert retrieved_task.status.state == TaskState.submitted
    await db_store_parameterized.delete(task_to_save.id)  # Cleanup


@pytest.mark.asyncio
async def test_get_nonexistent_task(
    db_store_parameterized: DatabaseTaskStore,
) -> None:
    """Test retrieving a nonexistent task."""
    retrieved_task = await db_store_parameterized.get('nonexistent-task-id')
    assert retrieved_task is None


@pytest.mark.asyncio
async def test_delete_task(db_store_parameterized: DatabaseTaskStore) -> None:
    """Test deleting a task from the DatabaseTaskStore."""
    task_id = f'delete-test-task-{db_store_parameterized.engine.url.drivername}'
    task_to_save_and_delete = MINIMAL_TASK_OBJ.model_copy(
        update={'id': task_id}
    )
    await db_store_parameterized.save(task_to_save_and_delete)

    assert (
        await db_store_parameterized.get(task_to_save_and_delete.id) is not None
    )
    await db_store_parameterized.delete(task_to_save_and_delete.id)
    assert await db_store_parameterized.get(task_to_save_and_delete.id) is None


@pytest.mark.asyncio
async def test_delete_nonexistent_task(
    db_store_parameterized: DatabaseTaskStore,
) -> None:
    """Test deleting a nonexistent task. Should not error."""
    await db_store_parameterized.delete('nonexistent-delete-task-id')


@pytest.mark.asyncio
async def test_save_and_get_detailed_task(
    db_store_parameterized: DatabaseTaskStore,
) -> None:
    """Test saving and retrieving a task with more fields populated."""
    task_id = f'detailed-task-{db_store_parameterized.engine.url.drivername}'
    test_task = Task(
        id=task_id,
        context_id='test-session-1',
        status=TaskStatus(
            state=TaskState.working, timestamp='2023-01-01T12:00:00Z'
        ),
        kind='task',
        metadata={'key1': 'value1', 'key2': 123},
        artifacts=[
            Artifact(
                artifact_id='artifact-1',
                parts=[Part(root=TextPart(text='hello'))],
            )
        ],
        history=[
            Message(
                message_id='msg-1',
                role=Role.user,
                parts=[Part(root=TextPart(text='user input'))],
            )
        ],
    )

    await db_store_parameterized.save(test_task)
    retrieved_task = await db_store_parameterized.get(test_task.id)

    assert retrieved_task is not None
    assert retrieved_task.id == test_task.id
    assert retrieved_task.context_id == test_task.context_id
    assert retrieved_task.status.state == TaskState.working
    assert retrieved_task.status.timestamp == '2023-01-01T12:00:00Z'
    assert retrieved_task.metadata == {'key1': 'value1', 'key2': 123}

    # Pydantic models handle their own serialization for comparison if model_dump is used
    assert (
        retrieved_task.model_dump()['artifacts']
        == test_task.model_dump()['artifacts']
    )
    assert (
        retrieved_task.model_dump()['history']
        == test_task.model_dump()['history']
    )

    await db_store_parameterized.delete(test_task.id)
    assert await db_store_parameterized.get(test_task.id) is None


@pytest.mark.asyncio
async def test_update_task(db_store_parameterized: DatabaseTaskStore) -> None:
    """Test updating an existing task."""
    task_id = f'update-test-task-{db_store_parameterized.engine.url.drivername}'
    original_task = Task(
        id=task_id,
        context_id='session-update',
        status=TaskStatus(
            state=TaskState.submitted, timestamp='2023-01-02T10:00:00Z'
        ),
        kind='task',
        metadata=None,  # Explicitly None
        artifacts=[],
        history=[],
    )
    await db_store_parameterized.save(original_task)

    retrieved_before_update = await db_store_parameterized.get(task_id)
    assert retrieved_before_update is not None
    assert retrieved_before_update.status.state == TaskState.submitted
    assert retrieved_before_update.metadata is None

    updated_task = original_task.model_copy(deep=True)
    updated_task.status.state = TaskState.completed
    updated_task.status.timestamp = '2023-01-02T11:00:00Z'
    updated_task.metadata = {'update_key': 'update_value'}

    await db_store_parameterized.save(updated_task)

    retrieved_after_update = await db_store_parameterized.get(task_id)
    assert retrieved_after_update is not None
    assert retrieved_after_update.status.state == TaskState.completed
    assert retrieved_after_update.metadata == {'update_key': 'update_value'}

    await db_store_parameterized.delete(task_id)


@pytest.mark.asyncio
async def test_metadata_field_mapping(
    db_store_parameterized: DatabaseTaskStore,
) -> None:
    """Test that metadata field is correctly mapped between Pydantic and SQLAlchemy.

    This test verifies:
    1. Metadata can be None
    2. Metadata can be a simple dict
    3. Metadata can contain nested structures
    4. Metadata is correctly saved and retrieved
    5. The mapping between task.metadata and task_metadata column works
    """
    # Test 1: Task with no metadata (None)
    task_no_metadata = Task(
        id='task-metadata-test-1',
        context_id='session-meta-1',
        status=TaskStatus(state=TaskState.submitted),
        kind='task',
        metadata=None,
    )
    await db_store_parameterized.save(task_no_metadata)
    retrieved_no_metadata = await db_store_parameterized.get(
        'task-metadata-test-1'
    )
    assert retrieved_no_metadata is not None
    assert retrieved_no_metadata.metadata is None

    # Test 2: Task with simple metadata
    simple_metadata = {'key': 'value', 'number': 42, 'boolean': True}
    task_simple_metadata = Task(
        id='task-metadata-test-2',
        context_id='session-meta-2',
        status=TaskStatus(state=TaskState.working),
        kind='task',
        metadata=simple_metadata,
    )
    await db_store_parameterized.save(task_simple_metadata)
    retrieved_simple = await db_store_parameterized.get('task-metadata-test-2')
    assert retrieved_simple is not None
    assert retrieved_simple.metadata == simple_metadata

    # Test 3: Task with complex nested metadata
    complex_metadata = {
        'level1': {
            'level2': {
                'level3': ['a', 'b', 'c'],
                'numeric': 3.14159,
            },
            'array': [1, 2, {'nested': 'value'}],
        },
        'special_chars': 'Hello\nWorld\t!',
        'unicode': '🚀 Unicode test 你好',
        'null_value': None,
    }
    task_complex_metadata = Task(
        id='task-metadata-test-3',
        context_id='session-meta-3',
        status=TaskStatus(state=TaskState.completed),
        kind='task',
        metadata=complex_metadata,
    )
    await db_store_parameterized.save(task_complex_metadata)
    retrieved_complex = await db_store_parameterized.get('task-metadata-test-3')
    assert retrieved_complex is not None
    assert retrieved_complex.metadata == complex_metadata

    # Test 4: Update metadata from None to dict
    task_update_metadata = Task(
        id='task-metadata-test-4',
        context_id='session-meta-4',
        status=TaskStatus(state=TaskState.submitted),
        kind='task',
        metadata=None,
    )
    await db_store_parameterized.save(task_update_metadata)

    # Update metadata
    task_update_metadata.metadata = {'updated': True, 'timestamp': '2024-01-01'}
    await db_store_parameterized.save(task_update_metadata)

    retrieved_updated = await db_store_parameterized.get('task-metadata-test-4')
    assert retrieved_updated is not None
    assert retrieved_updated.metadata == {
        'updated': True,
        'timestamp': '2024-01-01',
    }

    # Test 5: Update metadata from dict to None
    task_update_metadata.metadata = None
    await db_store_parameterized.save(task_update_metadata)

    retrieved_none = await db_store_parameterized.get('task-metadata-test-4')
    assert retrieved_none is not None
    assert retrieved_none.metadata is None

    # Cleanup
    await db_store_parameterized.delete('task-metadata-test-1')
    await db_store_parameterized.delete('task-metadata-test-2')
    await db_store_parameterized.delete('task-metadata-test-3')
    await db_store_parameterized.delete('task-metadata-test-4')


# Ensure aiosqlite, asyncpg, and aiomysql are installed in the test environment (added to pyproject.toml).
