import os
from datetime import datetime, timezone

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

from _pytest.mark.structures import ParameterSet
from a2a.types.a2a_pb2 import ListTasksRequest


# Skip entire test module if SQLAlchemy is not installed
pytest.importorskip('sqlalchemy', reason='Database tests require SQLAlchemy')

# Now safe to import SQLAlchemy-dependent modules
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.inspection import inspect

from google.protobuf.json_format import MessageToDict

from a2a.server.models import Base, TaskModel  # Important: To get Base.metadata
from a2a.server.tasks.database_task_store import DatabaseTaskStore
from a2a.types.a2a_pb2 import (
    Artifact,
    ListTasksRequest,
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.auth.user import User
from a2a.server.context import ServerCallContext
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE


class TestUser(User):
    """A test implementation of the User interface."""

    def __init__(self, user_name: str):
        self._user_name = user_name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._user_name


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
task_status_submitted = TaskStatus(state=TaskState.TASK_STATE_SUBMITTED)
MINIMAL_TASK_OBJ = Task(
    id='task-abc',
    context_id='session-xyz',
    status=task_status_submitted,
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
    # Create a copy of the minimal task with a unique ID
    task_to_save = Task()
    task_to_save.CopyFrom(MINIMAL_TASK_OBJ)
    # Ensure unique ID for parameterized tests if needed, or rely on table isolation
    task_to_save.id = (
        f'save-task-{db_store_parameterized.engine.url.drivername}'
    )
    await db_store_parameterized.save(task_to_save)

    retrieved_task = await db_store_parameterized.get(task_to_save.id)
    assert retrieved_task is not None
    assert retrieved_task.id == task_to_save.id
    assert MessageToDict(retrieved_task) == MessageToDict(task_to_save)
    await db_store_parameterized.delete(task_to_save.id)  # Cleanup


@pytest.mark.asyncio
async def test_get_task(db_store_parameterized: DatabaseTaskStore) -> None:
    """Test retrieving a task from the DatabaseTaskStore."""
    task_id = f'get-test-task-{db_store_parameterized.engine.url.drivername}'
    task_to_save = Task()
    task_to_save.CopyFrom(MINIMAL_TASK_OBJ)
    task_to_save.id = task_id
    await db_store_parameterized.save(task_to_save)

    retrieved_task = await db_store_parameterized.get(task_to_save.id)
    assert retrieved_task is not None
    assert retrieved_task.id == task_to_save.id
    assert retrieved_task.context_id == task_to_save.context_id
    assert retrieved_task.status.state == TaskState.TASK_STATE_SUBMITTED
    await db_store_parameterized.delete(task_to_save.id)  # Cleanup


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'params, expected_ids, total_count, next_page_token',
    [
        # No parameters, should return all tasks
        (
            ListTasksRequest(),
            ['task-2', 'task-1', 'task-0', 'task-4', 'task-3'],
            5,
            None,
        ),
        # Unknown context
        (
            ListTasksRequest(context_id='nonexistent'),
            [],
            0,
            None,
        ),
        # Pagination (first page)
        (
            ListTasksRequest(page_size=2),
            ['task-2', 'task-1'],
            5,
            'dGFzay0w',  # base64 for 'task-0'
        ),
        # Pagination (same timestamp)
        (
            ListTasksRequest(
                page_size=2,
                page_token='dGFzay0x',  # base64 for 'task-1'
            ),
            ['task-1', 'task-0'],
            5,
            'dGFzay00',  # base64 for 'task-4'
        ),
        # Pagination (final page)
        (
            ListTasksRequest(
                page_size=2,
                page_token='dGFzay0z',  # base64 for 'task-3'
            ),
            ['task-3'],
            5,
            None,
        ),
        # Filtering by context_id
        (
            ListTasksRequest(context_id='context-1'),
            ['task-1', 'task-3'],
            2,
            None,
        ),
        # Filtering by status
        (
            ListTasksRequest(status=TaskState.TASK_STATE_WORKING),
            ['task-1', 'task-3'],
            2,
            None,
        ),
        # Combined filtering (context_id and status)
        (
            ListTasksRequest(
                context_id='context-0', status=TaskState.TASK_STATE_SUBMITTED
            ),
            ['task-2', 'task-0'],
            2,
            None,
        ),
        # Combined filtering and pagination
        (
            ListTasksRequest(
                context_id='context-0',
                page_size=1,
            ),
            ['task-2'],
            3,
            'dGFzay0w',  # base64 for 'task-0'
        ),
    ],
)
async def test_list_tasks(
    db_store_parameterized: DatabaseTaskStore,
    params: ListTasksRequest,
    expected_ids: list[str],
    total_count: int,
    next_page_token: str,
) -> None:
    """Test listing tasks with various filters and pagination."""
    tasks_to_create = [
        Task(
            id='task-0',
            context_id='context-0',
            status=TaskStatus(
                state=TaskState.TASK_STATE_SUBMITTED,
                timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            ),
        ),
        Task(
            id='task-1',
            context_id='context-1',
            status=TaskStatus(
                state=TaskState.TASK_STATE_WORKING,
                timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            ),
        ),
        Task(
            id='task-2',
            context_id='context-0',
            status=TaskStatus(
                state=TaskState.TASK_STATE_SUBMITTED,
                timestamp=datetime(2025, 1, 2, tzinfo=timezone.utc),
            ),
        ),
        Task(
            id='task-3',
            context_id='context-1',
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        ),
        Task(
            id='task-4',
            context_id='context-0',
            status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        ),
    ]
    for task in tasks_to_create:
        await db_store_parameterized.save(task)

    page = await db_store_parameterized.list(params)

    retrieved_ids = [task.id for task in page.tasks]
    assert retrieved_ids == expected_ids
    assert page.total_size == total_count
    assert page.next_page_token == (next_page_token or '')
    assert page.page_size == (params.page_size or DEFAULT_LIST_TASKS_PAGE_SIZE)

    # Cleanup
    for task in tasks_to_create:
        await db_store_parameterized.delete(task.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'params, expected_error_message',
    [
        (
            ListTasksRequest(
                page_size=2,
                page_token='invalid',
            ),
            'Token is not a valid base64-encoded cursor.',
        ),
        (
            ListTasksRequest(
                page_size=2,
                page_token='dGFzay0xMDA=',  # base64 for 'task-100'
            ),
            'Invalid page token: dGFzay0xMDA=',
        ),
    ],
)
async def test_list_tasks_fails(
    db_store_parameterized: DatabaseTaskStore,
    params: ListTasksRequest,
    expected_error_message: str,
) -> None:
    """Test listing tasks with invalid parameters that should fail."""
    tasks_to_create = [
        Task(
            id='task-0',
            context_id='context-0',
            status=TaskStatus(
                state=TaskState.TASK_STATE_SUBMITTED,
                timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            ),
        ),
        Task(
            id='task-1',
            context_id='context-1',
            status=TaskStatus(
                state=TaskState.TASK_STATE_WORKING,
                timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            ),
        ),
    ]
    for task in tasks_to_create:
        await db_store_parameterized.save(task)

    with pytest.raises(ValueError) as excinfo:
        await db_store_parameterized.list(params)

    assert expected_error_message in str(excinfo.value)

    # Cleanup
    for task in tasks_to_create:
        await db_store_parameterized.delete(task.id)


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
    task_to_save_and_delete = Task()
    task_to_save_and_delete.CopyFrom(MINIMAL_TASK_OBJ)
    task_to_save_and_delete.id = task_id
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
    test_timestamp = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    test_task = Task(
        id=task_id,
        context_id='test-session-1',
        status=TaskStatus(
            state=TaskState.TASK_STATE_WORKING, timestamp=test_timestamp
        ),
        metadata={'key1': 'value1', 'key2': 123},
        artifacts=[
            Artifact(
                artifact_id='artifact-1',
                parts=[Part(text='hello')],
            )
        ],
        history=[
            Message(
                message_id='msg-1',
                role=Role.ROLE_USER,
                parts=[Part(text='user input')],
            )
        ],
    )

    await db_store_parameterized.save(test_task)
    retrieved_task = await db_store_parameterized.get(test_task.id)

    assert retrieved_task is not None
    assert retrieved_task.id == test_task.id
    assert retrieved_task.context_id == test_task.context_id
    assert retrieved_task.status.state == TaskState.TASK_STATE_WORKING
    # Compare timestamps - proto Timestamp has ToDatetime() method
    assert (
        retrieved_task.status.timestamp.ToDatetime()
        == test_timestamp.replace(tzinfo=None)
    )
    assert dict(retrieved_task.metadata) == {'key1': 'value1', 'key2': 123}

    # Use MessageToDict for proto serialization comparisons
    assert (
        MessageToDict(retrieved_task)['artifacts']
        == MessageToDict(test_task)['artifacts']
    )
    assert (
        MessageToDict(retrieved_task)['history']
        == MessageToDict(test_task)['history']
    )

    await db_store_parameterized.delete(test_task.id)
    assert await db_store_parameterized.get(test_task.id) is None


@pytest.mark.asyncio
async def test_update_task(db_store_parameterized: DatabaseTaskStore) -> None:
    """Test updating an existing task."""
    task_id = f'update-test-task-{db_store_parameterized.engine.url.drivername}'
    original_timestamp = datetime(2023, 1, 2, 10, 0, 0, tzinfo=timezone.utc)
    original_task = Task(
        id=task_id,
        context_id='session-update',
        status=TaskStatus(
            state=TaskState.TASK_STATE_SUBMITTED, timestamp=original_timestamp
        ),
        # Proto metadata is a Struct, can't be None - leave empty
        artifacts=[],
        history=[],
    )
    await db_store_parameterized.save(original_task)

    retrieved_before_update = await db_store_parameterized.get(task_id)
    assert retrieved_before_update is not None
    assert (
        retrieved_before_update.status.state == TaskState.TASK_STATE_SUBMITTED
    )
    assert (
        len(retrieved_before_update.metadata) == 0
    )  # Proto map is empty, not None

    updated_timestamp = datetime(2023, 1, 2, 11, 0, 0, tzinfo=timezone.utc)
    updated_task = Task()
    updated_task.CopyFrom(original_task)
    updated_task.status.state = TaskState.TASK_STATE_COMPLETED
    updated_task.status.timestamp.FromDatetime(updated_timestamp)
    updated_task.metadata['update_key'] = 'update_value'

    await db_store_parameterized.save(updated_task)

    retrieved_after_update = await db_store_parameterized.get(task_id)
    assert retrieved_after_update is not None
    assert retrieved_after_update.status.state == TaskState.TASK_STATE_COMPLETED
    assert dict(retrieved_after_update.metadata) == {
        'update_key': 'update_value'
    }

    await db_store_parameterized.delete(task_id)


@pytest.mark.asyncio
async def test_metadata_field_mapping(
    db_store_parameterized: DatabaseTaskStore,
) -> None:
    """Test that metadata field is correctly mapped between Proto and SQLAlchemy.

    This test verifies:
    1. Metadata can be empty (proto Struct can't be None)
    2. Metadata can be a simple dict
    3. Metadata can contain nested structures
    4. Metadata is correctly saved and retrieved
    5. The mapping between task.metadata and task_metadata column works
    """
    # Test 1: Task with no metadata (empty Struct in proto)
    task_no_metadata = Task(
        id='task-metadata-test-1',
        context_id='session-meta-1',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )
    await db_store_parameterized.save(task_no_metadata)
    retrieved_no_metadata = await db_store_parameterized.get(
        'task-metadata-test-1'
    )
    assert retrieved_no_metadata is not None
    # Proto Struct is empty, not None
    assert len(retrieved_no_metadata.metadata) == 0

    # Test 2: Task with simple metadata
    simple_metadata = {'key': 'value', 'number': 42, 'boolean': True}
    task_simple_metadata = Task(
        id='task-metadata-test-2',
        context_id='session-meta-2',
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        metadata=simple_metadata,
    )
    await db_store_parameterized.save(task_simple_metadata)
    retrieved_simple = await db_store_parameterized.get('task-metadata-test-2')
    assert retrieved_simple is not None
    assert dict(retrieved_simple.metadata) == simple_metadata

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
    }
    task_complex_metadata = Task(
        id='task-metadata-test-3',
        context_id='session-meta-3',
        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
        metadata=complex_metadata,
    )
    await db_store_parameterized.save(task_complex_metadata)
    retrieved_complex = await db_store_parameterized.get('task-metadata-test-3')
    assert retrieved_complex is not None
    # Convert proto Struct to dict for comparison
    retrieved_meta = MessageToDict(retrieved_complex.metadata)
    assert retrieved_meta == complex_metadata

    # Test 4: Update metadata from empty to dict
    task_update_metadata = Task(
        id='task-metadata-test-4',
        context_id='session-meta-4',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )
    await db_store_parameterized.save(task_update_metadata)

    # Update metadata
    task_update_metadata.metadata['updated'] = True
    task_update_metadata.metadata['timestamp'] = '2024-01-01'
    await db_store_parameterized.save(task_update_metadata)

    retrieved_updated = await db_store_parameterized.get('task-metadata-test-4')
    assert retrieved_updated is not None
    assert dict(retrieved_updated.metadata) == {
        'updated': True,
        'timestamp': '2024-01-01',
    }

    # Test 5: Clear metadata (set to empty)
    task_update_metadata.metadata.Clear()
    await db_store_parameterized.save(task_update_metadata)

    retrieved_none = await db_store_parameterized.get('task-metadata-test-4')
    assert retrieved_none is not None
    assert len(retrieved_none.metadata) == 0

    # Cleanup
    await db_store_parameterized.delete('task-metadata-test-1')
    await db_store_parameterized.delete('task-metadata-test-2')
    await db_store_parameterized.delete('task-metadata-test-3')
    await db_store_parameterized.delete('task-metadata-test-4')


@pytest.mark.asyncio
async def test_owner_resource_scoping(
    db_store_parameterized: DatabaseTaskStore,
) -> None:
    """Test that operations are scoped to the correct owner."""
    task_store = db_store_parameterized

    context_user1 = ServerCallContext(user=TestUser(user_name='user1'))
    context_user2 = ServerCallContext(user=TestUser(user_name='user2'))
    context_user3 = ServerCallContext(user=TestUser(user_name='user3')) # user with no tasks

    # Create tasks for different owners
    task1_user1, task2_user1, task1_user2 = Task(), Task(), Task()
    task1_user1.CopyFrom(MINIMAL_TASK_OBJ)
    task1_user1.id = 'u1-task1'
    task2_user1.CopyFrom(MINIMAL_TASK_OBJ)
    task2_user1.id = 'u1-task2'
    task1_user2.CopyFrom(MINIMAL_TASK_OBJ)
    task1_user2.id = 'u2-task1'

    await task_store.save(task1_user1, context_user1)
    await task_store.save(task2_user1, context_user1)
    await task_store.save(task1_user2, context_user2)

    # Test GET
    assert await task_store.get('u1-task1', context_user1) is not None
    assert await task_store.get('u1-task1', context_user2) is None
    assert await task_store.get('u2-task1', context_user1) is None
    assert await task_store.get('u2-task1', context_user2) is not None

    # Test LIST
    params = ListTasksRequest()
    page_user1 = await task_store.list(params, context_user1)
    assert len(page_user1.tasks) == 2
    assert {t.id for t in page_user1.tasks} == {'u1-task1', 'u1-task2'}
    assert page_user1.total_size == 2

    page_user2 = await task_store.list(params, context_user2)
    assert len(page_user2.tasks) == 1
    assert {t.id for t in page_user2.tasks} == {'u2-task1'}
    assert page_user2.total_size == 1

    page_user3 = await task_store.list(params, context_user3)
    assert len(page_user3.tasks) == 0
    assert page_user3.total_size == 0

    # Test DELETE
    await task_store.delete('u1-task1', context_user2)  # Should not delete
    assert await task_store.get('u1-task1', context_user1) is not None

    await task_store.delete('u1-task1', context_user1)  # Should delete
    assert await task_store.get('u1-task1', context_user1) is None

    # Cleanup remaining tasks
    await task_store.delete('u1-task2', context_user1)
    await task_store.delete('u2-task1', context_user2)


# Ensure aiosqlite, asyncpg, and aiomysql are installed in the test environment (added to pyproject.toml).
