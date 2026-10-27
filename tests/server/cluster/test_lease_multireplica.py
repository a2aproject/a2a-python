import asyncio

import pytest

from a2a.server.cluster import StoredTask, TaskVersion, VersionedTaskStore
from a2a.server.cluster.recovery import (
    ABANDONED_TASK_REASON,
    fail_abandoned_tasks,
)
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event
from a2a.server.tasks.push_notification_sender import PushNotificationSender
from a2a.types.a2a_pb2 import (
    CancelTaskRequest,
    GetTaskRequest,
    ListTasksRequest,
    ListTasksResponse,
    SubscribeToTaskRequest,
    Task,
    TaskState,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import UnsupportedOperationError

from .conftest import (
    ControlledAgent,
    InMemoryTaskLeaseStore,
    InputRequiredThenCompleteAgent,
    build_send_request,
    make_replica,
    wait_for_state,
)


class _SeverableStore(VersionedTaskStore):
    """One replica's handle on a shared store, which can lose its connection."""

    def __init__(self, store: VersionedTaskStore) -> None:
        self._store = store
        self.severed = False

    def _check(self) -> None:
        if self.severed:
            raise ConnectionError('database unreachable')

    async def save(
        self,
        task: Task,
        *,
        event: Event | None,
        prev: Task | None,
        prev_version: TaskVersion,
        context: ServerCallContext,
    ) -> TaskVersion:
        self._check()
        return await self._store.save(
            task,
            event=event,
            prev=prev,
            prev_version=prev_version,
            context=context,
        )

    async def get(
        self, task_id: str, context: ServerCallContext
    ) -> StoredTask | None:
        self._check()
        return await self._store.get(task_id, context)

    async def list(
        self, params: ListTasksRequest, context: ServerCallContext
    ) -> ListTasksResponse:
        self._check()
        return await self._store.list(params, context)

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        self._check()
        await self._store.delete(task_id, context)


class _TrackedAgent(ControlledAgent):
    """`ControlledAgent` that counts its runs and notes being cancelled."""

    def __init__(self) -> None:
        super().__init__()
        self.runs = 0
        self.cancelled = asyncio.Event()

    async def execute(self, context, event_queue) -> None:  # noqa: ANN001
        self.runs += 1
        try:
            await super().execute(context, event_queue)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class _RecordingPushSender(PushNotificationSender):
    def __init__(self) -> None:
        self.sent: list[tuple[str, int]] = []

    async def send_notification(self, task_id, event) -> None:  # noqa: ANN001
        self.sent.append((task_id, event.status.state))


async def _start_turn(replica, agent: ControlledAgent, store, context):  # noqa: ANN001, ANN202
    """Starts a turn on `replica` and waits until the agent reports WORKING."""
    events: list = []

    async def collect() -> None:
        async for event in replica.on_message_send_stream(
            build_send_request('go', message_id='m1'), context
        ):
            events.append(event)

    stream = asyncio.create_task(collect())
    await asyncio.wait_for(agent.working.wait(), timeout=5)
    task_id = next(iter(replica._active_task_registry._active_tasks))  # noqa: SLF001
    await wait_for_state(store, task_id, TaskState.TASK_STATE_WORKING, context)
    return stream, task_id, events


async def _crash(replica, task_id: str, store: _SeverableStore, leases) -> None:  # noqa: ANN001
    """Stops a replica the way a killed process would: nothing more is written."""
    store.severed = True
    leases.unreachable = True
    active = await replica._active_task_registry.get(task_id)  # noqa: SLF001
    tasks = [
        t
        for t in (
            active._producer_task,  # noqa: SLF001
            active._consumer_task,  # noqa: SLF001
            active._heartbeat_task,  # noqa: SLF001
        )
        if t is not None
    ]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _cancel(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _wait_until_released(
    leases: InMemoryTaskLeaseStore, task_id: str
) -> None:
    """A turn's lease is released once the consumer has saved its last event,
    which can be just after the response for that turn was returned."""
    for _ in range(200):
        if not leases.held(task_id):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f'lease of {task_id} was not released')


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_follow_up_while_another_replica_runs_the_turn_is_rejected(
    shared_store, shared_stream, context
) -> None:
    leases = InMemoryTaskLeaseStore()
    agent = _TrackedAgent()
    replica_a = make_replica(
        shared_store, shared_stream, agent, lease_store=leases
    )
    replica_b = make_replica(
        shared_store, shared_stream, agent, lease_store=leases.view()
    )
    try:
        stream, task_id, _ = await _start_turn(
            replica_a, agent, shared_store, context
        )
        stored = await shared_store.get(task_id, context)
        assert stored is not None

        with pytest.raises(
            UnsupportedOperationError, match='processed by another replica'
        ):
            await replica_b.on_message_send(
                build_send_request(
                    'again',
                    task_id=task_id,
                    context_id=stored.task.context_id,
                    message_id='m2',
                ),
                context,
            )

        assert agent.runs == 1
        rejected = await shared_store.get(task_id, context)
        assert rejected is not None
        assert rejected.task.status.state == TaskState.TASK_STATE_WORKING

        agent.release.set()
        await asyncio.wait_for(stream, timeout=5)
        final = await shared_store.get(task_id, context)
        assert final is not None
        assert final.task.status.state == TaskState.TASK_STATE_COMPLETED
        await _wait_until_released(leases, task_id)
    finally:
        await replica_a.aclose()
        await replica_b.aclose()


async def _wait_until_unregistered(replica, task_id: str) -> None:  # noqa: ANN001
    for _ in range(200):
        if await replica._active_task_registry.get(task_id) is None:  # noqa: SLF001
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f'{task_id} is still registered')


@pytest.mark.asyncio
@pytest.mark.timeout(15)
@pytest.mark.parametrize('interrupted_task_timeout', [None, 60.0])
async def test_rejected_follow_up_leaves_no_local_task_behind(
    shared_store, shared_stream, context, interrupted_task_timeout
) -> None:
    """After rejecting a follow-up, B must not keep a local task: a later
    subscription on B would tap it and never see A's events."""
    leases = InMemoryTaskLeaseStore()
    agent = _TrackedAgent()
    replica_a = make_replica(
        shared_store, shared_stream, agent, lease_store=leases
    )
    replica_b = make_replica(
        shared_store,
        shared_stream,
        agent,
        lease_store=leases.view(),
        interrupted_task_timeout=interrupted_task_timeout,
    )
    try:
        stream, task_id, _ = await _start_turn(
            replica_a, agent, shared_store, context
        )
        stored = await shared_store.get(task_id, context)
        assert stored is not None
        with pytest.raises(UnsupportedOperationError):
            await replica_b.on_message_send(
                build_send_request(
                    'again',
                    task_id=task_id,
                    context_id=stored.task.context_id,
                    message_id='m2',
                ),
                context,
            )
        await _wait_until_unregistered(replica_b, task_id)

        states: list = []

        async def follow() -> None:
            async for event in replica_b.on_subscribe_to_task(
                SubscribeToTaskRequest(id=task_id), context
            ):
                if isinstance(event, Task | TaskStatusUpdateEvent):
                    states.append(event.status.state)

        follower = asyncio.create_task(follow())
        await asyncio.sleep(0.05)
        agent.release.set()
        await asyncio.wait_for(stream, timeout=5)
        await asyncio.wait_for(follower, timeout=5)
        assert states[-1] == TaskState.TASK_STATE_COMPLETED
        assert agent.runs == 1
    finally:
        await replica_a.aclose()
        await replica_b.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_task_released_while_waiting_for_input_holds_no_lease(
    shared_store, shared_stream, context
) -> None:
    leases = InMemoryTaskLeaseStore()
    replica = make_replica(
        shared_store,
        shared_stream,
        InputRequiredThenCompleteAgent(),
        lease_store=leases,
        interrupted_task_timeout=0.05,
    )
    try:
        first = await replica.on_message_send(
            build_send_request('q1', message_id='m1'), context
        )
        assert first.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        await _wait_until_unregistered(replica, first.id)
        assert not leases.held(first.id)

        leases.clock.advance(120)
        assert await fail_abandoned_tasks(leases, shared_store) == 0

        resumed = await replica.on_message_send(
            build_send_request(
                'a1',
                task_id=first.id,
                context_id=first.context_id,
                message_id='m2',
            ),
            context,
        )
        assert resumed.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        await _wait_until_released(leases, first.id)
    finally:
        await replica.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_multiturn_across_replicas_releases_each_turns_lease(
    shared_store, shared_stream, context
) -> None:
    leases = InMemoryTaskLeaseStore()
    agent = InputRequiredThenCompleteAgent()
    replica_a = make_replica(
        shared_store, shared_stream, agent, lease_store=leases
    )
    replica_b = make_replica(
        shared_store, shared_stream, agent, lease_store=leases.view()
    )
    try:
        first = await replica_a.on_message_send(
            build_send_request('q1', message_id='m1'), context
        )
        assert first.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        await _wait_until_released(leases, first.id)

        for replica, message_id in ((replica_b, 'm2'), (replica_a, 'm3')):
            result = await replica.on_message_send(
                build_send_request(
                    'answer',
                    task_id=first.id,
                    context_id=first.context_id,
                    message_id=message_id,
                ),
                context,
            )
            await _wait_until_released(leases, first.id)
        assert result.status.state == TaskState.TASK_STATE_COMPLETED
    finally:
        await replica_a.aclose()
        await replica_b.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_canceled_turn_releases_its_lease(
    shared_store, shared_stream, context
) -> None:
    leases = InMemoryTaskLeaseStore()
    agent = _TrackedAgent()
    replica = make_replica(
        shared_store, shared_stream, agent, lease_store=leases
    )
    try:
        stream, task_id, _ = await _start_turn(
            replica, agent, shared_store, context
        )
        assert leases.held(task_id)

        await replica.on_cancel_task(CancelTaskRequest(id=task_id), context)
        await _cancel(stream)

        await _wait_until_released(leases, task_id)
    finally:
        await replica.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_lease_left_behind_by_finished_turn_does_not_fail_it(
    shared_store, shared_stream, context
) -> None:
    """A lease that could not be released at the end of a turn expires, and
    the sweep that claims it leaves the finished task as it is."""
    leases = InMemoryTaskLeaseStore()
    agent = _TrackedAgent()
    leases_a = leases.view()
    replica = make_replica(
        shared_store, shared_stream, agent, lease_store=leases_a
    )
    try:
        stream, task_id, _ = await _start_turn(
            replica, agent, shared_store, context
        )
        leases_a.unreachable = True
        agent.release.set()
        await asyncio.wait_for(stream, timeout=5)
        await wait_for_state(
            shared_store, task_id, TaskState.TASK_STATE_COMPLETED, context
        )

        leases.clock.advance(2)
        assert await fail_abandoned_tasks(leases, shared_store) == 0

        final = await shared_store.get(task_id, context)
        assert final is not None
        assert final.task.status.state == TaskState.TASK_STATE_COMPLETED
        assert not leases.held(task_id)
    finally:
        leases_a.unreachable = False
        await replica.aclose()


class _SlowReleaseLeases(InMemoryTaskLeaseStore):
    """Releases take a moment, like a database round trip."""

    async def release(self, lease) -> None:  # noqa: ANN001
        await asyncio.sleep(0.05)
        await super().release(lease)

    def view(self) -> InMemoryTaskLeaseStore:
        other = _SlowReleaseLeases(self.clock, self._owner_resolver)
        other._leases = self._leases  # noqa: SLF001
        return other


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_follow_up_sent_as_soon_as_input_is_requested_is_accepted(
    shared_store, shared_stream, context
) -> None:
    """A returns INPUT_REQUIRED a moment before it releases the lease; a
    follow-up that reaches B in between waits for the lease."""
    leases = _SlowReleaseLeases()
    agent = InputRequiredThenCompleteAgent()
    replica_a = make_replica(
        shared_store, shared_stream, agent, lease_store=leases
    )
    replica_b = make_replica(
        shared_store, shared_stream, agent, lease_store=leases.view()
    )
    try:
        first = await replica_a.on_message_send(
            build_send_request('q1', message_id='m1'), context
        )
        assert first.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert leases.held(first.id)

        await replica_b.on_message_send(
            build_send_request(
                'a1',
                task_id=first.id,
                context_id=first.context_id,
                message_id='m2',
            ),
            context,
        )
        stored = await shared_store.get(first.id, context)
        assert stored is not None
        assert 'm2' in {m.message_id for m in stored.task.history}
    finally:
        await replica_a.aclose()
        await replica_b.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_follow_up_to_waiting_task_with_a_stuck_lease_is_rejected(
    shared_store, shared_stream, context
) -> None:
    leases = InMemoryTaskLeaseStore()
    agent = InputRequiredThenCompleteAgent()
    replica = make_replica(
        shared_store, shared_stream, agent, lease_store=leases
    )
    try:
        first = await replica.on_message_send(
            build_send_request('q1', message_id='m1'), context
        )
        await _wait_until_released(leases, first.id)
        # Held by a replica that died after saving INPUT_REQUIRED.
        assert await leases.acquire(first.id, context, 60) is not None

        with pytest.raises(UnsupportedOperationError):
            await replica.on_message_send(
                build_send_request(
                    'a1',
                    task_id=first.id,
                    context_id=first.context_id,
                    message_id='m2',
                ),
                context,
            )
    finally:
        await replica.aclose()


class _FailingReleaseLeases(InMemoryTaskLeaseStore):
    """A replica's view of `leases` whose releases fail."""

    def __init__(self, leases: InMemoryTaskLeaseStore) -> None:
        super().__init__(leases.clock)
        self._leases = leases._leases  # noqa: SLF001

    async def release(self, lease) -> None:  # noqa: ANN001
        raise ConnectionError('lease store unreachable')


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_lease_left_behind_by_turn_waiting_for_input_does_not_fail_it(
    shared_store, shared_stream, context
) -> None:
    leases = InMemoryTaskLeaseStore()
    replica = make_replica(
        shared_store,
        shared_stream,
        InputRequiredThenCompleteAgent(),
        lease_store=_FailingReleaseLeases(leases),
    )
    try:
        first = await replica.on_message_send(
            build_send_request('q1', message_id='m1'), context
        )
        assert first.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        await asyncio.sleep(0.05)

        leases.clock.advance(2)
        assert await fail_abandoned_tasks(leases, shared_store) == 0

        stored = await shared_store.get(first.id, context)
        assert stored is not None
        assert stored.task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        assert not leases.held(first.id)
    finally:
        await replica.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_task_waiting_for_input_is_never_recovered(
    shared_store, shared_stream, context
) -> None:
    leases = InMemoryTaskLeaseStore()
    replica = make_replica(
        shared_store,
        shared_stream,
        InputRequiredThenCompleteAgent(),
        lease_store=leases,
    )
    try:
        result = await replica.on_message_send(
            build_send_request('q1', message_id='m1'), context
        )
        assert result.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        await _wait_until_released(leases, result.id)

        leases.clock.advance(120)
        assert await fail_abandoned_tasks(leases, shared_store) == 0

        stored = await shared_store.get(result.id, context)
        assert stored is not None
        assert stored.task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    finally:
        await replica.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_task_of_crashed_replica_is_failed_after_its_lease_expires(
    shared_store, shared_stream, context
) -> None:
    leases = InMemoryTaskLeaseStore()
    agent = _TrackedAgent()
    store_a = _SeverableStore(shared_store)
    leases_a = leases.view()
    replica_a = make_replica(
        store_a, shared_stream, agent, lease_store=leases_a
    )
    replica_b = make_replica(shared_store, shared_stream, agent)
    push_sender = _RecordingPushSender()
    try:
        stream, task_id, _ = await _start_turn(
            replica_a, agent, shared_store, context
        )
        b_states: list = []

        async def follow_on_b() -> None:
            async for event in replica_b.on_subscribe_to_task(
                SubscribeToTaskRequest(id=task_id), context
            ):
                b_states.append(event.status.state)

        follow = asyncio.create_task(follow_on_b())
        await asyncio.sleep(0.05)

        await _crash(replica_a, task_id, store_a, leases_a)
        await _cancel(stream)

        assert await fail_abandoned_tasks(leases, shared_store) == 0
        leases.clock.advance(2)
        assert (
            await fail_abandoned_tasks(
                leases,
                shared_store,
                push_sender=push_sender,
                event_stream=shared_stream,
            )
            == 1
        )

        await asyncio.wait_for(follow, timeout=5)
        assert b_states[-1] == TaskState.TASK_STATE_FAILED
        task = await replica_b.on_get_task(GetTaskRequest(id=task_id), context)
        assert task is not None
        assert task.status.state == TaskState.TASK_STATE_FAILED
        assert task.status.message.parts[0].text == ABANDONED_TASK_REASON
        assert push_sender.sent == [(task_id, TaskState.TASK_STATE_FAILED)]
        assert not leases.held(task_id)
    finally:
        await replica_b.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_replica_that_lost_its_lease_stops_its_agent(
    shared_store, shared_stream, context
) -> None:
    """A replica cut off from the lease store is recovered as abandoned. When
    it reconnects, its renewal fails and it stops; its late events are
    dropped, so the task stays FAILED."""
    leases = InMemoryTaskLeaseStore()
    agent = _TrackedAgent()
    leases_a = leases.view()
    replica_a = make_replica(
        shared_store, shared_stream, agent, lease_store=leases_a
    )
    try:
        stream, task_id, _ = await _start_turn(
            replica_a, agent, shared_store, context
        )

        leases_a.unreachable = True
        await asyncio.sleep(0.05)
        leases.clock.advance(2)
        assert await fail_abandoned_tasks(leases, shared_store) == 1
        leases_a.unreachable = False

        await asyncio.wait_for(agent.cancelled.wait(), timeout=5)
        await asyncio.wait_for(stream, timeout=5)
        final = await shared_store.get(task_id, context)
        assert final is not None
        assert final.task.status.state == TaskState.TASK_STATE_FAILED
    finally:
        await replica_a.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_turn_cut_short_in_process_is_recovered(
    shared_store, shared_stream, context
) -> None:
    """Closing without failing in-flight turns must not drop the lease of a
    task left WORKING, or nothing could recover it."""
    leases = InMemoryTaskLeaseStore()
    agent = _TrackedAgent()
    replica_a = make_replica(
        shared_store, shared_stream, agent, lease_store=leases
    )
    stream, task_id, _ = await _start_turn(
        replica_a, agent, shared_store, context
    )

    await replica_a.aclose(fail_in_flight=False)
    await _cancel(stream)
    stored = await shared_store.get(task_id, context)
    assert stored is not None
    assert stored.task.status.state == TaskState.TASK_STATE_WORKING

    leases.clock.advance(2)
    assert await fail_abandoned_tasks(leases, shared_store) == 1
    final = await shared_store.get(task_id, context)
    assert final is not None
    assert final.task.status.state == TaskState.TASK_STATE_FAILED


@pytest.mark.asyncio
@pytest.mark.timeout(15)
async def test_graceful_close_fails_running_turns_only(
    shared_store, shared_stream, context
) -> None:
    leases = InMemoryTaskLeaseStore()
    push_sender = _RecordingPushSender()
    waiting = make_replica(
        shared_store,
        shared_stream,
        InputRequiredThenCompleteAgent(),
        lease_store=leases,
    )
    agent = _TrackedAgent()
    running = make_replica(
        shared_store,
        shared_stream,
        agent,
        push_sender=push_sender,
        lease_store=leases.view(),
    )
    observer = make_replica(shared_store, shared_stream, agent)
    try:
        paused = await waiting.on_message_send(
            build_send_request('q1', message_id='p1'), context
        )
        stream, task_id, _ = await _start_turn(
            running, agent, shared_store, context
        )
        states: list = []

        async def follow() -> None:
            async for event in observer.on_subscribe_to_task(
                SubscribeToTaskRequest(id=task_id), context
            ):
                states.append(event.status.state)

        follower = asyncio.create_task(follow())
        await asyncio.sleep(0.05)

        await waiting.aclose()
        await running.aclose()
        await _cancel(stream)
        await asyncio.wait_for(follower, timeout=5)

        failed = await shared_store.get(task_id, context)
        assert failed is not None
        assert failed.task.status.state == TaskState.TASK_STATE_FAILED
        assert 'shut down' in failed.task.status.message.parts[0].text
        assert states[-1] == TaskState.TASK_STATE_FAILED
        assert push_sender.sent[-1] == (task_id, TaskState.TASK_STATE_FAILED)
        still_paused = await shared_store.get(paused.id, context)
        assert still_paused is not None
        assert (
            still_paused.task.status.state
            == TaskState.TASK_STATE_INPUT_REQUIRED
        )
    finally:
        await observer.aclose()
