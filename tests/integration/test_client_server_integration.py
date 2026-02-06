import asyncio
from collections.abc import AsyncGenerator
from typing import NamedTuple, Any
from unittest.mock import ANY, AsyncMock, patch

import grpc
import httpx
import pytest
import pytest_asyncio
from google.protobuf.json_format import MessageToDict
from grpc.aio import Channel

from jwt.api_jwk import PyJWK
from a2a.client import ClientConfig
from a2a.client.base_client import BaseClient
from a2a.client.transports import JsonRpcTransport, RestTransport
from a2a.client.transports.base import ClientTransport
from a2a.client.transports.grpc import GrpcTransport
from a2a.types import a2a_pb2_grpc
from a2a.server.apps import A2AFastAPIApplication, A2ARESTFastAPIApplication
from a2a.server.request_handlers import GrpcHandler, RequestHandler
from a2a.utils.constants import (
    TRANSPORT_HTTP_JSON,
    TRANSPORT_GRPC,
    TRANSPORT_JSONRPC,
)
from a2a.utils.signing import (
    create_agent_card_signer,
    create_signature_verifier,
)
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    CancelTaskRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    Message,
    Part,
    PushNotificationConfig,
    Role,
    SendMessageRequest,
    CreateTaskPushNotificationConfigRequest,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from cryptography.hazmat.primitives import asymmetric

# --- Test Constants ---

TASK_FROM_STREAM = Task(
    id='task-123-stream',
    context_id='ctx-456-stream',
    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
)

TASK_FROM_BLOCKING = Task(
    id='task-789-blocking',
    context_id='ctx-101-blocking',
    status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED),
)

GET_TASK_RESPONSE = Task(
    id='task-get-456',
    context_id='ctx-get-789',
    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
)

CANCEL_TASK_RESPONSE = Task(
    id='task-cancel-789',
    context_id='ctx-cancel-101',
    status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
)

CALLBACK_CONFIG = TaskPushNotificationConfig(
    task_id='task-callback-123',
    id='pnc-abc',
    push_notification_config=PushNotificationConfig(
        id='pnc-abc', url='http://callback.example.com', token=''
    ),
)

RESUBSCRIBE_EVENT = TaskStatusUpdateEvent(
    task_id='task-resub-456',
    context_id='ctx-resub-789',
    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
)


def create_key_provider(verification_key: PyJWK | str | bytes):
    """Creates a key provider function for testing."""

    def key_provider(kid: str | None, jku: str | None):
        return verification_key

    return key_provider


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
    handler.on_create_task_push_notification_config.return_value = (
        CALLBACK_CONFIG
    )
    handler.on_get_task_push_notification_config.return_value = CALLBACK_CONFIG

    async def resubscribe_side_effect(*args, **kwargs):
        yield RESUBSCRIBE_EVENT

    handler.on_subscribe_to_task.side_effect = resubscribe_side_effect

    return handler


@pytest.fixture
def agent_card() -> AgentCard:
    """Provides a sample AgentCard for tests."""
    return AgentCard(
        name='Test Agent',
        description='An agent for integration testing.',
        version='1.0.0',
        capabilities=AgentCapabilities(streaming=True, push_notifications=True),
        skills=[],
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        supported_interfaces=[
            AgentInterface(
                protocol_binding=TRANSPORT_HTTP_JSON,
                url='http://testserver',
            ),
            AgentInterface(protocol_binding='grpc', url='localhost:50051'),
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
        role=Role.ROLE_USER,
        message_id='msg-integration-test',
        parts=[Part(text='Hello, integration test!')],
    )
    params = SendMessageRequest(message=message_to_send)

    stream = transport.send_message_streaming(request=params)
    events = [event async for event in stream]

    assert len(events) == 1
    first_event = events[0]

    # StreamResponse wraps the Task in its 'task' field
    assert first_event.task.id == TASK_FROM_STREAM.id
    assert first_event.task.context_id == TASK_FROM_STREAM.context_id

    handler.on_message_send_stream.assert_called_once()
    call_args, _ = handler.on_message_send_stream.call_args
    received_params: SendMessageRequest = call_args[0]

    assert received_params.message.message_id == message_to_send.message_id
    assert (
        received_params.message.parts[0].text == message_to_send.parts[0].text
    )

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

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-grpc-integration-test',
        parts=[Part(text='Hello, gRPC integration test!')],
    )
    params = SendMessageRequest(message=message_to_send)

    stream = transport.send_message_streaming(request=params)
    first_event = await anext(stream)

    # StreamResponse wraps the Task in its 'task' field
    assert first_event.task.id == TASK_FROM_STREAM.id
    assert first_event.task.context_id == TASK_FROM_STREAM.context_id

    handler.on_message_send_stream.assert_called_once()
    call_args, _ = handler.on_message_send_stream.call_args
    received_params: SendMessageRequest = call_args[0]

    assert received_params.message.message_id == message_to_send.message_id
    assert (
        received_params.message.parts[0].text == message_to_send.parts[0].text
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
        role=Role.ROLE_USER,
        message_id='msg-integration-test-blocking',
        parts=[Part(text='Hello, blocking test!')],
    )
    params = SendMessageRequest(message=message_to_send)

    result = await transport.send_message(request=params)

    # SendMessageResponse wraps Task in its 'task' field
    assert result.task.id == TASK_FROM_BLOCKING.id
    assert result.task.context_id == TASK_FROM_BLOCKING.context_id

    handler.on_message_send.assert_awaited_once()
    call_args, _ = handler.on_message_send.call_args
    received_params: SendMessageRequest = call_args[0]

    assert received_params.message.message_id == message_to_send.message_id
    assert (
        received_params.message.parts[0].text == message_to_send.parts[0].text
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

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-grpc-integration-test-blocking',
        parts=[Part(text='Hello, gRPC blocking test!')],
    )
    params = SendMessageRequest(message=message_to_send)

    result = await transport.send_message(request=params)

    # SendMessageResponse wraps Task in its 'task' field
    assert result.task.id == TASK_FROM_BLOCKING.id
    assert result.task.context_id == TASK_FROM_BLOCKING.context_id

    handler.on_message_send.assert_awaited_once()
    call_args, _ = handler.on_message_send.call_args
    received_params: SendMessageRequest = call_args[0]

    assert received_params.message.message_id == message_to_send.message_id
    assert (
        received_params.message.parts[0].text == message_to_send.parts[0].text
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

    # Use GetTaskRequest with name (AIP resource format)
    params = GetTaskRequest(id=GET_TASK_RESPONSE.id)
    result = await transport.get_task(request=params)

    assert result.id == GET_TASK_RESPONSE.id
    handler.on_get_task.assert_awaited_once()

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_grpc_transport_get_task(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> None:
    server_address, handler = grpc_server_and_handler

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    # Use GetTaskRequest with name (AIP resource format)
    params = GetTaskRequest(id=f'{GET_TASK_RESPONSE.id}')
    result = await transport.get_task(request=params)

    assert result.id == GET_TASK_RESPONSE.id
    handler.on_get_task.assert_awaited_once()

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

    # Use CancelTaskRequest with name (AIP resource format)
    params = CancelTaskRequest(id=f'{CANCEL_TASK_RESPONSE.id}')
    result = await transport.cancel_task(request=params)

    assert result.id == CANCEL_TASK_RESPONSE.id
    handler.on_cancel_task.assert_awaited_once()

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_grpc_transport_cancel_task(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> None:
    server_address, handler = grpc_server_and_handler

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    # Use CancelTaskRequest with name (AIP resource format)
    params = CancelTaskRequest(id=f'{CANCEL_TASK_RESPONSE.id}')
    result = await transport.cancel_task(request=params)

    assert result.id == CANCEL_TASK_RESPONSE.id
    handler.on_cancel_task.assert_awaited_once()

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

    # Create CreateTaskPushNotificationConfigRequest with required fields
    params = CreateTaskPushNotificationConfigRequest(
        task_id='task-callback-123',
        config_id='pnc-abc',
        config=CALLBACK_CONFIG.push_notification_config,
    )
    result = await transport.set_task_callback(request=params)

    # TaskPushNotificationConfig has 'name' and 'push_notification_config'
    assert result.id == CALLBACK_CONFIG.id
    assert (
        result.push_notification_config.id
        == CALLBACK_CONFIG.push_notification_config.id
    )
    assert (
        result.push_notification_config.url
        == CALLBACK_CONFIG.push_notification_config.url
    )
    handler.on_create_task_push_notification_config.assert_awaited_once()

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_grpc_transport_set_task_callback(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> None:
    server_address, handler = grpc_server_and_handler

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    # Create CreateTaskPushNotificationConfigRequest with required fields
    params = CreateTaskPushNotificationConfigRequest(
        task_id='task-callback-123',
        config_id='pnc-abc',
        config=CALLBACK_CONFIG.push_notification_config,
    )
    result = await transport.set_task_callback(request=params)

    # TaskPushNotificationConfig has 'name' and 'push_notification_config'
    assert result.id == CALLBACK_CONFIG.id
    assert (
        result.push_notification_config.id
        == CALLBACK_CONFIG.push_notification_config.id
    )
    assert (
        result.push_notification_config.url
        == CALLBACK_CONFIG.push_notification_config.url
    )
    handler.on_create_task_push_notification_config.assert_awaited_once()

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

    # Use GetTaskPushNotificationConfigRequest with name field (resource name)
    params = GetTaskPushNotificationConfigRequest(
        task_id=f'{CALLBACK_CONFIG.task_id}', id=CALLBACK_CONFIG.id
    )
    result = await transport.get_task_callback(request=params)

    # TaskPushNotificationConfig has 'name' and 'push_notification_config'
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

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_grpc_transport_get_task_callback(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> None:
    server_address, handler = grpc_server_and_handler

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    # Use GetTaskPushNotificationConfigRequest with name field (resource name)
    params = GetTaskPushNotificationConfigRequest(
        task_id=f'{CALLBACK_CONFIG.task_id}', id=CALLBACK_CONFIG.id
    )
    result = await transport.get_task_callback(request=params)

    # TaskPushNotificationConfig has 'name' and 'push_notification_config'
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

    # Use SubscribeToTaskRequest with name (AIP resource format)
    params = SubscribeToTaskRequest(id=RESUBSCRIBE_EVENT.task_id)
    stream = transport.subscribe(request=params)
    first_event = await anext(stream)

    # StreamResponse wraps the status update in its 'status_update' field
    assert first_event.status_update.task_id == RESUBSCRIBE_EVENT.task_id
    handler.on_subscribe_to_task.assert_called_once()

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_grpc_transport_resubscribe(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> None:
    server_address, handler = grpc_server_and_handler

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    # Use SubscribeToTaskRequest with name (AIP resource format)
    params = SubscribeToTaskRequest(id=RESUBSCRIBE_EVENT.task_id)
    stream = transport.subscribe(request=params)
    first_event = await anext(stream)

    # StreamResponse wraps the status update in its 'status_update' field
    assert first_event.status_update.task_id == RESUBSCRIBE_EVENT.task_id
    handler.on_subscribe_to_task.assert_called_once()

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
    # Access the base card from the agent_card property.
    result = transport.agent_card

    assert result.name == agent_card.name
    assert transport.agent_card.name == agent_card.name
    # Only check _needs_extended_card if the transport supports it
    if hasattr(transport, '_needs_extended_card'):
        assert transport._needs_extended_card is False

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_http_transport_get_authenticated_card(
    agent_card: AgentCard,
    mock_request_handler: AsyncMock,
) -> None:
    agent_card.capabilities.extended_agent_card = True
    # Create a copy of the agent card for the extended card
    extended_agent_card = AgentCard()
    extended_agent_card.CopyFrom(agent_card)
    extended_agent_card.name = 'Extended Agent Card'

    app_builder = A2ARESTFastAPIApplication(
        agent_card,
        mock_request_handler,
        extended_agent_card=extended_agent_card,
    )
    app = app_builder.build()
    httpx_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))

    transport = RestTransport(httpx_client=httpx_client, agent_card=agent_card)
    result = await transport.get_extended_agent_card()
    assert result.name == extended_agent_card.name
    assert transport.agent_card is not None
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

    def channel_factory(address: str) -> Channel:
        return grpc.aio.insecure_channel(address)

    channel = channel_factory(server_address)
    transport = GrpcTransport(channel=channel, agent_card=agent_card)

    # The transport starts with a minimal card, get_extended_agent_card() fetches the full one
    assert transport.agent_card is not None
    transport.agent_card.capabilities.extended_agent_card = True
    result = await transport.get_extended_agent_card()

    assert result.name == agent_card.name
    assert transport.agent_card.name == agent_card.name
    assert transport._needs_extended_card is False

    await transport.close()


@pytest.mark.asyncio
async def test_json_transport_base_client_send_message_with_extensions(
    jsonrpc_setup: TransportSetup, agent_card: AgentCard
) -> None:
    """
    Integration test for BaseClient with JSON-RPC transport to ensure extensions are included in headers.
    """
    transport = jsonrpc_setup.transport
    agent_card.capabilities.streaming = False

    # Create a BaseClient instance
    client = BaseClient(
        card=agent_card,
        config=ClientConfig(streaming=False),
        transport=transport,
        consumers=[],
        middleware=[],
    )

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-integration-test-extensions',
        parts=[Part(text='Hello, extensions test!')],
    )
    extensions = [
        'https://example.com/test-ext/v1',
        'https://example.com/test-ext/v2',
    ]

    with patch.object(
        transport, '_send_request', new_callable=AsyncMock
    ) as mock_send_request:
        # Mock returns a JSON-RPC response with SendMessageResponse structure
        mock_send_request.return_value = {
            'id': '123',
            'jsonrpc': '2.0',
            'result': {'task': MessageToDict(TASK_FROM_BLOCKING)},
        }

        # Call send_message on the BaseClient
        async for _ in client.send_message(
            request=message_to_send, extensions=extensions
        ):
            pass

        mock_send_request.assert_called_once()
        call_args, _ = mock_send_request.call_args
        kwargs = call_args[1]
        headers = kwargs.get('headers', {})
        assert 'X-A2A-Extensions' in headers
        assert (
            headers['X-A2A-Extensions']
            == 'https://example.com/test-ext/v1,https://example.com/test-ext/v2'
        )

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_json_transport_get_signed_base_card(
    jsonrpc_setup: TransportSetup, agent_card: AgentCard
) -> None:
    """Tests fetching and verifying a symmetrically signed AgentCard via JSON-RPC.

    The client transport is initialized without a card, forcing it to fetch
    the base card from the server. The server signs the card using HS384.
    The client then verifies the signature.
    """
    mock_request_handler = jsonrpc_setup.handler
    agent_card.capabilities.extended_agent_card = False

    # Setup signing on the server side
    key = 'key12345'
    signer = create_agent_card_signer(
        signing_key=key,
        protected_header={
            'alg': 'HS384',
            'kid': 'testkey',
            'jku': None,
            'typ': 'JOSE',
        },
    )

    app_builder = A2AFastAPIApplication(
        agent_card,
        mock_request_handler,
        card_modifier=signer,  # Sign the base card
    )
    app = app_builder.build()
    httpx_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))

    transport = JsonRpcTransport(
        httpx_client=httpx_client,
        url=agent_card.supported_interfaces[0].url,
        agent_card=None,
    )

    # Get the card, this will trigger verification in get_card
    signature_verifier = create_signature_verifier(
        create_key_provider(key), ['HS384']
    )
    result = await transport.get_extended_agent_card(
        signature_verifier=signature_verifier
    )
    assert result.name == agent_card.name
    assert len(result.signatures) == 1
    assert transport.agent_card is not None
    assert transport.agent_card.name == agent_card.name
    assert transport._needs_extended_card is False

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_json_transport_get_signed_extended_card(
    jsonrpc_setup: TransportSetup, agent_card: AgentCard
) -> None:
    """Tests fetching and verifying an asymmetrically signed extended AgentCard via JSON-RPC.

    The client has a base card and fetches the extended card, which is signed
    by the server using ES256. The client verifies the signature on the
    received extended card.
    """
    mock_request_handler = jsonrpc_setup.handler
    agent_card.capabilities.extended_agent_card = True
    extended_agent_card = AgentCard()
    extended_agent_card.CopyFrom(agent_card)
    extended_agent_card.name = 'Extended Agent Card'

    # Setup signing on the server side
    private_key = asymmetric.ec.generate_private_key(asymmetric.ec.SECP256R1())
    public_key = private_key.public_key()
    signer = create_agent_card_signer(
        signing_key=private_key,
        protected_header={
            'alg': 'ES256',
            'kid': 'testkey',
            'jku': None,
            'typ': 'JOSE',
        },
    )

    app_builder = A2AFastAPIApplication(
        agent_card,
        mock_request_handler,
        extended_agent_card=extended_agent_card,
        extended_card_modifier=lambda card, ctx: signer(
            card
        ),  # Sign the extended card
    )
    app = app_builder.build()
    httpx_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))

    transport = JsonRpcTransport(
        httpx_client=httpx_client, agent_card=agent_card
    )

    # Get the card, this will trigger verification in get_card
    signature_verifier = create_signature_verifier(
        create_key_provider(public_key), ['HS384', 'ES256']
    )
    result = await transport.get_extended_agent_card(
        signature_verifier=signature_verifier
    )
    assert result.name == extended_agent_card.name
    assert result.signatures is not None
    assert len(result.signatures) == 1
    assert transport.agent_card is not None
    assert transport.agent_card.name == extended_agent_card.name
    assert transport._needs_extended_card is False

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_json_transport_get_signed_base_and_extended_cards(
    jsonrpc_setup: TransportSetup, agent_card: AgentCard
) -> None:
    """Tests fetching and verifying both base and extended cards via JSON-RPC when no card is initially provided.

    The client starts with no card. It first fetches the base card, which is
    signed. It then fetches the extended card, which is also signed. Both signatures
    are verified independently upon retrieval.
    """
    mock_request_handler = jsonrpc_setup.handler
    assert len(agent_card.signatures) == 0
    agent_card.capabilities.extended_agent_card = True
    extended_agent_card = AgentCard()
    extended_agent_card.CopyFrom(agent_card)
    extended_agent_card.name = 'Extended Agent Card'

    # Setup signing on the server side
    private_key = asymmetric.ec.generate_private_key(asymmetric.ec.SECP256R1())
    public_key = private_key.public_key()
    signer = create_agent_card_signer(
        signing_key=private_key,
        protected_header={
            'alg': 'ES256',
            'kid': 'testkey',
            'jku': None,
            'typ': 'JOSE',
        },
    )

    app_builder = A2AFastAPIApplication(
        agent_card,
        mock_request_handler,
        extended_agent_card=extended_agent_card,
        card_modifier=signer,  # Sign the base card
        extended_card_modifier=lambda card, ctx: signer(
            card
        ),  # Sign the extended card
    )
    app = app_builder.build()
    httpx_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))

    transport = JsonRpcTransport(
        httpx_client=httpx_client,
        url=agent_card.supported_interfaces[0].url,
        agent_card=None,
    )

    # Get the card, this will trigger verification in get_card
    signature_verifier = create_signature_verifier(
        create_key_provider(public_key), ['HS384', 'ES256', 'RS256']
    )
    result = await transport.get_extended_agent_card(
        signature_verifier=signature_verifier
    )
    assert result.name == extended_agent_card.name
    assert len(result.signatures) == 1
    assert transport.agent_card is not None
    assert transport.agent_card.name == extended_agent_card.name
    assert transport._needs_extended_card is False

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_rest_transport_get_signed_card(
    rest_setup: TransportSetup, agent_card: AgentCard
) -> None:
    """Tests fetching and verifying signed base and extended cards via REST.

    The client starts with no card. It first fetches the base card, which is
    signed. It then fetches the extended card, which is also signed. Both signatures
    are verified independently upon retrieval.
    """
    mock_request_handler = rest_setup.handler
    agent_card.capabilities.extended_agent_card = True
    extended_agent_card = AgentCard()
    extended_agent_card.CopyFrom(agent_card)
    extended_agent_card.name = 'Extended Agent Card'

    # Setup signing on the server side
    private_key = asymmetric.ec.generate_private_key(asymmetric.ec.SECP256R1())
    public_key = private_key.public_key()
    signer = create_agent_card_signer(
        signing_key=private_key,
        protected_header={
            'alg': 'ES256',
            'kid': 'testkey',
            'jku': None,
            'typ': 'JOSE',
        },
    )

    app_builder = A2ARESTFastAPIApplication(
        agent_card,
        mock_request_handler,
        extended_agent_card=extended_agent_card,
        card_modifier=signer,  # Sign the base card
        extended_card_modifier=lambda card, ctx: signer(
            card
        ),  # Sign the extended card
    )
    app = app_builder.build()
    httpx_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))

    transport = RestTransport(
        httpx_client=httpx_client,
        url=agent_card.supported_interfaces[0].url,
        agent_card=None,
    )

    # Get the card, this will trigger verification in get_card
    signature_verifier = create_signature_verifier(
        create_key_provider(public_key), ['HS384', 'ES256', 'RS256']
    )
    result = await transport.get_extended_agent_card(
        signature_verifier=signature_verifier
    )
    assert result.name == extended_agent_card.name
    assert result.signatures is not None
    assert len(result.signatures) == 1
    assert transport.agent_card is not None
    assert transport.agent_card.name == extended_agent_card.name
    assert transport._needs_extended_card is False

    if hasattr(transport, 'close'):
        await transport.close()


@pytest.mark.asyncio
async def test_grpc_transport_get_signed_card(
    mock_request_handler: AsyncMock, agent_card: AgentCard
) -> None:
    """Tests fetching and verifying a signed AgentCard via gRPC."""
    # Setup signing on the server side
    agent_card.capabilities.extended_agent_card = True

    private_key = asymmetric.ec.generate_private_key(asymmetric.ec.SECP256R1())
    public_key = private_key.public_key()
    signer = create_agent_card_signer(
        signing_key=private_key,
        protected_header={
            'alg': 'ES256',
            'kid': 'testkey',
            'jku': None,
            'typ': 'JOSE',
        },
    )

    server = grpc.aio.server()
    port = server.add_insecure_port('[::]:0')
    server_address = f'localhost:{port}'
    agent_card.supported_interfaces[0].url = server_address

    servicer = GrpcHandler(
        agent_card,
        mock_request_handler,
        card_modifier=signer,
    )
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(servicer, server)
    await server.start()

    transport = None  # Initialize transport
    try:

        def channel_factory(address: str) -> Channel:
            return grpc.aio.insecure_channel(address)

        channel = channel_factory(server_address)
        transport = GrpcTransport(channel=channel, agent_card=agent_card)
        transport.agent_card = None
        assert transport._needs_extended_card is True

        # Get the card, this will trigger verification in get_card
        signature_verifier = create_signature_verifier(
            create_key_provider(public_key), ['HS384', 'ES256', 'RS256']
        )
        result = await transport.get_extended_agent_card(
            signature_verifier=signature_verifier
        )
        assert result.signatures is not None
        assert len(result.signatures) == 1
        assert transport._needs_extended_card is False
    finally:
        if transport:
            await transport.close()
        await server.stop(0)  # Gracefully stop the server
