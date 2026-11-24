"""Task generation through DefaultRequestHandlerV2: A2A 1.1 section 3.2.7."""

import asyncio

import pytest

from a2a.helpers.proto_helpers import new_task_from_user_message
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.tasks import (
    InMemoryTaskStore,
    PushNotificationEvent,
    PushNotificationSender,
    TaskUpdater,
)
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    CancelTaskRequest,
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import (
    TaskGenerationMismatchError,
    UnsupportedOperationError,
)


class _TurnAgent(AgentExecutor):
    """Turn 1 asks for input; later turns emit an artifact and complete.

    `gate` holds a turn after it reaches WORKING until the test releases it.
    """

    def __init__(self) -> None:
        self.gate: asyncio.Event | None = None
        self.working = asyncio.Event()
        self.seen_history: list[list[str]] = []

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
            updater = TaskUpdater(event_queue, task.id, task.context_id)
            await updater.requires_input()
            return
        self.seen_history.append([m.message_id for m in task.history])
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()
        self.working.set()
        if self.gate is not None:
            await self.gate.wait()
        await updater.add_artifact([Part(text='result')])
        await updater.complete()

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        pass


def _handler(agent: AgentExecutor, **kwargs) -> DefaultRequestHandlerV2:
    return DefaultRequestHandlerV2(
        agent_executor=agent,
        task_store=InMemoryTaskStore(),
        agent_card=AgentCard(capabilities=AgentCapabilities(streaming=True)),
        **kwargs,
    )


def _message(message_id: str, task: Task | None = None) -> Message:
    message = Message(
        message_id=message_id, role=Role.ROLE_USER, parts=[Part(text='hi')]
    )
    if task is not None:
        message.task_id = task.id
        message.context_id = task.context_id
    return message


async def _start_task(handler: DefaultRequestHandlerV2) -> Task:
    task = await handler.on_message_send(
        SendMessageRequest(message=_message('m-1')), ServerCallContext()
    )
    assert isinstance(task, Task)
    assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    return task


def _generations(events: list) -> list[int]:
    return [
        e.generation
        for e in events
        if isinstance(e, Task | TaskStatusUpdateEvent | TaskArtifactUpdateEvent)
    ]


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_streamed_events_carry_consecutive_generations() -> None:
    handler = _handler(_TurnAgent())
    try:
        first = [
            e
            async for e in handler.on_message_send_stream(
                SendMessageRequest(message=_message('m-1')),
                ServerCallContext(),
            )
        ]
        assert _generations(first) == [1, 2]

        second = [
            e
            async for e in handler.on_message_send_stream(
                SendMessageRequest(message=_message('m-2', first[0])),
                ServerCallContext(),
            )
        ]
        # Generation 3 is the client message; the agent's events follow.
        assert _generations(second) == [4, 5, 6]

        task = await handler.on_get_task(
            GetTaskRequest(id=first[0].id), ServerCallContext()
        )
        assert task.generation == 6
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_follow_up_is_saved_before_its_turn_runs() -> None:
    agent = _TurnAgent()
    handler = _handler(agent)
    try:
        task = await _start_task(handler)
        await handler.on_message_send(
            SendMessageRequest(message=_message('m-2', task)),
            ServerCallContext(),
        )
        assert agent.seen_history == [['m-1', 'm-2']]
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_if_generation_match_accepts_current_generation() -> None:
    handler = _handler(_TurnAgent())
    try:
        task = await _start_task(handler)
        result = await handler.on_message_send(
            SendMessageRequest(
                message=_message('m-2', task),
                configuration=SendMessageConfiguration(
                    if_generation_match=task.generation
                ),
            ),
            ServerCallContext(),
        )
        assert result.status.state == TaskState.TASK_STATE_COMPLETED
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_if_generation_match_rejects_stale_generation() -> None:
    handler = _handler(_TurnAgent())
    try:
        task = await _start_task(handler)
        with pytest.raises(TaskGenerationMismatchError) as exc_info:
            await handler.on_message_send(
                SendMessageRequest(
                    message=_message('m-2', task),
                    configuration=SendMessageConfiguration(
                        if_generation_match=task.generation - 1
                    ),
                ),
                ServerCallContext(),
            )
        assert exc_info.value.data == {
            'taskId': task.id,
            'expectedGeneration': str(task.generation - 1),
            'currentGeneration': str(task.generation),
        }

        stored = await handler.on_get_task(
            GetTaskRequest(id=task.id), ServerCallContext()
        )
        assert stored.generation == task.generation
        assert [m.message_id for m in stored.history] == ['m-1']
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_if_generation_match_is_ignored_for_a_new_task() -> None:
    handler = _handler(_TurnAgent())
    try:
        result = await handler.on_message_send(
            SendMessageRequest(
                message=_message('m-1'),
                configuration=SendMessageConfiguration(if_generation_match=42),
            ),
            ServerCallContext(),
        )
        assert result.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_concurrent_conditional_sends_admit_exactly_one() -> None:
    agent = _TurnAgent()
    agent.gate = asyncio.Event()
    handler = _handler(agent)
    try:
        task = await _start_task(handler)

        async def send(message_id: str) -> object:
            try:
                return await handler.on_message_send(
                    SendMessageRequest(
                        message=_message(message_id, task),
                        configuration=SendMessageConfiguration(
                            if_generation_match=task.generation
                        ),
                    ),
                    ServerCallContext(),
                )
            except TaskGenerationMismatchError as e:
                return e

        sends = [asyncio.create_task(send(f'm-{i}')) for i in (2, 3)]
        await agent.working.wait()
        agent.gate.set()
        results = await asyncio.gather(*sends)

        mismatches = [
            r for r in results if isinstance(r, TaskGenerationMismatchError)
        ]
        assert len(mismatches) == 1
        stored = await handler.on_get_task(
            GetTaskRequest(id=task.id), ServerCallContext()
        )
        assert sum(m.role == Role.ROLE_USER for m in stored.history) == 2
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_follow_up_to_terminal_task_saves_nothing() -> None:
    handler = _handler(_TurnAgent())
    try:
        task = await _start_task(handler)
        done = await handler.on_message_send(
            SendMessageRequest(message=_message('m-2', task)),
            ServerCallContext(),
        )
        assert done.status.state == TaskState.TASK_STATE_COMPLETED
        with pytest.raises(UnsupportedOperationError, match='terminal'):
            await handler.on_message_send(
                SendMessageRequest(message=_message('m-3', task)),
                ServerCallContext(),
            )
        stored = await handler.on_get_task(
            GetTaskRequest(id=task.id), ServerCallContext()
        )
        assert stored.generation == done.generation
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_long_poll_returns_immediately_when_task_is_ahead() -> None:
    handler = _handler(_TurnAgent(), long_poll_timeout=5)
    try:
        task = await _start_task(handler)
        result = await asyncio.wait_for(
            handler.on_get_task(
                GetTaskRequest(
                    id=task.id, current_generation=task.generation - 1
                ),
                ServerCallContext(),
            ),
            timeout=1,
        )
        assert result.generation == task.generation
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_long_poll_waits_for_the_next_change() -> None:
    agent = _TurnAgent()
    agent.gate = asyncio.Event()
    handler = _handler(agent, long_poll_timeout=5)
    try:
        task = await _start_task(handler)
        poll = asyncio.create_task(
            handler.on_get_task(
                GetTaskRequest(id=task.id, current_generation=task.generation),
                ServerCallContext(),
            )
        )
        await asyncio.sleep(0.05)
        assert not poll.done()

        send = asyncio.create_task(
            handler.on_message_send(
                SendMessageRequest(message=_message('m-2', task)),
                ServerCallContext(),
            )
        )
        result = await asyncio.wait_for(poll, timeout=2)
        assert result.generation == task.generation + 1

        agent.gate.set()
        await send
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_long_poll_returns_current_state_on_timeout() -> None:
    handler = _handler(_TurnAgent(), long_poll_timeout=0.1)
    try:
        task = await _start_task(handler)
        result = await handler.on_get_task(
            GetTaskRequest(id=task.id, current_generation=task.generation),
            ServerCallContext(),
        )
        assert result.generation == task.generation
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_long_poll_does_not_hold_terminal_tasks() -> None:
    handler = _handler(_TurnAgent(), long_poll_timeout=5)
    try:
        task = await _start_task(handler)
        done = await handler.on_message_send(
            SendMessageRequest(message=_message('m-2', task)),
            ServerCallContext(),
        )
        result = await asyncio.wait_for(
            handler.on_get_task(
                GetTaskRequest(
                    id=task.id, current_generation=done.generation + 10
                ),
                ServerCallContext(),
            ),
            timeout=1,
        )
        assert result.status.state == TaskState.TASK_STATE_COMPLETED
    finally:
        await handler.aclose()


def test_long_poll_timeout_must_be_positive() -> None:
    with pytest.raises(ValueError, match='long_poll_timeout'):
        _handler(_TurnAgent(), long_poll_timeout=0)


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_resume_without_event_log_opens_with_snapshot() -> None:
    handler = _handler(_TurnAgent())
    try:
        task = await _start_task(handler)
        events = handler.on_subscribe_to_task(
            SubscribeToTaskRequest(id=task.id, last_seen_generation=1),
            ServerCallContext(),
        )
        first = await asyncio.wait_for(anext(events), timeout=1)
        await events.aclose()
        assert isinstance(first, Task)
        assert first.generation == task.generation
    finally:
        await handler.aclose()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_cancel_of_waiting_task_is_one_event() -> None:
    handler = _handler(_TurnAgent())
    try:
        task = await _start_task(handler)
        cancelled = await handler.on_cancel_task(
            CancelTaskRequest(id=task.id), ServerCallContext()
        )
        assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
        assert cancelled.generation == task.generation + 1
        assert cancelled.status.HasField('timestamp')
    finally:
        await handler.aclose()


class _GatedPushSender(PushNotificationSender):
    """Holds the consumer between saving an event and delivering it."""

    def __init__(self) -> None:
        self.gate = asyncio.Event()
        self.gate.set()
        self.entered = asyncio.Event()

    async def send_notification(
        self, task_id: str, event: PushNotificationEvent
    ) -> None:
        self.entered.set()
        await self.gate.wait()


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_subscribe_never_repeats_the_snapshot_generation() -> None:
    sender = _GatedPushSender()
    agent = _TurnAgent()
    agent.gate = asyncio.Event()
    handler = _handler(agent, push_sender=sender)
    try:
        task = await _start_task(handler)
        sender.gate.clear()
        sender.entered.clear()

        async def follow_up() -> list:
            return [
                e
                async for e in handler.on_message_send_stream(
                    SendMessageRequest(message=_message('m-2', task)),
                    ServerCallContext(),
                )
            ]

        turn = asyncio.create_task(follow_up())
        # WORKING is saved, and the snapshot will include it, but it has not
        # been delivered yet.
        await asyncio.wait_for(sender.entered.wait(), timeout=5)
        events = handler.on_subscribe_to_task(
            SubscribeToTaskRequest(id=task.id), ServerCallContext()
        )
        snapshot = await anext(events)
        sender.gate.set()
        agent.gate.set()
        rest = [e async for e in events]
        await turn

        assert isinstance(snapshot, Task)
        assert snapshot.status.state == TaskState.TASK_STATE_WORKING
        assert _generations(rest) == [
            snapshot.generation + 1,
            snapshot.generation + 2,
        ]
    finally:
        await handler.aclose()


class _FailingAgent(AgentExecutor):
    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        raise RuntimeError('boom')

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        pass


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_task_created_by_failure_starts_at_generation_one() -> None:
    handler = _handler(_FailingAgent())
    params = SendMessageRequest(message=_message('m-1'))
    try:
        with pytest.raises(RuntimeError):
            await handler.on_message_send(params, ServerCallContext())
        # The handler assigns the new task's id onto the request message.
        stored = await handler.on_get_task(
            GetTaskRequest(id=params.message.task_id), ServerCallContext()
        )
        assert stored.status.state == TaskState.TASK_STATE_FAILED
        assert stored.generation == 1
    finally:
        await handler.aclose()


class _GrpcContext:
    def __init__(self, remaining: float | None) -> None:
        self._remaining = remaining

    def time_remaining(self) -> float | None:
        return self._remaining


@pytest.mark.parametrize(
    ('remaining', 'expected'),
    [(None, 30.0), (100.0, 30.0), (10.0, 9.5), (0.2, 0.0)],
)
def test_long_poll_hold_ends_before_grpc_deadline(
    remaining: float | None, expected: float
) -> None:
    handler = _handler(_TurnAgent(), long_poll_timeout=30)
    context = ServerCallContext(state={'grpc_context': _GrpcContext(remaining)})
    assert handler._hold_timeout(context) == expected


class _FailsOnSecondTurnAgent(AgentExecutor):
    """Turn 1 asks for input; turn 2 goes WORKING, waits, then raises."""

    def __init__(self) -> None:
        self.working = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        task = context.current_task
        if task is None:
            task = new_task_from_user_message(context.message)
            await event_queue.enqueue_event(task)
            await TaskUpdater(
                event_queue, task.id, task.context_id
            ).requires_input()
            return
        await TaskUpdater(event_queue, task.id, task.context_id).start_work()
        self.working.set()
        await self.release.wait()
        raise RuntimeError('boom')

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        pass


class _PausingFailedSaveStore(InMemoryTaskStore):
    """Holds the first FAILED write until the test releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.failed_save_started = asyncio.Event()
        self.resume = asyncio.Event()

    async def save(self, task: Task, context=None) -> None:
        if (
            task.status.state == TaskState.TASK_STATE_FAILED
            and not self.failed_save_started.is_set()
        ):
            self.failed_save_started.set()
            await self.resume.wait()
        await super().save(task, context)


@pytest.mark.asyncio
@pytest.mark.timeout(10)
async def test_follow_up_waits_for_the_failure_write() -> None:
    """A follow-up arriving while a failed turn is being recorded waits for
    it, then sees the task terminal; it never shares the FAILED generation."""
    agent = _FailsOnSecondTurnAgent()
    store = _PausingFailedSaveStore()
    handler = DefaultRequestHandlerV2(
        agent_executor=agent,
        task_store=store,
        agent_card=AgentCard(capabilities=AgentCapabilities(streaming=True)),
    )
    try:
        task = await _start_task(handler)
        failing = asyncio.create_task(
            handler.on_message_send(
                SendMessageRequest(message=_message('m-2', task)),
                ServerCallContext(),
            )
        )
        await agent.working.wait()
        agent.release.set()
        await store.failed_save_started.wait()

        follow_up = asyncio.create_task(
            handler.on_message_send(
                SendMessageRequest(message=_message('m-3', task)),
                ServerCallContext(),
            )
        )
        await asyncio.sleep(0.1)
        assert not follow_up.done()
        store.resume.set()

        with pytest.raises(RuntimeError):
            await failing
        with pytest.raises(UnsupportedOperationError, match='terminal'):
            await follow_up
        stored = await handler.on_get_task(
            GetTaskRequest(id=task.id), ServerCallContext()
        )
        assert stored.status.state == TaskState.TASK_STATE_FAILED
        assert 'm-3' not in [m.message_id for m in stored.history]
    finally:
        await handler.aclose()
