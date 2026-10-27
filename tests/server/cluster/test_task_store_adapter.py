"""Tests for `LegacyTaskStoreAdapter` and the `VersionedTaskStore` contract."""

import pytest

from a2a.auth.user import User
from a2a.server.cluster import (
    ConcurrentTaskModificationError,
    LegacyTaskStoreAdapter,
    TaskVersion,
    VersionedTaskStore,
)
from a2a.server.context import ServerCallContext
from a2a.server.tasks import InMemoryTaskStore
from a2a.types.a2a_pb2 import (
    ListTasksRequest,
    Task,
    TaskState,
    TaskStatus,
)


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


TEST_CONTEXT = ServerCallContext(user=SampleUser('test_user'))


def create_minimal_task(
    task_id: str = 'task-abc', context_id: str = 'session-xyz'
) -> Task:
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )


def make_adapter() -> LegacyTaskStoreAdapter:
    return LegacyTaskStoreAdapter(InMemoryTaskStore())


def test_concurrent_modification_error_carries_task_id() -> None:
    err = ConcurrentTaskModificationError('task-abc')
    assert err.task_id == 'task-abc'
    assert 'task-abc' in str(err)
    assert isinstance(err, Exception)


def test_adapter_is_a_versioned_task_store() -> None:
    assert isinstance(make_adapter(), VersionedTaskStore)


def test_store_property_exposes_wrapped_store() -> None:
    store = InMemoryTaskStore()
    adapter = LegacyTaskStoreAdapter(store)
    assert adapter.store is store


@pytest.mark.asyncio
async def test_save_returns_missing_version() -> None:
    adapter = make_adapter()
    task = create_minimal_task()
    version = await adapter.save(
        task,
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    assert version.is_missing


@pytest.mark.asyncio
async def test_get_returns_task_and_missing_version() -> None:
    adapter = make_adapter()
    task = create_minimal_task()
    await adapter.save(
        task,
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )

    stored = await adapter.get('task-abc', TEST_CONTEXT)
    assert stored is not None
    assert stored.task == task
    assert stored.version.is_missing


@pytest.mark.asyncio
async def test_get_missing_task_returns_none() -> None:
    adapter = make_adapter()
    assert await adapter.get('does-not-exist', TEST_CONTEXT) is None


@pytest.mark.asyncio
async def test_save_never_raises_on_stale_prev_version() -> None:
    # An unversioned store performs no CAS: a "stale" prev_version is ignored
    # and the write succeeds (last-writer-wins), matching today's behaviour.
    adapter = make_adapter()
    task = create_minimal_task()
    await adapter.save(
        task,
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    # Save again with a deliberately non-matching, non-missing prev_version.
    updated = create_minimal_task()
    updated.status.state = TaskState.TASK_STATE_WORKING
    version = await adapter.save(
        updated,
        event=None,
        prev=task,
        prev_version=TaskVersion(999),
        context=TEST_CONTEXT,
    )
    assert version.is_missing
    stored = await adapter.get('task-abc', TEST_CONTEXT)
    assert stored is not None
    assert stored.task.status.state == TaskState.TASK_STATE_WORKING


@pytest.mark.asyncio
async def test_delete_delegates_to_inner() -> None:
    adapter = make_adapter()
    task = create_minimal_task()
    await adapter.save(
        task,
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=TEST_CONTEXT,
    )
    await adapter.delete('task-abc', TEST_CONTEXT)
    assert await adapter.get('task-abc', TEST_CONTEXT) is None


@pytest.mark.asyncio
async def test_list_delegates_to_inner() -> None:
    adapter = make_adapter()
    for i in range(3):
        await adapter.save(
            create_minimal_task(task_id=f'task-{i}'),
            event=None,
            prev=None,
            prev_version=TaskVersion.MISSING,
            context=TEST_CONTEXT,
        )
    response = await adapter.list(ListTasksRequest(), TEST_CONTEXT)
    assert {t.id for t in response.tasks} == {'task-0', 'task-1', 'task-2'}
