from collections.abc import AsyncGenerator
from typing import NamedTuple

import grpc
import httpx
import pytest
import pytest_asyncio

from a2a.client.base_client import BaseClient
from a2a.client.client import ClientConfig
from a2a.client.client_factory import ClientFactory
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.apps import A2AFastAPIApplication, A2ARESTFastAPIApplication
from a2a.server.events import EventQueue
from a2a.server.events.in_memory_queue_manager import InMemoryQueueManager
from a2a.server.request_handlers import DefaultRequestHandler, GrpcHandler
from a2a.server.tasks import TaskUpdater
from a2a.server.tasks.inmemory_task_store import InMemoryTaskStore
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    GetTaskRequest,
    ListTasksRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    TaskState,
    a2a_pb2_grpc,
)
from a2a.utils import TransportProtocol


def assert_message_matches(message, expected_role, expected_text):
    assert message.role == expected_role
    assert message.parts[0].text == expected_text


def assert_history_matches(history, expected_history):
    assert len(history) == len(expected_history)
    for msg, (expected_role, expected_text) in zip(
        history, expected_history, strict=True
    ):
        assert_message_matches(msg, expected_role, expected_text)


def assert_artifacts_match(artifacts, expected_artifacts):
    assert len(artifacts) == len(expected_artifacts)
    for artifact, (expected_name, expected_text) in zip(
        artifacts, expected_artifacts, strict=True
    ):
        assert artifact.name == expected_name
        assert artifact.parts[0].text == expected_text


def assert_events_match(events, expected_events):
    assert len(events) == len(expected_events)
    for (event, _), (expected_type, expected_val) in zip(
        events, expected_events, strict=True
    ):
        assert event.HasField(expected_type)
        if expected_type == 'status_update':
            assert event.status_update.status.state == expected_val
        elif expected_type == 'artifact_update':
            if expected_val is not None:
                assert_artifacts_match(
                    [event.artifact_update.artifact],
                    expected_val,
                )
        else:
            raise ValueError(f'Unexpected event type: {expected_type}')


class MockAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        task_updater = TaskUpdater(
            event_queue,
            context.task_id,
            context.context_id,
        )
        user_input = context.get_user_input()

        is_input_required_resumption = (
            context.current_task is not None
            and context.current_task.status.state
            == TaskState.TASK_STATE_INPUT_REQUIRED
        )

        if not is_input_required_resumption:
            await task_updater.update_status(
                TaskState.TASK_STATE_SUBMITTED,
                message=task_updater.new_agent_message(
                    [Part(text='task submitted')]
                ),
            )

        await task_updater.update_status(
            TaskState.TASK_STATE_WORKING,
            message=task_updater.new_agent_message([Part(text='task working')]),
        )

        if user_input == 'Need input':
            await task_updater.update_status(
                TaskState.TASK_STATE_INPUT_REQUIRED,
                message=task_updater.new_agent_message(
                    [Part(text='Please provide input')]
                ),
            )
        else:
            await task_updater.add_artifact(
                parts=[Part(text='artifact content')], name='test-artifact'
            )
            await task_updater.update_status(
                TaskState.TASK_STATE_COMPLETED,
                message=task_updater.new_agent_message([Part(text='done')]),
            )

    async def cancel(self, context: RequestContext, event_queue: EventQueue):
        raise NotImplementedError('Cancellation is not supported')


@pytest.fixture
def agent_card() -> AgentCard:
    return AgentCard(
        name='Integration Agent',
        description='Real in-memory integration testing.',
        version='1.0.0',
        capabilities=AgentCapabilities(
            streaming=True, push_notifications=False
        ),
        skills=[],
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TransportProtocol.HTTP_JSON,
                url='http://testserver',
            ),
            AgentInterface(
                protocol_binding=TransportProtocol.JSONRPC,
                url='http://testserver',
            ),
            AgentInterface(
                protocol_binding=TransportProtocol.GRPC,
                url='localhost:50051',
            ),
        ],
    )


class ClientSetup(NamedTuple):
    """Holds the client and task_store for a given test."""

    client: BaseClient
    task_store: InMemoryTaskStore


@pytest.fixture
def base_e2e_setup():
    task_store = InMemoryTaskStore()
    handler = DefaultRequestHandler(
        agent_executor=MockAgentExecutor(),
        task_store=task_store,
        queue_manager=InMemoryQueueManager(),
    )
    return task_store, handler


@pytest.fixture
def rest_setup(agent_card, base_e2e_setup) -> ClientSetup:
    task_store, handler = base_e2e_setup
    app_builder = A2ARESTFastAPIApplication(agent_card, handler)
    app = app_builder.build()
    httpx_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://testserver'
    )
    factory = ClientFactory(
        config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
        )
    )
    client = factory.create(agent_card)
    return ClientSetup(
        client=client,
        task_store=task_store,
    )


@pytest.fixture
def jsonrpc_setup(agent_card, base_e2e_setup) -> ClientSetup:
    task_store, handler = base_e2e_setup
    app_builder = A2AFastAPIApplication(
        agent_card, handler, extended_agent_card=agent_card
    )
    app = app_builder.build()
    httpx_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://testserver'
    )
    factory = ClientFactory(
        config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.JSONRPC],
        )
    )
    client = factory.create(agent_card)
    return ClientSetup(
        client=client,
        task_store=task_store,
    )


@pytest_asyncio.fixture
async def grpc_setup(
    agent_card: AgentCard, base_e2e_setup
) -> AsyncGenerator[ClientSetup, None]:
    task_store, handler = base_e2e_setup
    server = grpc.aio.server()
    port = server.add_insecure_port('[::]:0')
    server_address = f'localhost:{port}'

    grpc_agent_card = AgentCard()
    grpc_agent_card.CopyFrom(agent_card)

    # Update the gRPC interface dynamically based on the assigned port
    for interface in grpc_agent_card.supported_interfaces:
        if interface.protocol_binding == TransportProtocol.GRPC:
            interface.url = server_address
            break
    else:
        raise ValueError('No gRPC interface found in agent card')

    servicer = GrpcHandler(grpc_agent_card, handler)
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(servicer, server)
    await server.start()

    factory = ClientFactory(
        config=ClientConfig(
            grpc_channel_factory=grpc.aio.insecure_channel,
            supported_protocol_bindings=[TransportProtocol.GRPC],
        )
    )
    client = factory.create(grpc_agent_card)
    yield ClientSetup(
        client=client,
        task_store=task_store,
    )

    await client.close()
    await server.stop(0)


@pytest.fixture(
    params=[
        pytest.param('rest_setup', id='REST'),
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('grpc_setup', id='gRPC'),
    ]
)
def transport_setups(request) -> ClientSetup:
    """Parametrized fixture that runs tests against all supported transports."""
    return request.getfixturevalue(request.param)


@pytest.mark.asyncio
async def test_end_to_end_send_message_blocking(transport_setups):
    client = transport_setups.client
    client._config.streaming = False

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-e2e-blocking',
        parts=[Part(text='Run dummy agent!')],
    )
    configuration = SendMessageConfiguration(blocking=True)

    events = [
        event
        async for event in client.send_message(
            request=message_to_send, configuration=configuration
        )
    ]
    assert len(events) == 1
    response, _ = events[0]
    assert response.task.id
    assert response.task.status.state == TaskState.TASK_STATE_COMPLETED
    assert_artifacts_match(
        response.task.artifacts,
        [('test-artifact', 'artifact content')],
    )
    assert_history_matches(
        response.task.history,
        [
            (Role.ROLE_USER, 'Run dummy agent!'),
            (Role.ROLE_AGENT, 'task submitted'),
            (Role.ROLE_AGENT, 'task working'),
        ],
    )


@pytest.mark.asyncio
async def test_end_to_end_send_message_non_blocking(transport_setups):
    client = transport_setups.client
    client._config.streaming = False

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-e2e-non-blocking',
        parts=[Part(text='Run dummy agent!')],
    )
    configuration = SendMessageConfiguration(blocking=False)

    events = [
        event
        async for event in client.send_message(
            request=message_to_send, configuration=configuration
        )
    ]
    assert len(events) == 1
    response, _ = events[0]
    assert response.task.id
    assert response.task.status.state == TaskState.TASK_STATE_SUBMITTED
    assert_history_matches(
        response.task.history,
        [
            (Role.ROLE_USER, 'Run dummy agent!'),
        ],
    )


@pytest.mark.asyncio
async def test_end_to_end_send_message_streaming(transport_setups):
    client = transport_setups.client

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-e2e-streaming',
        parts=[Part(text='Run dummy agent!')],
    )

    events = [
        event async for event in client.send_message(request=message_to_send)
    ]

    assert_events_match(
        events,
        [
            ('status_update', TaskState.TASK_STATE_SUBMITTED),
            ('status_update', TaskState.TASK_STATE_WORKING),
            ('artifact_update', [('test-artifact', 'artifact content')]),
            ('status_update', TaskState.TASK_STATE_COMPLETED),
        ],
    )

    task = await client.get_task(request=GetTaskRequest(id=events[0][1].id))
    assert_history_matches(
        task.history,
        [
            (Role.ROLE_USER, 'Run dummy agent!'),
            (Role.ROLE_AGENT, 'task submitted'),
            (Role.ROLE_AGENT, 'task working'),
        ],
    )
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert_message_matches(task.status.message, Role.ROLE_AGENT, 'done')


@pytest.mark.asyncio
async def test_end_to_end_get_task(transport_setups):
    client = transport_setups.client

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-e2e-get',
        parts=[Part(text='Test Get Task')],
    )
    events = [
        event async for event in client.send_message(request=message_to_send)
    ]
    _, task = events[-1]
    task_id = task.id

    get_request = GetTaskRequest(id=task_id)
    retrieved_task = await client.get_task(request=get_request)

    assert retrieved_task.id == task_id
    assert retrieved_task.status.state in {
        TaskState.TASK_STATE_SUBMITTED,
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_COMPLETED,
    }
    assert_history_matches(
        retrieved_task.history,
        [
            (Role.ROLE_USER, 'Test Get Task'),
            (Role.ROLE_AGENT, 'task submitted'),
            (Role.ROLE_AGENT, 'task working'),
        ],
    )


@pytest.mark.asyncio
async def test_end_to_end_list_tasks(transport_setups):
    client = transport_setups.client

    total_items = 6
    page_size = 2

    expected_task_ids = []
    for i in range(total_items):
        # One event is enough to get the task ID
        _, task = await anext(
            client.send_message(
                request=Message(
                    role=Role.ROLE_USER,
                    message_id=f'msg-e2e-list-{i}',
                    parts=[Part(text=f'Test List Tasks {i}')],
                )
            )
        )
        expected_task_ids.append(task.id)

    list_request = ListTasksRequest(page_size=page_size)

    actual_task_ids = []
    token = None

    while token != '':
        if token:
            list_request.page_token = token

        list_response = await client.list_tasks(request=list_request)
        assert 0 < len(list_response.tasks) <= page_size
        assert list_response.total_size == total_items
        assert list_response.page_size == page_size

        actual_task_ids.extend([task.id for task in list_response.tasks])

        for task in list_response.tasks:
            assert len(task.history) >= 1
            assert task.history[0].role == Role.ROLE_USER
            assert task.history[0].parts[0].text.startswith('Test List Tasks ')

        token = list_response.next_page_token

    assert len(actual_task_ids) == total_items
    assert sorted(actual_task_ids) == sorted(expected_task_ids)


@pytest.mark.asyncio
async def test_end_to_end_input_required(transport_setups):
    client = transport_setups.client

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-e2e-input-req-1',
        parts=[Part(text='Need input')],
    )

    events = [
        event async for event in client.send_message(request=message_to_send)
    ]

    assert_events_match(
        events,
        [
            ('status_update', TaskState.TASK_STATE_SUBMITTED),
            ('status_update', TaskState.TASK_STATE_WORKING),
            ('status_update', TaskState.TASK_STATE_INPUT_REQUIRED),
        ],
    )

    task = await client.get_task(request=GetTaskRequest(id=events[0][1].id))

    assert task.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
    assert_history_matches(
        task.history,
        [
            (Role.ROLE_USER, 'Need input'),
            (Role.ROLE_AGENT, 'task submitted'),
            (Role.ROLE_AGENT, 'task working'),
        ],
    )
    assert_message_matches(
        task.status.message, Role.ROLE_AGENT, 'Please provide input'
    )

    # Follow-up message
    follow_up_message = Message(
        task_id=task.id,
        role=Role.ROLE_USER,
        message_id='msg-e2e-input-req-2',
        parts=[Part(text='Here is the input')],
    )

    follow_up_events = [
        event async for event in client.send_message(request=follow_up_message)
    ]

    assert_events_match(
        follow_up_events,
        [
            ('status_update', TaskState.TASK_STATE_WORKING),
            ('artifact_update', [('test-artifact', 'artifact content')]),
            ('status_update', TaskState.TASK_STATE_COMPLETED),
        ],
    )

    task = await client.get_task(request=GetTaskRequest(id=task.id))

    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert_artifacts_match(
        task.artifacts,
        [('test-artifact', 'artifact content')],
    )

    assert_history_matches(
        task.history,
        [
            (Role.ROLE_USER, 'Need input'),
            (Role.ROLE_AGENT, 'task submitted'),
            (Role.ROLE_AGENT, 'task working'),
            (Role.ROLE_AGENT, 'Please provide input'),
            (Role.ROLE_USER, 'Here is the input'),
            (Role.ROLE_AGENT, 'task working'),
        ],
    )
    assert_message_matches(task.status.message, Role.ROLE_AGENT, 'done')
