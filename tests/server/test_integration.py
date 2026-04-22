import asyncio
from unittest import mock

import pytest
from starlette.authentication import (
    AuthCredentials,
    AuthenticationBackend,
    BaseUser,
    SimpleUser,
)
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from a2a.server.jsonrpc_models import (
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    JSONParseError,
    MethodNotFoundError,
)
from a2a.server.routes import create_agent_card_routes, create_jsonrpc_routes
from a2a.types import (
    UnsupportedOperationError,
)
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Artifact,
    Message,
    Part,
    Role,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
)
from a2a.utils import (
    AGENT_CARD_WELL_KNOWN_PATH,
)


# === TEST SETUP ===

MINIMAL_AGENT_SKILL = AgentSkill(
    id='skill-123',
    name='Recipe Finder',
    description='Finds recipes',
    tags=['cooking'],
)

AGENT_CAPS = AgentCapabilities(push_notifications=True, streaming=True)

MINIMAL_AGENT_CARD_DATA = AgentCard(
    capabilities=AGENT_CAPS,
    default_input_modes=['text/plain'],
    default_output_modes=['application/json'],
    description='Test Agent',
    name='TestAgent',
    skills=[MINIMAL_AGENT_SKILL],
    supported_interfaces=[
        AgentInterface(
            url='http://example.com/agent', protocol_binding='HTTP+JSON'
        )
    ],
    version='1.0',
)

EXTENDED_AGENT_SKILL = AgentSkill(
    id='skill-extended',
    name='Extended Skill',
    description='Does more things',
    tags=['extended'],
)

EXTENDED_AGENT_CARD_DATA = AgentCard(
    capabilities=AGENT_CAPS,
    default_input_modes=['text/plain'],
    default_output_modes=['application/json'],
    description='Test Agent with more details',
    name='TestAgent Extended',
    skills=[MINIMAL_AGENT_SKILL, EXTENDED_AGENT_SKILL],
    supported_interfaces=[
        AgentInterface(
            url='http://example.com/agent', protocol_binding='HTTP+JSON'
        )
    ],
    version='1.0',
)
from google.protobuf.struct_pb2 import Struct, Value

TEXT_PART_DATA = Part(text='Hello')

# For proto, Part.data takes a Value(struct_value=Struct)
_struct = Struct()
_struct.update({'key': 'value'})
DATA_PART = Part(data=Value(struct_value=_struct))

MINIMAL_MESSAGE_USER = Message(
    role=Role.ROLE_USER,
    parts=[TEXT_PART_DATA],
    message_id='msg-123',
)

MINIMAL_TASK_STATUS = TaskStatus(state=TaskState.TASK_STATE_SUBMITTED)

FULL_TASK_STATUS = TaskStatus(
    state=TaskState.TASK_STATE_WORKING,
    message=MINIMAL_MESSAGE_USER,
)


@pytest.fixture
def agent_card():
    return MINIMAL_AGENT_CARD_DATA


@pytest.fixture
def extended_agent_card_fixture():
    return EXTENDED_AGENT_CARD_DATA


@pytest.fixture
def handler():
    handler = mock.AsyncMock()
    handler.on_message_send = mock.AsyncMock()
    handler.on_cancel_task = mock.AsyncMock()
    handler.on_get_task = mock.AsyncMock()
    handler.set_push_notification = mock.AsyncMock()
    handler.get_push_notification = mock.AsyncMock()
    handler.on_message_send_stream = mock.Mock()
    handler.on_subscribe_to_task = mock.Mock()
    return handler


class AppBuilder:
    def __init__(self, agent_card, handler, card_modifier=None):
        self.agent_card = agent_card
        self.handler = handler
        self.card_modifier = card_modifier

    def build(
        self,
        rpc_url='/',
        agent_card_url=AGENT_CARD_WELL_KNOWN_PATH,
        middleware=None,
        routes=None,
    ):
        from starlette.applications import Starlette

        app_instance = Starlette(middleware=middleware, routes=routes or [])

        # Agent card router
        card_routes = create_agent_card_routes(
            self.agent_card,
            card_url=agent_card_url,
            card_modifier=self.card_modifier,
        )
        app_instance.routes.extend(card_routes)

        # JSON-RPC router
        rpc_routes = create_jsonrpc_routes(self.handler, rpc_url=rpc_url)
        app_instance.routes.extend(rpc_routes)

        return app_instance


@pytest.fixture
def app(agent_card: AgentCard, handler: mock.AsyncMock):
    return AppBuilder(agent_card, handler)


@pytest.fixture
def client(app, **kwargs):
    """Create a test client with the app builder."""
    return TestClient(app.build(**kwargs), headers={'A2A-Version': '1.0'})


# === BASIC FUNCTIONALITY TESTS ===


def test_agent_card_endpoint(client: TestClient, agent_card: AgentCard):
    """Test the agent card endpoint returns expected data."""
    response = client.get(AGENT_CARD_WELL_KNOWN_PATH)
    assert response.status_code == 200
    data = response.json()
    assert data['name'] == agent_card.name
    assert data['version'] == agent_card.version
    assert 'streaming' in data['capabilities']


def test_agent_card_custom_url(app, agent_card: AgentCard):
    """Test the agent card endpoint with a custom URL."""
    client = TestClient(app.build(agent_card_url='/my-agent'))
    response = client.get('/my-agent')
    assert response.status_code == 200
    data = response.json()
    assert data['name'] == agent_card.name


def test_starlette_rpc_endpoint_custom_url(app, handler: mock.AsyncMock):
    """Test the RPC endpoint with a custom URL."""
    # Provide a valid Task object as the return value
    task_status = MINIMAL_TASK_STATUS
    task = Task(id='task1', context_id='ctx1', status=task_status)
    handler.on_get_task.return_value = task
    client = TestClient(
        app.build(rpc_url='/api/rpc'), headers={'A2A-Version': '1.0'}
    )
    response = client.post(
        '/api/rpc',
        json={
            'jsonrpc': '2.0',
            'id': '123',
            'method': 'GetTask',
            'params': {'id': 'task1'},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data['result']['id'] == 'task1'


def test_fastapi_rpc_endpoint_custom_url(app, handler: mock.AsyncMock):
    """Test the RPC endpoint with a custom URL."""
    # Provide a valid Task object as the return value
    task_status = MINIMAL_TASK_STATUS
    task = Task(id='task1', context_id='ctx1', status=task_status)
    handler.on_get_task.return_value = task
    client = TestClient(
        app.build(rpc_url='/api/rpc'), headers={'A2A-Version': '1.0'}
    )
    response = client.post(
        '/api/rpc',
        json={
            'jsonrpc': '2.0',
            'id': '123',
            'method': 'GetTask',
            'params': {'id': 'task1'},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data['result']['id'] == 'task1'


def test_starlette_build_with_extra_routes(app, agent_card: AgentCard):
    """Test building the app with additional routes."""

    def custom_handler(request):
        return JSONResponse({'message': 'Hello'})

    extra_route = Route('/hello', custom_handler, methods=['GET'])
    test_app = app.build(routes=[extra_route])
    client = TestClient(test_app, headers={'A2A-Version': '1.0'})

    # Test the added route
    response = client.get('/hello')
    assert response.status_code == 200
    assert response.json() == {'message': 'Hello'}

    # Ensure default routes still work
    response = client.get(AGENT_CARD_WELL_KNOWN_PATH)
    assert response.status_code == 200
    data = response.json()
    assert data['name'] == agent_card.name


def test_fastapi_build_with_extra_routes(app, agent_card: AgentCard):
    """Test building the app with additional routes."""

    def custom_handler(request):
        return JSONResponse({'message': 'Hello'})

    extra_route = Route('/hello', custom_handler, methods=['GET'])
    test_app = app.build(routes=[extra_route])
    client = TestClient(test_app)

    # Test the added route
    response = client.get('/hello')
    assert response.status_code == 200
    assert response.json() == {'message': 'Hello'}

    # Ensure default routes still work
    response = client.get(AGENT_CARD_WELL_KNOWN_PATH)
    assert response.status_code == 200
    data = response.json()
    assert data['name'] == agent_card.name


def test_fastapi_build_custom_agent_card_path(app, agent_card: AgentCard):
    """Test building the app with a custom agent card path."""

    test_app = app.build(agent_card_url='/agent-card')
    client = TestClient(test_app)

    # Ensure custom card path works
    response = client.get('/agent-card')
    assert response.status_code == 200
    data = response.json()
    assert data['name'] == agent_card.name

    # Ensure default path returns 404
    default_response = client.get(AGENT_CARD_WELL_KNOWN_PATH)
    assert default_response.status_code == 404


# === REQUEST METHODS TESTS ===


def test_send_message(client: TestClient, handler: mock.AsyncMock):
    """Test sending a message."""
    # Prepare mock response
    task_status = MINIMAL_TASK_STATUS
    mock_task = Task(
        id='task1',
        context_id='session-xyz',
        status=task_status,
    )
    handler.on_message_send.return_value = mock_task

    # Send request
    response = client.post(
        '/',
        json={
            'jsonrpc': '2.0',
            'id': '123',
            'method': 'SendMessage',
            'params': {
                'message': {
                    'role': 'ROLE_AGENT',
                    'parts': [{'text': 'Hello'}],
                    'messageId': '111',
                    'taskId': 'task1',
                    'contextId': 'session-xyz',
                }
            },
        },
    )

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert 'result' in data
    # Result is wrapped in SendMessageResponse with task field
    assert data['result']['task']['id'] == 'task1'
    assert data['result']['task']['status']['state'] == 'TASK_STATE_SUBMITTED'

    # Verify handler was called
    handler.on_message_send.assert_awaited_once()


def test_cancel_task(client: TestClient, handler: mock.AsyncMock):
    """Test cancelling a task."""
    # Setup mock response
    task_status = MINIMAL_TASK_STATUS
    task_status.state = TaskState.TASK_STATE_CANCELED  # 'cancelled' #
    task = Task(id='task1', context_id='ctx1', status=task_status)
    handler.on_cancel_task.return_value = task

    # Send request
    response = client.post(
        '/',
        json={
            'jsonrpc': '2.0',
            'id': '123',
            'method': 'CancelTask',
            'params': {'id': 'task1'},
        },
    )

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data['result']['id'] == 'task1'
    assert data['result']['status']['state'] == 'TASK_STATE_CANCELED'

    # Verify handler was called
    handler.on_cancel_task.assert_awaited_once()


def test_get_task(client: TestClient, handler: mock.AsyncMock):
    """Test getting a task."""
    # Setup mock response
    task_status = MINIMAL_TASK_STATUS
    task = Task(id='task1', context_id='ctx1', status=task_status)
    handler.on_get_task.return_value = task  # JSONRPCResponse(root=task)

    # Send request
    response = client.post(
        '/',
        json={
            'jsonrpc': '2.0',
            'id': '123',
            'method': 'GetTask',
            'params': {'id': 'task1'},
        },
    )

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data['result']['id'] == 'task1'

    # Verify handler was called
    handler.on_get_task.assert_awaited_once()


def test_set_push_notification_config(
    client: TestClient, handler: mock.AsyncMock
):
    """Test setting push notification configuration."""
    # Setup mock response
    task_push_config = TaskPushNotificationConfig(
        task_id='t2', url='https://example.com', token='secret-token'
    )
    handler.on_create_task_push_notification_config.return_value = (
        task_push_config
    )

    # Send request
    response = client.post(
        '/',
        json={
            'jsonrpc': '2.0',
            'id': '123',
            'method': 'CreateTaskPushNotificationConfig',
            'params': {
                'task_id': 't2',
                'url': 'https://example.com',
                'token': 'secret-token',
            },
        },
    )

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data['result']['token'] == 'secret-token'

    # Verify handler was called
    handler.on_create_task_push_notification_config.assert_awaited_once()


def test_get_push_notification_config(
    client: TestClient, handler: mock.AsyncMock
):
    """Test getting push notification configuration."""
    # Setup mock response
    task_push_config = TaskPushNotificationConfig(
        task_id='task1', url='https://example.com', token='secret-token'
    )

    handler.on_get_task_push_notification_config.return_value = task_push_config

    # Send request
    response = client.post(
        '/',
        json={
            'jsonrpc': '2.0',
            'id': '123',
            'method': 'GetTaskPushNotificationConfig',
            'params': {
                'task_id': 'task1',
                'id': 'pushNotificationConfig',
            },
        },
    )

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert data['result']['token'] == 'secret-token'

    # Verify handler was called
    handler.on_get_task_push_notification_config.assert_awaited_once()


def test_server_auth(app, handler: mock.AsyncMock):
    class TestAuthMiddleware(AuthenticationBackend):
        async def authenticate(
            self, conn: HTTPConnection
        ) -> tuple[AuthCredentials, BaseUser] | None:
            # For the purposes of this test, all requests are authenticated!
            return (AuthCredentials(['authenticated']), SimpleUser('test_user'))

    client = TestClient(
        app.build(
            middleware=[
                Middleware(
                    AuthenticationMiddleware, backend=TestAuthMiddleware()
                )
            ]
        ),
        headers={'A2A-Version': '1.0'},
    )

    # Set the output message to be the authenticated user name
    handler.on_message_send.side_effect = lambda params, context: Message(
        context_id='session-xyz',
        message_id='112',
        role=Role.ROLE_AGENT,
        parts=[
            Part(text=context.user.user_name),
        ],
    )

    # Send request
    response = client.post(
        '/',
        json={
            'jsonrpc': '2.0',
            'id': '123',
            'method': 'SendMessage',
            'params': {
                'message': {
                    'role': 'ROLE_AGENT',
                    'parts': [{'text': 'Hello'}],
                    'messageId': '111',
                    'taskId': 'task1',
                    'contextId': 'session-xyz',
                }
            },
        },
    )

    # Verify response
    assert response.status_code == 200
    data = response.json()
    assert 'result' in data
    # Result is wrapped in SendMessageResponse with message field
    assert data['result']['message']['parts'][0]['text'] == 'test_user'

    # Verify handler was called
    handler.on_message_send.assert_awaited_once()


# === STREAMING TESTS ===


@pytest.mark.asyncio
async def test_message_send_stream(app, handler: mock.AsyncMock) -> None:
    """Test streaming message sending."""

    # Setup mock streaming response
    async def stream_generator():
        for i in range(3):
            artifact = Artifact(
                artifact_id=f'artifact-{i}',
                name='result_data',
                parts=[TEXT_PART_DATA, DATA_PART],
            )
            last = [False, False, True]
            yield TaskArtifactUpdateEvent(
                artifact=artifact,
                task_id='task_id',
                context_id='session-xyz',
                append=False,
                last_chunk=last[i],
            )

    handler.on_message_send_stream.return_value = stream_generator()

    client = None
    try:
        # Create client
        client = TestClient(
            app.build(),
            raise_server_exceptions=False,
            headers={'A2A-Version': '1.0'},
        )
        # Send request
        with client.stream(
            'POST',
            '/',
            json={
                'jsonrpc': '2.0',
                'id': '123',
                'method': 'SendStreamingMessage',
                'params': {
                    'message': {
                        'role': 'ROLE_AGENT',
                        'parts': [{'text': 'Hello'}],
                        'messageId': '111',
                        'taskId': 'task_id',
                        'contextId': 'session-xyz',
                    }
                },
            },
        ) as response:
            # Verify response is a stream
            assert response.status_code == 200
            assert response.headers['content-type'].startswith(
                'text/event-stream'
            )

            # Read some content to verify streaming works
            content = b''
            event_count = 0

            for chunk in response.iter_bytes():
                content += chunk
                if b'data' in chunk:  # Naive check for SSE data lines
                    event_count += 1

            # Check content has event data (e.g., part of the first event)
            assert b'artifact-0' in content  # Check for the actual JSON payload
            assert b'artifact-1' in content  # Check for the actual JSON payload
            assert b'artifact-2' in content  # Check for the actual JSON payload
            assert event_count > 0
    finally:
        # Ensure the client is closed
        if client:
            client.close()
        # Allow event loop to process any pending callbacks
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_task_resubscription(app, handler: mock.AsyncMock) -> None:
    """Test task resubscription streaming."""

    # Setup mock streaming response
    async def stream_generator():
        for i in range(3):
            artifact = Artifact(
                artifact_id=f'artifact-{i}',
                name='result_data',
                parts=[TEXT_PART_DATA, DATA_PART],
            )
            last = [False, False, True]
            yield TaskArtifactUpdateEvent(
                artifact=artifact,
                task_id='task_id',
                context_id='session-xyz',
                append=False,
                last_chunk=last[i],
            )

    handler.on_subscribe_to_task.return_value = stream_generator()

    # Create client
    client = TestClient(
        app.build(),
        raise_server_exceptions=False,
        headers={'A2A-Version': '1.0'},
    )

    try:
        # Send request using client.stream() context manager
        # Send request
        with client.stream(
            'POST',
            '/',
            json={
                'jsonrpc': '2.0',
                'id': '123',  # This ID is used in the success_event above
                'method': 'SubscribeToTask',
                'params': {'id': 'task1'},
            },
        ) as response:
            # Verify response is a stream
            assert response.status_code == 200
            assert (
                response.headers['content-type']
                == 'text/event-stream; charset=utf-8'
            )

            # Read some content to verify streaming works
            content = b''
            event_count = 0
            for chunk in response.iter_bytes():
                content += chunk
                # A more robust check would be to parse each SSE event
                if b'data:' in chunk:  # Naive check for SSE data lines
                    event_count += 1
                if (
                    event_count >= 1 and len(content) > 20
                ):  # Ensure we've processed at least one event
                    break

            # Check content has event data (e.g., part of the first event)
            assert b'artifact-0' in content  # Check for the actual JSON payload
            assert b'artifact-1' in content  # Check for the actual JSON payload
            assert b'artifact-2' in content  # Check for the actual JSON payload
            assert event_count > 0
    finally:
        # Ensure the client is closed
        if client:
            client.close()
        # Allow event loop to process any pending callbacks
        await asyncio.sleep(0.1)


# === ERROR HANDLING TESTS ===


def test_invalid_json(client: TestClient):
    """Test handling invalid JSON."""
    response = client.post('/', content=b'This is not JSON')  # Use bytes
    assert response.status_code == 200  # JSON-RPC errors still return 200
    data = response.json()
    assert 'error' in data
    assert data['error']['code'] == JSONParseError().code


def test_invalid_request_structure(client: TestClient):
    """Test handling an invalid request structure."""
    response = client.post(
        '/',
        json={
            'jsonrpc': 'aaaa',  # Missing or wrong required fields
            'id': '123',
            'method': 'foo/bar',
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert 'error' in data
    # The jsonrpc library returns InvalidRequestError for invalid requests format
    assert data['error']['code'] == InvalidRequestError().code


def test_invalid_request_method(client: TestClient):
    """Test handling an invalid request method."""
    response = client.post(
        '/',
        json={
            'jsonrpc': '2.0',  # Missing or wrong required fields
            'id': '123',
            'method': 'foo/bar',
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert 'error' in data
    # The jsonrpc library returns MethodNotFoundError for invalid request method
    assert data['error']['code'] == MethodNotFoundError().code


# === DYNAMIC CARD MODIFIER TESTS ===


def test_dynamic_agent_card_modifier(
    agent_card: AgentCard, handler: mock.AsyncMock
):
    """Test that the card_modifier dynamically alters the public agent card."""

    async def modifier(card: AgentCard) -> AgentCard:
        modified_card = AgentCard()
        modified_card.CopyFrom(card)
        modified_card.name = 'Dynamically Modified Agent'
        return modified_card

    app_instance = AppBuilder(agent_card, handler, card_modifier=modifier)
    client = TestClient(app_instance.build())

    response = client.get(AGENT_CARD_WELL_KNOWN_PATH)
    assert response.status_code == 200
    data = response.json()
    assert data['name'] == 'Dynamically Modified Agent'
    assert (
        data['version'] == agent_card.version
    )  # Ensure other fields are intact


def test_dynamic_agent_card_modifier_sync(
    agent_card: AgentCard, handler: mock.AsyncMock
):
    """Test that a synchronous card_modifier dynamically alters the public agent card."""

    async def modifier(card: AgentCard) -> AgentCard:
        modified_card = AgentCard()
        modified_card.CopyFrom(card)
        modified_card.name = 'Dynamically Modified Agent'
        return modified_card

    app_instance = AppBuilder(agent_card, handler, card_modifier=modifier)
    client = TestClient(app_instance.build())

    response = client.get(AGENT_CARD_WELL_KNOWN_PATH)
    assert response.status_code == 200
    data = response.json()
    assert data['name'] == 'Dynamically Modified Agent'
    assert (
        data['version'] == agent_card.version
    )  # Ensure other fields are intact


def test_fastapi_dynamic_agent_card_modifier(
    agent_card: AgentCard, handler: mock.AsyncMock
):
    """Test that the card_modifier dynamically alters the public agent card for FastAPI."""

    async def modifier(card: AgentCard) -> AgentCard:
        modified_card = AgentCard()
        modified_card.CopyFrom(card)
        modified_card.name = 'Dynamically Modified Agent'
        return modified_card

    app_instance = AppBuilder(agent_card, handler, card_modifier=modifier)
    client = TestClient(app_instance.build())

    response = client.get(AGENT_CARD_WELL_KNOWN_PATH)
    assert response.status_code == 200
    data = response.json()
    assert data['name'] == 'Dynamically Modified Agent'


def test_fastapi_dynamic_agent_card_modifier_sync(
    agent_card: AgentCard, handler: mock.AsyncMock
):
    """Test that a synchronous card_modifier dynamically alters the public agent card for FastAPI."""

    async def modifier(card: AgentCard) -> AgentCard:
        modified_card = AgentCard()
        modified_card.CopyFrom(card)
        modified_card.name = 'Dynamically Modified Agent'
        return modified_card

    app_instance = AppBuilder(agent_card, handler, card_modifier=modifier)
    client = TestClient(app_instance.build())

    response = client.get(AGENT_CARD_WELL_KNOWN_PATH)
    assert response.status_code == 200
    data = response.json()
    assert data['name'] == 'Dynamically Modified Agent'


def test_unsupported_operation_error(
    client: TestClient, handler: mock.AsyncMock
):
    """Test handling UnsupportedOperationError."""
    handler.on_get_task.side_effect = UnsupportedOperationError()

    response = client.post(
        '/',
        json={
            'jsonrpc': '2.0',
            'id': '123',
            'method': 'GetTask',
            'params': {'id': 'task1'},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert 'error' in data
    assert data['error']['code'] == -32004  # UnsupportedOperationError


def test_unknown_method(client: TestClient):
    """Test handling unknown method."""
    response = client.post(
        '/',
        json={
            'jsonrpc': '2.0',
            'id': '123',
            'method': 'unknown/method',
            'params': {},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert 'error' in data
    # This should produce an UnsupportedOperationError error code
    assert data['error']['code'] == MethodNotFoundError().code


def test_validation_error(client: TestClient):
    """Test handling validation error."""
    # Missing required fields in the message
    response = client.post(
        '/',
        json={
            'jsonrpc': '2.0',
            'id': '123',
            'method': 'SendMessage',
            'params': {
                'message': {
                    # Missing required fields
                    'text': 'Hello'
                }
            },
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert 'error' in data
    assert data['error']['code'] == InvalidParamsError().code


def test_unhandled_exception(client: TestClient, handler: mock.AsyncMock):
    """Test handling unhandled exception."""
    handler.on_get_task.side_effect = Exception('Unexpected error')

    response = client.post(
        '/',
        json={
            'jsonrpc': '2.0',
            'id': '123',
            'method': 'GetTask',
            'params': {'id': 'task1'},
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert 'error' in data
    assert data['error']['code'] == InternalError().code
    assert 'Unexpected error' in data['error']['message']


def test_get_method_to_rpc_endpoint(client: TestClient):
    """Test sending GET request to RPC endpoint."""
    response = client.get('/')
    # Should return 405 Method Not Allowed
    assert response.status_code == 405


def test_non_dict_json(client: TestClient):
    """Test handling JSON that's not a dict."""
    response = client.post('/', json=['not', 'a', 'dict'])
    assert response.status_code == 200
    data = response.json()
    assert 'error' in data
    assert data['error']['code'] == InvalidRequestError().code


def test_agent_card_backward_compatibility_supports_extended_card(
    agent_card: AgentCard, handler: mock.AsyncMock
):
    """Test that supportsAuthenticatedExtendedCard is injected when extended_agent_card is True."""
    agent_card.capabilities.extended_agent_card = True
    app_instance = AppBuilder(agent_card, handler)
    client = TestClient(app_instance.build())
    response = client.get(AGENT_CARD_WELL_KNOWN_PATH)
    assert response.status_code == 200
    data = response.json()
    assert data.get('supportsAuthenticatedExtendedCard') is True


def test_agent_card_backward_compatibility_no_extended_card(
    agent_card: AgentCard, handler: mock.AsyncMock
):
    """Test that supportsAuthenticatedExtendedCard is absent when extended_agent_card is False."""
    agent_card.capabilities.extended_agent_card = False
    app_instance = AppBuilder(agent_card, handler)
    client = TestClient(app_instance.build())
    response = client.get(AGENT_CARD_WELL_KNOWN_PATH)
    assert response.status_code == 200
    data = response.json()
    assert 'supportsAuthenticatedExtendedCard' not in data
