import asyncio
from collections.abc import AsyncGenerator
from typing import NamedTuple
from unittest.mock import ANY, AsyncMock

import grpc
import httpx
import pytest
import pytest_asyncio
from grpc.aio import Channel

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
    GetTaskPushNotificationConfigParams,
    Message,
    MessageSendParams,
    ListTasksParams,
    ListTasksResult,
    Part,
    PushNotificationConfig,
    Role,
    Task,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
    TransportProtocol,
)

# --- Test Constants ---

TASK_FROM_STREAM = Task(
    id='task-123-stream',
    context_id='ctx-456-stream',
    status=TaskStatus(state=TaskState.completed),
    kind='task',
)

TASK_FROM_BLOCKING = Task(
    id='task-789-blocking',
    context_id='ctx-101-blocking',
    status=TaskStatus(state=TaskState.completed),
    kind='task',
)

GET_TASK_RESPONSE = Task(
    id='task-get-456',
    context_id='ctx-get-789',
    status=TaskStatus(state=TaskState.working),
    kind='task',
)

CANCEL_TASK_RESPONSE = Task(
    id='task-cancel-789',
    context_id='ctx-cancel-101',
    status=TaskStatus(state=TaskState.canceled),
    kind='task',
)

CALLBACK_CONFIG = TaskPushNotificationConfig(
    task_id='task-callback-123',
    push_notification_config=PushNotificationConfig(
        id='pnc-abc', url='http://callback.example.com', token=''
    ),
)

RESUBSCRIBE_EVENT = TaskStatusUpdateEvent(
    task_id='task-resub-456',
    context_id='ctx-resub-789',
    status=TaskStatus(state=TaskState.working),
    final=False,
)


# --- Test Fixtures ---


@pytest.fixture
def mock_request_handler() -> AsyncMock:
    """Provides a mock RequestHandler for the server-side handlers."""
    handler = AsyncMock(spec=RequestHandler)

    # Configure on_message_send for non-streaming calls
    handler.on_message_send.return_value = TASK_FROM_BLOCKING

    # Configure on_message_send_stream for streaming calls
    async def stream_side_effect(*args, **kwargs):
        yield TASK_FROM_STREAM

    handler.on_message_send_stream.side_effect = stream_side_effect

    # Configure other methods
    handler.on_get_task.return_value = GET_TASK_RESPONSE
    handler.on_cancel_task.return_value = CANCEL_TASK_RESPONSE
    handler.on_set_task_push_notification_config.side_effect = (
        lambda params, context: params
    )
    handler.on_get_task_push_notification_config.return_value = CALLBACK_CONFIG
    handler.on_list_tasks.return_value = ListTasksResult(
        tasks=[TASK_FROM_BLOCKING],
        next_page_token='',
        page_size=50,
        total_size=1,
    )

    async def resubscribe_side_effect(*args, **kwargs):
        yield RESUBSCRIBE_EVENT

    handler.on_resubscribe_to_task.side_effect = resubscribe_side_effect

    return handler


@pytest.fixture
def agent_card() -> AgentCard:
    """Provides a sample AgentCard for tests."""
    return AgentCard(
        name='Test Agent',
        description='An agent for integration testing.',
        url='http://testserver',
        version='1.0.0',
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        skills=[],
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        preferred_transport=TransportProtocol.jsonrpc,
        supports_authenticated_extended_card=False,
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
    app_builder = A2AFastAPIApplication(
        agent_card, mock_request_handler, extended_agent_card=agent_card
    )
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


# --- The Integration Tests ---


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'transport_setup_fixture',
    [
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('rest_setup', id='REST'),
    ],
)
async def test_http_transport_sends_message_streaming(
    transport_setup_fixture: str, request
) -> None:
    """
    Integration test for HTTP-based transports (JSON-RPC, REST) streaming.
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
async def test_grpc_transport_sends_message_streaming(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> None:
    """
    Integration test specifically for the gRPC transport streaming.
    """
    server_address, handler = grpc_server_and_handler
    agent_card.url = server_address

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'transport_setup_fixture',
    [
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('rest_setup', id='REST'),
    ],
)
async def test_http_transport_sends_message_blocking(
    transport_setup_fixture: str, request
) -> None:
    """
    Integration test for HTTP-based transports (JSON-RPC, REST) blocking.
    """
    transport_setup: TransportSetup = request.getfixturevalue(
        transport_setup_fixture
    )
    transport = transport_setup.transport
    handler = transport_setup.handler

    message_to_send = Message(
        role=Role.user,
        message_id='msg-integration-test-blocking',
        parts=[Part(root=TextPart(text='Hello, blocking test!'))],
    )
    params = MessageSendParams(message=message_to_send)

    result = await transport.send_message(request=params)

    assert result.id == TASK_FROM_BLOCKING.id
    assert result.context_id == TASK_FROM_BLOCKING.context_id

    handler.on_message_send.assert_awaited_once()
    call_args, _ = handler.on_message_send.call_args
    received_params: MessageSendParams = call_args[0]

    assert received_params.message.message_id == message_to_send.message_id
    assert (
        received_params.message.parts[0].root.text
        == message_to_send.parts[0].root.text
    )

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_grpc_transport_sends_message_blocking(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> None:
    """
    Integration test specifically for the gRPC transport blocking.
    """
    server_address, handler = grpc_server_and_handler
    agent_card.url = server_address

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    message_to_send = Message(
        role=Role.user,
        message_id='msg-grpc-integration-test-blocking',
        parts=[Part(root=TextPart(text='Hello, gRPC blocking test!'))],
    )
    params = MessageSendParams(message=message_to_send)

    result = await transport.send_message(request=params)

    assert result.id == TASK_FROM_BLOCKING.id
    assert result.context_id == TASK_FROM_BLOCKING.context_id

    handler.on_message_send.assert_awaited_once()
    call_args, _ = handler.on_message_send.call_args
    received_params: MessageSendParams = call_args[0]

    assert received_params.message.message_id == message_to_send.message_id
    assert (
        received_params.message.parts[0].root.text
        == message_to_send.parts[0].root.text
    )

    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'transport_setup_fixture',
    [
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('rest_setup', id='REST'),
    ],
)
async def test_http_transport_get_task(
    transport_setup_fixture: str, request
) -> None:
    transport_setup: TransportSetup = request.getfixturevalue(
        transport_setup_fixture
    )
    transport = transport_setup.transport
    handler = transport_setup.handler

    params = TaskQueryParams(id=GET_TASK_RESPONSE.id)
    result = await transport.get_task(request=params)

    assert result.id == GET_TASK_RESPONSE.id
    handler.on_get_task.assert_awaited_once_with(params, ANY)

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_grpc_transport_get_task(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> None:
    server_address, handler = grpc_server_and_handler
    agent_card.url = server_address

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    params = TaskQueryParams(id=GET_TASK_RESPONSE.id)
    result = await transport.get_task(request=params)

    assert result.id == GET_TASK_RESPONSE.id
    handler.on_get_task.assert_awaited_once()
    assert handler.on_get_task.call_args[0][0].id == GET_TASK_RESPONSE.id

    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'transport_setup_fixture',
    [
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('rest_setup', id='REST'),
    ],
)
async def test_http_transport_list_tasks(
    transport_setup_fixture: str, request
) -> None:
    transport_setup: TransportSetup = request.getfixturevalue(
        transport_setup_fixture
    )
    transport = transport_setup.transport
    handler = transport_setup.handler

    print(handler.on_list_tasks.call_args)

    params = ListTasksParams()
    result = await transport.list_tasks(params)

    handler.on_list_tasks.assert_awaited_once_with(params, ANY)
    assert result.next_page_token == ''
    assert result.page_size == 50
    assert len(result.tasks) == 1
    assert result.total_size == 1

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_grpc_transport_list_tasks(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> None:
    server_address, handler = grpc_server_and_handler
    agent_card.url = server_address

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    result = await transport.list_tasks(ListTasksParams())

    handler.on_list_tasks.assert_awaited_once()
    assert result.next_page_token == ''
    assert result.page_size == 50
    assert len(result.tasks) == 1
    assert result.total_size == 1

    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'transport_setup_fixture',
    [
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('rest_setup', id='REST'),
    ],
)
async def test_http_transport_cancel_task(
    transport_setup_fixture: str, request
) -> None:
    transport_setup: TransportSetup = request.getfixturevalue(
        transport_setup_fixture
    )
    transport = transport_setup.transport
    handler = transport_setup.handler

    params = TaskIdParams(id=CANCEL_TASK_RESPONSE.id)
    result = await transport.cancel_task(request=params)

    assert result.id == CANCEL_TASK_RESPONSE.id
    handler.on_cancel_task.assert_awaited_once_with(params, ANY)

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_grpc_transport_cancel_task(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> None:
    server_address, handler = grpc_server_and_handler
    agent_card.url = server_address

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    params = TaskIdParams(id=CANCEL_TASK_RESPONSE.id)
    result = await transport.cancel_task(request=params)

    assert result.id == CANCEL_TASK_RESPONSE.id
    handler.on_cancel_task.assert_awaited_once()
    assert handler.on_cancel_task.call_args[0][0].id == CANCEL_TASK_RESPONSE.id

    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'transport_setup_fixture',
    [
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('rest_setup', id='REST'),
    ],
)
async def test_http_transport_set_task_callback(
    transport_setup_fixture: str, request
) -> None:
    transport_setup: TransportSetup = request.getfixturevalue(
        transport_setup_fixture
    )
    transport = transport_setup.transport
    handler = transport_setup.handler

    params = CALLBACK_CONFIG
    result = await transport.set_task_callback(request=params)

    assert result.task_id == CALLBACK_CONFIG.task_id
    assert (
        result.push_notification_config.id
        == CALLBACK_CONFIG.push_notification_config.id
    )
    assert (
        result.push_notification_config.url
        == CALLBACK_CONFIG.push_notification_config.url
    )
    handler.on_set_task_push_notification_config.assert_awaited_once_with(
        params, ANY
    )

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_grpc_transport_set_task_callback(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> None:
    server_address, handler = grpc_server_and_handler
    agent_card.url = server_address

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    params = CALLBACK_CONFIG
    result = await transport.set_task_callback(request=params)

    assert result.task_id == CALLBACK_CONFIG.task_id
    assert (
        result.push_notification_config.id
        == CALLBACK_CONFIG.push_notification_config.id
    )
    assert (
        result.push_notification_config.url
        == CALLBACK_CONFIG.push_notification_config.url
    )
    handler.on_set_task_push_notification_config.assert_awaited_once()
    assert (
        handler.on_set_task_push_notification_config.call_args[0][0].task_id
        == CALLBACK_CONFIG.task_id
    )

    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'transport_setup_fixture',
    [
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('rest_setup', id='REST'),
    ],
)
async def test_http_transport_get_task_callback(
    transport_setup_fixture: str, request
) -> None:
    transport_setup: TransportSetup = request.getfixturevalue(
        transport_setup_fixture
    )
    transport = transport_setup.transport
    handler = transport_setup.handler

    params = GetTaskPushNotificationConfigParams(
        id=CALLBACK_CONFIG.task_id,
        push_notification_config_id=CALLBACK_CONFIG.push_notification_config.id,
    )
    result = await transport.get_task_callback(request=params)

    assert result.task_id == CALLBACK_CONFIG.task_id
    assert (
        result.push_notification_config.id
        == CALLBACK_CONFIG.push_notification_config.id
    )
    assert (
        result.push_notification_config.url
        == CALLBACK_CONFIG.push_notification_config.url
    )
    handler.on_get_task_push_notification_config.assert_awaited_once_with(
        params, ANY
    )

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_grpc_transport_get_task_callback(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> None:
    server_address, handler = grpc_server_and_handler
    agent_card.url = server_address

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    params = GetTaskPushNotificationConfigParams(
        id=CALLBACK_CONFIG.task_id,
        push_notification_config_id=CALLBACK_CONFIG.push_notification_config.id,
    )
    result = await transport.get_task_callback(request=params)

    assert result.task_id == CALLBACK_CONFIG.task_id
    assert (
        result.push_notification_config.id
        == CALLBACK_CONFIG.push_notification_config.id
    )
    assert (
        result.push_notification_config.url
        == CALLBACK_CONFIG.push_notification_config.url
    )
    handler.on_get_task_push_notification_config.assert_awaited_once()
    assert (
        handler.on_get_task_push_notification_config.call_args[0][0].id
        == CALLBACK_CONFIG.task_id
    )

    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'transport_setup_fixture',
    [
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('rest_setup', id='REST'),
    ],
)
async def test_http_transport_resubscribe(
    transport_setup_fixture: str, request
) -> None:
    transport_setup: TransportSetup = request.getfixturevalue(
        transport_setup_fixture
    )
    transport = transport_setup.transport
    handler = transport_setup.handler

    params = TaskIdParams(id=RESUBSCRIBE_EVENT.task_id)
    stream = transport.resubscribe(request=params)
    first_event = await anext(stream)

    assert first_event.task_id == RESUBSCRIBE_EVENT.task_id
    handler.on_resubscribe_to_task.assert_called_once_with(params, ANY)

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_grpc_transport_resubscribe(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> None:
    server_address, handler = grpc_server_and_handler
    agent_card.url = server_address

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    params = TaskIdParams(id=RESUBSCRIBE_EVENT.task_id)
    stream = transport.resubscribe(request=params)
    first_event = await anext(stream)

    assert first_event.task_id == RESUBSCRIBE_EVENT.task_id
    handler.on_resubscribe_to_task.assert_called_once()
    assert (
        handler.on_resubscribe_to_task.call_args[0][0].id
        == RESUBSCRIBE_EVENT.task_id
    )

    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'transport_setup_fixture',
    [
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('rest_setup', id='REST'),
    ],
)
async def test_http_transport_get_card(
    transport_setup_fixture: str, request, agent_card: AgentCard
) -> None:
    transport_setup: TransportSetup = request.getfixturevalue(
        transport_setup_fixture
    )
    transport = transport_setup.transport
    # Get the base card.
    result = await transport.get_card()

    assert result.name == agent_card.name
    assert transport.agent_card.name == agent_card.name
    assert transport._needs_extended_card is False

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_http_transport_get_authenticated_card(
    agent_card: AgentCard,
    mock_request_handler: AsyncMock,
) -> None:
    agent_card.supports_authenticated_extended_card = True
    extended_agent_card = agent_card.model_copy(deep=True)
    extended_agent_card.name = 'Extended Agent Card'

    app_builder = A2ARESTFastAPIApplication(
        agent_card,
        mock_request_handler,
        extended_agent_card=extended_agent_card,
    )
    app = app_builder.build()
    httpx_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))

    transport = RestTransport(httpx_client=httpx_client, agent_card=agent_card)
    result = await transport.get_card()
    assert result.name == extended_agent_card.name
    assert transport.agent_card.name == extended_agent_card.name
    assert transport._needs_extended_card is False

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_grpc_transport_get_card(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> None:
    server_address, _ = grpc_server_and_handler
    agent_card.url = server_address

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    # The transport starts with a minimal card, get_card() fetches the full one
    transport.agent_card.supports_authenticated_extended_card = True
    result = await transport.get_card()

    assert result.name == agent_card.name
    assert transport.agent_card.name == agent_card.name
    assert transport._needs_extended_card is False

    await transport.close()
