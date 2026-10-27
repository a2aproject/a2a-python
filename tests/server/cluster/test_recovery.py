import asyncio

import pytest

from a2a.server.cluster import ConcurrentTaskModificationError, TaskVersion
from a2a.server.cluster.recovery import (
    ABANDONED_TASK_REASON,
    AbandonedTaskSweeper,
    context_for_owner,
    fail_abandoned_tasks,
    fail_task,
)
from a2a.server.owner_resolver import resolve_user_scope
from a2a.server.tasks.push_notification_sender import PushNotificationSender
from a2a.types.a2a_pb2 import (
    Message,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from .conftest import (
    InMemoryTaskEventStream,
    InMemoryTaskLeaseStore,
    VersionedInMemoryTaskStore,
    make_context,
)


ALICE = make_context('alice')


class _RecordingPushSender(PushNotificationSender):
    def __init__(self) -> None:
        self.sent: list[tuple[str, int]] = []

    async def send_notification(self, task_id, event) -> None:  # noqa: ANN001
        self.sent.append((task_id, event.status.state))


class _RaisingPushSender(PushNotificationSender):
    async def send_notification(self, task_id, event) -> None:  # noqa: ANN001
        raise RuntimeError('push failed')


class _ConflictingStore(VersionedInMemoryTaskStore):
    """Rejects the first `conflicts` saves, as if another writer won them."""

    def __init__(self, conflicts: int) -> None:
        super().__init__()
        self.conflicts = conflicts

    async def save(self, task: Task, **kwargs) -> TaskVersion:  # noqa: ANN003
        if self.conflicts:
            self.conflicts -= 1
            raise ConcurrentTaskModificationError(task.id)
        return await super().save(task, **kwargs)


async def _store_task(
    store: VersionedInMemoryTaskStore,
    state: TaskState = TaskState.TASK_STATE_WORKING,
    task_id: str = 'task-1',
) -> Task:
    task = Task(
        id=task_id,
        context_id='ctx-1',
        status=TaskStatus(
            state=state,
            message=Message(
                message_id='agent-1',
                role=Role.ROLE_AGENT,
                parts=[Part(text='thinking')],
            ),
        ),
    )
    await store.save(
        task,
        event=None,
        prev=None,
        prev_version=TaskVersion.MISSING,
        context=ALICE,
    )
    return task


def test_context_for_owner_maps_back_to_the_owner() -> None:
    assert resolve_user_scope(context_for_owner('alice')) == 'alice'
    assert context_for_owner('alice').user.is_authenticated
    assert not context_for_owner('').user.is_authenticated


@pytest.mark.asyncio
async def test_fail_task_writes_failed_and_notifies() -> None:
    store = VersionedInMemoryTaskStore()
    stream = InMemoryTaskEventStream()
    push_sender = _RecordingPushSender()
    await _store_task(store)
    subscription = stream.subscribe('task-1', after=TaskVersion.MISSING)
    next_event = asyncio.ensure_future(anext(subscription))
    await asyncio.sleep(0)

    failed = await fail_task(
        store,
        'task-1',
        ALICE,
        reason='replica gone',
        push_sender=push_sender,
        event_stream=stream,
    )

    assert failed is not None
    stored = await store.get('task-1', ALICE)
    assert stored is not None
    assert stored.task.status.state == TaskState.TASK_STATE_FAILED
    assert stored.task.status.message.parts[0].text == 'replica gone'
    assert stored.task.status.HasField('timestamp')
    assert [m.message_id for m in stored.task.history] == ['agent-1']
    published = await asyncio.wait_for(next_event, timeout=1)
    assert isinstance(published.event, TaskStatusUpdateEvent)
    assert published.event.status.state == TaskState.TASK_STATE_FAILED
    assert published.version == stored.version
    assert push_sender.sent == [('task-1', TaskState.TASK_STATE_FAILED)]
    await subscription.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'state',
    [
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_INPUT_REQUIRED,
        TaskState.TASK_STATE_AUTH_REQUIRED,
    ],
)
async def test_fail_task_leaves_a_finished_turn_alone(state: TaskState) -> None:
    store = VersionedInMemoryTaskStore()
    push_sender = _RecordingPushSender()
    await _store_task(store, state)

    result = await fail_task(
        store, 'task-1', ALICE, reason='x', push_sender=push_sender
    )

    assert result is None
    stored = await store.get('task-1', ALICE)
    assert stored is not None
    assert stored.task.status.state == state
    assert push_sender.sent == []


@pytest.mark.asyncio
async def test_fail_task_ignores_a_missing_task() -> None:
    store = VersionedInMemoryTaskStore()

    assert await fail_task(store, 'nope', ALICE, reason='x') is None


@pytest.mark.asyncio
async def test_fail_task_rereads_after_one_conflict() -> None:
    store = _ConflictingStore(conflicts=0)
    await _store_task(store)
    store.conflicts = 1

    assert await fail_task(store, 'task-1', ALICE, reason='x') is not None
    stored = await store.get('task-1', ALICE)
    assert stored is not None
    assert stored.task.status.state == TaskState.TASK_STATE_FAILED


@pytest.mark.asyncio
async def test_fail_task_gives_up_after_repeated_conflicts() -> None:
    store = _ConflictingStore(conflicts=0)
    await _store_task(store)
    store.conflicts = 2

    assert await fail_task(store, 'task-1', ALICE, reason='x') is None


@pytest.mark.asyncio
async def test_fail_task_survives_a_raising_push_sender() -> None:
    store = VersionedInMemoryTaskStore()
    await _store_task(store)

    result = await fail_task(
        store, 'task-1', ALICE, reason='x', push_sender=_RaisingPushSender()
    )

    assert result is not None


@pytest.mark.asyncio
async def test_fail_abandoned_tasks_fails_owned_task_and_releases_lease() -> (
    None
):
    store = VersionedInMemoryTaskStore()
    leases = InMemoryTaskLeaseStore()
    await _store_task(store)
    await leases.acquire('task-1', ALICE, ttl=10)
    leases.clock.advance(11)

    assert await fail_abandoned_tasks(leases, store) == 1

    stored = await store.get('task-1', ALICE)
    assert stored is not None
    assert stored.task.status.state == TaskState.TASK_STATE_FAILED
    assert stored.task.status.message.parts[0].text == ABANDONED_TASK_REASON
    assert not leases.held('task-1')
    assert await leases.claim_expired(ttl=60, limit=10) == []


@pytest.mark.asyncio
async def test_fail_abandoned_tasks_skips_live_and_finished_tasks() -> None:
    store = VersionedInMemoryTaskStore()
    leases = InMemoryTaskLeaseStore()
    await _store_task(store, TaskState.TASK_STATE_COMPLETED, 'done')
    await _store_task(store, TaskState.TASK_STATE_WORKING, 'live')
    await leases.acquire('done', ALICE, ttl=10)
    leases.clock.advance(11)
    await leases.acquire('live', ALICE, ttl=60)

    assert await fail_abandoned_tasks(leases, store) == 0

    assert not leases.held('done')
    assert leases.held('live')
    live = await store.get('live', ALICE)
    assert live is not None
    assert live.task.status.state == TaskState.TASK_STATE_WORKING


@pytest.mark.asyncio
async def test_fail_abandoned_tasks_keeps_the_claim_when_recovery_fails() -> (
    None
):
    store = VersionedInMemoryTaskStore()
    leases = InMemoryTaskLeaseStore()
    await _store_task(store)
    await leases.acquire('task-1', ALICE, ttl=10)
    leases.clock.advance(11)

    def unreachable(owner: str):  # noqa: ANN202
        raise ConnectionError('task store unreachable')

    assert (
        await fail_abandoned_tasks(
            leases, store, context_factory=unreachable, claim_ttl=30
        )
        == 0
    )
    assert leases.held('task-1')
    leases.clock.advance(31)
    assert await fail_abandoned_tasks(leases, store) == 1


@pytest.mark.asyncio
async def test_fail_abandoned_tasks_uses_the_context_factory() -> None:
    store = VersionedInMemoryTaskStore()
    leases = InMemoryTaskLeaseStore()
    await _store_task(store)
    await leases.acquire('task-1', ALICE, ttl=10)
    leases.clock.advance(11)
    owners: list[str] = []

    def factory(owner: str):  # noqa: ANN202
        owners.append(owner)
        return ALICE

    assert await fail_abandoned_tasks(leases, store, context_factory=factory)
    assert owners == ['alice']


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_sweeper_recovers_in_the_background_and_stops() -> None:
    store = VersionedInMemoryTaskStore()
    leases = InMemoryTaskLeaseStore()
    await _store_task(store)
    await leases.acquire('task-1', ALICE, ttl=10)
    leases.clock.advance(11)
    leases.unreachable = True
    sweeper = AbandonedTaskSweeper(leases, store, interval=0.01)

    sweeper.start()
    sweeper.start()
    await asyncio.sleep(0.05)
    leases.unreachable = False
    for _ in range(100):
        stored = await store.get('task-1', ALICE)
        assert stored is not None
        if stored.task.status.state == TaskState.TASK_STATE_FAILED:
            break
        await asyncio.sleep(0.01)
    await sweeper.aclose()
    await sweeper.aclose()

    assert stored.task.status.state == TaskState.TASK_STATE_FAILED
