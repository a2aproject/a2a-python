import asyncio
import logging

from unittest.mock import AsyncMock

import pytest

from a2a.server.agent_execution.active_task_registry import ActiveTaskRegistry
from a2a.server.agent_execution.agent_executor import AgentExecutor
from a2a.server.agent_execution.context import RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue_v2 import EventQueue
from a2a.server.tasks import InMemoryTaskStore
from a2a.types.a2a_pb2 import (
    Artifact,
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


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
async def test_reused_idle_active_task_refreshes_shared_store_snapshot():
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

        task_from_b = await active_b.get_task()
        task_from_b.artifacts.append(
            Artifact(
                artifact_id='replica-b-artifact',
                parts=[Part(text='persisted by replica B')],
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

        resume_request = SendMessageRequest(
            message=Message(
                task_id=task_id,
                context_id=context_id,
                message_id='replica-a-resume',
                role=Role.ROLE_USER,
                parts=[Part(text='resume from replica A')],
            )
        )
        request_context = RequestContext(
            call_context=call_context,
            request=resume_request,
            task_id=task_id,
            context_id=context_id,
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
    finally:
        await registry_a.aclose()
        await registry_b.aclose()
