from collections.abc import AsyncGenerator
from typing import NamedTuple

import grpc
import httpx
import pytest
import pytest_asyncio

from a2a.client.transports import (
    ClientTransport,
    GrpcTransport,
    JsonRpcTransport,
    RestTransport,
)
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
    SendMessageRequest,
    TaskState,
    a2a_pb2_grpc,
)
from a2a.utils import TRANSPORT_GRPC, TRANSPORT_HTTP_JSON, TRANSPORT_JSONRPC


class MockAgentExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue):
        task_updater = TaskUpdater(
            event_queue,
            context.task_id,
            context.context_id,
        )
        await task_updater.update_status(TaskState.TASK_STATE_SUBMITTED)
        await task_updater.update_status(TaskState.TASK_STATE_WORKING)
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
                protocol_binding=TRANSPORT_HTTP_JSON,
                url='http://testserver',
            ),
            AgentInterface(
                protocol_binding=TRANSPORT_JSONRPC,
                url='http://testserver',
            ),
            AgentInterface(
                protocol_binding=TRANSPORT_GRPC,
                url='localhost:50051',
            ),
        ],
    )


class TransportSetup(NamedTuple):
    """Holds the transport and task_store for a given test."""

    transport: ClientTransport
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
def rest_setup(agent_card, base_e2e_setup) -> TransportSetup:
    task_store, handler = base_e2e_setup
    app_builder = A2ARESTFastAPIApplication(agent_card, handler)
    app = app_builder.build()
    httpx_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://testserver'
    )
    transport = RestTransport(httpx_client=httpx_client, agent_card=agent_card)
    return TransportSetup(
        transport=transport,
        task_store=task_store,
    )


@pytest.fixture
def jsonrpc_setup(agent_card, base_e2e_setup) -> TransportSetup:
    task_store, handler = base_e2e_setup
    app_builder = A2AFastAPIApplication(
        agent_card, handler, extended_agent_card=agent_card
    )
    app = app_builder.build()
    httpx_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url='http://testserver'
    )
    transport = JsonRpcTransport(
        httpx_client=httpx_client, agent_card=agent_card
    )
    return TransportSetup(
        transport=transport,
        task_store=task_store,
    )


@pytest_asyncio.fixture
async def grpc_setup(
    agent_card: AgentCard, base_e2e_setup
) -> AsyncGenerator[TransportSetup, None]:
    task_store, handler = base_e2e_setup
    server = grpc.aio.server()
    port = server.add_insecure_port('[::]:0')
    server_address = f'localhost:{port}'

    grpc_agent_card = AgentCard()
    grpc_agent_card.CopyFrom(agent_card)

    # Update the gRPC interface dynamically based on the assigned port
    for interface in grpc_agent_card.supported_interfaces:
        if interface.protocol_binding == TRANSPORT_GRPC:
            interface.url = server_address
            break
    else:
        raise ValueError('No gRPC interface found in agent card')

    servicer = GrpcHandler(grpc_agent_card, handler)
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(servicer, server)
    await server.start()

    channel = grpc.aio.insecure_channel(server_address)
    transport = GrpcTransport(agent_card=grpc_agent_card, channel=channel)
    yield TransportSetup(
        transport=transport,
        task_store=task_store,
    )

    await channel.close()
    await server.stop(0)


@pytest.fixture(
    params=[
        pytest.param('rest_setup', id='REST'),
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('grpc_setup', id='gRPC'),
    ]
)
def transport_setups(request) -> TransportSetup:
    """Parametrized fixture that runs tests against all supported transports."""
    return request.getfixturevalue(request.param)


@pytest.mark.asyncio
async def test_end_to_end_send_message_blocking(transport_setups):
    transport = transport_setups.transport

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-e2e-blocking',
        parts=[Part(text='Run dummy agent!')],
    )
    configuration = SendMessageConfiguration(blocking=True)
    params = SendMessageRequest(
        message=message_to_send, configuration=configuration
    )

    response = await transport.send_message(request=params)

    task = response.task
    assert task.id
    assert task.status.state == TaskState.TASK_STATE_COMPLETED


@pytest.mark.asyncio
async def test_end_to_end_send_message_non_blocking(transport_setups):
    transport = transport_setups.transport

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-e2e-non-blocking',
        parts=[Part(text='Run dummy agent!')],
    )
    configuration = SendMessageConfiguration(blocking=False)
    params = SendMessageRequest(
        message=message_to_send, configuration=configuration
    )

    response = await transport.send_message(request=params)

    task = response.task
    assert task.id


@pytest.mark.asyncio
async def test_end_to_end_send_message_streaming(transport_setups):
    transport = transport_setups.transport

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-e2e-streaming',
        parts=[Part(text='Run dummy agent!')],
    )
    params = SendMessageRequest(message=message_to_send)

    events = [
        event
        async for event in transport.send_message_streaming(request=params)
    ]

    assert len(events) > 0
    final_event = events[-1]

    assert final_event.HasField('status_update')
    assert final_event.status_update.task_id
    assert (
        final_event.status_update.status.state == TaskState.TASK_STATE_COMPLETED
    )


@pytest.mark.asyncio
async def test_end_to_end_get_task(transport_setups):
    transport = transport_setups.transport

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-e2e-get',
        parts=[Part(text='Test Get Task')],
    )
    response = await transport.send_message(
        request=SendMessageRequest(message=message_to_send)
    )
    task_id = response.task.id

    get_request = GetTaskRequest(id=task_id)
    retrieved_task = await transport.get_task(request=get_request)

    assert retrieved_task.id == task_id
    assert retrieved_task.status.state in {
        TaskState.TASK_STATE_SUBMITTED,
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_COMPLETED,
    }


@pytest.mark.asyncio
async def test_end_to_end_list_tasks(transport_setups):
    transport = transport_setups.transport

    total_items = 6
    page_size = 2

    for i in range(total_items):
        await transport.send_message(
            request=SendMessageRequest(
                message=Message(
                    role=Role.ROLE_USER,
                    message_id=f'msg-e2e-list-{i}',
                    parts=[Part(text=f'Test List Tasks {i}')],
                ),
                configuration=SendMessageConfiguration(blocking=False),
            )
        )

    list_request = ListTasksRequest(page_size=page_size)

    unique_task_ids = set()
    token = None

    while token != '':
        if token:
            list_request.page_token = token

        list_response = await transport.list_tasks(request=list_request)
        assert 0 < len(list_response.tasks) <= page_size

        for task in list_response.tasks:
            unique_task_ids.add(task.id)

        token = list_response.next_page_token

    assert len(unique_task_ids) == total_items
