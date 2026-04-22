import asyncio

from collections.abc import AsyncGenerator
from typing import Any, NamedTuple
from unittest.mock import ANY, AsyncMock, patch

import grpc
import httpx
import pytest
import pytest_asyncio

from cryptography.hazmat.primitives.asymmetric import ec
from google.protobuf.json_format import MessageToDict
from google.protobuf.timestamp_pb2 import Timestamp
from starlette.applications import Starlette

from a2a.client import Client, ClientConfig
from a2a.client.base_client import BaseClient
from a2a.client.card_resolver import A2ACardResolver
from a2a.client.client import ClientCallContext
from a2a.client.client_factory import ClientFactory
from a2a.client.service_parameters import (
    ServiceParametersFactory,
    with_a2a_extensions,
)
from a2a.client.transports import JsonRpcTransport, RestTransport

# Compat v0.3 imports for dedicated tests
from a2a.compat.v0_3 import a2a_v0_3_pb2_grpc
from a2a.compat.v0_3.grpc_handler import CompatGrpcHandler
from a2a.server.request_handlers import GrpcHandler, RequestHandler
from a2a.server.routes import (
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.request_handlers.default_request_handler import (
    LegacyRequestHandler,
)
from a2a.types import a2a_pb2_grpc
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    Part,
    Role,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.constants import (
    PROTOCOL_VERSION_CURRENT,
    VERSION_HEADER,
    TransportProtocol,
)
from a2a.utils.errors import (
    ContentTypeNotSupportedError,
    ExtendedAgentCardNotConfiguredError,
    ExtensionSupportRequiredError,
    InvalidAgentResponseError,
    PushNotificationNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
    VersionNotSupportedError,
)
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
    id='pnc-abc',
    url='http://callback.example.com',
    token='',
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
def mock_request_handler(agent_card) -> AsyncMock:
    """Provides a mock RequestHandler for the server-side handlers."""
    handler = AsyncMock(spec=RequestHandler)

    # Configure on_message_send for non-streaming calls
    handler._agent_card = agent_card
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
    handler.on_list_task_push_notification_configs.return_value = (
        ListTaskPushNotificationConfigsResponse(configs=[CALLBACK_CONFIG])
    )
    handler.on_delete_task_push_notification_config.return_value = None

    # Use async def to ensure it returns an awaitable
    async def get_extended_agent_card_mock(*args, **kwargs):
        return agent_card

    handler.on_get_extended_agent_card.side_effect = (
        get_extended_agent_card_mock  # type: ignore[union-attr]
    )

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
        capabilities=AgentCapabilities(
            streaming=True, push_notifications=True, extended_agent_card=True
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
                protocol_binding=TransportProtocol.GRPC, url='localhost:50051'
            ),
        ],
    )


class TransportSetup(NamedTuple):
    """Holds the client and handler for a given test."""

    client: Client
    handler: RequestHandler | AsyncMock


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
    agent_card_routes = create_agent_card_routes(
        agent_card=agent_card, card_url='/'
    )
    jsonrpc_routes = create_jsonrpc_routes(
        request_handler=mock_request_handler, rpc_url='/'
    )
    app = Starlette(routes=[*agent_card_routes, *jsonrpc_routes])
    httpx_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    factory = ClientFactory(
        config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.JSONRPC],
        )
    )
    client = factory.create(agent_card)
    return TransportSetup(client=client, handler=mock_request_handler)


@pytest.fixture
def rest_setup(http_base_setup) -> TransportSetup:
    """Sets up the RestTransport and in-memory server."""
    mock_request_handler, agent_card = http_base_setup
    rest_routes = create_rest_routes(mock_request_handler)
    agent_card_routes = create_agent_card_routes(
        agent_card=agent_card, card_url='/'
    )
    app = Starlette(routes=[*rest_routes, *agent_card_routes])
    httpx_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app))
    factory = ClientFactory(
        config=ClientConfig(
            httpx_client=httpx_client,
            supported_protocol_bindings=[TransportProtocol.HTTP_JSON],
        )
    )
    client = factory.create(agent_card)
    return TransportSetup(client=client, handler=mock_request_handler)


@pytest_asyncio.fixture
async def grpc_setup(
    grpc_server_and_handler: tuple[str, AsyncMock],
    agent_card: AgentCard,
) -> TransportSetup:
    """Sets up the GrpcTransport and in-process server."""
    server_address, handler = grpc_server_and_handler

    # Update the gRPC interface dynamically based on the assigned port
    for interface in agent_card.supported_interfaces:
        if interface.protocol_binding == TransportProtocol.GRPC:
            interface.url = server_address
            break
    else:
        raise ValueError('No gRPC interface found in agent card')

    factory = ClientFactory(
        config=ClientConfig(
            grpc_channel_factory=grpc.aio.insecure_channel,
            supported_protocol_bindings=[TransportProtocol.GRPC],
        )
    )
    client = factory.create(agent_card)
    return TransportSetup(client=client, handler=handler)


@pytest.fixture(
    params=[
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('rest_setup', id='REST'),
        pytest.param('grpc_setup', id='gRPC'),
    ]
)
def transport_setups(request) -> TransportSetup:
    """Parametrized fixture that runs tests against all supported transports."""
    return request.getfixturevalue(request.param)


@pytest.fixture(
    params=[
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('rest_setup', id='REST'),
        pytest.param('grpc_setup', id='gRPC'),
        pytest.param('grpc_03_setup', id='gRPC-0.3'),
    ]
)
def error_handling_setups(request) -> TransportSetup:
    """Parametrized fixture for error tests including compat 0.3 endpoint verification."""
    return request.getfixturevalue(request.param)


@pytest.fixture(
    params=[
        pytest.param('jsonrpc_setup', id='JSON-RPC'),
        pytest.param('rest_setup', id='REST'),
    ]
)
def http_transport_setups(request) -> TransportSetup:
    """Parametrized fixture that runs tests against HTTP-based transports only."""
    return request.getfixturevalue(request.param)


# --- gRPC Setup ---


@pytest_asyncio.fixture
async def grpc_server_and_handler(
    mock_request_handler: AsyncMock, agent_card: AgentCard
) -> AsyncGenerator[tuple[str, AsyncMock], None]:
    """Creates and manages an in-process gRPC test server."""
    server = grpc.aio.server()
    port = server.add_insecure_port('[::]:0')
    server_address = f'localhost:{port}'
    servicer = GrpcHandler(request_handler=mock_request_handler)
    a2a_pb2_grpc.add_A2AServiceServicer_to_server(servicer, server)
    await server.start()
    try:
        yield server_address, mock_request_handler
    finally:
        await server.stop(None)


@pytest_asyncio.fixture
async def grpc_03_server_and_handler(
    mock_request_handler: AsyncMock, agent_card: AgentCard
) -> AsyncGenerator[tuple[str, AsyncMock], None]:
    """Creates and manages an in-process v0.3 compat gRPC test server."""
    server = grpc.aio.server()
    port = server.add_insecure_port('[::]:0')
    server_address = f'localhost:{port}'
    servicer = CompatGrpcHandler(
        request_handler=mock_request_handler,
    )
    a2a_v0_3_pb2_grpc.add_A2AServiceServicer_to_server(servicer, server)
    await server.start()
    try:
        yield server_address, mock_request_handler
    finally:
        await server.stop(None)


@pytest.fixture
def grpc_03_setup(
    grpc_03_server_and_handler, agent_card: AgentCard
) -> TransportSetup:
    """Sets up the CompatGrpcTransport and in-process 0.3 server."""
    server_address, handler = grpc_03_server_and_handler
    from a2a.client.base_client import BaseClient
    from a2a.client.client import ClientConfig
    from a2a.compat.v0_3.grpc_transport import CompatGrpcTransport

    channel = grpc.aio.insecure_channel(server_address)
    transport = CompatGrpcTransport(channel=channel, agent_card=agent_card)

    client = BaseClient(
        card=agent_card,
        config=ClientConfig(),
        transport=transport,
        interceptors=[],
    )
    return TransportSetup(client=client, handler=handler)


# --- The Integration Tests ---


@pytest.mark.asyncio
async def test_client_sends_message_streaming(transport_setups) -> None:
    """Integration test for all transports streaming."""
    client = transport_setups.client
    handler = transport_setups.handler

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-integration-test',
        parts=[Part(text='Hello, integration test!')],
    )
    params = SendMessageRequest(message=message_to_send)

    stream = client.send_message(request=params)
    events = [event async for event in stream]

    assert len(events) == 1
    event = events[0]
    task = event.task
    assert task is not None
    assert task.id == TASK_FROM_STREAM.id

    handler.on_message_send_stream.assert_called_once_with(params, ANY)

    await client.close()


@pytest.mark.asyncio
async def test_client_sends_message_blocking(transport_setups) -> None:
    """Integration test for all transports blocking."""
    client = transport_setups.client
    handler = transport_setups.handler

    # Disable streaming to force blocking call
    assert isinstance(client, BaseClient)
    client._config.streaming = False

    message_to_send = Message(
        role=Role.ROLE_USER,
        message_id='msg-integration-test-blocking',
        parts=[Part(text='Hello, blocking test!')],
    )
    params = SendMessageRequest(message=message_to_send)

    events = [event async for event in client.send_message(request=params)]

    assert len(events) == 1
    event = events[0]
    task = event.task
    assert task is not None
    assert task.id == TASK_FROM_BLOCKING.id
    handler.on_message_send.assert_awaited_once_with(params, ANY)

    await client.close()


@pytest.mark.asyncio
async def test_client_get_task(transport_setups) -> None:
    client = transport_setups.client
    handler = transport_setups.handler

    params = GetTaskRequest(id=GET_TASK_RESPONSE.id)
    result = await client.get_task(request=params)

    assert result.id == GET_TASK_RESPONSE.id
    handler.on_get_task.assert_awaited_once_with(params, ANY)

    await client.close()


@pytest.mark.asyncio
async def test_client_list_tasks(transport_setups) -> None:
    client = transport_setups.client
    handler = transport_setups.handler

    t = Timestamp()
    t.FromJsonString('2024-03-09T16:00:00Z')
    params = ListTasksRequest(
        context_id='ctx-1',
        status=TaskState.TASK_STATE_WORKING,
        page_size=10,
        page_token='page-1',
        history_length=5,
        status_timestamp_after=t,
        include_artifacts=True,
    )
    result = await client.list_tasks(request=params)

    assert len(result.tasks) == 2
    assert result.next_page_token == 'page-2'
    handler.on_list_tasks.assert_awaited_once_with(params, ANY)

    await client.close()


@pytest.mark.asyncio
async def test_client_cancel_task(transport_setups) -> None:
    client = transport_setups.client
    handler = transport_setups.handler

    params = CancelTaskRequest(id=CANCEL_TASK_RESPONSE.id)
    result = await client.cancel_task(request=params)

    assert result.id == CANCEL_TASK_RESPONSE.id
    handler.on_cancel_task.assert_awaited_once_with(params, ANY)

    await client.close()


@pytest.mark.asyncio
async def test_client_create_task_push_notification_config(
    transport_setups,
) -> None:
    client = transport_setups.client
    handler = transport_setups.handler

    params = TaskPushNotificationConfig(task_id='task-callback-123')
    result = await client.create_task_push_notification_config(request=params)

    assert result.id == CALLBACK_CONFIG.id
    handler.on_create_task_push_notification_config.assert_awaited_once_with(
        params, ANY
    )

    await client.close()


@pytest.mark.asyncio
async def test_client_get_task_push_notification_config(
    transport_setups,
) -> None:
    client = transport_setups.client
    handler = transport_setups.handler

    params = GetTaskPushNotificationConfigRequest(
        task_id=CALLBACK_CONFIG.task_id,
        id=CALLBACK_CONFIG.id,
    )
    result = await client.get_task_push_notification_config(request=params)

    assert result.id == CALLBACK_CONFIG.id
    handler.on_get_task_push_notification_config.assert_awaited_once_with(
        params, ANY
    )

    await client.close()


@pytest.mark.asyncio
async def test_client_list_task_push_notification_configs(
    transport_setups,
) -> None:
    client = transport_setups.client
    handler = transport_setups.handler

    params = ListTaskPushNotificationConfigsRequest(
        task_id=CALLBACK_CONFIG.task_id,
    )
    result = await client.list_task_push_notification_configs(request=params)

    assert len(result.configs) == 1
    handler.on_list_task_push_notification_configs.assert_awaited_once_with(
        params, ANY
    )

    await client.close()


@pytest.mark.asyncio
async def test_client_delete_task_push_notification_config(
    transport_setups,
) -> None:
    client = transport_setups.client
    handler = transport_setups.handler

    params = DeleteTaskPushNotificationConfigRequest(
        task_id=CALLBACK_CONFIG.task_id,
        id=CALLBACK_CONFIG.id,
    )
    await client.delete_task_push_notification_config(request=params)

    handler.on_delete_task_push_notification_config.assert_awaited_once_with(
        params, ANY
    )

    await client.close()


@pytest.mark.asyncio
async def test_client_subscribe(transport_setups) -> None:
    client = transport_setups.client
    handler = transport_setups.handler

    params = SubscribeToTaskRequest(id=RESUBSCRIBE_EVENT.task_id)
    stream = client.subscribe(request=params)
    first_event = await stream.__anext__()

    assert first_event.status_update.task_id == RESUBSCRIBE_EVENT.task_id
    handler.on_subscribe_to_task.assert_called_once()

    await client.close()


@pytest.mark.asyncio
async def test_client_get_extended_agent_card(
    transport_setups, agent_card
) -> None:
    client = transport_setups.client
    result = await client.get_extended_agent_card(GetExtendedAgentCardRequest())
    # The result could be the original card or a slightly modified one depending on transport
    assert result.name in [agent_card.name, 'Extended Agent Card']

    await client.close()


@pytest.mark.asyncio
async def test_json_transport_base_client_send_message_with_extensions(
    jsonrpc_setup: TransportSetup, agent_card: AgentCard
) -> None:
    """
    Integration test for BaseClient with JSON-RPC transport to ensure extensions are included in headers.
    """
    client_obj = jsonrpc_setup.client
    assert isinstance(client_obj, BaseClient)
    transport = client_obj._transport
    agent_card.capabilities.streaming = False

    # Create a BaseClient instance
    client = BaseClient(
        card=agent_card,
        config=ClientConfig(streaming=False),
        transport=transport,
        interceptors=[],
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

        service_params = ServiceParametersFactory.create(
            [with_a2a_extensions(extensions)]
        )
        context = ClientCallContext(service_parameters=service_params)

        # Call send_message on the BaseClient
        async for _ in client.send_message(
            request=SendMessageRequest(message=message_to_send), context=context
        ):
            pass

        mock_send_request.assert_called_once()
        call_args, call_kwargs = mock_send_request.call_args
        called_context = (
            call_args[1] if len(call_args) > 1 else call_kwargs.get('context')
        )
        service_params = getattr(called_context, 'service_parameters', {})
        assert 'A2A-Extensions' in service_params
        assert (
            service_params['A2A-Extensions']
            == 'https://example.com/test-ext/v1,https://example.com/test-ext/v2'
        )

    await client.close()


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
    key = 'testkey12345678901234567890123456789012345678901'
    signer = create_agent_card_signer(
        signing_key=key,
        protected_header={
            'alg': 'HS384',
            'kid': 'testkey',
            'jku': None,
            'typ': 'JOSE',
        },
    )

    async def async_signer(card: AgentCard) -> AgentCard:
        return signer(card)

    agent_card_routes = create_agent_card_routes(
        agent_card=agent_card, card_url='/', card_modifier=async_signer
    )
    jsonrpc_routes = create_jsonrpc_routes(
        request_handler=mock_request_handler, rpc_url='/'
    )
    app = Starlette(routes=[*agent_card_routes, *jsonrpc_routes])
    httpx_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        headers={VERSION_HEADER: PROTOCOL_VERSION_CURRENT},
    )

    agent_url = agent_card.supported_interfaces[0].url
    signature_verifier = create_signature_verifier(
        create_key_provider(key), ['HS384']
    )

    resolver = A2ACardResolver(
        httpx_client=httpx_client,
        base_url=agent_url,
    )

    # Verification happens here
    result = await resolver.get_agent_card(
        relative_card_path='/',
        signature_verifier=signature_verifier,
    )

    # Create transport with the verified card
    transport = JsonRpcTransport(
        httpx_client=httpx_client,
        agent_card=result,
        url=agent_url,
    )

    assert result.name == agent_card.name
    assert len(result.signatures) == 1

    await transport.close()


@pytest.mark.asyncio
async def test_client_get_signed_extended_card(
    jsonrpc_setup: TransportSetup, agent_card: AgentCard
) -> None:
    """Tests fetching and verifying an asymmetrically signed extended AgentCard at the client level.

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

    async def get_extended_agent_card_mock_2(*args, **kwargs) -> AgentCard:
        return signer(extended_agent_card)

    mock_request_handler.on_get_extended_agent_card.side_effect = (
        get_extended_agent_card_mock_2  # type: ignore[union-attr]
    )

    agent_card_routes = create_agent_card_routes(
        agent_card=agent_card, card_url='/'
    )
    jsonrpc_routes = create_jsonrpc_routes(
        request_handler=mock_request_handler, rpc_url='/'
    )
    app = Starlette(routes=[*agent_card_routes, *jsonrpc_routes])
    httpx_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        headers={VERSION_HEADER: PROTOCOL_VERSION_CURRENT},
    )

    transport = JsonRpcTransport(
        httpx_client=httpx_client,
        agent_card=agent_card,
        url=agent_card.supported_interfaces[0].url,
    )
    client = BaseClient(
        card=agent_card,
        config=ClientConfig(streaming=False),
        transport=transport,
        interceptors=[],
    )

    signature_verifier = create_signature_verifier(
        create_key_provider(public_key), ['HS384', 'ES256']
    )
    # Get the card, this will trigger verification in get_extended_agent_card
    result = await client.get_extended_agent_card(
        GetExtendedAgentCardRequest(),
        signature_verifier=signature_verifier,
    )
    assert result.name == extended_agent_card.name
    assert result.signatures is not None
    assert len(result.signatures) == 1

    await client.close()


@pytest.mark.asyncio
async def test_client_get_signed_base_and_extended_cards(
    jsonrpc_setup: TransportSetup, agent_card: AgentCard
) -> None:
    """Tests fetching and verifying both base and extended cards at the client level when no card is initially provided.

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
    signer(extended_agent_card)

    # Use async def to ensure it returns an awaitable
    async def get_extended_agent_card_mock_3(*args, **kwargs):
        return extended_agent_card

    mock_request_handler.on_get_extended_agent_card.side_effect = (
        get_extended_agent_card_mock_3  # type: ignore[union-attr]
    )

    async def async_signer(card: AgentCard) -> AgentCard:
        return signer(card)

    agent_card_routes = create_agent_card_routes(
        agent_card=agent_card, card_url='/', card_modifier=async_signer
    )
    jsonrpc_routes = create_jsonrpc_routes(
        request_handler=mock_request_handler, rpc_url='/'
    )
    app = Starlette(routes=[*agent_card_routes, *jsonrpc_routes])
    httpx_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        headers={VERSION_HEADER: PROTOCOL_VERSION_CURRENT},
    )

    agent_url = agent_card.supported_interfaces[0].url
    signature_verifier = create_signature_verifier(
        create_key_provider(public_key), ['HS384', 'ES256', 'RS256']
    )

    resolver = A2ACardResolver(
        httpx_client=httpx_client,
        base_url=agent_url,
    )

    # 1. Fetch base card
    base_card = await resolver.get_agent_card(
        relative_card_path='/',
        signature_verifier=signature_verifier,
    )

    # 2. Create transport with base card
    transport = JsonRpcTransport(
        httpx_client=httpx_client,
        agent_card=base_card,
        url=agent_url,
    )
    client = BaseClient(
        card=base_card,
        config=ClientConfig(streaming=False),
        transport=transport,
        interceptors=[],
    )

    # 3. Fetch extended card via client
    result = await client.get_extended_agent_card(
        GetExtendedAgentCardRequest(),
        signature_verifier=signature_verifier,
    )
    assert result.name == extended_agent_card.name
    assert len(result.signatures) == 1

    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'error_cls',
    [
        TaskNotFoundError,
        TaskNotCancelableError,
        PushNotificationNotSupportedError,
        UnsupportedOperationError,
        ContentTypeNotSupportedError,
        InvalidAgentResponseError,
        ExtendedAgentCardNotConfiguredError,
        ExtensionSupportRequiredError,
        VersionNotSupportedError,
    ],
)
async def test_client_handles_a2a_errors(transport_setups, error_cls) -> None:
    """Integration test to verify error propagation from handler to client."""
    client = transport_setups.client
    handler = transport_setups.handler

    # Mock the handler to raise the error
    handler.on_get_task.side_effect = error_cls('Test error message')

    params = GetTaskRequest(id='some-id')

    # We expect the client to raise the same error_cls.
    with pytest.raises(error_cls) as exc_info:
        await client.get_task(request=params)

    assert 'Test error message' in str(exc_info.value)

    # Reset side_effect for other tests
    handler.on_get_task.side_effect = None

    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'error_cls',
    [
        TaskNotFoundError,
        TaskNotCancelableError,
        PushNotificationNotSupportedError,
        UnsupportedOperationError,
        ContentTypeNotSupportedError,
        InvalidAgentResponseError,
        ExtendedAgentCardNotConfiguredError,
        ExtensionSupportRequiredError,
        VersionNotSupportedError,
    ],
)
@pytest.mark.parametrize(
    'handler_attr, client_method, request_params',
    [
        pytest.param(
            'on_message_send_stream',
            'send_message',
            SendMessageRequest(
                message=Message(
                    role=Role.ROLE_USER,
                    message_id='msg-integration-test',
                    parts=[Part(text='Hello, integration test!')],
                )
            ),
            id='stream',
        ),
        pytest.param(
            'on_subscribe_to_task',
            'subscribe',
            SubscribeToTaskRequest(id='some-id'),
            id='subscribe',
        ),
    ],
)
async def test_client_handles_a2a_errors_streaming(
    transport_setups, error_cls, handler_attr, client_method, request_params
) -> None:
    """Integration test to verify error propagation from streaming handlers to client.

    The handler raises an A2AError before yielding any events. All transports
    must propagate this as the exact error_cls, not wrapped in an ExceptionGroup
    or converted to a generic client error.
    """
    client = transport_setups.client
    handler = transport_setups.handler

    async def mock_generator(*args, **kwargs):
        raise error_cls('Test error message')
        yield

    getattr(handler, handler_attr).side_effect = mock_generator

    with pytest.raises(error_cls) as exc_info:
        async for _ in getattr(client, client_method)(request=request_params):
            pass

    assert 'Test error message' in str(exc_info.value)

    getattr(handler, handler_attr).side_effect = None

    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'error_cls,handler_attr,client_method,request_params',
    [
        pytest.param(
            UnsupportedOperationError,
            'on_subscribe_to_task',
            'subscribe',
            SubscribeToTaskRequest(id='some-id'),
            id='subscribe',
        ),
    ],
)
async def test_server_rejects_stream_on_validation_error(
    transport_setups, error_cls, handler_attr, client_method, request_params
) -> None:
    """Verify that the server returns an error directly and doesn't open a stream on validation error."""
    client = transport_setups.client
    handler = transport_setups.handler

    async def mock_generator(*args, **kwargs):
        raise error_cls('Validation failed')
        yield

    getattr(handler, handler_attr).side_effect = mock_generator

    transport = client._transport

    if isinstance(transport, (RestTransport, JsonRpcTransport)):
        # Spy on httpx client to check response headers
        original_send = transport.httpx_client.send
        response_headers = {}

        async def mock_send(*args, **kwargs):
            resp = await original_send(*args, **kwargs)
            response_headers['Content-Type'] = resp.headers.get('Content-Type')
            return resp

        transport.httpx_client.send = mock_send

        try:
            with pytest.raises(error_cls):
                async for _ in getattr(client, client_method)(
                    request=request_params
                ):
                    pass
        finally:
            transport.httpx_client.send = original_send

        # Verify that the response content type was NOT text/event-stream
        assert not response_headers.get('Content-Type', '').startswith(
            'text/event-stream'
        )
    else:
        # For gRPC, we just verify it raises the error
        with pytest.raises(error_cls):
            async for _ in getattr(client, client_method)(
                request=request_params
            ):
                pass

    getattr(handler, handler_attr).side_effect = None
    await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'request_kwargs, expected_error_code',
    [
        pytest.param(
            {'content': 'not a json'},
            -32700,  # Parse error
            id='invalid-json',
        ),
        pytest.param(
            {
                'json': {
                    'jsonrpc': '2.0',
                    'method': 'SendMessage',
                    'params': {'message': 'should be an object'},
                    'id': 1,
                }
            },
            -32602,  # Invalid params
            id='wrong-params-type',
        ),
    ],
)
async def test_jsonrpc_malformed_payload(
    jsonrpc_setup: TransportSetup,
    request_kwargs: dict[str, Any],
    expected_error_code: int,
) -> None:
    """Integration test to verify that JSON-RPC malformed payloads don't return 500."""
    client_obj = jsonrpc_setup.client
    assert isinstance(client_obj, BaseClient)
    transport = client_obj._transport
    assert isinstance(transport, JsonRpcTransport)
    client = transport.httpx_client
    url = transport.url

    response = await client.post(url, **request_kwargs)
    assert response.status_code == 200
    assert response.json()['error']['code'] == expected_error_code

    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'method, path, request_kwargs',
    [
        pytest.param(
            'POST',
            '/message:send',
            {'content': 'not a json'},
            id='invalid-json',
        ),
        pytest.param(
            'POST',
            '/message:send',
            {'json': {'message': 'should be an object'}},
            id='wrong-body-type',
        ),
        pytest.param(
            'GET',
            '/tasks',
            {'params': {'historyLength': 'not_an_int'}},
            id='wrong-query-param-type',
        ),
    ],
)
async def test_rest_malformed_payload(
    rest_setup: TransportSetup,
    method: str,
    path: str,
    request_kwargs: dict[str, Any],
) -> None:
    """Integration test to verify that REST malformed payloads don't return 500."""
    client_obj = rest_setup.client
    assert isinstance(client_obj, BaseClient)
    transport = client_obj._transport
    assert isinstance(transport, RestTransport)
    client = transport.httpx_client
    url = transport.url

    response = await client.request(method, f'{url}{path}', **request_kwargs)
    assert response.status_code == 400

    await transport.close()


@pytest.mark.asyncio
async def test_validate_version_unsupported(http_transport_setups) -> None:
    """Integration test for @validate_version decorator."""
    client = http_transport_setups.client

    service_params = {'A2A-Version': '2.0.0'}
    context = ClientCallContext(service_parameters=service_params)

    params = GetTaskRequest(id=GET_TASK_RESPONSE.id)

    with pytest.raises(VersionNotSupportedError):
        await client.get_task(request=params, context=context)

    await client.close()


@pytest.mark.asyncio
async def test_validate_decorator_push_notifications_disabled(
    error_handling_setups, agent_card: AgentCard
) -> None:
    """Integration test for @validate decorator with push notifications disabled."""
    client = error_handling_setups.client

    real_handler = LegacyRequestHandler(
        agent_executor=AsyncMock(),
        task_store=AsyncMock(),
        agent_card=agent_card,
    )

    error_handling_setups.handler.on_create_task_push_notification_config.side_effect = real_handler.on_create_task_push_notification_config

    params = TaskPushNotificationConfig(
        task_id='123',
        id='pnc-123',
        url='http://example.com',
    )

    with pytest.raises(PushNotificationNotSupportedError):
        await client.create_task_push_notification_config(request=params)

    await client.close()


@pytest.mark.asyncio
async def test_validate_streaming_disabled(
    error_handling_setups, agent_card: AgentCard
) -> None:
    """Integration test for @validate decorator when streaming is disabled."""
    client = error_handling_setups.client
    transport = client._transport

    agent_card.capabilities.streaming = False

    real_handler = LegacyRequestHandler(
        agent_executor=AsyncMock(),
        task_store=AsyncMock(),
        agent_card=agent_card,
    )

    error_handling_setups.handler.on_message_send_stream.side_effect = (
        real_handler.on_message_send_stream
    )
    error_handling_setups.handler.on_subscribe_to_task.side_effect = (
        real_handler.on_subscribe_to_task
    )

    params = SendMessageRequest(
        message=Message(
            role=Role.ROLE_USER,
            parts=[Part(text='hi')],
            message_id='msg-123',
        )
    )

    stream = transport.send_message_streaming(request=params)

    with pytest.raises(UnsupportedOperationError):
        async for _ in stream:
            pass

    await transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'error_cls',
    [
        TaskNotFoundError,
        TaskNotCancelableError,
        PushNotificationNotSupportedError,
        UnsupportedOperationError,
        ContentTypeNotSupportedError,
        InvalidAgentResponseError,
        ExtendedAgentCardNotConfiguredError,
        ExtensionSupportRequiredError,
        VersionNotSupportedError,
    ],
)
@pytest.mark.parametrize(
    'handler_attr, client_method, request_params',
    [
        pytest.param(
            'on_message_send_stream',
            'send_message',
            SendMessageRequest(
                message=Message(
                    role=Role.ROLE_USER,
                    message_id='msg-midstream-test',
                    parts=[Part(text='Hello, mid-stream test!')],
                )
            ),
            id='stream',
        ),
        pytest.param(
            'on_subscribe_to_task',
            'subscribe',
            SubscribeToTaskRequest(id='some-id'),
            id='subscribe',
        ),
    ],
)
async def test_client_handles_mid_stream_a2a_errors(
    transport_setups,
    error_cls,
    handler_attr,
    client_method,
    request_params,
) -> None:
    """Integration test for mid-stream errors sent as SSE error events.

    The handler yields one event successfully, then raises an A2AError.
    The client must receive the first event and then get the error as the
    exact error_cls exception. This mirrors test_client_handles_a2a_errors_streaming
    but verifies the error occurs *after* the stream has started producing events.
    """
    client = transport_setups.client
    handler = transport_setups.handler

    async def mock_generator(*args, **kwargs):
        yield TASK_FROM_STREAM
        raise error_cls('Mid-stream error')

    getattr(handler, handler_attr).side_effect = mock_generator

    received_events = []
    with pytest.raises(error_cls) as exc_info:
        async for event in getattr(client, client_method)(
            request=request_params
        ):
            received_events.append(event)  # noqa: PERF401

    assert 'Mid-stream error' in str(exc_info.value)
    assert len(received_events) == 1

    getattr(handler, handler_attr).side_effect = None

    await client.close()
