import asyncio
import logging
import pytest
import httpx
from typing import Any, AsyncGenerator

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.events import Event, EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.context import ServerCallContext
from a2a.auth.user import User
from a2a.types.a2a_pb2 import (
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatus,
    CancelTaskRequest,
    GetTaskRequest,
    SubscribeToTaskRequest,
    ListTasksRequest,
    AgentCard,
    AgentCapabilities,
    AgentInterface,
    SendMessageConfiguration,
    TaskArtifactUpdateEvent,
    TaskStatusUpdateEvent,
    Artifact,
)
from fastapi import FastAPI
from a2a.server.routes.rest_routes import create_rest_routes
from a2a.client.client_factory import ClientFactory
from a2a.client.client import ClientConfig
from a2a.client.base_client import BaseClient
from a2a.utils import TransportProtocol
from a2a.server.tasks.inmemory_push_notification_config_store import (
    InMemoryPushNotificationConfigStore,
)
import pytest_asyncio

# Configure logging
logging.basicConfig(level=logging.DEBUG)


class MockUser(User):
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def user_name(self) -> str:
        return 'test-user'


class MagicalAgentExecutor(AgentExecutor):
    def __init__(self, trigger_events):
        self.trigger_events = trigger_events

    async def execute(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        message = context.message
        if not message or not message.parts:
            return
        task_id = context.task_id or 'default-task'
        context_id = context.context_id or 'default-context'
        commands = message.parts[0].text.strip().split('\n')
        for cmd in commands:
            cmd = cmd.strip()
            if not cmd:
                continue
            logging.info(f'MagicalAgentExecutor executing command: {cmd}')
            if cmd.startswith('SET_STATE '):
                state_str = cmd.split(' ', 1)[1].strip()
                state = TaskState.Value(state_str)
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        task_id=task_id,
                        context_id=context_id,
                        status=TaskStatus(state=state),
                    )
                )
            elif cmd.startswith('EMIT_ARTIFACT '):
                text = cmd.split(' ', 1)[1].strip()
                await event_queue.enqueue_event(
                    TaskArtifactUpdateEvent(
                        task_id=task_id,
                        context_id=context_id,
                        artifact=Artifact(
                            artifact_id=f'art_id_{text}',
                            name=f'Art_{text}',
                            parts=[Part(text=text)],
                        ),
                    )
                )
            elif cmd.startswith('WAIT_EVENT_'):
                event_id = cmd.split('_')[-1]
                await self.trigger_events[event_id].wait()
            elif cmd.startswith('SLEEP '):
                duration = float(cmd.split(' ', 1)[1].strip())
                await asyncio.sleep(duration)
            elif cmd.startswith('FAIL '):
                error_msg = cmd.split(' ', 1)[1].strip()
                raise RuntimeError(error_msg)
        await event_queue.close()

    async def cancel(
        self, context: RequestContext, event_queue: EventQueue
    ) -> None:
        task_id = context.task_id
        context_id = context.context_id
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
            )
        )
        await event_queue.close()


@pytest_asyncio.fixture
async def trigger_events():
    return {
        '1': asyncio.Event(),
        '2': asyncio.Event(),
        '3': asyncio.Event(),
        '4': asyncio.Event(),
    }


@pytest_asyncio.fixture
async def client(trigger_events):
    agent_card = AgentCard(
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
    executor = MagicalAgentExecutor(trigger_events)
    # Use copying=True for realistic behavior and to avoid shared state issues in tests
    task_store = InMemoryTaskStore(use_copying=True)
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        push_config_store=InMemoryPushNotificationConfigStore(),
    )
    app = FastAPI()
    app.routes.extend(create_rest_routes(agent_card, handler))
    httpx_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url='http://testserver',
        headers={'a2a-version': '1.0'},
    )
    factory = ClientFactory(
        config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
        )
    )
    client = factory.create(agent_card)
    yield client


@pytest.mark.asyncio
async def test_late_subscriber_parity(client: BaseClient, trigger_events):
    """Scenario: A subscriber joins after some events have been emitted.
    It should receive the current state and all subsequent events.
    """
    client._config.streaming = False
    commands = [
        'SET_STATE TASK_STATE_WORKING',
        'EMIT_ARTIFACT art1',
        'WAIT_EVENT_1',
        'EMIT_ARTIFACT art2',
        'SET_STATE TASK_STATE_COMPLETED',
    ]
    msg = Message(role=Role.ROLE_USER, parts=[Part(text='\n'.join(commands))])

    # Start task
    events = [
        e
        async for e in client.send_message(
            SendMessageRequest(
                message=msg,
                configuration=SendMessageConfiguration(return_immediately=True),
            )
        )
    ]
    task_id = events[0][0].task.id

    # Give it a moment to emit first artifact and hit WAIT_EVENT_1
    await asyncio.sleep(1.0)

    # Join as subscriber
    client._config.streaming = True
    collected_events = []

    async def subscribe_task():
        async for event in client.subscribe(SubscribeToTaskRequest(id=task_id)):
            collected_events.append(event)

    sub_task = asyncio.create_task(subscribe_task())
    await asyncio.sleep(1.0)  # Ensure sub is connected

    # Release agent
    trigger_events['1'].set()

    await asyncio.wait_for(sub_task, timeout=5.0)

    # Verify Subscriber received events
    artifact_texts = set()
    for sr, t in collected_events:
        if sr.HasField('artifact_update'):
            for p in sr.artifact_update.artifact.parts:
                if p.HasField('text'):
                    artifact_texts.add(p.text)
        elif sr.HasField('task'):
            for a in sr.task.artifacts:
                for p in a.parts:
                    if p.HasField('text'):
                        artifact_texts.add(p.text)

    logging.info(f'Collected artifact texts: {artifact_texts}')
    assert 'art1' in artifact_texts
    assert 'art2' in artifact_texts
    assert any(
        e[1].status.state == TaskState.TASK_STATE_COMPLETED
        for e in collected_events
        if e[1]
    )


@pytest.mark.asyncio
async def test_parallel_waiters(client: BaseClient, trigger_events):
    """Scenario: Multiple clients call send_message (non-streaming) for the same task.
    They should all wait until the task reaches a terminal or interrupted state.
    """
    client._config.streaming = False
    commands = [
        'SET_STATE TASK_STATE_WORKING',
        'WAIT_EVENT_1',
        'SET_STATE TASK_STATE_COMPLETED',
    ]
    msg = Message(role=Role.ROLE_USER, parts=[Part(text='\n'.join(commands))])

    # Start task with first client
    events1_task = asyncio.create_task(
        anext(
            client.send_message(
                SendMessageRequest(
                    message=msg,
                    configuration=SendMessageConfiguration(
                        return_immediately=False
                    ),
                )
            )
        )
    )

    await asyncio.sleep(0.5)

    # Second client tries to join.
    # DefaultRequestHandler should raise TaskAlreadyStartedError because on_message_send
    # calls active_task.start() which fails if already started.
    task_list = await client.list_tasks(ListTasksRequest())
    task_id = task_list.tasks[0].id
    msg2 = Message(
        task_id=task_id, role=Role.ROLE_USER, parts=[Part(text='ping')]
    )

    from a2a.utils.errors import TaskAlreadyStartedError

    with pytest.raises(TaskAlreadyStartedError):
        await anext(client.send_message(SendMessageRequest(message=msg2)))

    # Release agent
    trigger_events['1'].set()
    res1, _ = await events1_task
    assert res1.task.status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
async def test_cancellation_during_wait(client: BaseClient, trigger_events):
    """Scenario: One client is waiting for completion, another client cancels the task."""
    client._config.streaming = False
    commands = [
        'SET_STATE TASK_STATE_WORKING',
        'WAIT_EVENT_1',
        'SET_STATE TASK_STATE_COMPLETED',
    ]
    msg = Message(role=Role.ROLE_USER, parts=[Part(text='\n'.join(commands))])

    # Client 1: Start and wait. Collect all events.

    async def print_event(e):
        logging.info(f'Event: {e}')
        return e

    wait_task = asyncio.create_task(
       [
                print_event(e) async for e in client.send_message(
                SendMessageRequest(
                    message=msg,
                    configuration=SendMessageConfiguration(
                        return_immediately=False
                    ),
                )
            )
        ]
    )

    await asyncio.sleep(0.5)
    task_list = await client.list_tasks(ListTasksRequest())
    task, = task_list.tasks
    task_id = task.id

    # Client 2: Cancel
    cancel_res = await client.cancel_task(CancelTaskRequest(id=task_id))
    assert cancel_res.status.state == TaskState.TASK_STATE_CANCELED

    # Client 1 should also finish with CANCELED
    res1, _ = await wait_task
    assert res1.task.status.state == TaskState.TASK_STATE_CANCELED


@pytest.mark.asyncio
async def test_agent_failure_propagation(client: BaseClient, trigger_events):
    """Scenario: Agent fails with an exception. All waiters and subscribers should receive the error."""
    client._config.streaming = False
    commands = [
        'SET_STATE TASK_STATE_WORKING',
        'WAIT_EVENT_1',
        'FAIL Agent died',
    ]
    msg = Message(role=Role.ROLE_USER, parts=[Part(text='\n'.join(commands))])

    # Client 1: Start and wait
    wait_task = asyncio.create_task(
        anext(
            client.send_message(
                SendMessageRequest(
                    message=msg,
                    configuration=SendMessageConfiguration(
                        return_immediately=False
                    ),
                )
            )
        )
    )

    await asyncio.sleep(0.5)
    task_list = await client.list_tasks(ListTasksRequest())
    task_id = task_list.tasks[0].id

    # Client 2: Subscriber
    client._config.streaming = True
    collected_events = []

    async def subscribe_task():
        async for event in client.subscribe(SubscribeToTaskRequest(id=task_id)):
            collected_events.append(event)

    sub_task = asyncio.create_task(subscribe_task())
    await asyncio.sleep(0.5)

    # Release agent to fail
    trigger_events['1'].set()

    # Waiter should get exception
    with pytest.raises(Exception):
        await wait_task

    # Subscriber should also get exception
    with pytest.raises(Exception):
        await sub_task
