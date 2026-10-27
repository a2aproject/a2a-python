import asyncio

from collections.abc import AsyncGenerator
from typing import cast

import pytest

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events.event_queue import (
    DEFAULT_MAX_QUEUE_SIZE,
    Event,
    EventQueue,
)
from a2a.server.events.event_queue_v2 import EventQueueSink, EventQueueSource
from a2a.server.request_handlers import (
    DefaultRequestHandler,
    DefaultRequestHandlerV2,
)
from a2a.server.tasks import InMemoryTaskStore
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    CancelTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)


class _GatedExecutor(AgentExecutor):
    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        await event_queue.enqueue_event(
            Task(
                id=context.task_id or '',
                context_id=context.context_id or '',
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
        await self.release.wait()
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id or '',
                context_id=context.context_id or '',
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        return None


def _agent_card() -> AgentCard:
    return AgentCard(
        name='queue-capacity-test',
        version='1.0',
        capabilities=AgentCapabilities(streaming=True),
    )


def _send_request() -> SendMessageRequest:
    return SendMessageRequest(
        message=Message(
            message_id='message-1',
            role=Role.ROLE_USER,
            parts=[Part(text='start')],
        )
    )


@pytest.mark.parametrize('max_event_queue_size', [0, -1])
def test_handler_rejects_nonpositive_event_queue_size(
    max_event_queue_size: int,
) -> None:
    with pytest.raises(ValueError, match='max_event_queue_size'):
        DefaultRequestHandlerV2(
            agent_executor=_GatedExecutor(),
            task_store=InMemoryTaskStore(),
            agent_card=_agent_card(),
            max_event_queue_size=max_event_queue_size,
        )


@pytest.mark.asyncio
@pytest.mark.timeout(5)
@pytest.mark.parametrize('max_event_queue_size', [None, 1, 3])
async def test_handler_event_queue_capacity_reaches_all_local_buffers(
    max_event_queue_size: int | None,
) -> None:
    kwargs = (
        {}
        if max_event_queue_size is None
        else {'max_event_queue_size': max_event_queue_size}
    )
    expected = (
        DEFAULT_MAX_QUEUE_SIZE
        if max_event_queue_size is None
        else max_event_queue_size
    )
    executor = _GatedExecutor()
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=_agent_card(),
        **kwargs,
    )
    context = ServerCallContext()
    stream = handler.on_message_send_stream(_send_request(), context)
    subscriptions: list[AsyncGenerator[Event, None]] = []
    reader: asyncio.Task[Event] | None = None
    try:
        first = await anext(stream)
        assert isinstance(first, Task)
        active = await handler._active_task_registry.get(first.id)
        assert active is not None

        for _ in range(2):
            subscription = handler.on_subscribe_to_task(
                SubscribeToTaskRequest(id=first.id), context
            )
            subscriptions.append(subscription)
            assert isinstance(await anext(subscription), Task)

        agent_source = active._event_queue_agent
        subscriber_source = active._event_queue_subscribers
        assert DEFAULT_MAX_QUEUE_SIZE == 1024
        assert agent_source._incoming_queue.maxsize == expected
        assert agent_source.queue.maxsize == expected
        assert subscriber_source._incoming_queue.maxsize == expected
        assert subscriber_source._default_sink is None
        assert len(subscriber_source._sinks) == 3
        assert all(
            sink.queue.maxsize == expected for sink in subscriber_source._sinks
        )

        reader = asyncio.create_task(anext(stream))
        cancelled = await handler.on_cancel_task(
            CancelTaskRequest(id=first.id), context
        )
        assert cancelled is not None
        assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
        with pytest.raises(StopAsyncIteration):
            await reader
    finally:
        if reader is not None:
            if not reader.done():
                reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        for subscription in subscriptions:
            await subscription.aclose()
        await stream.aclose()
        await handler.aclose()

    assert active._producer_task is not None
    assert active._producer_task.done()
    assert active._consumer_task is not None
    assert active._consumer_task.done()
    assert agent_source._dispatcher_task.done()
    assert subscriber_source._dispatcher_task.done()


@pytest.mark.asyncio
@pytest.mark.timeout(5)
@pytest.mark.parametrize('streaming', [False, True])
async def test_small_event_queue_capacity_completes_requests(
    streaming: bool,
) -> None:
    executor = _GatedExecutor()
    executor.release.set()
    handler = DefaultRequestHandlerV2(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
        agent_card=_agent_card(),
        max_event_queue_size=1,
    )
    try:
        if streaming:
            events = [
                event
                async for event in handler.on_message_send_stream(
                    _send_request(), ServerCallContext()
                )
            ]
            assert isinstance(events[0], Task)
            result = events[-1]
            assert isinstance(result, TaskStatusUpdateEvent)
        else:
            result = await handler.on_message_send(
                _send_request(), ServerCallContext()
            )
            assert isinstance(result, Task)
        assert result.status.state == TaskState.TASK_STATE_COMPLETED
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_evicted_send_returns_current_task_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resume_requester = asyncio.Event()
    requester_waiting = asyncio.Event()
    requester_evicted = asyncio.Event()
    original_dequeue = EventQueueSink.dequeue_event
    original_close = EventQueueSink.close

    async def gated_dequeue(sink: EventQueueSink) -> Event:
        if sink._evict_on_full:
            requester_waiting.set()
            await resume_requester.wait()
        return await original_dequeue(sink)

    async def observe_close(
        sink: EventQueueSink, immediate: bool = False
    ) -> None:
        await original_close(sink, immediate=immediate)
        if sink._evict_on_full and immediate:
            requester_evicted.set()

    monkeypatch.setattr(EventQueueSink, 'dequeue_event', gated_dequeue)
    monkeypatch.setattr(EventQueueSink, 'close', observe_close)
    handler = DefaultRequestHandlerV2(
        agent_executor=_GatedExecutor(),
        task_store=InMemoryTaskStore(),
        agent_card=_agent_card(),
        max_event_queue_size=1,
    )
    request = asyncio.create_task(
        handler.on_message_send(_send_request(), ServerCallContext())
    )
    try:
        await requester_waiting.wait()
        # The internal request-start marker fills the tap; the Task evicts it.
        await requester_evicted.wait()
        resume_requester.set()
        result = await request
        assert isinstance(result, Task)
        assert result.status.state == TaskState.TASK_STATE_WORKING
        active = await handler._active_task_registry.get(result.id)
        assert active is not None
        assert active._producer_task is not None
        assert not active._producer_task.done()
    finally:
        resume_requester.set()
        if not request.done():
            request.cancel()
        await asyncio.gather(request, return_exceptions=True)
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(5)
async def test_small_event_queue_backpressures_producer_while_store_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = InMemoryTaskStore()
    store_waiting = asyncio.Event()
    resume_store = asyncio.Event()
    producer_blocked = asyncio.Event()
    producer_resumed = asyncio.Event()
    original_save = store.save

    async def gated_save(task: Task, context: ServerCallContext) -> None:
        store_waiting.set()
        await resume_store.wait()
        await original_save(task, context)

    executor = _GatedExecutor()

    async def burst(context: RequestContext, event_queue: EventQueue) -> None:
        source = cast('EventQueueSource', event_queue)
        original_enqueue = source.enqueue_event

        async def observe_enqueue(event: Event) -> None:
            blocked = (
                store_waiting.is_set()
                and source.queue.full()
                and source._incoming_queue.full()
            )
            if blocked:
                producer_blocked.set()
            await original_enqueue(event)
            if blocked:
                producer_resumed.set()

        monkeypatch.setattr(source, 'enqueue_event', observe_enqueue)
        await event_queue.enqueue_event(
            Task(
                id=context.task_id or '',
                context_id=context.context_id or '',
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
        for _ in range(10):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id or '',
                    context_id=context.context_id or '',
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
        await executor.release.wait()

    monkeypatch.setattr(store, 'save', gated_save)
    monkeypatch.setattr(executor, 'execute', burst)
    handler = DefaultRequestHandlerV2(
        agent_executor=executor,
        task_store=store,
        agent_card=_agent_card(),
        max_event_queue_size=1,
    )
    request = asyncio.create_task(
        handler.on_message_send(_send_request(), ServerCallContext())
    )
    try:
        await store_waiting.wait()
        await producer_blocked.wait()
        assert not producer_resumed.is_set()
        assert not request.done()
        resume_store.set()
        await producer_resumed.wait()
    finally:
        resume_store.set()
        await handler.aclose()
        if not request.done():
            request.cancel()
        await asyncio.wait_for(
            asyncio.gather(request, return_exceptions=True), timeout=1
        )
