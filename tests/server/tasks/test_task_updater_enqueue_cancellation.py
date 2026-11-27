import asyncio
import contextlib

import pytest

from a2a.server.events.event_queue import EventQueueLegacy
from a2a.server.events.event_queue_v2 import EventQueueSource
from a2a.server.tasks import TaskUpdater
from a2a.types.a2a_pb2 import TaskState, TaskStatusUpdateEvent


@pytest.mark.asyncio
@pytest.mark.timeout(5)
@pytest.mark.parametrize(
    ('queue_type', 'buffered_updates'),
    [(EventQueueLegacy, 1), (EventQueueSource, 3)],
)
@pytest.mark.parametrize(
    'state',
    [
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
        TaskState.TASK_STATE_WORKING,
    ],
)
async def test_cancelled_enqueue_can_retry_status(
    queue_type: type[EventQueueLegacy] | type[EventQueueSource],
    buffered_updates: int,
    state: TaskState.ValueType,
) -> None:
    """Cancelling a blocked publication must not commit an unpublished state."""
    queue = queue_type(max_queue_size=1)
    updater = TaskUpdater(queue, task_id='task', context_id='context')
    pending: asyncio.Task[None] | None = None
    try:
        for _ in range(buffered_updates):
            await updater.start_work()

        pending = asyncio.create_task(updater.update_status(state))
        await asyncio.sleep(0)
        assert not pending.done()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

        for _ in range(buffered_updates):
            event = await queue.dequeue_event()
            queue.task_done()
            assert isinstance(event, TaskStatusUpdateEvent)
            assert event.status.state == TaskState.TASK_STATE_WORKING

        await updater.update_status(state)
        event = await queue.dequeue_event()
        queue.task_done()
        assert isinstance(event, TaskStatusUpdateEvent)
        assert event.status.state == state

        if state != TaskState.TASK_STATE_WORKING:
            with pytest.raises(
                RuntimeError, match='already in a terminal state'
            ):
                await updater.start_work()
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pending
        await queue.close(immediate=True)
