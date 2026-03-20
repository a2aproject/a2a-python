import logging
import json

from typing import Any
from unittest.mock import MagicMock

import pytest

from fastapi import FastAPI
from google.protobuf import json_format
from httpx import ASGITransport, AsyncClient

from a2a.server.apps.rest import fastapi_app, rest_adapter
from a2a.server.apps.rest.fastapi_app import A2ARESTFastAPIApplication
from a2a.server.apps.rest.rest_adapter import RESTAdapter
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import a2a_pb2
from a2a.types.a2a_pb2 import (
    AgentCard,
    ListTaskPushNotificationConfigsResponse,
    ListTasksResponse,
    Message,
    Part,
    Role,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
)


logger = logging.getLogger(__name__)


@pytest.fixture
async def agent_card() -> AgentCard:
    mock_agent_card = MagicMock(spec=AgentCard)
    mock_agent_card.url = 'http://mockurl.com'

    # Mock the capabilities object with streaming enabled
    mock_capabilities = MagicMock()
    mock_capabilities.streaming = True
    mock_capabilities.push_notifications = True
    mock_capabilities.extended_agent_card = True
    mock_agent_card.capabilities = mock_capabilities

    return mock_agent_card


@pytest.fixture
async def streaming_agent_card() -> AgentCard:
    """Agent card that supports streaming for testing streaming endpoints."""
    mock_agent_card = MagicMock(spec=AgentCard)
    mock_agent_card.url = 'http://mockurl.com'

    # Mock the capabilities object with streaming enabled
    mock_capabilities = MagicMock()
    mock_capabilities.streaming = True
    mock_agent_card.capabilities = mock_capabilities

    return mock_agent_card


@pytest.fixture
async def request_handler() -> RequestHandler:
    return MagicMock(spec=RequestHandler)


@pytest.fixture
async def extended_card_modifier() -> MagicMock | None:
    return None


@pytest.fixture
async def streaming_app(
    streaming_agent_card: AgentCard, request_handler: RequestHandler
) -> FastAPI:
    """Builds the FastAPI application for testing streaming endpoints."""

    return A2ARESTFastAPIApplication(
        streaming_agent_card, request_handler
    ).build(agent_card_url='/well-known/agent-card.json', rpc_url='')


@pytest.fixture
async def streaming_client(streaming_app: FastAPI) -> AsyncClient:
    """HTTP client for the streaming FastAPI application."""
    return AsyncClient(
        transport=ASGITransport(app=streaming_app),
        base_url='http://test',
        headers={'A2A-Version': '1.0'},
    )


@pytest.fixture
async def app(
    agent_card: AgentCard,
    request_handler: RequestHandler,
    extended_card_modifier: MagicMock | None,
) -> FastAPI:
    """Builds the FastAPI application for testing."""

    return A2ARESTFastAPIApplication(
        agent_card,
        request_handler,
        extended_card_modifier=extended_card_modifier,
    ).build(agent_card_url='/well-known/agent.json', rpc_url='')


@pytest.fixture
async def client(app: FastAPI) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url='http://testapp',
        headers={'A2A-Version': '1.0'},
    )


@pytest.fixture
def mark_pkg_starlette_not_installed():
    pkg_starlette_installed_flag = rest_adapter._package_starlette_installed
    rest_adapter._package_starlette_installed = False
    yield
    rest_adapter._package_starlette_installed = pkg_starlette_installed_flag


@pytest.fixture
def mark_pkg_fastapi_not_installed():
    pkg_fastapi_installed_flag = fastapi_app._package_fastapi_installed
    fastapi_app._package_fastapi_installed = False
    yield
    fastapi_app._package_fastapi_installed = pkg_fastapi_installed_flag


@pytest.mark.anyio
async def test_create_rest_adapter_with_present_deps_succeeds(
    agent_card: AgentCard, request_handler: RequestHandler
):
    try:
        _app = RESTAdapter(agent_card, request_handler)
    except ImportError:
        pytest.fail(
            'With packages starlette and see-starlette present, creating an'
            ' RESTAdapter instance should not raise ImportError'
        )


@pytest.mark.anyio
async def test_create_rest_adapter_with_missing_deps_raises_importerror(
    agent_card: AgentCard,
    request_handler: RequestHandler,
    mark_pkg_starlette_not_installed: Any,
):
    with pytest.raises(
        ImportError,
        match=(
            r'Packages `starlette` and `sse-starlette` are required to use'
            r' the `RESTAdapter`.'
        ),
    ):
        _app = RESTAdapter(agent_card, request_handler)


@pytest.mark.anyio
async def test_create_a2a_rest_fastapi_app_with_present_deps_succeeds(
    agent_card: AgentCard, request_handler: RequestHandler
):
    try:
        _app = A2ARESTFastAPIApplication(agent_card, request_handler).build(
            agent_card_url='/well-known/agent.json', rpc_url=''
        )
    except ImportError:
        pytest.fail(
            'With the fastapi package present, creating a'
            ' A2ARESTFastAPIApplication instance should not raise ImportError'
        )


@pytest.mark.anyio
async def test_create_a2a_rest_fastapi_app_with_missing_deps_raises_importerror(
    agent_card: AgentCard,
    request_handler: RequestHandler,
    mark_pkg_fastapi_not_installed: Any,
):
    with pytest.raises(
        ImportError,
        match=(
            'The `fastapi` package is required to use the'
            ' `A2ARESTFastAPIApplication`'
        ),
    ):
        _app = A2ARESTFastAPIApplication(agent_card, request_handler).build(
            agent_card_url='/well-known/agent.json', rpc_url=''
        )


@pytest.mark.anyio
async def test_create_a2a_rest_fastapi_app_with_v0_3_compat(
    agent_card: AgentCard, request_handler: RequestHandler
):
    app = A2ARESTFastAPIApplication(
        agent_card, request_handler, enable_v0_3_compat=True
    ).build(agent_card_url='/well-known/agent.json', rpc_url='')

    routes = [getattr(route, 'path', '') for route in app.routes]
    assert '/v1/message:send' in routes


@pytest.mark.anyio
async def test_send_message_success_message(
    client: AsyncClient, request_handler: MagicMock
) -> None:
    expected_response = a2a_pb2.SendMessageResponse(
        message=a2a_pb2.Message(
            message_id='test',
            role=a2a_pb2.Role.ROLE_AGENT,
            parts=[
                a2a_pb2.Part(text='response message'),
            ],
        ),
    )
    request_handler.on_message_send.return_value = Message(
        message_id='test',
        role=Role.ROLE_AGENT,
        parts=[Part(text='response message')],
    )

    request = a2a_pb2.SendMessageRequest(
        message=a2a_pb2.Message(),
        configuration=a2a_pb2.SendMessageConfiguration(),
    )
    # To see log output, run pytest with '--log-cli=true --log-cli-level=INFO'
    response = await client.post(
        '/message:send', json=json_format.MessageToDict(request)
    )
    # request should always be successful
    response.raise_for_status()

    actual_response = a2a_pb2.SendMessageResponse()
    json_format.Parse(response.text, actual_response)
    assert expected_response == actual_response


@pytest.mark.anyio
async def test_send_message_success_task(
    client: AsyncClient, request_handler: MagicMock
) -> None:
    expected_response = a2a_pb2.SendMessageResponse(
        task=a2a_pb2.Task(
            id='test_task_id',
            context_id='test_context_id',
            status=a2a_pb2.TaskStatus(
                state=a2a_pb2.TaskState.TASK_STATE_COMPLETED,
                message=a2a_pb2.Message(
                    message_id='test',
                    role=a2a_pb2.Role.ROLE_AGENT,
                    parts=[
                        a2a_pb2.Part(text='response task message'),
                    ],
                ),
            ),
        ),
    )
    request_handler.on_message_send.return_value = Task(
        id='test_task_id',
        context_id='test_context_id',
        status=TaskStatus(
            state=TaskState.TASK_STATE_COMPLETED,
            message=Message(
                message_id='test',
                role=Role.ROLE_AGENT,
                parts=[Part(text='response task message')],
            ),
        ),
    )

    request = a2a_pb2.SendMessageRequest(
        message=a2a_pb2.Message(),
        configuration=a2a_pb2.SendMessageConfiguration(),
    )
    # To see log output, run pytest with '--log-cli=true --log-cli-level=INFO'
    response = await client.post(
        '/message:send', json=json_format.MessageToDict(request)
    )
    # request should always be successful
    response.raise_for_status()

    actual_response = a2a_pb2.SendMessageResponse()
    json_format.Parse(response.text, actual_response)
    assert expected_response == actual_response


@pytest.mark.anyio
async def test_streaming_message_request_body_consumption(
    streaming_client: AsyncClient, request_handler: MagicMock
) -> None:
    """Test that streaming endpoint properly handles request body consumption.

    This test verifies the fix for the deadlock issue where request.body()
    was being consumed inside the EventSourceResponse context, causing
    the application to hang indefinitely.
    """

    # Mock the async generator response from the request handler
    async def mock_stream_response():
        """Mock streaming response generator."""
        yield Message(
            message_id='stream_msg_1',
            role=Role.ROLE_AGENT,
            parts=[Part(text='First streaming response')],
        )
        yield Message(
            message_id='stream_msg_2',
            role=Role.ROLE_AGENT,
            parts=[Part(text='Second streaming response')],
        )

    request_handler.on_message_send_stream.return_value = mock_stream_response()

    # Create a valid streaming request
    request = a2a_pb2.SendMessageRequest(
        message=a2a_pb2.Message(
            message_id='test_stream_msg',
            role=a2a_pb2.ROLE_USER,
            parts=[a2a_pb2.Part(text='Test streaming message')],
        ),
        configuration=a2a_pb2.SendMessageConfiguration(),
    )

    # This should not hang indefinitely (previously it would due to the deadlock)
    response = await streaming_client.post(
        '/message:stream',
        json=json_format.MessageToDict(request),
        headers={'Accept': 'text/event-stream'},
        timeout=10.0,  # Reasonable timeout to prevent hanging in tests
    )

    # The response should be successful
    response.raise_for_status()
    assert response.status_code == 200
    assert 'text/event-stream' in response.headers.get('content-type', '')

    # Verify that the request handler was called
    request_handler.on_message_send_stream.assert_called_once()


@pytest.mark.anyio
async def test_streaming_content_verification(
    streaming_client: AsyncClient, request_handler: MagicMock
) -> None:
    """Test that streaming endpoint returns correct SSE content."""

    async def mock_stream_response():
        yield Message(
            message_id='stream_msg_1',
            role=Role.ROLE_AGENT,
            parts=[Part(text='First chunk')],
        )
        yield Message(
            message_id='stream_msg_2',
            role=Role.ROLE_AGENT,
            parts=[Part(text='Second chunk')],
        )

    request_handler.on_message_send_stream.return_value = mock_stream_response()

    request = a2a_pb2.SendMessageRequest(
        message=a2a_pb2.Message(
            message_id='test_stream_msg',
            role=a2a_pb2.ROLE_USER,
            parts=[a2a_pb2.Part(text='Test message')],
        ),
    )

    response = await streaming_client.post(
        '/message:stream',
        headers={'Accept': 'text/event-stream'},
        json=json_format.MessageToDict(request),
    )

    response.raise_for_status()

    # Read the response content
    lines = [line async for line in response.aiter_lines()]

    # SSE format is "data: <json>\n\n"
    # httpx.aiter_lines() will give us each line.
    data_lines = [
        json.loads(line[6:]) for line in lines if line.startswith('data: ')
    ]

    expected_data_lines = [
        {
            'message': {
                'messageId': 'stream_msg_1',
                'role': 'ROLE_AGENT',
                'parts': [{'text': 'First chunk'}],
            }
        },
        {
            'message': {
                'messageId': 'stream_msg_2',
                'role': 'ROLE_AGENT',
                'parts': [{'text': 'Second chunk'}],
            }
        },
    ]

    assert data_lines == expected_data_lines


@pytest.mark.anyio
async def test_subscribe_to_task_get(
    streaming_client: AsyncClient, request_handler: MagicMock
) -> None:
    """Test that GET /tasks/{id}:subscribe works."""

    async def mock_stream_response():
        yield Task(
            id='task-1',
            context_id='ctx-1',
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )

    request_handler.on_subscribe_to_task.return_value = mock_stream_response()

    response = await streaming_client.get(
        '/tasks/task-1:subscribe',
        headers={'Accept': 'text/event-stream'},
    )

    response.raise_for_status()
    assert response.status_code == 200

    # Verify handler call
    request_handler.on_subscribe_to_task.assert_called_once()
    args, _ = request_handler.on_subscribe_to_task.call_args
    assert args[0].id == 'task-1'


@pytest.mark.anyio
async def test_subscribe_to_task_post(
    streaming_client: AsyncClient, request_handler: MagicMock
) -> None:
    """Test that POST /tasks/{id}:subscribe works."""

    async def mock_stream_response():
        yield Task(
            id='task-1',
            context_id='ctx-1',
            status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
        )

    request_handler.on_subscribe_to_task.return_value = mock_stream_response()

    response = await streaming_client.post(
        '/tasks/task-1:subscribe',
        headers={'Accept': 'text/event-stream'},
    )

    response.raise_for_status()
    assert response.status_code == 200

    # Verify handler call
    request_handler.on_subscribe_to_task.assert_called_once()
    args, _ = request_handler.on_subscribe_to_task.call_args
    assert args[0].id == 'task-1'


@pytest.mark.anyio
async def test_streaming_endpoint_with_invalid_content_type(
    streaming_client: AsyncClient, request_handler: MagicMock
) -> None:
    """Test streaming endpoint behavior with invalid content type."""

    async def mock_stream_response():
        yield Message(
            message_id='stream_msg_1',
            role=Role.ROLE_AGENT,
            parts=[Part(text='Response')],
        )

    request_handler.on_message_send_stream.return_value = mock_stream_response()

    request = a2a_pb2.SendMessageRequest(
        message=a2a_pb2.Message(
            message_id='test_stream_msg',
            role=a2a_pb2.ROLE_USER,
            parts=[a2a_pb2.Part(text='Test message')],
        ),
        configuration=a2a_pb2.SendMessageConfiguration(),
    )

    # Send request without proper event-stream headers
    response = await streaming_client.post(
        '/message:stream',
        json=json_format.MessageToDict(request),
        timeout=10.0,
    )

    # Should still succeed (the adapter handles content-type internally)
    response.raise_for_status()
    assert response.status_code == 200


@pytest.mark.anyio
async def test_send_message_rejected_task(
    client: AsyncClient, request_handler: MagicMock
) -> None:
    expected_response = a2a_pb2.SendMessageResponse(
        task=a2a_pb2.Task(
            id='test_task_id',
            context_id='test_context_id',
            status=a2a_pb2.TaskStatus(
                state=a2a_pb2.TaskState.TASK_STATE_REJECTED,
                message=a2a_pb2.Message(
                    message_id='test',
                    role=a2a_pb2.Role.ROLE_AGENT,
                    parts=[
                        a2a_pb2.Part(text="I don't want to work"),
                    ],
                ),
            ),
        ),
    )
    request_handler.on_message_send.return_value = Task(
        id='test_task_id',
        context_id='test_context_id',
        status=TaskStatus(
            state=TaskState.TASK_STATE_REJECTED,
            message=Message(
                message_id='test',
                role=Role.ROLE_AGENT,
                parts=[Part(text="I don't want to work")],
            ),
        ),
    )
    request = a2a_pb2.SendMessageRequest(
        message=a2a_pb2.Message(),
        configuration=a2a_pb2.SendMessageConfiguration(),
    )

    response = await client.post(
        '/message:send', json=json_format.MessageToDict(request)
    )

    response.raise_for_status()
    actual_response = a2a_pb2.SendMessageResponse()
    json_format.Parse(response.text, actual_response)
    assert expected_response == actual_response


@pytest.mark.anyio
class TestTenantExtraction:
    @pytest.fixture(autouse=True)
    def configure_mocks(self, request_handler: MagicMock) -> None:
        # Setup default return values for all handlers
        async def mock_stream(*args, **kwargs):
            if False:
                yield

        request_handler.on_subscribe_to_task.side_effect = (
            lambda *args, **kwargs: mock_stream()
        )

        request_handler.on_message_send.return_value = Message(
            message_id='test',
            role=Role.ROLE_AGENT,
            parts=[Part(text='response message')],
        )
        request_handler.on_cancel_task.return_value = Task(id='1')
        request_handler.on_get_task.return_value = Task(id='1')
        request_handler.on_list_tasks.return_value = ListTasksResponse()
        request_handler.on_create_task_push_notification_config.return_value = (
            TaskPushNotificationConfig()
        )
        request_handler.on_get_task_push_notification_config.return_value = (
            TaskPushNotificationConfig()
        )
        request_handler.on_list_task_push_notification_configs.return_value = (
            ListTaskPushNotificationConfigsResponse()
        )
        request_handler.on_delete_task_push_notification_config.return_value = (
            None
        )

    @pytest.fixture
    def extended_card_modifier(self) -> MagicMock:
        modifier = MagicMock()
        modifier.return_value = AgentCard()
        return modifier

    @pytest.mark.parametrize(
        'path_template, method, handler_method_name, json_body',
        [
            ('/message:send', 'POST', 'on_message_send', {'message': {}}),
            ('/tasks/1:cancel', 'POST', 'on_cancel_task', None),
            ('/tasks/1:subscribe', 'GET', 'on_subscribe_to_task', None),
            ('/tasks/1:subscribe', 'POST', 'on_subscribe_to_task', None),
            ('/tasks/1', 'GET', 'on_get_task', None),
            ('/tasks', 'GET', 'on_list_tasks', None),
            (
                '/tasks/1/pushNotificationConfigs/p1',
                'GET',
                'on_get_task_push_notification_config',
                None,
            ),
            (
                '/tasks/1/pushNotificationConfigs/p1',
                'DELETE',
                'on_delete_task_push_notification_config',
                None,
            ),
            (
                '/tasks/1/pushNotificationConfigs',
                'POST',
                'on_create_task_push_notification_config',
                {'url': 'http://foo'},
            ),
            (
                '/tasks/1/pushNotificationConfigs',
                'GET',
                'on_list_task_push_notification_configs',
                None,
            ),
        ],
    )
    async def test_tenant_extraction_parametrized(  # noqa: PLR0913  # Test parametrization requires many arguments
        self,
        client: AsyncClient,
        request_handler: MagicMock,
        path_template: str,
        method: str,
        handler_method_name: str,
        json_body: dict | None,
    ) -> None:
        """Test tenant extraction for standard REST endpoints."""
        # Test with tenant
        tenant = 'my-tenant'
        tenant_path = f'/{tenant}{path_template}'

        response = await client.request(method, tenant_path, json=json_body)
        response.raise_for_status()

        # Verify handler call
        handler_mock = getattr(request_handler, handler_method_name)

        assert handler_mock.called
        args, _ = handler_mock.call_args
        context = args[1]
        assert context.tenant == tenant

        # Reset mock for non-tenant test
        handler_mock.reset_mock()

        # Test without tenant
        response = await client.request(method, path_template, json=json_body)
        response.raise_for_status()

        # Verify context.tenant == ""
        assert handler_mock.called
        args, _ = handler_mock.call_args
        context = args[1]
        assert context.tenant == ''

    async def test_tenant_extraction_extended_agent_card(
        self,
        client: AsyncClient,
        extended_card_modifier: MagicMock,
    ) -> None:
        """Test tenant extraction specifically for extendedAgentCard endpoint."""
        # Test with tenant
        tenant = 'my-tenant'
        tenant_path = f'/{tenant}/extendedAgentCard'

        response = await client.get(tenant_path)
        response.raise_for_status()

        # Verify extended_card_modifier called with tenant context
        assert extended_card_modifier.called
        args, _ = extended_card_modifier.call_args
        context = args[1]
        assert context.tenant == tenant

        # Reset mock for non-tenant test
        extended_card_modifier.reset_mock()

        # Test without tenant
        response = await client.get('/extendedAgentCard')
        response.raise_for_status()

        # Verify extended_card_modifier called with empty tenant context
        assert extended_card_modifier.called
        args, _ = extended_card_modifier.call_args
        context = args[1]
        assert context.tenant == ''


@pytest.mark.anyio
async def test_global_http_exception_handler_returns_rpc_status(
    client: AsyncClient,
) -> None:
    """Test that a standard FastAPI 404 is transformed into the A2A google.rpc.Status format."""

    # Send a request to an endpoint that does not exist
    response = await client.get('/non-existent-route')

    # Verify it returns a 404 with standard application/json
    assert response.status_code == 404
    assert response.headers.get('content-type') == 'application/json'

    data = response.json()

    # Assert the payload is wrapped in the "error" envelope
    assert 'error' in data
    error_payload = data['error']

    # Assert it has the correct AIP-193 format
    assert error_payload['code'] == 404
    assert error_payload['status'] == 'NOT_FOUND'
    assert 'Not Found' in error_payload['message']

    # Standard HTTP errors shouldn't leak details
    assert 'details' not in error_payload


if __name__ == '__main__':
    pytest.main([__file__])
