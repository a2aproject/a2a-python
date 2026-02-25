import asyncio

from collections.abc import AsyncGenerator
from typing import Any, NamedTuple
from unittest.mock import AsyncMock, patch

import grpc
import httpx
import pytest
import pytest_asyncio

from cryptography.hazmat.primitives.asymmetric import ec
from google.protobuf.json_format import MessageToDict

from a2a.client import ClientConfig
from a2a.client.base_client import BaseClient
from a2a.client.client_factory import ClientFactory
from a2a.server.apps import A2AFastAPIApplication, A2ARESTFastAPIApplication
from a2a.server.request_handlers import GrpcHandler, RequestHandler
from a2a.types import a2a_pb2_grpc
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    CancelTaskRequest,
    CreateTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    Part,
    PushNotificationConfig,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.constants import TransportProtocol
from a2a.utils.signing import (
    create_agent_card_signer,
    create_signature_verifier,
)


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
    push_notification_config=PushNotificationConfig(
        id='pnc-abc', url='http://callback.example.com', token=''
    ),
)

RESUBSCRIBE_EVENT = TaskStatusUpdateEvent(
    task_id='task-resub-456',
    context_id='ctx-resub-789',
    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
)

LIST_TASKS_RESPONSE = ListTasksResponse(
    tasks=[TASK_FROM_BLOCKING, GET_TASK_RESPONSE],
    next_page_token='page-2',
    total_size=12,
    page_size=10,
)


def create_key_provider(verification_key: Any):
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
    handler.on_list_tasks.return_value = LIST_TASKS_RESPONSE
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
                protocol_binding=TransportProtocol.HTTP_JSON,
                url='http://testserver',
            ),
            AgentInterface(
                protocol_binding=TransportProtocol.JSONRPC,
                url='http://testserver',
            ),
            AgentInterface(
                protocol_binding=TransportProtocol.GRPC, url='localhost:50051'
            ),
        ],
    )


class ClientSetup(NamedTuple):
    """Holds the client and handler for a given test."""

    client: BaseClient
    handler: AsyncMock


# --- HTTP/JSON-RPC/REST Setup ---


@pytest.fixture
def http_base_setup(mock_request_handler: AsyncMock, agent_card: AgentCard):
    """A base fixture to patch the sse-starlette event loop issue."""
    from sse_starlette import sse

    sse.AppStatus.should_exit_event = asyncio.Event()  # type: ignore[attr-defined]
    yield mock_request_handler, agent_card


@pytest_asyncio.fixture
async def rest_setup(http_base_setup) -> AsyncGenerator[ClientSetup, None]:
    mock_request_handler, agent_card = http_base_setup
    app_builder = A2ARESTFastAPIApplication(agent_card, mock_request_handler)
    app = app_builder.build()
    httpx_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))

    factory = ClientFactory(
        config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
        )
    )
    client = factory.create(agent_card)
    yield ClientSetup(client=client, handler=mock_request_handler)
    await client.close()


@pytest_asyncio.fixture
async def jsonrpc_setup(http_base_setup) -> AsyncGenerator[ClientSetup, None]:
    mock_request_handler, agent_card = http_base_setup
    app_builder = A2AFastAPIApplication(
        agent_card, mock_request_handler, extended_agent_card=agent_card
    )
    app = app_builder.build()
    httpx_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))

    factory = ClientFactory(
        config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.JSONRPC],
        )
    )
    client = factory.create(agent_card)
    yield ClientSetup(client=client, handler=mock_request_handler)
    await client.close()


@pytest_asyncio.fixture
async def grpc_setup(
    agent_card: AgentCard, mock_request_handler: AsyncMock
) -> AsyncGenerator[ClientSetup, None]:
    server = grpc.aio.server()
    port = server.add_insecure_port('[::]:0')
    server_address = f'localhost:{port}'

    grpc_agent_card = AgentCard()
    grpc_agent_card.CopyFrom(agent_card)

    for interface in grpc_agent_card.supported_interfaces:
        if interface.protocol_binding == TransportProtocol.GRPC:
            interface.url = server_address
            break
    else:
        raise ValueError('No gRPC interface found in agent card')

    servicer = GrpcHandler(grpc_agent_card, mock_request_handler)
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(servicer, server)
    await server.start()

    factory = ClientFactory(
        config=ClientConfig(
            grpc_channel_factory=lambda url: grpc.aio.insecure_channel(url),
            supported_protocol_bindings=[TransportProtocol.GRPC],
        )
    )
    client = factory.create(grpc_agent_card)
    yield ClientSetup(client=client, handler=mock_request_handler)

    await client.close()
    await server.stop(0)


@pytest.fixture(
    params=[
        pytest.param('rest_setup', id='REST'),
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('grpc_setup', id='gRPC'),
    ]
)
def client_setups(request) -> ClientSetup:
    """Parametrized fixture that runs tests against all supported transports."""
    return request.getfixturevalue(request.param)


# --- The Integration Tests ---


@pytest.mark.asyncio
async def test_client_sends_message_streaming(client_setups):
    client = client_setups.client
    handler = client_setups.handler

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-integration-test',
        parts=[Part(text='Hello, integration test!')],
    )

    stream = client.send_message(request=message_to_send)
    events = [event async for event in stream]

    assert len(events) == 1
    stream_response, task = events[0]

    # StreamResponse wraps the Task in its 'task' field
    assert stream_response.task.id == TASK_FROM_STREAM.id
    assert stream_response.task.context_id == TASK_FROM_STREAM.context_id
    assert task
    assert task.id == TASK_FROM_STREAM.id

    handler.on_message_send_stream.assert_called_once()
    call_args, _ = handler.on_message_send_stream.call_args
    received_params: SendMessageRequest = call_args[0]

    assert received_params.message.message_id == message_to_send.message_id
    assert (
        received_params.message.parts[0].text == message_to_send.parts[0].text
    )


@pytest.mark.asyncio
async def test_client_sends_message_blocking(client_setups):
    client = client_setups.client
    handler = client_setups.handler

    # Disable streaming to test the blocking/non-streaming transport route
    client._card.capabilities.streaming = False

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-integration-test-blocking',
        parts=[Part(text='Hello, blocking test!')],
    )
    configuration = SendMessageConfiguration(blocking=True)

    stream = client.send_message(
        request=message_to_send, configuration=configuration
    )
    events = [event async for event in stream]

    stream_response, task = events[-1]

    assert task
    assert task.id == TASK_FROM_BLOCKING.id
    assert task.context_id == TASK_FROM_BLOCKING.context_id
    assert stream_response.task.id == TASK_FROM_BLOCKING.id

    handler.on_message_send.assert_awaited_once()
    call_args, _ = handler.on_message_send.call_args
    received_params: SendMessageRequest = call_args[0]

    assert received_params.message.message_id == message_to_send.message_id
    assert (
        received_params.message.parts[0].text == message_to_send.parts[0].text
    )


@pytest.mark.asyncio
async def test_client_get_task(client_setups):
    client = client_setups.client
    handler = client_setups.handler

    params = GetTaskRequest(id=GET_TASK_RESPONSE.id)
    result = await client.get_task(request=params)

    assert result.id == GET_TASK_RESPONSE.id
    handler.on_get_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_list_tasks(client_setups):
    client = client_setups.client
    handler = client_setups.handler

    params = ListTasksRequest(page_size=10, page_token='page-1')
    result = await client.list_tasks(request=params)

    assert len(result.tasks) == 2
    assert result.next_page_token == 'page-2'
    assert result.total_size == 12
    assert result.page_size == 10
    handler.on_list_tasks.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_cancel_task(client_setups):
    client = client_setups.client
    handler = client_setups.handler

    params = CancelTaskRequest(id=f'{CANCEL_TASK_RESPONSE.id}')
    result = await client.cancel_task(request=params)

    assert result.id == CANCEL_TASK_RESPONSE.id
    handler.on_cancel_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_set_task_callback(client_setups):
    client = client_setups.client
    handler = client_setups.handler

    params = CreateTaskPushNotificationConfigRequest(
        task_id='task-callback-123',
        config=CALLBACK_CONFIG.push_notification_config,
    )
    result = await client.set_task_callback(request=params)

    assert (
        result.push_notification_config.id
        == CALLBACK_CONFIG.push_notification_config.id
    )
    assert (
        result.push_notification_config.url
        == CALLBACK_CONFIG.push_notification_config.url
    )
    handler.on_create_task_push_notification_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_get_task_callback(client_setups):
    client = client_setups.client
    handler = client_setups.handler

    params = GetTaskPushNotificationConfigRequest(
        task_id=f'{CALLBACK_CONFIG.task_id}',
        id=CALLBACK_CONFIG.push_notification_config.id,
    )
    result = await client.get_task_callback(request=params)

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


@pytest.mark.asyncio
async def test_client_resubscribe(client_setups):
    client = client_setups.client
    handler = client_setups.handler

    params = SubscribeToTaskRequest(id=RESUBSCRIBE_EVENT.task_id)
    stream = client.subscribe(request=params)
    first_event = await anext(stream)
    stream_response, _ = first_event

    assert stream_response.status_update.task_id == RESUBSCRIBE_EVENT.task_id
    handler.on_subscribe_to_task.assert_called_once()


# Signed card behavior and special extension headers overrides tests below


@pytest.mark.asyncio
async def test_json_transport_base_client_send_message_with_extensions(
    jsonrpc_setup: ClientSetup, agent_card: AgentCard
) -> None:
    """
    Integration test for BaseClient with JSON-RPC transport to ensure extensions are included in headers.
    """
    client = jsonrpc_setup.client
    transport = client._transport

    # Disable streaming
    client._card.capabilities.streaming = False
    client._config.streaming = False

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


@pytest.mark.asyncio
async def test_json_transport_get_authenticated_card(
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

    client = await ClientFactory.connect(
        agent=agent_card.supported_interfaces[0].url,
        client_config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
        ),
    )

    result = await client.get_extended_agent_card()

    assert result.name == extended_agent_card.name
    assert client._card.name == extended_agent_card.name
    assert client._transport._needs_extended_card is False

    await client.close()


@pytest.mark.asyncio
async def test_grpc_transport_get_card(
    mock_request_handler: AsyncMock,
    agent_card: AgentCard,
) -> None:
    server = grpc.aio.server()
    port = server.add_insecure_port('[::]:0')
    server_address = f'localhost:{port}'

    grpc_agent_card = AgentCard()
    grpc_agent_card.CopyFrom(agent_card)
    for interface in grpc_agent_card.supported_interfaces:
        if interface.protocol_binding == TransportProtocol.GRPC:
            interface.url = server_address
            break

    servicer = GrpcHandler(grpc_agent_card, mock_request_handler)
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(servicer, server)
    await server.start()

    factory = ClientFactory(
        config=ClientConfig(
            grpc_channel_factory=lambda url: grpc.aio.insecure_channel(url),
            supported_protocol_bindings=[TransportProtocol.GRPC],
        )
    )
    client = factory.create(grpc_agent_card)

    # The transport starts with a minimal card, get_extended_agent_card() fetches the full one
    assert client._card is not None
    client._card.capabilities.extended_agent_card = True
    result = await client.get_extended_agent_card()

    assert result.name == grpc_agent_card.name
    assert client._card.name == grpc_agent_card.name
    assert client._transport._needs_extended_card is False

    await client.close()
    await server.stop(0)


@pytest.mark.asyncio
async def test_json_transport_get_signed_base_card(
    mock_request_handler: AsyncMock, agent_card: AgentCard
) -> None:
    """Tests fetching and verifying a symmetrically signed AgentCard via JSON-RPC.

    The client transport is initialized without a card, forcing it to fetch
    the base card from the server. The server signs the card using HS384.
    The client then verifies the signature.
    """
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

    agent_url = agent_card.supported_interfaces[0].url
    signature_verifier = create_signature_verifier(
        create_key_provider(key), ['HS384']
    )

    client = await ClientFactory.connect(
        agent=agent_url,
        client_config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.JSONRPC],
        ),
        signature_verifier=signature_verifier,
    )

    # We didn't pass card, so ClientFactory resolved it
    assert client._card.name == agent_card.name
    assert len(client._card.signatures) == 1
    assert client._transport.agent_card is not None
    assert client._transport.agent_card.name == agent_card.name
    assert client._transport._needs_extended_card is False

    await client.close()


@pytest.mark.asyncio
async def test_json_transport_get_signed_extended_card(
    mock_request_handler: AsyncMock, agent_card: AgentCard
) -> None:
    """Tests fetching and verifying an asymmetrically signed extended AgentCard via JSON-RPC.

    The client has a base card and fetches the extended card, which is signed
    by the server using ES256. The client verifies the signature on the
    received extended card.
    """
    agent_card.capabilities.extended_agent_card = True
    extended_agent_card = AgentCard()
    extended_agent_card.CopyFrom(agent_card)
    extended_agent_card.name = 'Extended Agent Card'

    # Setup signing on the server side
    private_key = ec.generate_private_key(ec.SECP256R1())
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

    factory = ClientFactory(
        config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.JSONRPC],
        )
    )
    client = factory.create(agent_card)

    # Get the card, this will trigger verification in get_card
    signature_verifier = create_signature_verifier(
        create_key_provider(public_key), ['HS384', 'ES256']
    )
    result = await client.get_extended_agent_card(
        signature_verifier=signature_verifier
    )
    assert result.name == extended_agent_card.name
    assert result.signatures is not None
    assert len(result.signatures) == 1
    assert client._card.name == extended_agent_card.name
    assert client._transport._needs_extended_card is False

    await client.close()


@pytest.mark.asyncio
async def test_json_transport_get_signed_base_and_extended_cards(
    mock_request_handler: AsyncMock, agent_card: AgentCard
) -> None:
    """Tests fetching and verifying both base and extended cards via JSON-RPC when no card is initially provided.

    The client starts with no card. It first fetches the base card, which is
    signed. It then fetches the extended card, which is also signed. Both signatures
    are verified independently upon retrieval.
    """
    assert len(agent_card.signatures) == 0
    agent_card.capabilities.extended_agent_card = True
    extended_agent_card = AgentCard()
    extended_agent_card.CopyFrom(agent_card)
    extended_agent_card.name = 'Extended Agent Card'

    # Setup signing on the server side
    private_key = ec.generate_private_key(ec.SECP256R1())
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

    agent_url = agent_card.supported_interfaces[0].url
    signature_verifier = create_signature_verifier(
        create_key_provider(public_key), ['HS384', 'ES256', 'RS256']
    )

    client = await ClientFactory.connect(
        agent=agent_url,
        client_config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.JSONRPC],
        ),
        signature_verifier=signature_verifier,
    )

    # 3. Fetch extended card via client
    result = await client.get_extended_agent_card(
        signature_verifier=signature_verifier
    )
    assert result.name == extended_agent_card.name
    assert len(result.signatures) == 1
    assert client._card is not None
    assert client._card.name == extended_agent_card.name
    assert client._transport._needs_extended_card is False

    await client.close()


@pytest.mark.asyncio
async def test_rest_transport_get_signed_card(
    mock_request_handler: AsyncMock, agent_card: AgentCard
) -> None:
    """Tests fetching and verifying signed base and extended cards via REST.

    The client starts with no card. It first fetches the base card, which is
    signed. It then fetches the extended card, which is also signed. Both signatures
    are verified independently upon retrieval.
    """
    agent_card.capabilities.extended_agent_card = True
    extended_agent_card = AgentCard()
    extended_agent_card.CopyFrom(agent_card)
    extended_agent_card.name = 'Extended Agent Card'

    # Setup signing on the server side
    private_key = ec.generate_private_key(ec.SECP256R1())
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

    agent_url = agent_card.supported_interfaces[0].url
    signature_verifier = create_signature_verifier(
        create_key_provider(public_key), ['HS384', 'ES256', 'RS256']
    )

    client = await ClientFactory.connect(
        agent=agent_url,
        client_config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
        ),
        signature_verifier=signature_verifier,
    )

    # 3. Fetch extended card
    result = await client.get_extended_agent_card(
        signature_verifier=signature_verifier
    )
    assert result.name == extended_agent_card.name
    assert result.signatures is not None
    assert len(result.signatures) == 1
    assert client._card is not None
    assert client._card.name == extended_agent_card.name
    assert client._transport._needs_extended_card is False

    await client.close()


@pytest.mark.asyncio
async def test_grpc_transport_get_signed_card(
    mock_request_handler: AsyncMock, agent_card: AgentCard
) -> None:
    """Tests fetching and verifying a signed AgentCard via gRPC."""
    # Setup signing on the server side
    agent_card.capabilities.extended_agent_card = True

    private_key = ec.generate_private_key(ec.SECP256R1())
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
    for interface in agent_card.supported_interfaces:
        if interface.protocol_binding == TransportProtocol.GRPC:
            interface.url = server_address
            break

    servicer = GrpcHandler(
        agent_card,
        mock_request_handler,
        card_modifier=signer,
    )
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(servicer, server)
    await server.start()

    client = None
    try:
        factory = ClientFactory(
            config=ClientConfig(
                grpc_channel_factory=lambda url: grpc.aio.insecure_channel(url),
                supported_protocol_bindings=[TransportProtocol.GRPC],
            )
        )
        client = factory.create(agent_card)
        client._card = AgentCard()
        client._transport.agent_card = None
        assert client._transport._needs_extended_card is True

        # Get the card, this will trigger verification in get_card
        signature_verifier = create_signature_verifier(
            create_key_provider(public_key), ['HS384', 'ES256', 'RS256']
        )
        result = await client.get_extended_agent_card(
            signature_verifier=signature_verifier
        )
        assert result.signatures is not None
        assert len(result.signatures) == 1
        assert client._transport._needs_extended_card is False
    finally:
        if client:
            await client.close()
        await server.stop(0)  # Gracefully stop the server
