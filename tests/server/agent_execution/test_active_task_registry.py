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
from a2a.server.tasks import InMemoryTaskStore
from a2a.types.a2a_pb2 import Task, TaskState, TaskStatus
from a2a.utils.errors import TaskNotFoundError


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


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_reused_idle_task_drops_stale_snapshot():
    """Issue #1188: reusing an idle ActiveTask after a non-terminal interrupt
    must drop its cached TaskManager snapshot, so the per-request get_task()
    in _run_producer re-reads the store instead of resuming from a
    pre-interrupt snapshot that would overwrite state another replica wrote.
    """
    store = InMemoryTaskStore()
    registry = ActiveTaskRegistry(
        agent_executor=_SlowExecutor(), task_store=store
    )
    ctx = _ctx('alice')

    await store.save(
        Task(
            id='task-1',
            status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
        ),
        ctx,
    )
    active = await registry.get_or_create(
        'task-1', call_context=ctx, create_task_if_missing=True
    )

    # Simulate the producer having cached a pre-interrupt snapshot, then the
    # task going idle (its previous request's subscriber has detached).
    stale = Task(
        id='task-1',
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
    )
    active._task_manager._current_task = stale
    assert active._reference_count == 1  # idle: no in-flight subscriber

    reused = await registry.get_or_create(
        'task-1', call_context=ctx, create_task_if_missing=False
    )

    assert reused is active
    assert active._task_manager._current_task is None

    await registry.aclose()


@pytest.mark.timeout(5)
@pytest.mark.asyncio
async def test_reused_streaming_task_keeps_snapshot():
    """Issue #1188 guard: a reused ActiveTask with a subscriber stream still in
    flight (reference_count > 1) must KEEP its snapshot. Re-reading the store
    mid-stream would drop the open artifact and the next append=True chunk
    would fail with InvalidAgentResponseError.
    """
    store = InMemoryTaskStore()
    registry = ActiveTaskRegistry(
        agent_executor=_SlowExecutor(), task_store=store
    )
    ctx = _ctx('alice')

    await store.save(
        Task(
            id='task-1',
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        ),
        ctx,
    )
    active = await registry.get_or_create(
        'task-1', call_context=ctx, create_task_if_missing=True
    )

    snapshot = Task(
        id='task-1',
        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
    )
    active._task_manager._current_task = snapshot
    # Simulate an in-flight subscriber tailing the current stream.
    active._reference_count = 2

    reused = await registry.get_or_create(
        'task-1', call_context=ctx, create_task_if_missing=False
    )

    assert reused is active
    assert active._task_manager._current_task is snapshot

    active._reference_count = 1
    await registry.aclose()
