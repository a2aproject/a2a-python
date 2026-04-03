import asyncio
import collections
import logging

from typing import Any

import grpc
import pytest
import pytest_asyncio

from a2a.auth.user import User
from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.client.errors import A2AClientError
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import EventQueue
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.request_handlers import DefaultRequestHandlerV2, GrpcHandler
from a2a.server.request_handlers.default_request_handler import (
    LegacyRequestHandler,
)
from a2a.server.routes import CallContextBuilder
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import a2a_pb2_grpc
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Artifact,
    CancelTaskRequest,
    GetTaskRequest,
    ListTasksRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils import TransportProtocol
from a2a.utils.errors import (
    InvalidParamsError,
    TaskNotCancelableError,
    TaskNotFoundError,
)


logger = logging.getLogger(__name__)


async def wait_for_state(
    client: Any,
    task_id: str,
    expected_states: set[TaskState.ValueType],
    timeout: float = 1.0,
) -> None:
    """Wait for the task to reach one of the expected states."""
    start_time = asyncio.get_event_loop().time()
    while True:
        task = await client.get_task(GetTaskRequest(id=task_id))
        if task.status.state in expected_states:
            return

        if asyncio.get_event_loop().time() - start_time > timeout:
            raise TimeoutError(
                f'Task {task_id} did not reach expected states {expected_states} within {timeout}s. '
                f'Current state: {task.status.state}'
            )
        await asyncio.sleep(0.01)


async def get_all_events(stream):
    return [event async for event in stream]


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


def agent_card():
    return AgentCard(
        name='Test Agent',
        version='1.0.0',
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TransportProtocol.GRPC,
                url='http://testserver',
            )
        ],
    )


def get_state(event):
    if event.HasField('task'):
        return event.task.status.state
    return event.status_update.status.state


def validate_state(event, expected_state):
    assert get_state(event) == expected_state


_test_servers = []


@pytest_asyncio.fixture(autouse=True)
async def cleanup_test_servers():
    yield
    for server in _test_servers:
        await server.stop(None)
    _test_servers.clear()


# TODO: Test different transport (e.g. HTTP_JSON hangs for some tests).
async def create_client(handler, agent_card, streaming=False):
    server = grpc.aio.server()
    port = server.add_insecure_port('[::]:0')
    server_address = f'localhost:{port}'

    agent_card.supported_interfaces[0].url = server_address
    agent_card.supported_interfaces[0].protocol_binding = TransportProtocol.GRPC

    servicer = GrpcHandler(
        agent_card, handler, context_builder=MockCallContextBuilder()
    )
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(servicer, server)
    await server.start()
    _test_servers.append(server)

    factory = ClientFactory(
        config=ClientConfig(
            grpc_channel_factory=grpc.aio.insecure_channel,
            supported_protocol_bindings=[TransportProtocol.GRPC],
            streaming=streaming,
        )
    )
    client = factory.create(agent_card)
    client._server = server  # Keep reference to prevent garbage collection
    return client


def create_handler(
    agent_executor, use_legacy, task_store=None, queue_manager=None
):
    task_store = task_store or InMemoryTaskStore()
    queue_manager = queue_manager or InMemoryQueueManager()
    return (
        LegacyRequestHandler(agent_executor, task_store, queue_manager)
        if use_legacy
        else DefaultRequestHandlerV2(agent_executor, task_store, queue_manager)
    )


# Scenario 1: Cancellation of already terminal task
# This also covers test_scenario_7_cancel_terminal_task from test_handler_comparison
@pytest.mark.timeout(2.0)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
@pytest.mark.parametrize(
    'streaming', [False, True], ids=['blocking', 'streaming']
)
async def test_scenario_1_cancel_terminal_task(use_legacy, streaming):
    class DummyAgentExecutor(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            pass

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            pass

    task_store = InMemoryTaskStore()
    handler = create_handler(
        DummyAgentExecutor(), use_legacy, task_store=task_store
    )
    client = await create_client(
        handler, agent_card=agent_card(), streaming=streaming
    )

    task_id = 'terminal-task'
    await task_store.save(
        Task(
            id=task_id, status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)
        ),
        ServerCallContext(user=MockUser()),
    )
    with pytest.raises(TaskNotCancelableError):
        await client.cancel_task(CancelTaskRequest(id=task_id))


@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
async def test_scenario_4_simple_streaming(use_legacy):
    class DummyAgentExecutor(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                )
            )

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            pass

    handler = create_handler(DummyAgentExecutor(), use_legacy)
    client = await create_client(
        handler, agent_card=agent_card(), streaming=True
    )
    msg = Message(
        message_id='test-msg', role=Role.ROLE_USER, parts=[Part(text='hello')]
    )
    events = [
        event
        async for event in client.send_message(SendMessageRequest(message=msg))
    ]
    assert [event.status_update.status.state for event in events] == [
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_COMPLETED,
    ]


# Scenario 5: Re-subscribing to a finished task
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
async def test_scenario_5_resubscribe_to_finished(use_legacy):
    class DummyAgentExecutor(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                )
            )

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            pass

    handler = create_handler(DummyAgentExecutor(), use_legacy)
    client = await create_client(handler, agent_card=agent_card())
    msg = Message(
        message_id='test-msg', role=Role.ROLE_USER, parts=[Part(text='hello')]
    )
    it = client.send_message(
        SendMessageRequest(
            message=msg,
            configuration=SendMessageConfiguration(return_immediately=False),
        )
    )

    (event,) = [event async for event in it]
    task_id = event.task.id

    await wait_for_state(
        client, task_id, expected_states={TaskState.TASK_STATE_COMPLETED}
    )
    # TODO: Use different transport.
    with pytest.raises(
        NotImplementedError,
        match='client and/or server do not support resubscription',
    ):
        async for _ in client.subscribe(SubscribeToTaskRequest(id=task_id)):
            pass


# Scenario 6-8: Parity for Error cases
@pytest.mark.timeout(2.0)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
@pytest.mark.parametrize(
    'streaming', [False, True], ids=['blocking', 'streaming']
)
async def test_scenarios_simple_errors(use_legacy, streaming):
    class DummyAgentExecutor(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                )
            )

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            pass

    handler = create_handler(DummyAgentExecutor(), use_legacy)
    client = await create_client(
        handler, agent_card=agent_card(), streaming=streaming
    )

    with pytest.raises(TaskNotFoundError):
        await client.get_task(GetTaskRequest(id='missing'))

    msg1 = Message(
        task_id='missing',
        message_id='missing-task',
        role=Role.ROLE_USER,
        parts=[Part(text='h')],
    )
    with pytest.raises(TaskNotFoundError):
        async for _ in client.send_message(SendMessageRequest(message=msg1)):
            pass

    msg = Message(
        message_id='test-msg', role=Role.ROLE_USER, parts=[Part(text='hello')]
    )
    it = client.send_message(
        SendMessageRequest(
            message=msg,
            configuration=SendMessageConfiguration(return_immediately=False),
        )
    )
    (event,) = [event async for event in it]

    if streaming:
        assert event.HasField('status_update')
        task_id = event.status_update.task_id
        assert (
            event.status_update.status.state == TaskState.TASK_STATE_COMPLETED
        )
    else:
        assert event.HasField('task')
        task_id = event.task.id
        assert event.task.status.state == TaskState.TASK_STATE_COMPLETED

    logger.info('Sending message to completed task %s', task_id)
    msg2 = Message(
        message_id='test-msg-2',
        task_id=task_id,
        role=Role.ROLE_USER,
        parts=[Part(text='message to completed task')],
    )
    # TODO: Is it correct error code ?
    with pytest.raises(InvalidParamsError):
        async for _ in client.send_message(SendMessageRequest(message=msg2)):
            pass

    (task,) = (await client.list_tasks(ListTasksRequest())).tasks
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    (message,) = task.history
    assert message.role == Role.ROLE_USER
    (message_part,) = message.parts
    assert message_part.text == 'hello'


# Scenario 9: Exception before any event.
@pytest.mark.timeout(2.0)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
@pytest.mark.parametrize(
    'streaming', [False, True], ids=['blocking', 'streaming']
)
async def test_scenario_9_error_before_blocking(use_legacy, streaming):
    class ErrorBeforeAgent(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            raise ValueError('TEST_ERROR_IN_EXECUTE')

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            pass

    handler = create_handler(ErrorBeforeAgent(), use_legacy)
    client = await create_client(
        handler, agent_card=agent_card(), streaming=streaming
    )
    msg = Message(
        message_id='test-msg', role=Role.ROLE_USER, parts=[Part(text='hello')]
    )

    # TODO: Is it correct error code ?
    with pytest.raises(A2AClientError, match='TEST_ERROR_IN_EXECUTE'):
        async for _ in client.send_message(
            SendMessageRequest(
                message=msg,
                configuration=SendMessageConfiguration(
                    return_immediately=False
                ),
            )
        ):
            pass

    if use_legacy:
        # Legacy is not creating tasks for agent failures.
        assert len((await client.list_tasks(ListTasksRequest())).tasks) == 0
    else:
        # TODO: should it be TASK_STATE_FAILED ?
        (task,) = (await client.list_tasks(ListTasksRequest())).tasks
        assert task.status.state == TaskState.TASK_STATE_SUBMITTED


# Scenario 12/13: Exception after initial event
@pytest.mark.timeout(2.0)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
@pytest.mark.parametrize(
    'streaming', [False, True], ids=['blocking', 'streaming']
)
async def test_scenario_12_13_error_after_initial_event(use_legacy, streaming):
    started_event = asyncio.Event()
    continue_event = asyncio.Event()

    class ErrorAfterAgent(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
            started_event.set()
            await continue_event.wait()
            raise ValueError('TEST_ERROR_IN_EXECUTE')

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            pass

    handler = create_handler(ErrorAfterAgent(), use_legacy)
    client = await create_client(
        handler, agent_card=agent_card(), streaming=streaming
    )
    msg = Message(
        message_id='test-msg', role=Role.ROLE_USER, parts=[Part(text='hello')]
    )

    it = client.send_message(SendMessageRequest(message=msg))

    tasks = []

    if streaming:
        res = await it.__anext__()
        assert res.status_update.status.state == TaskState.TASK_STATE_WORKING
        continue_event.set()
    else:

        async def release_agent():
            await started_event.wait()
            continue_event.set()

        tasks.append(asyncio.create_task(release_agent()))

    with pytest.raises(A2AClientError, match='TEST_ERROR_IN_EXECUTE'):
        async for _ in it:
            pass

    await asyncio.gather(*tasks)

    # TODO: should it be TASK_STATE_FAILED ?
    (task,) = (await client.list_tasks(ListTasksRequest())).tasks
    assert task.status.state == TaskState.TASK_STATE_WORKING


# Scenario 14: Exception in Cancel
@pytest.mark.timeout(2.0)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
@pytest.mark.parametrize(
    'streaming', [False, True], ids=['blocking', 'streaming']
)
async def test_scenario_14_error_in_cancel(use_legacy, streaming):
    started_event = asyncio.Event()
    hang_event = asyncio.Event()

    class ErrorCancelAgent(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
            started_event.set()
            await hang_event.wait()

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            raise ValueError('TEST_ERROR_IN_CANCEL')

    handler = create_handler(ErrorCancelAgent(), use_legacy)
    client = await create_client(
        handler, agent_card=agent_card(), streaming=streaming
    )

    msg = Message(
        message_id='test-msg',
        role=Role.ROLE_USER,
        parts=[Part(text='hello')],
    )

    it = client.send_message(
        SendMessageRequest(
            message=msg,
            configuration=SendMessageConfiguration(return_immediately=True),
        )
    )
    res = await it.__anext__()
    task_id = res.task.id if res.HasField('task') else res.status_update.task_id

    await asyncio.wait_for(started_event.wait(), timeout=1.0)

    with pytest.raises(A2AClientError, match='TEST_ERROR_IN_CANCEL'):
        await client.cancel_task(CancelTaskRequest(id=task_id))

    # TODO: should it be TASK_STATE_CANCELED or TASK_STATE_FAILED?
    (task,) = (await client.list_tasks(ListTasksRequest())).tasks
    assert task.status.state == TaskState.TASK_STATE_WORKING


# Scenario 15: Subscribe to task that errors out
@pytest.mark.timeout(2.0)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
async def test_scenario_15_subscribe_error(use_legacy):
    started_event = asyncio.Event()
    continue_event = asyncio.Event()

    class ErrorAfterAgent(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
            started_event.set()
            await continue_event.wait()
            raise ValueError('TEST_ERROR_IN_EXECUTE')

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            pass

    handler = create_handler(ErrorAfterAgent(), use_legacy)
    client = await create_client(
        handler, agent_card=agent_card(), streaming=True
    )
    msg = Message(
        message_id='test-msg', role=Role.ROLE_USER, parts=[Part(text='hello')]
    )

    it_start = client.send_message(
        SendMessageRequest(
            message=msg,
            configuration=SendMessageConfiguration(return_immediately=True),
        )
    )
    res = await it_start.__anext__()
    task_id = res.task.id if res.HasField('task') else res.status_update.task_id

    async def consume_events():
        async for _ in client.subscribe(SubscribeToTaskRequest(id=task_id)):
            pass

    consume_task = asyncio.create_task(consume_events())
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(consume_task), timeout=0.1)

    await asyncio.wait_for(started_event.wait(), timeout=1.0)
    continue_event.set()

    if use_legacy:
        # Legacy client hangs forever.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(consume_task, timeout=0.1)
    else:
        with pytest.raises(A2AClientError, match='TEST_ERROR_IN_EXECUTE'):
            await consume_task

    # TODO: should it be TASK_STATE_FAILED?
    (task,) = (await client.list_tasks(ListTasksRequest())).tasks
    assert task.status.state == TaskState.TASK_STATE_WORKING


# Scenario 16: Slow execution and return_immediately=True
@pytest.mark.timeout(2.0)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
@pytest.mark.parametrize(
    'streaming', [False, True], ids=['blocking', 'streaming']
)
async def test_scenario_16_slow_execution(use_legacy, streaming):
    started_event = asyncio.Event()
    hang_event = asyncio.Event()

    class SlowAgent(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            started_event.set()
            await hang_event.wait()

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            pass

    queue_manager = InMemoryQueueManager()
    handler = create_handler(
        SlowAgent(), use_legacy, queue_manager=queue_manager
    )
    client = await create_client(
        handler, agent_card=agent_card(), streaming=streaming
    )

    msg = Message(
        message_id='test-msg',
        role=Role.ROLE_USER,
        parts=[Part(text='hello')],
    )

    async def send_message_and_get_first_response():
        it = client.send_message(
            SendMessageRequest(
                message=msg,
                configuration=SendMessageConfiguration(return_immediately=True),
            )
        )
        return await asyncio.wait_for(it.__anext__(), timeout=0.1)

    if use_legacy:
        # Legacy client hangs forever.
        with pytest.raises(asyncio.TimeoutError):
            await send_message_and_get_first_response()
    else:
        event = await send_message_and_get_first_response()
        task = event.task
        assert task.status.state == TaskState.TASK_STATE_SUBMITTED
        (message,) = task.history
        assert message.message_id == 'test-msg'

    tasks = (await client.list_tasks(ListTasksRequest())).tasks
    if use_legacy:
        # Legacy didn't create a task
        assert len(tasks) == 0
    else:
        (task,) = tasks
        assert task.status.state == TaskState.TASK_STATE_SUBMITTED


# Scenario 17: Cancellation of a working task.
# @pytest.mark.skip
@pytest.mark.timeout(2.0)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
@pytest.mark.parametrize(
    'streaming', [False, True], ids=['blocking', 'streaming']
)
async def test_scenario_cancel_working_task_empty_cancel(use_legacy, streaming):
    started_event = asyncio.Event()
    hang_event = asyncio.Event()

    class DummyCancelAgent(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
            started_event.set()
            await hang_event.wait()

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            # TODO: this should be done automatically by the framework ?
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
                )
            )

    handler = create_handler(DummyCancelAgent(), use_legacy)
    client = await create_client(
        handler, agent_card=agent_card(), streaming=streaming
    )

    msg = Message(
        message_id='test-msg', role=Role.ROLE_USER, parts=[Part(text='hello')]
    )

    it = client.send_message(
        SendMessageRequest(
            message=msg,
            configuration=SendMessageConfiguration(return_immediately=True),
        )
    )
    res = await it.__anext__()
    task_id = res.task.id if res.HasField('task') else res.status_update.task_id

    await asyncio.wait_for(started_event.wait(), timeout=1.0)

    task_before = await client.get_task(GetTaskRequest(id=task_id))
    assert task_before.status.state == TaskState.TASK_STATE_WORKING

    cancel_res = await client.cancel_task(CancelTaskRequest(id=task_id))
    assert cancel_res.status.state == TaskState.TASK_STATE_CANCELED

    task_after = await client.get_task(GetTaskRequest(id=task_id))
    assert task_after.status.state == TaskState.TASK_STATE_CANCELED

    (task_from_list,) = (await client.list_tasks(ListTasksRequest())).tasks
    assert task_from_list.status.state == TaskState.TASK_STATE_CANCELED


# Scenario 18: Complex streaming with multiple subscribers
@pytest.mark.timeout(2.0)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
async def test_scenario_18_streaming_subscribers(use_legacy):
    started_event = asyncio.Event()
    working_event = asyncio.Event()
    completed_event = asyncio.Event()

    class ComplexAgent(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
            started_event.set()
            await working_event.wait()

            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    artifact=Artifact(artifact_id='test-art'),
                )
            )
            await completed_event.wait()

            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                )
            )

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            pass

    handler = create_handler(ComplexAgent(), use_legacy)
    client = await create_client(
        handler, agent_card=agent_card(), streaming=True
    )

    msg = Message(
        message_id='test-msg', role=Role.ROLE_USER, parts=[Part(text='hello')]
    )

    it = client.send_message(
        SendMessageRequest(
            message=msg,
            configuration=SendMessageConfiguration(return_immediately=True),
        )
    )
    res = await it.__anext__()
    task_id = res.task.id if res.HasField('task') else res.status_update.task_id

    await asyncio.wait_for(started_event.wait(), timeout=1.0)

    # create first subscriber
    sub1 = client.subscribe(SubscribeToTaskRequest(id=task_id))

    # first subscriber receives current task state (WORKING)
    validate_state(await sub1.__anext__(), TaskState.TASK_STATE_WORKING)

    # create second subscriber
    sub2 = client.subscribe(SubscribeToTaskRequest(id=task_id))

    # second subscriber receives current task state (WORKING)
    validate_state(await sub2.__anext__(), TaskState.TASK_STATE_WORKING)

    working_event.set()

    # validate what both subscribers observed (artifact)
    res1_art = await sub1.__anext__()
    assert res1_art.artifact_update.artifact.artifact_id == 'test-art'

    res2_art = await sub2.__anext__()
    assert res2_art.artifact_update.artifact.artifact_id == 'test-art'

    completed_event.set()

    # validate what both subscribers observed (completed)
    validate_state(await sub1.__anext__(), TaskState.TASK_STATE_COMPLETED)
    validate_state(await sub2.__anext__(), TaskState.TASK_STATE_COMPLETED)

    # validate final task state with getTask
    final_task = await client.get_task(GetTaskRequest(id=task_id))
    assert final_task.status.state == TaskState.TASK_STATE_COMPLETED

    (artifact,) = final_task.artifacts
    assert artifact.artifact_id == 'test-art'

    (message,) = final_task.history
    assert message.parts[0].text == 'hello'


# Scenario 19: Parallel executions for the same task should not happen simultaneously.
@pytest.mark.timeout(2.0)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
@pytest.mark.parametrize(
    'streaming', [False, True], ids=['blocking', 'streaming']
)
async def test_scenario_19_no_parallel_executions(use_legacy, streaming):
    started_event = asyncio.Event()
    continue_event = asyncio.Event()
    executions_count = 0

    class CountingAgent(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            nonlocal executions_count
            executions_count += 1

            if executions_count > 1:
                await event_queue.enqueue_event(
                    TaskArtifactUpdateEvent(
                        task_id=context.task_id,
                        context_id=context.context_id,
                        artifact=Artifact(artifact_id='SECOND_EXECUTION'),
                    )
                )
                return

            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
            started_event.set()
            await continue_event.wait()
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                )
            )

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            pass

    handler = create_handler(CountingAgent(), use_legacy)
    client1 = await create_client(
        handler, agent_card=agent_card(), streaming=streaming
    )
    client2 = await create_client(
        handler, agent_card=agent_card(), streaming=streaming
    )

    msg1 = Message(
        message_id='test-msg-1',
        role=Role.ROLE_USER,
        parts=[Part(text='hello 1')],
    )

    # First client sends initial message
    it1 = client1.send_message(
        SendMessageRequest(
            message=msg1,
            configuration=SendMessageConfiguration(return_immediately=False),
        )
    )
    task1 = asyncio.create_task(it1.__anext__())

    # Wait for the first execution to reach the WORKING state
    await asyncio.wait_for(started_event.wait(), timeout=1.0)
    assert executions_count == 1

    # Extract task_id from the first call using list_tasks
    (task,) = (await client1.list_tasks(ListTasksRequest())).tasks
    task_id = task.id

    msg2 = Message(
        message_id='test-msg-2',
        task_id=task_id,
        role=Role.ROLE_USER,
        parts=[Part(text='hello 2')],
    )

    # Second client sends a message to the same task
    it2 = client2.send_message(
        SendMessageRequest(
            message=msg2,
            configuration=SendMessageConfiguration(return_immediately=False),
        )
    )

    task2 = asyncio.create_task(it2.__anext__())

    if use_legacy:
        # Legacy handler executes the second request in parallel.
        await task2
        assert executions_count == 2
    else:
        # V2 handler queues the second request.
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(task2), timeout=0.1)
        assert executions_count == 1

    # Unblock AgentExecutor
    continue_event.set()

    # Verify that both calls for clients finished.
    if use_legacy and not streaming:
        # Legacy handler fails on first execution.
        with pytest.raises(A2AClientError, match='NoTaskQueue'):
            await task1
    else:
        await task1
    await task2

    # Consume remaining events if any
    async def consume(it):
        async for _ in it:
            pass

    await asyncio.gather(consume(it1), consume(it2))
    assert executions_count == 2

    # Validate final task state.
    final_task = await client1.get_task(GetTaskRequest(id=task_id))

    if use_legacy:
        # Legacy handler fails to complete the task.
        assert final_task.status.state == TaskState.TASK_STATE_WORKING
    else:
        assert final_task.status.state == TaskState.TASK_STATE_COMPLETED

    # TODO: What is expected state of messages and artifacts?


# Scenario: Validate return_immediately flag behaviour.
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
@pytest.mark.parametrize(
    'streaming', [False, True], ids=['blocking', 'streaming']
)
async def test_scenario_return_immediately(use_legacy, streaming):
    class ImmediateAgent(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                )
            )

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            pass

    handler = create_handler(ImmediateAgent(), use_legacy)
    client = await create_client(
        handler, agent_card=agent_card(), streaming=streaming
    )

    msg = Message(
        message_id='test-msg', role=Role.ROLE_USER, parts=[Part(text='hello')]
    )

    # Test non-blocking return.
    it = client.send_message(
        SendMessageRequest(
            message=msg,
            configuration=SendMessageConfiguration(return_immediately=True),
        )
    )
    states = [get_state(event) async for event in it]

    if use_legacy:
        if streaming:
            assert states == [
                TaskState.TASK_STATE_WORKING,
                TaskState.TASK_STATE_COMPLETED,
            ]
        else:
            assert states == [TaskState.TASK_STATE_WORKING]
    elif streaming:
        assert states == [
            TaskState.TASK_STATE_SUBMITTED,
            TaskState.TASK_STATE_WORKING,
            TaskState.TASK_STATE_COMPLETED,
        ]
    else:
        assert states == [TaskState.TASK_STATE_SUBMITTED]

    # Test blocking return.
    it = client.send_message(
        SendMessageRequest(
            message=msg,
            configuration=SendMessageConfiguration(return_immediately=False),
        )
    )
    states = [get_state(event) async for event in it]

    if streaming:
        assert states == [
            TaskState.TASK_STATE_WORKING,
            TaskState.TASK_STATE_COMPLETED,
        ]
    else:
        assert states == [TaskState.TASK_STATE_COMPLETED]


# Scenario: Test TASK_STATE_INPUT_REQUIRED.
@pytest.mark.timeout(2.0)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
@pytest.mark.parametrize(
    'streaming', [False, True], ids=['blocking', 'streaming']
)
async def test_scenario_resumption_from_interrupted(use_legacy, streaming):
    class ResumingAgent(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            message = context.message
            if message and message.parts and message.parts[0].text == 'start':
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=context.task_id,
                        context_id=context.context_id,
                        status=TaskStatus(
                            state=TaskState.TASK_STATE_INPUT_REQUIRED
                        ),
                    )
                )
            elif (
                message
                and message.parts
                and message.parts[0].text == 'here is input'
            ):
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=context.task_id,
                        context_id=context.context_id,
                        status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                    )
                )
            else:
                raise ValueError('Unexpected message')

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            pass

    handler = create_handler(ResumingAgent(), use_legacy)
    client = await create_client(
        handler, agent_card=agent_card(), streaming=streaming
    )

    # First send message to get it into input required state
    msg1 = Message(
        message_id='msg-start', role=Role.ROLE_USER, parts=[Part(text='start')]
    )

    it = client.send_message(
        SendMessageRequest(
            message=msg1,
            configuration=SendMessageConfiguration(return_immediately=False),
        )
    )

    events1 = [event async for event in it]
    assert [get_state(event) for event in events1] == [
        TaskState.TASK_STATE_INPUT_REQUIRED,
    ]
    task_id = events1[0].status_update.task_id
    context_id = events1[0].status_update.context_id

    # Now send another message to resume
    msg2 = Message(
        task_id=task_id,
        context_id=context_id,
        message_id='msg-resume',
        role=Role.ROLE_USER,
        parts=[Part(text='here is input')],
    )

    it2 = client.send_message(
        SendMessageRequest(
            message=msg2,
            configuration=SendMessageConfiguration(return_immediately=False),
        )
    )

    assert [get_state(event) async for event in it2] == [
        TaskState.TASK_STATE_COMPLETED,
    ]


# Scenario: Auth required and side channel unblocking
# Migrated from: test_workflow_auth_required_side_channel in test_handler_comparison
@pytest.mark.timeout(2.0)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
@pytest.mark.parametrize(
    'streaming', [False, True], ids=['blocking', 'streaming']
)
async def test_scenario_auth_required_side_channel(use_legacy, streaming):
    side_channel_event = asyncio.Event()

    class AuthAgent(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_AUTH_REQUIRED),
                )
            )

            await side_channel_event.wait()

            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                )
            )

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            pass

    handler = create_handler(AuthAgent(), use_legacy)
    client = await create_client(
        handler, agent_card=agent_card(), streaming=streaming
    )

    msg = Message(
        message_id='test-msg', role=Role.ROLE_USER, parts=[Part(text='start')]
    )

    it = client.send_message(
        SendMessageRequest(
            message=msg,
            configuration=SendMessageConfiguration(return_immediately=False),
        )
    )

    if streaming:
        event1 = await asyncio.wait_for(it.__anext__(), timeout=1.0)
        assert get_state(event1) == TaskState.TASK_STATE_WORKING

        event2 = await asyncio.wait_for(it.__anext__(), timeout=1.0)
        assert get_state(event2) == TaskState.TASK_STATE_AUTH_REQUIRED

        task_id = event2.status_update.task_id

        side_channel_event.set()

        # Remaining event.
        (event3,) = [event async for event in it]
        assert get_state(event3) == TaskState.TASK_STATE_COMPLETED
    else:
        (event,) = [event async for event in it]
        assert get_state(event) == TaskState.TASK_STATE_AUTH_REQUIRED
        task_id = event.task.id

        side_channel_event.set()

        await wait_for_state(
            client, task_id, expected_states={TaskState.TASK_STATE_COMPLETED}
        )


# Scenario: Parallel subscribe attach detach
# Migrated from: test_parallel_subscribe_attach_detach in test_handler_comparison
@pytest.mark.timeout(5.0)
@pytest.mark.asyncio
@pytest.mark.parametrize('use_legacy', [False, True], ids=['v2', 'legacy'])
async def test_scenario_parallel_subscribe_attach_detach(use_legacy):
    events = collections.defaultdict(asyncio.Event)

    class EmitAgent(AgentExecutor):
        async def execute(
            self, context: RequestContext, event_queue: EventQueue
        ):
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )

            phases = [
                ('trigger_phase_1', 'artifact_1'),
                ('trigger_phase_2', 'artifact_2'),
                ('trigger_phase_3', 'artifact_3'),
                ('trigger_phase_4', 'artifact_4'),
            ]

            for trigger_name, artifact_id in phases:
                await events[trigger_name].wait()
                await event_queue.enqueue_event(
                    TaskArtifactUpdateEvent(
                        task_id=context.task_id,
                        context_id=context.context_id,
                        artifact=Artifact(
                            artifact_id=artifact_id,
                            parts=[Part(text=artifact_id)],
                        ),
                    )
                )

            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
                )
            )

        async def cancel(
            self, context: RequestContext, event_queue: EventQueue
        ):
            pass

    handler = create_handler(EmitAgent(), use_legacy)
    client = await create_client(
        handler, agent_card=agent_card(), streaming=True
    )

    msg = Message(
        message_id='test-msg', role=Role.ROLE_USER, parts=[Part(text='start')]
    )

    it = client.send_message(
        SendMessageRequest(
            message=msg,
            configuration=SendMessageConfiguration(return_immediately=True),
        )
    )

    res = await it.__anext__()
    task_id = res.task.id if res.HasField('task') else res.status_update.task_id

    async def monitor_artifacts():
        try:
            async for event in client.subscribe(
                SubscribeToTaskRequest(id=task_id)
            ):
                if event.HasField('artifact_update'):
                    artifact_id = event.artifact_update.artifact.artifact_id
                    if artifact_id.startswith('artifact_'):
                        phase_num = artifact_id.split('_')[1]
                        events[f'emitted_phase_{phase_num}'].set()
        except asyncio.CancelledError:
            pass

    monitor_task = asyncio.create_task(monitor_artifacts())

    async def subscribe_and_collect(artifacts_to_collect: int | None = None):
        ready_event = asyncio.Event()

        async def collect():
            collected = []
            artifacts_seen = 0
            try:
                async for event in client.subscribe(
                    SubscribeToTaskRequest(id=task_id)
                ):
                    collected.append(event)
                    ready_event.set()
                    if event.HasField('artifact_update'):
                        artifacts_seen += 1
                        if (
                            artifacts_to_collect is not None
                            and artifacts_seen >= artifacts_to_collect
                        ):
                            break
            except asyncio.CancelledError:
                pass
            return collected

        task = asyncio.create_task(collect())
        await ready_event.wait()
        return task

    sub1_task = await subscribe_and_collect()

    events['trigger_phase_1'].set()
    await events['emitted_phase_1'].wait()

    sub2_task = await subscribe_and_collect(artifacts_to_collect=1)
    sub3_task = await subscribe_and_collect(artifacts_to_collect=2)

    events['trigger_phase_2'].set()
    await events['emitted_phase_2'].wait()

    events['trigger_phase_3'].set()
    await events['emitted_phase_3'].wait()

    sub4_task = await subscribe_and_collect()

    events['trigger_phase_4'].set()
    await events['emitted_phase_4'].wait()

    def get_artifact_updates(evs):
        txts = []
        for sr in evs:
            if sr.HasField('artifact_update'):
                txts.append([p.text for p in sr.artifact_update.artifact.parts])
        return txts

    assert get_artifact_updates(await sub1_task) == [
        ['artifact_1'],
        ['artifact_2'],
        ['artifact_3'],
        ['artifact_4'],
    ]

    assert get_artifact_updates(await sub2_task) == [
        ['artifact_2'],
    ]
    assert get_artifact_updates(await sub3_task) == [
        ['artifact_2'],
        ['artifact_3'],
    ]
    assert get_artifact_updates(await sub4_task) == [
        ['artifact_4'],
    ]

    monitor_task.cancel()
