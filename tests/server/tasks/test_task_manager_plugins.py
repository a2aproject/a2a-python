from unittest.mock import AsyncMock

import pytest

from a2a import plugins
from a2a.plugins import TaskEventInfo, TaskObserverPlugin
from a2a.server.cluster.task_store import ConcurrentTaskModificationError
from a2a.server.context import ServerCallContext
from a2a.server.tasks import InMemoryTaskStore, TaskManager
from a2a.types.a2a_pb2 import (
    Artifact,
    Part,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


class Collect(TaskObserverPlugin):
    def __init__(self) -> None:
        self.infos: list[TaskEventInfo] = []

    async def on_task_event(self, info: TaskEventInfo) -> None:
        self.infos.append(info)

    def transitions(self) -> list[tuple]:
        return [(i.from_state, i.to_state) for i in self.infos]


def status(task_id: str, state: TaskState) -> TaskStatusUpdateEvent:
    return TaskStatusUpdateEvent(
        task_id=task_id, context_id='c-1', status=TaskStatus(state=state)
    )


def manager(store, collect, task_id='t-1') -> TaskManager:
    return TaskManager(
        task_store=store,
        context=ServerCallContext(),
        task_id=task_id,
        context_id='c-1',
        initial_message=None,
        plugins=plugins.sequential(collect),
    )


@pytest.mark.asyncio
async def test_transitions_from_creation_to_completion():
    collect = Collect()
    tm = manager(InMemoryTaskStore(), collect)

    await tm.save_task_event(status('t-1', TaskState.TASK_STATE_WORKING))
    await tm.save_task_event(
        TaskArtifactUpdateEvent(
            task_id='t-1',
            context_id='c-1',
            artifact=Artifact(artifact_id='a-1', parts=[Part(text='x')]),
        )
    )
    await tm.save_task_event(status('t-1', TaskState.TASK_STATE_COMPLETED))

    W, S, C = (
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_SUBMITTED,
        TaskState.TASK_STATE_COMPLETED,
    )
    assert collect.transitions() == [(None, S), (S, W), (W, W), (W, C)]
    # The creation write has no event; the others carry the agent event.
    assert collect.infos[0].event is None
    assert isinstance(collect.infos[1].event, TaskStatusUpdateEvent)
    assert isinstance(collect.infos[2].event, TaskArtifactUpdateEvent)
    assert collect.infos[-1].task.status.state == C


@pytest.mark.asyncio
async def test_from_state_comes_from_existing_task_in_store():
    store = InMemoryTaskStore()
    context = ServerCallContext()
    await store.save(
        Task(
            id='t-1',
            context_id='c-1',
            status=TaskStatus(state=TaskState.TASK_STATE_INPUT_REQUIRED),
        ),
        context,
    )
    collect = Collect()
    tm = manager(store, collect)

    await tm.save_task_event(status('t-1', TaskState.TASK_STATE_WORKING))

    assert collect.transitions() == [
        (TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_WORKING)
    ]


@pytest.mark.asyncio
async def test_get_task_and_invalidate_refresh_persisted_state():
    store = InMemoryTaskStore()
    context = ServerCallContext()
    collect = Collect()
    tm = manager(store, collect)
    assert await tm.get_task() is None

    await tm.save_task_event(
        Task(
            id='t-1',
            context_id='c-1',
            status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        )
    )
    # Another writer advances the task behind the manager's back.
    await store.save(
        Task(
            id='t-1',
            context_id='c-1',
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        ),
        context,
    )
    tm.invalidate()
    assert (await tm.get_task()).status.state == TaskState.TASK_STATE_WORKING
    await tm.save_task_event(status('t-1', TaskState.TASK_STATE_COMPLETED))

    assert collect.transitions() == [
        (None, TaskState.TASK_STATE_SUBMITTED),
        (TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_COMPLETED),
    ]


@pytest.mark.asyncio
async def test_failed_write_reports_nothing():
    collect = Collect()
    store = InMemoryTaskStore()
    tm = manager(store, collect)
    tm._versioned_store.save = AsyncMock(
        side_effect=ConcurrentTaskModificationError('stale')
    )

    with pytest.raises(ConcurrentTaskModificationError):
        await tm.save_task_event(status('t-1', TaskState.TASK_STATE_WORKING))

    assert collect.infos == []


@pytest.mark.asyncio
async def test_no_plugins_or_no_task_hooks_reports_nothing():
    class OnlyServer(plugins.ServerPlugin):
        pass

    for manager_arg in (None, plugins.sequential(OnlyServer())):
        tm = TaskManager(
            task_store=InMemoryTaskStore(),
            context=ServerCallContext(),
            task_id='t-1',
            context_id='c-1',
            initial_message=None,
            plugins=manager_arg,
        )
        assert tm._plugins is None
        await tm.save_task_event(status('t-1', TaskState.TASK_STATE_WORKING))
