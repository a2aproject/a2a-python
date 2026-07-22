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
