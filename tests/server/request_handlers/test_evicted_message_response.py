import asyncio

from collections.abc import AsyncGenerator
from contextlib import aclosing
from typing import Any

import pytest

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.agent_execution.active_task import ActiveTask, EventConsumer
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import Event, EventQueue
from a2a.server.events.event_queue_v2 import EventQueueSink, EventQueueSource
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.tasks import InMemoryTaskStore
from a2a.types.a2a_pb2 import (
    AgentCard,
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import InvalidAgentResponseError


class _MessageExecutor(AgentExecutor):
    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.release = asyncio.Event()
        self.release_tail = asyncio.Event()
        if outcome == 'message':
            self.release.set()

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        if self.outcome == 'status':
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id or '',
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
            await self.release_tail.wait()
            return
        message = Message(
            message_id='reply',
            role=Role.ROLE_AGENT,
            parts=[Part(text='response')],
        )
        await event_queue.enqueue_event(message)
        await self.release.wait()
        if self.outcome in ('duplicate', 'duplicate-tail'):
            await event_queue.enqueue_event(message)
        elif self.outcome in ('task', 'task-tail'):
            await event_queue.enqueue_event(
                Task(
                    id=context.task_id or '',
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
        elif self.outcome in ('error', 'save-error'):
            raise RuntimeError('executor failed after Message')
        if self.outcome.endswith('-tail'):
            await self.release_tail.wait()

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.timeout(5)
@pytest.mark.parametrize(
    ('capacity', 'outcome'),
    [
        (1, 'message'),
        (2, 'message'),
        (None, 'message'),
        (1, 'duplicate'),
        (1, 'task'),
        (1, 'error'),
        (1, 'save-error'),
        (1, 'cancel'),
        (1, 'duplicate-tail'),
        (None, 'duplicate-tail'),
        (1, 'task-tail'),
        (None, 'task-tail'),
        (1, 'status'),
        (None, 'status'),
        (1, 'force-close'),
        (1, 'consumer-cancel'),
    ],
)
async def test_evicted_message_send_finishes_validation(
    capacity: int | None,
    outcome: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resume_requester = asyncio.Event()
    delivery_ready = asyncio.Event()
    subscription_ended = asyncio.Event()
    message_received = asyncio.Event()
    original_dequeue = EventQueueSink.dequeue_event
    original_delivery = EventQueueSource._deliver_to_sink
    original_subscribe = ActiveTask.subscribe
    deliveries = 0
    active_tasks: list[ActiveTask] = []

    async def gated_dequeue(sink: EventQueueSink) -> Event:
        if sink._evict_on_full:
            await resume_requester.wait()
        return await original_dequeue(sink)

    async def observed_delivery(
        source: EventQueueSource,
        sink: EventQueueSink,
        event: Event,
    ) -> None:
        nonlocal deliveries
        await original_delivery(source, sink, event)
        if sink._evict_on_full:
            deliveries += 1
            required = 3 if capacity == 2 else 2
            if sink.is_closed() or deliveries >= required:
                delivery_ready.set()

    async def observed_subscribe(
        active: ActiveTask,
        **kwargs: Any,
    ) -> AsyncGenerator[Event, None]:
        active_tasks.append(active)
        try:
            async with aclosing(original_subscribe(active, **kwargs)) as sub:
                async for event in sub:
                    if isinstance(event, Message):
                        message_received.set()
                    yield event
        finally:
            subscription_ended.set()

    async def failing_status_write(consumer: EventConsumer) -> Task | None:
        raise ValueError('failure status could not be saved')

    monkeypatch.setattr(EventQueueSink, 'dequeue_event', gated_dequeue)
    monkeypatch.setattr(EventQueueSource, '_deliver_to_sink', observed_delivery)
    monkeypatch.setattr(ActiveTask, 'subscribe', observed_subscribe)
    if outcome == 'save-error':
        monkeypatch.setattr(
            EventConsumer, '_write_failed_status', failing_status_write
        )
    executor = _MessageExecutor(outcome)
    kwargs = {} if capacity is None else {'max_event_queue_size': capacity}
    handler = DefaultRequestHandlerV2(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=AgentCard(name='message-test', version='1'),
        **kwargs,
    )
    request = asyncio.create_task(
        handler.on_message_send(
            SendMessageRequest(
                message=Message(
                    message_id='request',
                    role=Role.ROLE_USER,
                    parts=[Part(text='hello')],
                )
            ),
            ServerCallContext(),
        )
    )
    try:
        await delivery_ready.wait()
        resume_requester.set()
        if capacity is None and outcome != 'message':
            if outcome != 'status':
                await message_received.wait()
        else:
            await subscription_ended.wait()
        if outcome != 'message':
            if outcome != 'status':
                assert not request.done()
        if outcome == 'cancel':
            request.cancel()
            with pytest.raises(asyncio.CancelledError):
                await request
            assert active_tasks[0]._consumer_task is not None
            assert not active_tasks[0]._consumer_task.cancelled()
            assert not active_tasks[0]._consumer_task.done()
        elif outcome in ('force-close', 'consumer-cancel'):
            active = active_tasks[0]
            if outcome == 'force-close':
                await asyncio.wait_for(active.aclose(), timeout=1)
            else:
                assert active._consumer_task is not None
                active._consumer_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(request), timeout=1)
        else:
            executor.release.set()
            if outcome in (
                'duplicate',
                'task',
                'duplicate-tail',
                'task-tail',
                'status',
            ):
                with pytest.raises(InvalidAgentResponseError):
                    await asyncio.wait_for(asyncio.shield(request), timeout=1)
                assert not executor.release_tail.is_set()
            elif outcome in ('error', 'save-error'):
                with pytest.raises(RuntimeError, match='executor failed'):
                    await asyncio.wait_for(asyncio.shield(request), timeout=1)
            else:
                result = await asyncio.wait_for(
                    asyncio.shield(request), timeout=1
                )
                assert isinstance(result, Message)
                assert result.message_id == 'reply'
    finally:
        resume_requester.set()
        executor.release.set()
        executor.release_tail.set()
        if not request.done():
            request.cancel()
        await asyncio.wait_for(handler.aclose(), timeout=2)
        await asyncio.wait_for(
            asyncio.gather(request, return_exceptions=True),
            timeout=1,
        )

    assert request.done()
    assert not handler._active_task_registry._active_tasks
    assert all(
        task is None or task.done()
        for active in active_tasks
        for task in (active._producer_task, active._consumer_task)
    )
