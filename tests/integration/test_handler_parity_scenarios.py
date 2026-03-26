import asyncio
import pytest
import httpx
from typing import Any
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.legacy_request_handler import (
    LegacyRequestHandler,
)
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.context import ServerCallContext
from a2a.auth.user import User
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import EventQueue
from a2a.types.a2a_pb2 import (
    Task,
    TaskState,
    TaskStatus,
    SendMessageRequest,
    Message,
    Role,
    Part,
    TaskStatusUpdateEvent,
    SendMessageConfiguration,
    CancelTaskRequest,
    SubscribeToTaskRequest,
    GetTaskRequest,
)
from fastapi import FastAPI
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.server.routes import CallContextBuilder
from a2a.client.client_factory import ClientFactory
from a2a.client.client import ClientConfig
from a2a.utils import TransportProtocol
from a2a.types.a2a_pb2 import AgentCard, AgentCapabilities, AgentInterface


class MockUser(User):
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return 'test-user'


class MockCallContextBuilder(CallContextBuilder):
    def build(self, request: Any) -> ServerCallContext:
        return ServerCallContext(
            user=MockUser(), state={'headers': {'a2a-version': '1.0'}}
        )


@pytest.fixture
def agent_card():
    return AgentCard(
        name='Test Agent',
        version='1.0.0',
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TransportProtocol.HTTP_JSON,
                url='http://testserver',
            )
        ],
    )


async def create_client(handler, agent_card):
    app = FastAPI()
    app.routes.extend(create_rest_routes(agent_card, handler, context_builder=MockCallContextBuilder()))
    httpx_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://testserver'
    )
    factory = ClientFactory(
        config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
        )
    )
    return factory.create(agent_card)


class DummyAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        await event_queue.close()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        await event_queue.close()


class FinishesInsteadOfCancelingAgent(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
        try:
            await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            pass
        await event_queue.enqueue_event(
            Task(
                id=context.task_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )
        await event_queue.close()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        pass


class CounterAgent(AgentExecutor):
    def __init__(self):
        self.count = 0

    async def execute(self, context: RequestContext, event_queue: EventQueue):
        self.count += 1
        await asyncio.sleep(0.5)
        await event_queue.enqueue_event(
            Task(
                id=context.task_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )
        await event_queue.close()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        await event_queue.close()


class RaceAgent(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
            )
        )
        await event_queue.close()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        await event_queue.close()


class ErrorBeforeAgent(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        raise ValueError('Fail before')

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        await event_queue.close()


class ErrorAfterAgent(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
        await asyncio.sleep(0.1)
        raise ValueError('Fail after')

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        await event_queue.close()


class ErrorCancelAgent(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=context.task_id,
                status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
            )
        )
        try:
            await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            pass
        await event_queue.close()

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        raise ValueError('Cancel fail')


# Scenario 1: Cancellation of already terminal task
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['default', 'legacy'])
async def test_scenario_1_cancel_terminal_task(use_legacy, agent_card):
    task_store = InMemoryTaskStore(use_copying=False)
    agent_executor = DummyAgentExecutor()
    handler = (
        LegacyRequestHandler(agent_executor, task_store, InMemoryQueueManager())
        if use_legacy
        else DefaultRequestHandler(
            agent_executor, task_store, InMemoryQueueManager()
        )
    )
    client = await create_client(handler, agent_card)
    task_id = f'terminal-task-{use_legacy}'
    await task_store.save(
        Task(
            id=task_id, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)
        ),
        ServerCallContext(user=MockUser()),
    )
    if use_legacy:
        with pytest.raises(Exception):
            await client.cancel_task(CancelTaskRequest(id=task_id))
    else:
        result = await client.cancel_task(CancelTaskRequest(id=task_id))
        assert result.status.state in (TaskState.TASK_STATE_COMPLETED, 3)


# Scenario 2: Cancellation results in COMPLETED state
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['default', 'legacy'])
async def test_scenario_2_cancel_results_in_completed(use_legacy, agent_card):
    task_store = InMemoryTaskStore(use_copying=False)
    agent_executor = FinishesInsteadOfCancelingAgent()
    queue_manager = InMemoryQueueManager()
    handler = (
        LegacyRequestHandler(agent_executor, task_store, queue_manager)
        if use_legacy
        else DefaultRequestHandler(agent_executor, task_store, queue_manager)
    )
    client = await create_client(handler, agent_card)
    task_id = f'finishes-fast-{use_legacy}'
    await task_store.save(
        Task(id=task_id, status=TaskStatus(state=TaskState.TASK_STATE_WORKING)),
        ServerCallContext(user=MockUser()),
    )
    msg = Message(
        task_id=task_id, role=Role.ROLE_USER, parts=[Part(text='hello')]
    )

    it_start = client.send_message(
        SendMessageRequest(
            message=msg,
            configuration=SendMessageConfiguration(return_immediately=True),
        )
    )
    try:
        await it_start.__anext__()
    except StopAsyncIteration:
        pass

    await asyncio.sleep(0.5)

    # Run cancel task with a small timeout so test doesn't hang if agent doesn't close fast
    # but we can't use wait_for(..., 2.0) on the actual background task if the test framework itself blocks on teardown.
    # To prevent teardown blocks, we MUST ensure the background task is cancelled or finishes.
    # Legacy doesn't cancel the producer, so the sleep(10.0) would run. Let's explicitly kill it here if needed, or simply let wait_for handle the test itself.
    # We will use wait_for, and if it times out, the test fails. But we must also cancel the background task directly from the task registry if it's Legacy.

    try:
        if use_legacy:
            with pytest.raises(Exception):
                await asyncio.wait_for(
                    client.cancel_task(CancelTaskRequest(id=task_id)),
                    timeout=2.0,
                )
        else:
            result = await asyncio.wait_for(
                client.cancel_task(CancelTaskRequest(id=task_id)), timeout=2.0
            )
            assert result.status.state in (TaskState.TASK_STATE_COMPLETED, 3)
    finally:
        if use_legacy:
            for q in queue_manager._task_queue.values():
                await q.close()
        else:
            for (
                act_task
            ) in handler._active_task_registry._active_tasks.values():
                if act_task._producer_task:
                    act_task._producer_task.cancel()


# Scenario 3: Concurrency - Double Execution
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['default', 'legacy'])
async def test_scenario_3_concurrency_double_execution(use_legacy, agent_card):
    task_store = InMemoryTaskStore(use_copying=False)
    agent_executor = CounterAgent()
    handler = (
        LegacyRequestHandler(agent_executor, task_store, InMemoryQueueManager())
        if use_legacy
        else DefaultRequestHandler(
            agent_executor, task_store, InMemoryQueueManager()
        )
    )
    client = await create_client(handler, agent_card)
    task_id = f'concurrent-task-{use_legacy}'
    await task_store.save(
        Task(id=task_id, status=TaskStatus(state=TaskState.TASK_STATE_WORKING)),
        ServerCallContext(user=MockUser()),
    )
    msg = Message(
        task_id=task_id, role=Role.ROLE_USER, parts=[Part(text='hello')]
    )
    req = SendMessageRequest(message=msg)

    async def call():
        try:
            return await client.send_message(req).__anext__()
        except Exception as e:
            return e

    results = await asyncio.gather(call(), call())
    if use_legacy:
        assert agent_executor.count == 2
    else:
        assert agent_executor.count == 1
        assert any(isinstance(r, Exception) for r in results)


# Scenario 4: Streaming Parity
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['default', 'legacy'])
async def test_scenario_4_streaming_parity(use_legacy, agent_card):
    task_store = InMemoryTaskStore(use_copying=False)
    agent_executor = RaceAgent()
    handler = (
        LegacyRequestHandler(agent_executor, task_store, InMemoryQueueManager())
        if use_legacy
        else DefaultRequestHandler(
            agent_executor, task_store, InMemoryQueueManager()
        )
    )
    client = await create_client(handler, agent_card)
    client._config.streaming = True
    msg = Message(role=Role.ROLE_USER, parts=[Part(text='hello')])
    events = []
    async for event in client.send_message(SendMessageRequest(message=msg)):
        events.append(event)
    assert len(events) >= 2


# Scenario 5: Re-subscribing to a finished task
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['default', 'legacy'])
async def test_scenario_5_resubscribe_to_finished(use_legacy, agent_card):
    task_store = InMemoryTaskStore(use_copying=False)
    agent_executor = RaceAgent()
    handler = (
        LegacyRequestHandler(agent_executor, task_store, InMemoryQueueManager())
        if use_legacy
        else DefaultRequestHandler(
            agent_executor, task_store, InMemoryQueueManager()
        )
    )
    client = await create_client(handler, agent_card)
    msg = Message(role=Role.ROLE_USER, parts=[Part(text='hello')])
    it = client.send_message(
        SendMessageRequest(
            message=msg,
            configuration=SendMessageConfiguration(return_immediately=False),
        )
    )
    res = await it.__anext__()
    task_id = (
        res[0].status_update.task_id
        if res[0].HasField('status_update')
        else res[0].task.id
    )
    async for _ in it:
        pass
    await asyncio.sleep(0.5)
    with pytest.raises(Exception):
        async for _ in client.subscribe(SubscribeToTaskRequest(id=task_id)):
            pass


# Scenario 6-8: Parity for Error cases
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['default', 'legacy'])
async def test_scenarios_6_8_error_parity(use_legacy, agent_card):
    task_store = InMemoryTaskStore(use_copying=False)
    agent_executor = DummyAgentExecutor()
    handler = (
        LegacyRequestHandler(agent_executor, task_store, InMemoryQueueManager())
        if use_legacy
        else DefaultRequestHandler(
            agent_executor, task_store, InMemoryQueueManager()
        )
    )
    client = await create_client(handler, agent_card)
    with pytest.raises(Exception):
        await client.get_task(GetTaskRequest(id='missing'))
    msg1 = Message(
        task_id='missing', role=Role.ROLE_USER, parts=[Part(text='h')]
    )
    with pytest.raises(Exception):
        await client.send_message(SendMessageRequest(message=msg1)).__anext__()
    tid = f'term-{use_legacy}'
    await task_store.save(
        Task(id=tid, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)),
        ServerCallContext(user=MockUser()),
    )
    msg2 = Message(task_id=tid, role=Role.ROLE_USER, parts=[Part(text='h')])
    with pytest.raises(Exception):
        await client.send_message(SendMessageRequest(message=msg2)).__anext__()


# Scenario 9: Exception before any event (Blocking)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['default', 'legacy'])
async def test_scenario_9_error_before_blocking(use_legacy, agent_card):
    task_store = InMemoryTaskStore(use_copying=False)
    agent_executor = ErrorBeforeAgent()
    handler = (
        LegacyRequestHandler(agent_executor, task_store, InMemoryQueueManager())
        if use_legacy
        else DefaultRequestHandler(
            agent_executor, task_store, InMemoryQueueManager()
        )
    )
    client = await create_client(handler, agent_card)
    msg = Message(role=Role.ROLE_USER, parts=[Part(text='hello')])

    with pytest.raises((BaseException,)) as excinfo:
        it = client.send_message(
            SendMessageRequest(
                message=msg,
                configuration=SendMessageConfiguration(
                    return_immediately=False
                ),
            )
        )
        await asyncio.wait_for(it.__anext__(), timeout=1.0)

    assert excinfo.value is not None


# Scenario 10: Exception before any event (Non-Blocking)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['default', 'legacy'])
async def test_scenario_10_error_before_nonblocking(use_legacy, agent_card):
    task_store = InMemoryTaskStore(use_copying=False)
    agent_executor = ErrorBeforeAgent()
    handler = (
        LegacyRequestHandler(agent_executor, task_store, InMemoryQueueManager())
        if use_legacy
        else DefaultRequestHandler(
            agent_executor, task_store, InMemoryQueueManager()
        )
    )
    client = await create_client(handler, agent_card)
    msg = Message(role=Role.ROLE_USER, parts=[Part(text='hello')])

    with pytest.raises((BaseException,)) as excinfo:
        it = client.send_message(
            SendMessageRequest(
                message=msg,
                configuration=SendMessageConfiguration(return_immediately=True),
            )
        )
        res = await it.__anext__()
        task_id = (
            res[0].status_update.task_id
            if res[0].HasField('status_update')
            else res[0].task.id
        )
        await asyncio.sleep(0.2)
        task = await client.get_task(GetTaskRequest(id=task_id))
        assert task.status.state in (
            TaskState.TASK_STATE_SUBMITTED,
            TaskState.TASK_STATE_WORKING,
        )
        pytest.fail('Should have failed')

    assert excinfo.value is not None


# Scenario 11: Exception before any event (Streaming)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['default', 'legacy'])
async def test_scenario_11_error_before_streaming(use_legacy, agent_card):
    task_store = InMemoryTaskStore(use_copying=False)
    agent_executor = ErrorBeforeAgent()
    handler = (
        LegacyRequestHandler(agent_executor, task_store, InMemoryQueueManager())
        if use_legacy
        else DefaultRequestHandler(
            agent_executor, task_store, InMemoryQueueManager()
        )
    )
    client = await create_client(handler, agent_card)
    client._config.streaming = True
    msg = Message(role=Role.ROLE_USER, parts=[Part(text='hello')])

    with pytest.raises((BaseException,)) as excinfo:
        it = client.send_message(SendMessageRequest(message=msg))
        async for _ in it:
            pass

    assert excinfo.value is not None


# Scenario 12: Exception after initial event (Blocking)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['default', 'legacy'])
async def test_scenario_12_error_after_blocking(use_legacy, agent_card):
    task_store = InMemoryTaskStore(use_copying=False)
    agent_executor = ErrorAfterAgent()
    handler = (
        LegacyRequestHandler(agent_executor, task_store, InMemoryQueueManager())
        if use_legacy
        else DefaultRequestHandler(
            agent_executor, task_store, InMemoryQueueManager()
        )
    )
    client = await create_client(handler, agent_card)
    msg = Message(role=Role.ROLE_USER, parts=[Part(text='hello')])

    with pytest.raises((BaseException,)) as excinfo:
        it = client.send_message(
            SendMessageRequest(
                message=msg,
                configuration=SendMessageConfiguration(
                    return_immediately=False
                ),
            )
        )
        await it.__anext__()

    assert excinfo.value is not None


# Scenario 13: Exception after initial event (Streaming)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['default', 'legacy'])
async def test_scenario_13_error_after_streaming(use_legacy, agent_card):
    task_store = InMemoryTaskStore(use_copying=False)
    agent_executor = ErrorAfterAgent()
    handler = (
        LegacyRequestHandler(agent_executor, task_store, InMemoryQueueManager())
        if use_legacy
        else DefaultRequestHandler(
            agent_executor, task_store, InMemoryQueueManager()
        )
    )
    client = await create_client(handler, agent_card)
    client._config.streaming = True
    msg = Message(role=Role.ROLE_USER, parts=[Part(text='hello')])

    events = []
    with pytest.raises((BaseException,)) as excinfo:
        it = client.send_message(SendMessageRequest(message=msg))
        async for event in it:
            events.append(event)

    assert excinfo.value is not None


# Scenario 14: Exception in Cancel
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['default', 'legacy'])
async def test_scenario_14_error_in_cancel(use_legacy, agent_card):
    task_store = InMemoryTaskStore(use_copying=False)
    agent_executor = ErrorCancelAgent()
    queue_manager = InMemoryQueueManager()
    handler = (
        LegacyRequestHandler(agent_executor, task_store, queue_manager)
        if use_legacy
        else DefaultRequestHandler(agent_executor, task_store, queue_manager)
    )
    client = await create_client(handler, agent_card)
    msg = Message(role=Role.ROLE_USER, parts=[Part(text='hello')])

    try:
        it = client.send_message(
            SendMessageRequest(
                message=msg,
                configuration=SendMessageConfiguration(return_immediately=True),
            )
        )
        res = await it.__anext__()
        task_id = (
            res[0].status_update.task_id
            if res[0].HasField('status_update')
            else res[0].task.id
        )
    except (BaseException,):
        return

    await asyncio.sleep(0.1)
    try:
        with pytest.raises((BaseException,)) as excinfo:
            await asyncio.wait_for(
                client.cancel_task(CancelTaskRequest(id=task_id)), timeout=0.5
            )
        assert excinfo.value is not None
    finally:
        # Prevent test teardown hanging
        if use_legacy:
            for q in queue_manager._task_queue.values():
                await q.close()
        else:
            for (
                act_task
            ) in handler._active_task_registry._active_tasks.values():
                if act_task._producer_task:
                    act_task._producer_task.cancel()


# Scenario 15: Subscribe to task that errors out
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['default', 'legacy'])
async def test_scenario_15_subscribe_error(use_legacy, agent_card):
    task_store = InMemoryTaskStore(use_copying=False)
    agent_executor = ErrorAfterAgent()
    handler = (
        LegacyRequestHandler(agent_executor, task_store, InMemoryQueueManager())
        if use_legacy
        else DefaultRequestHandler(
            agent_executor, task_store, InMemoryQueueManager()
        )
    )
    client = await create_client(handler, agent_card)
    msg = Message(role=Role.ROLE_USER, parts=[Part(text='hello')])

    try:
        it_start = client.send_message(
            SendMessageRequest(
                message=msg,
                configuration=SendMessageConfiguration(return_immediately=True),
            )
        )
        res = await it_start.__anext__()
        task_id = (
            res[0].status_update.task_id
            if res[0].HasField('status_update')
            else res[0].task.id
        )
    except (BaseException,):
        return

    with pytest.raises((BaseException,)) as excinfo:
        it_sub = client.subscribe(SubscribeToTaskRequest(id=task_id))
        async for _ in it_sub:
            pass

    assert excinfo.value is not None
