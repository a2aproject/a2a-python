import asyncio
from typing import Any, AsyncGenerator, NamedTuple
from unittest.mock import AsyncMock

import grpc
import httpx
import pytest
import pytest_asyncio
from grpc.aio import Channel
from starlette.testclient import TestClient

from a2a.client.transports import JsonRpcTransport, RestTransport
from a2a.client.transports.base import ClientTransport
from a2a.client.transports.grpc import GrpcTransport
from a2a.grpc import a2a_pb2_grpc
from a2a.server.apps import A2AFastAPIApplication, A2ARESTFastAPIApplication
from a2a.server.request_handlers import GrpcHandler, RequestHandler
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    Message,
    MessageSendParams,
    Part,
    Role,
    Task,
    TaskState,
    TaskStatus,
    TextPart,
    TransportProtocol,
)

# --- Test Constants ---

TASK_FROM_STREAM = Task(
    id="task-123-stream",
    context_id="ctx-456-stream",
    status=TaskStatus(state=TaskState.completed),
    kind="task",
)


# --- Test Fixtures ---


@pytest.fixture
def mock_request_handler() -> AsyncMock:
    """Provides a mock RequestHandler for the server-side handlers."""
    handler = AsyncMock(spec=RequestHandler)

    async def stream_side_effect(*args, **kwargs):
        yield TASK_FROM_STREAM

    handler.on_message_send_stream.side_effect = stream_side_effect
    return handler


@pytest.fixture
def agent_card() -> AgentCard:
    """Provides a sample AgentCard for tests."""
    return AgentCard(
        name='Test Agent',
        description='An agent for integration testing.',
        url='http://testserver',
        version='1.0.0',
        capabilities=AgentCapabilities(streaming=True),
        skills=[],
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        preferred_transport=TransportProtocol.jsonrpc,
        additional_interfaces=[
            AgentInterface(
                transport=TransportProtocol.http_json, url='http://testserver'
            ),
            AgentInterface(
                transport=TransportProtocol.grpc, url='localhost:50051'
            ),
        ],
    )


class TransportSetup(NamedTuple):
    """Holds the transport and handler for a given test."""

    transport: ClientTransport
    handler: AsyncMock


# --- HTTP/JSON-RPC/REST Setup ---


@pytest.fixture
def http_base_setup(mock_request_handler: AsyncMock, agent_card: AgentCard):
    """A base fixture to patch the sse-starlette event loop issue."""
    from sse_starlette import sse

    sse.AppStatus.should_exit_event = asyncio.Event()
    yield mock_request_handler, agent_card


@pytest.fixture
def jsonrpc_setup(http_base_setup) -> TransportSetup:
    """Sets up the JsonRpcTransport and in-memory server."""
    mock_request_handler, agent_card = http_base_setup
    app_builder = A2AFastAPIApplication(agent_card, mock_request_handler)
    app = app_builder.build()
    httpx_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    transport = JsonRpcTransport(
        httpx_client=httpx_client, agent_card=agent_card
    )
    return TransportSetup(transport=transport, handler=mock_request_handler)


@pytest.fixture
def rest_setup(http_base_setup) -> TransportSetup:
    """Sets up the RestTransport and in-memory server."""
    mock_request_handler, agent_card = http_base_setup
    app_builder = A2ARESTFastAPIApplication(agent_card, mock_request_handler)
    app = app_builder.build()
    httpx_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    transport = RestTransport(httpx_client=httpx_client, agent_card=agent_card)
    return TransportSetup(transport=transport, handler=mock_request_handler)


# --- gRPC Setup ---


@pytest_asyncio.fixture
async def grpc_server_and_handler(
    mock_request_handler: AsyncMock, agent_card: AgentCard
) -> AsyncGenerator[tuple[str, AsyncMock], None]:
    """Creates and manages an in-process gRPC test server."""
    server = grpc.aio.server()
    port = server.add_insecure_port('[::]:0')
    server_address = f'localhost:{port}'
    servicer = GrpcHandler(agent_card, mock_request_handler)
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(servicer, server)
    await server.start()
    yield server_address, mock_request_handler
    await server.stop(0)


# --- The Integration Test ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'transport_setup_fixture',
    [
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('rest_setup', id='REST'),
    ],
)
async def test_http_transport_sends_message(
    transport_setup_fixture: str, request
) -> None:
    """
    Integration test for HTTP-based transports (JSON-RPC, REST).
    """
    transport_setup: TransportSetup = request.getfixturevalue(
        transport_setup_fixture
    )
    transport = transport_setup.transport
    handler = transport_setup.handler

    message_to_send = Message(
        role=Role.user,
        message_id='msg-integration-test',
        parts=[Part(root=TextPart(text='Hello, integration test!'))],
    )
    params = MessageSendParams(message=message_to_send)

    stream = transport.send_message_streaming(request=params)
    first_event = await anext(stream)

    assert first_event.id == TASK_FROM_STREAM.id
    assert first_event.context_id == TASK_FROM_STREAM.context_id

    handler.on_message_send_stream.assert_called_once()
    call_args, _ = handler.on_message_send_stream.call_args
    received_params: MessageSendParams = call_args[0]

    assert received_params.message.message_id == message_to_send.message_id
    assert (
        received_params.message.parts[0].root.text
        == message_to_send.parts[0].root.text
    )

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_grpc_transport_sends_message(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> None:
    """
    Integration test specifically for the gRPC transport.
    """
    server_address, handler = grpc_server_and_handler
    agent_card.url = server_address

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    stub = a2a_pb2_grpc.A2AServiceStub(channel_factory(server_address))
    transport = GrpcTransport(grpc_stub=stub, agent_card=agent_card)

    message_to_send = Message(
        role=Role.user,
        message_id='msg-grpc-integration-test',
        parts=[Part(root=TextPart(text='Hello, gRPC integration test!'))],
    )
    params = MessageSendParams(message=message_to_send)

    stream = transport.send_message_streaming(request=params)
    first_event = await anext(stream)

    assert first_event.id == TASK_FROM_STREAM.id
    assert first_event.context_id == TASK_FROM_STREAM.context_id

    handler.on_message_send_stream.assert_called_once()
    call_args, _ = handler.on_message_send_stream.call_args
    received_params: MessageSendParams = call_args[0]

    assert received_params.message.message_id == message_to_send.message_id
    assert (
        received_params.message.parts[0].root.text
        == message_to_send.parts[0].root.text
    )

    await transport.close()
