import asyncio
import logging

from unittest.mock import AsyncMock

import pytest

from a2a.auth.user import User
from a2a.server.agent_execution.active_task_registry import ActiveTaskRegistry
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue_v2 import EventQueue
from a2a.server.tasks import InMemoryTaskStore, TaskStore
from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import InvalidParamsError, TaskNotFoundError


class _SlowExecutor(AgentExecutor):
    """An executor whose execute() blocks until cancelled."""

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        await asyncio.sleep(10)

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        return None


class _RecordingInputRequiredExecutor(AgentExecutor):
    """Records the task snapshot used for a resumed request."""

    def __init__(self) -> None:
        self.seen_tasks: list[Task] = []

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        if context.current_task is not None:
            task = Task()
            task.CopyFrom(context.current_task)
            self.seen_tasks.append(task)

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id or '',
                context_id=context.context_id or '',
                status=TaskStatus(
                    state=TaskState.TASK_STATE_INPUT_REQUIRED,
                ),
            )
        )

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        return None


class _PausedArtifactExecutor(AgentExecutor):
    """Keeps an artifact stream open after publishing its first chunk."""

    def __init__(self) -> None:
        self.release_append = asyncio.Event()

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        task_id = context.task_id or ''
        context_id = context.context_id or ''
        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                artifact=Artifact(
                    artifact_id='streamed-artifact',
                    parts=[Part(text='first chunk')],
                ),
            )
        )
        await self.release_append.wait()
        await event_queue.enqueue_event(
            TaskArtifactUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                artifact=Artifact(
                    artifact_id='streamed-artifact',
                    parts=[Part(text='second chunk')],
                ),
                append=True,
            )
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(
                    state=TaskState.TASK_STATE_INPUT_REQUIRED,
                ),
            )
        )

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        return None


class _QueuedResumeExecutor(AgentExecutor):
    """Records snapshots for a request queued behind a streaming request."""

    def __init__(self) -> None:
        self.calls = 0
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()
        self.second_started = asyncio.Event()
        self.seen_tasks: list[Task] = []

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        self.calls += 1
        if context.current_task is not None:
            task = Task()
            task.CopyFrom(context.current_task)
            self.seen_tasks.append(task)

        task_id = context.task_id or ''
        context_id = context.context_id or ''
        if self.calls == 1:
            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    artifact=Artifact(
                        artifact_id='first-request-artifact',
                        parts=[Part(text='first request')],
                    ),
                )
            )
            self.first_started.set()
            await self.release_first.wait()
        else:
            self.second_started.set()

        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(
                    state=TaskState.TASK_STATE_INPUT_REQUIRED,
                ),
            )
        )

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        return None


def _request_context(
    task_id: str,
    context_id: str,
    call_context: ServerCallContext,
    message_id: str,
    text: str,
) -> RequestContext:
    return RequestContext(
        call_context=call_context,
        request=SendMessageRequest(
            message=Message(
                task_id=task_id,
                context_id=context_id,
                message_id=message_id,
                role=Role.ROLE_USER,
                parts=[Part(text=text)],
            )
        ),
        task_id=task_id,
        context_id=context_id,
    )


def _make_registry() -> ActiveTaskRegistry:
    return ActiveTaskRegistry(
        agent_executor=_SlowExecutor(),
        task_store=InMemoryTaskStore(),
    )


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_aclose_reaps_active_tasks_and_empties_registry():
    """aclose() reaps background tasks and removes them."""
    registry = _make_registry()
    active = await registry.get_or_create(
        'task-1',
        call_context=ServerCallContext(),
        create_task_if_missing=True,
    )

    await registry.aclose()

    assert active._producer_task is not None
    assert active._producer_task.done()
    assert active._consumer_task is not None
    assert active._consumer_task.done()
    assert await registry.get('task-1') is None


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_aclose_is_idempotent():
    """Calling aclose() repeatedly is a safe no-op."""
    registry = _make_registry()
    await registry.get_or_create(
        'task-1',
        call_context=ServerCallContext(),
        create_task_if_missing=True,
    )

    await registry.aclose()
    await registry.aclose()


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_aclose_on_empty_registry():
    """aclose() with no active tasks returns immediately."""
    registry = _make_registry()
    await registry.aclose()


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_get_or_create_rejected_after_aclose():
    """A closed registry refuses to create new tasks (no orphan race)."""
    registry = _make_registry()
    await registry.aclose()

    with pytest.raises(RuntimeError):
        await registry.get_or_create(
            'task-1',
            call_context=ServerCallContext(),
            create_task_if_missing=True,
        )


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_aclose_logs_and_swallows_task_errors(caplog):
    """A failing ActiveTask.aclose is logged, not propagated."""
    registry = _make_registry()
    failing = AsyncMock()
    failing.aclose = AsyncMock(side_effect=ValueError('boom'))
    registry._active_tasks['bad'] = failing

    with caplog.at_level(logging.ERROR):
        await registry.aclose()

    assert 'Error draining active task' in caplog.text


@pytest.mark.timeout(5)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    'keep_passive_subscriber',
    [False, True],
    ids=['no-subscriber', 'passive-subscriber'],
)
async def test_reused_idle_active_task_refreshes_shared_store_snapshot(
    keep_passive_subscriber: bool,
):
    """A resumed request uses data persisted by another registry instance."""
    task_id = 'shared-task'
    context_id = 'shared-context'
    call_context = ServerCallContext()
    task_store = InMemoryTaskStore()
    await task_store.save(
        Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(
                state=TaskState.TASK_STATE_INPUT_REQUIRED,
                message=Message(
                    message_id='initial-status',
                    role=Role.ROLE_AGENT,
                    parts=[Part(text='initial status')],
                ),
            ),
        ),
        call_context,
    )

    replica_a_executor = _RecordingInputRequiredExecutor()
    registry_a = ActiveTaskRegistry(replica_a_executor, task_store)
    registry_b = ActiveTaskRegistry(_SlowExecutor(), task_store)
    passive_stream = None

    try:
        active_a = await registry_a.get_or_create(
            task_id,
            call_context=call_context,
            create_task_if_missing=False,
        )
        active_b = await registry_b.get_or_create(
            task_id,
            call_context=call_context,
            create_task_if_missing=False,
        )
        if keep_passive_subscriber:
            passive_stream = active_a.subscribe(include_initial_task=True)
            initial_task = await anext(passive_stream)
            assert isinstance(initial_task, Task)

        task_from_b = await active_b.get_task()
        task_from_b.artifacts.append(
            Artifact(
                artifact_id='replica-b-artifact',
                parts=[Part(text='persisted by replica B')],
            )
        )
        task_from_b.history.append(
            Message(
                message_id='replica-a-resume',
                role=Role.ROLE_USER,
                parts=[Part(text='resume from replica A')],
            )
        )
        task_from_b.status.CopyFrom(
            TaskStatus(
                state=TaskState.TASK_STATE_INPUT_REQUIRED,
                message=Message(
                    message_id='replica-b-status',
                    role=Role.ROLE_AGENT,
                    parts=[Part(text='replica B needs input')],
                ),
            )
        )
        await active_b._task_manager.save_task_event(task_from_b)

        active_a = await registry_a.get_or_create(
            task_id,
            call_context=call_context,
            create_task_if_missing=False,
        )

        request_context = _request_context(
            task_id,
            context_id,
            call_context,
            'replica-a-resume',
            'resume from replica A',
        )
        events = [
            event async for event in active_a.subscribe(request=request_context)
        ]

        assert events
        assert replica_a_executor.seen_tasks
        assert (
            replica_a_executor.seen_tasks[0].artifacts[0].artifact_id
            == 'replica-b-artifact'
        )
        assert (
            replica_a_executor.seen_tasks[0].status.message.message_id
            == 'replica-b-status'
        )

        persisted_task = await task_store.get(task_id, call_context)
        assert persisted_task is not None
        assert persisted_task.artifacts[0].artifact_id == 'replica-b-artifact'
        assert {message.message_id for message in persisted_task.history} >= {
            'replica-b-status',
            'replica-a-resume',
        }
        assert (
            sum(
                message.message_id == 'replica-a-resume'
                for message in persisted_task.history
            )
            == 1
        )
    finally:
        if passive_stream is not None:
            await passive_stream.aclose()
        await registry_a.aclose()
        await registry_b.aclose()


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_reused_task_rejects_terminal_passive_subscription():
    """A passive subscriber rejects a terminal snapshot from another replica."""
    task_id = 'terminal-task'
    context_id = 'terminal-context'
    call_context = ServerCallContext()
    task_store = InMemoryTaskStore()
    await task_store.save(
        Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
        ),
        call_context,
    )
    executor = _RecordingInputRequiredExecutor()
    registry = ActiveTaskRegistry(executor, task_store)

    try:
        active = await registry.get_or_create(
            task_id,
            call_context=call_context,
            create_task_if_missing=False,
        )
        await task_store.save(
            Task(
                id=task_id,
                context_id=context_id,
                artifacts=[
                    Artifact(
                        artifact_id='completed-artifact',
                        parts=[Part(text='completed elsewhere')],
                    )
                ],
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            ),
            call_context,
        )
        await registry.get_or_create(
            task_id,
            call_context=call_context,
            create_task_if_missing=False,
        )

        with pytest.raises(InvalidParamsError, match='terminal state'):
            await anext(active.subscribe(include_initial_task=True))

        assert not executor.seen_tasks
        persisted_task = await task_store.get(task_id, call_context)
        assert persisted_task is not None
        assert persisted_task.status.state == TaskState.TASK_STATE_COMPLETED
        assert persisted_task.artifacts[0].artifact_id == 'completed-artifact'
    finally:
        await registry.aclose()


@pytest.mark.timeout(5)
@pytest.mark.asyncio
@pytest.mark.parametrize(
    'stored_state',
    [
        TaskState.TASK_STATE_CANCELED,
        None,
    ],
    ids=['terminal', 'missing'],
)
async def test_reused_task_does_not_cancel_unavailable_snapshot(
    stored_state: TaskState | None,
):
    """Cancellation honors terminal and missing authoritative snapshots."""
    task_id = 'terminal-cancel-task'
    context_id = 'terminal-cancel-context'
    call_context = ServerCallContext()
    task_store = InMemoryTaskStore()
    await task_store.save(
        Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
        ),
        call_context,
    )
    executor = AsyncMock(spec=AgentExecutor)

    async def cancel(context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id or '',
                context_id=context.context_id or '',
                status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
            )
        )

    executor.cancel.side_effect = cancel
    registry = ActiveTaskRegistry(executor, task_store)

    try:
        active = await registry.get_or_create(
            task_id,
            call_context=call_context,
            create_task_if_missing=False,
        )
        if stored_state is None:
            await task_store.delete(task_id, call_context)
            with pytest.raises(TaskNotFoundError):
                await registry.get_or_create(
                    task_id,
                    call_context=call_context,
                    create_task_if_missing=False,
                )
        else:
            await task_store.save(
                Task(
                    id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=stored_state),
                ),
                call_context,
            )
            await registry.get_or_create(
                task_id,
                call_context=call_context,
                create_task_if_missing=False,
            )

        if stored_state is None:
            with pytest.raises(TaskNotFoundError):
                await active.cancel(call_context)
        else:
            result = await active.cancel(call_context)
            assert result.status.state == stored_state

        executor.cancel.assert_not_awaited()
        persisted_task = await task_store.get(task_id, call_context)
        if stored_state is None:
            assert persisted_task is None
        else:
            assert persisted_task is not None
            assert persisted_task.status.state == stored_state
    finally:
        await registry.aclose()


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_reused_task_does_not_recreate_missing_snapshot():
    """A task removed from the authoritative store is not resurrected."""
    task_id = 'removed-task'
    context_id = 'removed-context'
    call_context = ServerCallContext()
    task_store = InMemoryTaskStore()
    await task_store.save(
        Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
        ),
        call_context,
    )
    executor = _RecordingInputRequiredExecutor()
    registry = ActiveTaskRegistry(executor, task_store)

    try:
        active = await registry.get_or_create(
            task_id,
            call_context=call_context,
            create_task_if_missing=False,
        )
        await task_store.delete(task_id, call_context)
        with pytest.raises(TaskNotFoundError):
            await registry.get_or_create(
                task_id,
                call_context=call_context,
                create_task_if_missing=False,
            )

        with pytest.raises(TaskNotFoundError):
            async for _ in active.subscribe(
                request=_request_context(
                    task_id,
                    context_id,
                    call_context,
                    'late-resume',
                    'must not recreate',
                )
            ):
                pass

        assert not executor.seen_tasks
        assert await task_store.get(task_id, call_context) is None
    finally:
        await registry.aclose()


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_reuse_preserves_snapshot_during_background_artifact_stream():
    """A request boundary cannot refresh an artifact stream still in flight."""
    task_id = 'streaming-task'
    context_id = 'streaming-context'
    call_context = ServerCallContext()
    task_store = InMemoryTaskStore()
    await task_store.save(
        Task(
            id=task_id,
            context_id=context_id,
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        ),
        call_context,
    )

    executor = _PausedArtifactExecutor()
    registry = ActiveTaskRegistry(executor, task_store)
    active = await registry.get_or_create(
        task_id,
        call_context=call_context,
        create_task_if_missing=False,
    )
    request_context = _request_context(
        task_id,
        context_id,
        call_context,
        'streaming-request',
        'start streaming',
    )
    stream = active.subscribe(request=request_context)

    try:
        first_event = await anext(stream)
        assert isinstance(first_event, TaskArtifactUpdateEvent)
        await stream.aclose()
        assert active._reference_count == 1
        assert active._request_lock.locked()

        # Model a stale concurrent replica replacing the persisted snapshot
        # while this replica still owns an open artifact stream.
        await task_store.save(
            Task(
                id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            ),
            call_context,
        )
        reused = await registry.get_or_create(
            task_id,
            call_context=call_context,
            create_task_if_missing=False,
        )
        assert reused is active

        executor.release_append.set()
        request_finished = asyncio.create_task(active._request_lock.acquire())
        consumer_finished = asyncio.create_task(active._is_finished.wait())
        done, pending = await asyncio.wait(
            {request_finished, consumer_finished},
            timeout=1,
            return_when=asyncio.FIRST_COMPLETED,
        )
        assert done
        if request_finished in done:
            request_finished.result()
            active._request_lock.release()
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        persisted_task = await task_store.get(task_id, call_context)
        assert persisted_task is not None
        assert not active._is_finished.is_set()
        assert [part.text for part in persisted_task.artifacts[0].parts] == [
            'first chunk',
            'second chunk',
        ]
    finally:
        executor.release_append.set()
        await stream.aclose()
        await registry.aclose()


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_idle_refresh_wins_over_inflight_passive_snapshot_read():
    """A stale passive read cannot repopulate the cache after invalidation."""
    task_id = 'racing-read-task'
    context_id = 'racing-read-context'
    call_context = ServerCallContext()
    latest_task = Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
    )
    passive_get_started = asyncio.Event()
    release_passive_get = asyncio.Event()
    get_count = 0

    async def get_task(
        requested_task_id: str, context: ServerCallContext
    ) -> Task:
        nonlocal get_count
        assert requested_task_id == task_id
        assert context is call_context
        get_count += 1
        task = Task()
        task.CopyFrom(latest_task)
        if get_count == 3:
            passive_get_started.set()
            await release_passive_get.wait()
        return task

    async def save_task(task: Task, context: ServerCallContext) -> None:
        nonlocal latest_task
        assert context is call_context
        latest_task = Task()
        latest_task.CopyFrom(task)

    task_store = AsyncMock(spec=TaskStore)
    task_store.get.side_effect = get_task
    task_store.save.side_effect = save_task
    registry = ActiveTaskRegistry(_SlowExecutor(), task_store)
    active = await registry.get_or_create(
        task_id,
        call_context=call_context,
        create_task_if_missing=False,
    )
    await registry.get_or_create(
        task_id,
        call_context=call_context,
        create_task_if_missing=False,
    )
    passive_stream = active.subscribe(include_initial_task=True)
    passive_initial_task = asyncio.create_task(anext(passive_stream))

    try:
        await asyncio.wait_for(passive_get_started.wait(), timeout=1)
        await task_store.save(
            Task(
                id=task_id,
                context_id=context_id,
                artifacts=[
                    Artifact(
                        artifact_id='newer-artifact',
                        parts=[Part(text='newer replica state')],
                    )
                ],
                status=TaskStatus(
                    state=TaskState.TASK_STATE_INPUT_REQUIRED,
                    message=Message(
                        message_id='newer-status',
                        role=Role.ROLE_AGENT,
                        parts=[Part(text='newer input request')],
                    ),
                ),
            ),
            call_context,
        )
        reuse_task = asyncio.create_task(
            registry.get_or_create(
                task_id,
                call_context=call_context,
                create_task_if_missing=False,
            )
        )
        for _ in range(3):
            await asyncio.sleep(0)
        release_passive_get.set()
        initial_task, reused = await asyncio.gather(
            passive_initial_task, reuse_task
        )
        assert isinstance(initial_task, Task)
        assert reused is active

        refreshed_task = await active.get_task()
        assert refreshed_task.artifacts[0].artifact_id == 'newer-artifact'
        assert refreshed_task.status.message.message_id == 'newer-status'
    finally:
        release_passive_get.set()
        passive_initial_task.cancel()
        await asyncio.gather(passive_initial_task, return_exceptions=True)
        await passive_stream.aclose()
        await registry.aclose()


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_aclose_not_blocked_by_pending_idle_refresh():
    """Registry shutdown does not wait for a caller's blocked snapshot read."""
    task_id = 'blocked-refresh-task'
    context_id = 'blocked-refresh-context'
    call_context = ServerCallContext()
    task = Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
    )
    passive_get_started = asyncio.Event()
    release_passive_get = asyncio.Event()
    get_count = 0

    async def get_task(
        requested_task_id: str, context: ServerCallContext
    ) -> Task:
        nonlocal get_count
        assert requested_task_id == task_id
        assert context is call_context
        get_count += 1
        if get_count > 2:
            passive_get_started.set()
            await release_passive_get.wait()
        result = Task()
        result.CopyFrom(task)
        return result

    task_store = AsyncMock(spec=TaskStore)
    task_store.get.side_effect = get_task
    registry = ActiveTaskRegistry(_RecordingInputRequiredExecutor(), task_store)
    active = await registry.get_or_create(
        task_id,
        call_context=call_context,
        create_task_if_missing=False,
    )
    await registry.get_or_create(
        task_id,
        call_context=call_context,
        create_task_if_missing=False,
    )
    passive_stream = active.subscribe(include_initial_task=True)
    passive_initial_task = asyncio.create_task(anext(passive_stream))
    reuse_task = None

    try:
        await asyncio.wait_for(passive_get_started.wait(), timeout=1)
        reuse_task = asyncio.create_task(
            registry.get_or_create(
                task_id,
                call_context=call_context,
                create_task_if_missing=False,
            )
        )

        for _ in range(3):
            await asyncio.sleep(0)
        assert not reuse_task.done()
        await asyncio.wait_for(registry.aclose(), timeout=1)
    finally:
        release_passive_get.set()
        passive_initial_task.cancel()
        await asyncio.gather(passive_initial_task, return_exceptions=True)
        if reuse_task is not None:
            result = await asyncio.gather(reuse_task, return_exceptions=True)
            assert isinstance(result[0], RuntimeError)
        await passive_stream.aclose()
        await registry.aclose()


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_queued_request_refreshes_snapshot_when_previous_request_finishes():
    """A queued request re-reads the store when it begins processing."""
    task_id = 'queued-resume-task'
    context_id = 'queued-resume-context'
    call_context = ServerCallContext()
    latest_task = Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    second_get_started = asyncio.Event()
    release_second_get = asyncio.Event()
    get_count = 0

    async def get_task(
        requested_task_id: str, context: ServerCallContext
    ) -> Task:
        nonlocal get_count
        assert requested_task_id == task_id
        assert context is call_context
        get_count += 1
        if get_count == 4:
            second_get_started.set()
            await release_second_get.wait()
        task = Task()
        task.CopyFrom(latest_task)
        return task

    async def save_task(task: Task, context: ServerCallContext) -> None:
        nonlocal latest_task
        assert context is call_context
        latest_task = Task()
        latest_task.CopyFrom(task)

    task_store = AsyncMock(spec=TaskStore)
    task_store.get.side_effect = get_task
    task_store.save.side_effect = save_task
    executor = _QueuedResumeExecutor()
    registry = ActiveTaskRegistry(executor, task_store)
    active = await registry.get_or_create(
        task_id,
        call_context=call_context,
        create_task_if_missing=False,
    )

    try:
        await active.enqueue_request(
            _request_context(
                task_id,
                context_id,
                call_context,
                'first-request',
                'first-request',
            )
        )
        await asyncio.wait_for(executor.first_started.wait(), timeout=1)
        assert active._request_lock.locked()

        # The registry sees the second request while the first request is
        # active, so the actual producer boundary must perform the refresh.
        await registry.get_or_create(
            task_id,
            call_context=call_context,
            create_task_if_missing=False,
        )
        await active.enqueue_request(
            _request_context(
                task_id,
                context_id,
                call_context,
                'second-request',
                'second-request',
            )
        )
        executor.release_first.set()
        await asyncio.wait_for(second_get_started.wait(), timeout=1)

        await task_store.save(
            Task(
                id=task_id,
                context_id=context_id,
                artifacts=[
                    Artifact(
                        artifact_id='queued-newer-artifact',
                        parts=[Part(text='queued newer state')],
                    )
                ],
                status=TaskStatus(
                    state=TaskState.TASK_STATE_INPUT_REQUIRED,
                    message=Message(
                        message_id='queued-newer-status',
                        role=Role.ROLE_AGENT,
                        parts=[Part(text='queued newer input')],
                    ),
                ),
            ),
            call_context,
        )
        release_second_get.set()
        await asyncio.wait_for(executor.second_started.wait(), timeout=1)

        assert len(executor.seen_tasks) >= 2
        assert (
            executor.seen_tasks[1].artifacts[0].artifact_id
            == 'queued-newer-artifact'
        )
        assert (
            executor.seen_tasks[1].status.message.message_id
            == 'queued-newer-status'
        )
    finally:
        executor.release_first.set()
        release_second_get.set()
        await registry.aclose()


class _NamedUser(User):
    """Minimal authenticated test user identified by ``user_name``."""

    def __init__(self, user_name: str) -> None:
        self._user_name = user_name

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return self._user_name


def _ctx(user_name: str) -> ServerCallContext:
    return ServerCallContext(user=_NamedUser(user_name))


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_get_or_create_cache_hit_is_owner_scoped():
    """Issue #1159: resolving a LIVE (cached) task by id is owner-scoped at the
    registry, so a non-owner cannot retrieve another user's active task on the
    cache-hit path. The guarantee lives at the registry, not at each call site.
    """
    store = InMemoryTaskStore()
    registry = ActiveTaskRegistry(
        agent_executor=_SlowExecutor(), task_store=store
    )
    alice = _ctx('alice')
    bob = _ctx('bob')

    # Alice owns the task in the store and it is live in the registry, so the
    # next lookups take the cache-hit early return rather than the miss path.
    await store.save(
        Task(
            id='task-1',
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        ),
        alice,
    )
    active = await registry.get_or_create(
        'task-1', call_context=alice, create_task_if_missing=True
    )
    assert await registry.get('task-1') is not None

    # Bob (non-owner) is rejected on the cache-hit path, masked as not-found.
    with pytest.raises(TaskNotFoundError):
        await registry.get_or_create(
            'task-1', call_context=bob, create_task_if_missing=False
        )

    # Alice (owner) still resolves the same live task.
    again = await registry.get_or_create(
        'task-1', call_context=alice, create_task_if_missing=False
    )
    assert again is active

    await registry.aclose()
