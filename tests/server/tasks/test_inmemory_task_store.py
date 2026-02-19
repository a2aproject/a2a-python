from a2a.server.context import ServerCallContext
import pytest
from datetime import datetime, timezone

from a2a.server.tasks import InMemoryTaskStore
from a2a.types.a2a_pb2 import Task, TaskState, TaskStatus, ListTasksRequest
from a2a.utils.constants import DEFAULT_LIST_TASKS_PAGE_SIZE

from a2a.auth.user import User


class SampleUser(User):
    """A test implementation of the User interface."""

    def __init__(self, user_name: str):
        self._user_name = user_name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._user_name


def create_minimal_task(
    task_id: str = 'task-abc', context_id: str = 'session-xyz'
) -> Task:
    """Create a minimal task for testing."""
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )


@pytest.mark.asyncio
async def test_in_memory_task_store_save_and_get() -> None:
    """Test saving and retrieving a task from the in-memory store."""
    store = InMemoryTaskStore()
    task = create_minimal_task()
    await store.save(task)
    retrieved_task = await store.get('task-abc')
    assert retrieved_task == task


@pytest.mark.asyncio
async def test_in_memory_task_store_get_nonexistent() -> None:
    """Test retrieving a nonexistent task."""
    store = InMemoryTaskStore()
    retrieved_task = await store.get('nonexistent')
    assert retrieved_task is None


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
    params: ListTasksRequest,
    expected_ids: list[str],
    total_count: int,
    next_page_token: str,
) -> None:
    """Test listing tasks with various filters and pagination."""
    store = InMemoryTaskStore()
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
        await store.save(task)

    page = await store.list(params)

    retrieved_ids = [task.id for task in page.tasks]
    assert retrieved_ids == expected_ids
    assert page.total_size == total_count
    assert page.next_page_token == (next_page_token or '')
    assert page.page_size == (params.page_size or DEFAULT_LIST_TASKS_PAGE_SIZE)

    # Cleanup
    for task in tasks_to_create:
        await store.delete(task.id)


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
    params: ListTasksRequest, expected_error_message: str
) -> None:
    """Test listing tasks with invalid parameters that should fail."""
    store = InMemoryTaskStore()
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
        await store.save(task)

    with pytest.raises(ValueError) as excinfo:
        await store.list(params)

    assert expected_error_message in str(excinfo.value)

    # Cleanup
    for task in tasks_to_create:
        await store.delete(task.id)


@pytest.mark.asyncio
async def test_in_memory_task_store_delete() -> None:
    """Test deleting a task from the store."""
    store = InMemoryTaskStore()
    task = create_minimal_task()
    await store.save(task)
    await store.delete('task-abc')
    retrieved_task = await store.get('task-abc')
    assert retrieved_task is None


@pytest.mark.asyncio
async def test_in_memory_task_store_delete_nonexistent() -> None:
    """Test deleting a nonexistent task."""
    store = InMemoryTaskStore()
    await store.delete('nonexistent')


@pytest.mark.asyncio
async def test_owner_resource_scoping() -> None:
    """Test that operations are scoped to the correct owner."""
    store = InMemoryTaskStore()
    task = create_minimal_task()

    context_user1 = ServerCallContext(user=SampleUser(user_name='user1'))
    context_user2 = ServerCallContext(user=SampleUser(user_name='user2'))

    # Create tasks for different owners
    task1_user1 = Task()
    task1_user1.CopyFrom(task)
    task1_user1.id = 'u1-task1'

    task2_user1 = Task()
    task2_user1.CopyFrom(task)
    task2_user1.id = 'u1-task2'

    task1_user2 = Task()
    task1_user2.CopyFrom(task)
    task1_user2.id = 'u2-task1'

    await store.save(task1_user1, context_user1)
    await store.save(task2_user1, context_user1)
    await store.save(task1_user2, context_user2)

    # Test GET
    assert await store.get('u1-task1', context_user1) is not None
    assert await store.get('u1-task1', context_user2) is None
    assert await store.get('u2-task1', context_user1) is None
    assert await store.get('u2-task1', context_user2) is not None

    # Test LIST
    params = ListTasksRequest()
    page_user1 = await store.list(params, context_user1)
    assert len(page_user1.tasks) == 2
    assert {t.id for t in page_user1.tasks} == {'u1-task1', 'u1-task2'}
    assert page_user1.total_size == 2

    page_user2 = await store.list(params, context_user2)
    assert len(page_user2.tasks) == 1
    assert {t.id for t in page_user2.tasks} == {'u2-task1'}
    assert page_user2.total_size == 1

    # Test DELETE
    await store.delete('u1-task1', context_user2)  # Should not delete
    assert await store.get('u1-task1', context_user1) is not None

    await store.delete('u1-task1', context_user1)  # Should delete
    assert await store.get('u1-task1', context_user1) is None

    # Cleanup remaining tasks
    await store.delete('u1-task2', context_user1)
    await store.delete('u2-task1', context_user2)
