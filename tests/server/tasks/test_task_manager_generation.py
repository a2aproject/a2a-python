"""TaskManager assigns `generation`: 1 at creation, +1 on every write."""

import asyncio

import pytest

from a2a.server.cluster import (
    ConcurrentTaskModificationError,
    StoredTask,
    TaskVersion,
    VersionedTaskStore,
)
from a2a.server.context import ServerCallContext
from a2a.server.tasks import InMemoryTaskStore, TaskManager
from a2a.server.tasks.task_change_notifier import TaskChangeNotifier
from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskMessageUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


def _manager(store=None, **kwargs) -> TaskManager:
    return TaskManager(
        task_store=store or InMemoryTaskStore(),
        context=ServerCallContext(),
        task_id='t-1',
        context_id='c-1',
        initial_message=None,
        **kwargs,
    )


def _status(state: TaskState, generation: int = 0) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id='t-1',
        context_id='c-1',
        status=TaskStatus(state=state),
        generation=generation,
    )


def _artifact() -> TaskArtifactUpdateEvent:
    return TaskArtifactUpdateEvent(
        task_id='t-1',
        context_id='c-1',
        artifact=Artifact(artifact_id='a-1', parts=[Part(text='x')]),
    )


def _user_message(message_id: str) -> Message:
    return Message(
        message_id=message_id,
        role=Role.ROLE_USER,
        task_id='t-1',
        context_id='c-1',
        parts=[Part(text='hi')],
    )


@pytest.mark.asyncio
async def test_every_write_advances_generation_by_one() -> None:
    tm = _manager()
    events = [
        _status(TaskState.TASK_STATE_WORKING),
        _artifact(),
        _status(TaskState.TASK_STATE_INPUT_REQUIRED),
    ]
    for event in events:
        await tm.save_task_event(event)
    task = await tm.append_client_message(_user_message('m-2'))
    final = _status(TaskState.TASK_STATE_COMPLETED)
    task = await tm.save_task_event(final)

    assert [e.generation for e in events] == [1, 2, 3]
    assert final.generation == 5
    assert task is not None
    assert task.generation == 5


@pytest.mark.asyncio
async def test_task_event_starts_at_one() -> None:
    tm = _manager()
    task = Task(
        id='t-1',
        context_id='c-1',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )
    await tm.save_task_event(task)
    assert task.generation == 1


@pytest.mark.asyncio
async def test_agent_supplied_generation_is_overwritten() -> None:
    tm = _manager()
    await tm.save_task_event(_status(TaskState.TASK_STATE_WORKING))
    event = _status(TaskState.TASK_STATE_COMPLETED, generation=99)
    task = await tm.save_task_event(event)
    assert event.generation == 2
    assert task is not None
    assert task.generation == 2


@pytest.mark.asyncio
async def test_generation_is_persisted_and_continues_after_reload() -> None:
    store = InMemoryTaskStore()
    tm = _manager(store)
    await tm.save_task_event(_status(TaskState.TASK_STATE_WORKING))
    await tm.save_task_event(_artifact())

    reloaded = _manager(store)
    event = _status(TaskState.TASK_STATE_COMPLETED)
    await reloaded.save_task_event(event)

    stored = await store.get('t-1', ServerCallContext())
    assert stored is not None
    assert stored.generation == 3
    assert event.generation == 3


@pytest.mark.asyncio
async def test_client_message_is_one_change_with_its_own_event() -> None:
    class RecordingStore(InMemoryTaskStore):
        def __init__(self) -> None:
            super().__init__()
            self.saved: list[Task] = []

        async def save(self, task, context=None) -> None:
            self.saved.append(Task(generation=task.generation))
            await super().save(task, context)

    store = RecordingStore()
    tm = _manager(store)
    await tm.save_task_event(_status(TaskState.TASK_STATE_INPUT_REQUIRED))
    message = _user_message('m-2')

    task = await tm.append_client_message(message)

    assert [t.generation for t in store.saved] == [1, 2]
    assert task.history[-1] == message
    assert task.generation == 2


class _RecordingVersionedStore(VersionedTaskStore):
    """Keeps one task; can be told to reject the next save as stale."""

    def __init__(self) -> None:
        self.task: Task | None = None
        self.version = TaskVersion.MISSING
        self.events: list = []
        self.reject_next_save = False

    async def save(self, task, *, event, prev, prev_version, context):
        if self.reject_next_save:
            self.reject_next_save = False
            raise ConcurrentTaskModificationError(task.id)
        stored = Task()
        stored.CopyFrom(task)
        self.task = stored
        self.events.append(event)
        self.version = TaskVersion((prev_version._value or 0) + 1)  # noqa: SLF001
        return self.version

    async def get(self, task_id, context):
        if self.task is None:
            return None
        task = Task()
        task.CopyFrom(self.task)
        return StoredTask(task, self.version)

    async def list(self, params, context):
        raise NotImplementedError

    async def delete(self, task_id, context):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_failed_client_message_append_leaves_cache_untouched() -> None:
    store = _RecordingVersionedStore()
    tm = _manager(store)
    await tm.save_task_event(_status(TaskState.TASK_STATE_INPUT_REQUIRED))

    store.reject_next_save = True
    with pytest.raises(ConcurrentTaskModificationError):
        await tm.append_client_message(_user_message('m-2'))

    cached = await tm.get_task()
    assert cached is not None
    assert cached.generation == 1
    assert len(cached.history) == 0


@pytest.mark.asyncio
async def test_message_update_event_carries_the_generation() -> None:
    store = _RecordingVersionedStore()
    tm = _manager(store)
    await tm.save_task_event(_status(TaskState.TASK_STATE_INPUT_REQUIRED))
    await tm.append_client_message(_user_message('m-2'))

    event = store.events[-1]
    assert isinstance(event, TaskMessageUpdateEvent)
    assert event.generation == 2
    assert event.message.message_id == 'm-2'
    assert store.task is not None
    assert store.task.generation == 2


@pytest.mark.asyncio
async def test_change_notifier_fires_after_each_write() -> None:
    notifier = TaskChangeNotifier()
    tm = _manager(change_notifier=notifier)
    with notifier.watch('t-1') as changed:
        await tm.save_task_event(_status(TaskState.TASK_STATE_WORKING))
        await asyncio.wait_for(changed.wait(), timeout=1)


@pytest.mark.asyncio
async def test_default_update_retries_until_its_change_applies() -> None:
    store = _RecordingVersionedStore()
    store.task = Task(
        id='t-1',
        context_id='c-1',
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        generation=3,
    )
    store.version = TaskVersion(3)
    calls = 0

    def complete(task: Task) -> TaskStatusUpdateEvent:
        nonlocal calls
        calls += 1
        if calls == 1:
            store.reject_next_save = True
        task.status.state = TaskState.TASK_STATE_COMPLETED
        task.generation += 1
        return _status(TaskState.TASK_STATE_COMPLETED, task.generation)

    stored = await store.update('t-1', complete, context=ServerCallContext())

    assert calls == 2
    assert stored is not None
    assert stored.task.generation == 4
    assert stored.task.status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
async def test_default_update_skips_write_when_change_returns_none() -> None:
    store = _RecordingVersionedStore()
    assert (
        await store.update(
            't-1', lambda task: None, context=ServerCallContext()
        )
        is None
    )
    store.task = Task(id='t-1', generation=2)
    store.version = TaskVersion(2)
    stored = await store.update(
        't-1', lambda task: None, context=ServerCallContext()
    )
    assert stored is not None
    assert stored.version == TaskVersion(2)
    assert store.events == []


@pytest.mark.asyncio
async def test_read_overlapping_invalidate_still_returns_the_task() -> None:
    store = _RecordingVersionedStore()
    store.task = Task(id='t-1', context_id='c-1', generation=4)
    store.version = TaskVersion(4)
    tm = _manager(store)
    read_started = asyncio.Event()
    finish_read = asyncio.Event()
    original_get = store.get
    calls = 0

    async def slow_first_get(task_id, context):
        nonlocal calls
        calls += 1
        if calls == 1:
            read_started.set()
            await finish_read.wait()
        return await original_get(task_id, context)

    store.get = slow_first_get  # type: ignore[method-assign]
    reader = asyncio.create_task(tm.get_task())
    await read_started.wait()
    tm.invalidate()
    finish_read.set()

    task = await reader
    assert task is not None
    assert task.generation == 4
    assert calls == 2


def test_notify_ignores_a_waiter_whose_loop_closed() -> None:
    notifier = TaskChangeNotifier()
    loop = asyncio.new_event_loop()

    async def register() -> None:
        notifier.watch('t-1').__enter__()

    loop.run_until_complete(register())
    loop.close()
    notifier.notify('t-1')
