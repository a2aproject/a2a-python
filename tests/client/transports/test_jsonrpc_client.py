"""Tests for the JSON-RPC client transport."""

import json
from google.protobuf import json_format
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
import respx
from httpx_sse import EventSource, SSEError

from a2a.client.errors import (
    A2AClientHTTPError,
    A2AClientJSONError,
    A2AClientJSONRPCError,
    A2AClientTimeoutError,
)
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentInterface,
    AgentCard,
    CancelTaskRequest,
    CreateTaskPushNotificationConfigRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigRequest,
    ListTaskPushNotificationConfigRequest,
    ListTaskPushNotificationConfigResponse,
    GetTaskRequest,
    Message,
    Part,
    SendMessageConfiguration,
    SendMessageRequest,
    SendMessageResponse,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
)


@pytest.fixture
def mock_httpx_client():
    """Creates a mock httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.headers = httpx.Headers()
    client.timeout = httpx.Timeout(30.0)
    return client


@pytest.fixture
def agent_card():
    """Creates a minimal AgentCard for testing."""
    return AgentCard(
        name='Test Agent',
        description='A test agent',
        supported_interfaces=[
            AgentInterface(
                url='http://test-agent.example.com',
                protocol_binding='HTTP+JSON',
            )
        ],
        version='1.0.0',
        capabilities=AgentCapabilities(),
    )


@pytest.fixture
def transport(mock_httpx_client, agent_card):
    """Creates a JsonRpcTransport instance for testing."""
    return JsonRpcTransport(
        httpx_client=mock_httpx_client,
        agent_card=agent_card,
    )


@pytest.fixture
def transport_with_url(mock_httpx_client):
    """Creates a JsonRpcTransport with just a URL."""
    return JsonRpcTransport(
        httpx_client=mock_httpx_client,
        url='http://custom-url.example.com',
    )


def create_send_message_request(text='Hello'):
    """Helper to create a SendMessageRequest with proper proto structure."""
    return SendMessageRequest(
        message=Message(
            role='ROLE_USER',
            parts=[Part(text=text)],
            message_id='msg-123',
        ),
        configuration=SendMessageConfiguration(),
    )


from a2a.extensions.common import HTTP_EXTENSION_HEADER


def _assert_extensions_header(mock_kwargs: dict, expected_extensions: set[str]):
    headers = mock_kwargs.get('headers', {})
    assert HTTP_EXTENSION_HEADER in headers
    header_value = headers[HTTP_EXTENSION_HEADER]
    actual_extensions = {e.strip() for e in header_value.split(',')}
    assert actual_extensions == expected_extensions


class TestJsonRpcTransportInit:
    """Tests for JsonRpcTransport initialization."""

    def test_init_with_agent_card(self, mock_httpx_client, agent_card):
        """Test initialization with an agent card."""
        transport = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            agent_card=agent_card,
        )
        assert transport.url == 'http://test-agent.example.com'
        assert transport.agent_card == agent_card

    def test_init_with_url(self, mock_httpx_client):
        """Test initialization with a URL."""
        transport = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            url='http://custom-url.example.com',
        )
        assert transport.url == 'http://custom-url.example.com'
        assert transport.agent_card is None

    def test_init_url_takes_precedence(self, mock_httpx_client, agent_card):
        """Test that explicit URL takes precedence over agent card URL."""
        transport = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            agent_card=agent_card,
            url='http://override-url.example.com',
        )
        assert transport.url == 'http://override-url.example.com'

    def test_init_requires_url_or_agent_card(self, mock_httpx_client):
        """Test that initialization requires either URL or agent card."""
        with pytest.raises(
            ValueError, match='Must provide either agent_card or url'
        ):
            JsonRpcTransport(httpx_client=mock_httpx_client)

    def test_init_with_interceptors(self, mock_httpx_client, agent_card):
        """Test initialization with interceptors."""
        interceptor = MagicMock()
        transport = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            agent_card=agent_card,
            interceptors=[interceptor],
        )
        assert transport.interceptors == [interceptor]

    def test_init_with_extensions(self, mock_httpx_client, agent_card):
        """Test initialization with extensions."""
        extensions = ['https://example.com/ext1', 'https://example.com/ext2']
        transport = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            agent_card=agent_card,
            extensions=extensions,
        )
        assert transport.extensions == extensions


class TestSendMessage:
    """Tests for the send_message method."""

    @pytest.mark.asyncio
    async def test_send_message_success(self, transport, mock_httpx_client):
        """Test successful message sending."""
        task_id = str(uuid4())
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'jsonrpc': '2.0',
            'id': '1',
            'result': {
                'task': {
                    'id': task_id,
                    'contextId': 'ctx-123',
                    'status': {'state': 'TASK_STATE_COMPLETED'},
                }
            },
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        request = create_send_message_request()
        response = await transport.send_message(request)

        assert isinstance(response, SendMessageResponse)
        mock_httpx_client.post.assert_called_once()
        call_args = mock_httpx_client.post.call_args
        assert call_args[0][0] == 'http://test-agent.example.com'
        payload = call_args[1]['json']
        assert payload['method'] == 'SendMessage'

    @pytest.mark.asyncio
    async def test_send_message_jsonrpc_error(
        self, transport, mock_httpx_client
    ):
        """Test handling of JSON-RPC error response."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'jsonrpc': '2.0',
            'id': '1',
            'error': {'code': -32600, 'message': 'Invalid Request'},
            'result': None,
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        request = create_send_message_request()

        # The transport raises A2AClientJSONRPCError when there's an error response
        with pytest.raises(A2AClientJSONRPCError):
            await transport.send_message(request)

    @pytest.mark.asyncio
    async def test_send_message_timeout(self, transport, mock_httpx_client):
        """Test handling of request timeout."""
        mock_httpx_client.post.side_effect = httpx.ReadTimeout('Timeout')

        request = create_send_message_request()

        with pytest.raises(A2AClientTimeoutError, match='timed out'):
            await transport.send_message(request)

    @pytest.mark.asyncio
    async def test_send_message_http_error(self, transport, mock_httpx_client):
        """Test handling of HTTP errors."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_httpx_client.post.side_effect = httpx.HTTPStatusError(
            'Server Error', request=MagicMock(), response=mock_response
        )

        request = create_send_message_request()

        with pytest.raises(A2AClientHTTPError):
            await transport.send_message(request)

    @pytest.mark.asyncio
    async def test_send_message_json_decode_error(
        self, transport, mock_httpx_client
    ):
        """Test handling of invalid JSON response."""
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = json.JSONDecodeError('msg', 'doc', 0)
        mock_httpx_client.post.return_value = mock_response

        request = create_send_message_request()

        with pytest.raises(A2AClientJSONError):
            await transport.send_message(request)


class TestGetTask:
    """Tests for the get_task method."""

    @pytest.mark.asyncio
    async def test_get_task_success(self, transport, mock_httpx_client):
        """Test successful task retrieval."""
        task_id = str(uuid4())
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'jsonrpc': '2.0',
            'id': '1',
            'result': {
                'id': task_id,
                'contextId': 'ctx-123',
                'status': {'state': 'TASK_STATE_COMPLETED'},
            },
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        # Proto uses 'name' field for task identifier in request
        request = GetTaskRequest(id=f'{task_id}')
        response = await transport.get_task(request)

        assert isinstance(response, Task)
        assert response.id == task_id
        mock_httpx_client.post.assert_called_once()
        call_args = mock_httpx_client.post.call_args
        payload = call_args[1]['json']
        assert payload['method'] == 'GetTask'

    @pytest.mark.asyncio
    async def test_get_task_with_history(self, transport, mock_httpx_client):
        """Test task retrieval with history_length parameter."""
        task_id = str(uuid4())
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'jsonrpc': '2.0',
            'id': '1',
            'result': {
                'id': task_id,
                'contextId': 'ctx-123',
                'status': {'state': 'TASK_STATE_COMPLETED'},
            },
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        request = GetTaskRequest(id=f'{task_id}', history_length=10)
        response = await transport.get_task(request)

        assert isinstance(response, Task)
        call_args = mock_httpx_client.post.call_args
        payload = call_args[1]['json']
        assert payload['params']['historyLength'] == 10


class TestCancelTask:
    """Tests for the cancel_task method."""

    @pytest.mark.asyncio
    async def test_cancel_task_success(self, transport, mock_httpx_client):
        """Test successful task cancellation."""
        task_id = str(uuid4())
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'jsonrpc': '2.0',
            'id': '1',
            'result': {
                'id': task_id,
                'contextId': 'ctx-123',
                'status': {'state': 5},  # TASK_STATE_CANCELED = 5
            },
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        request = CancelTaskRequest(id=f'{task_id}')
        response = await transport.cancel_task(request)

        assert isinstance(response, Task)
        assert response.status.state == TaskState.TASK_STATE_CANCELED
        call_args = mock_httpx_client.post.call_args
        payload = call_args[1]['json']
        assert payload['method'] == 'CancelTask'


class TestTaskCallback:
    """Tests for the task callback methods."""

    @pytest.mark.asyncio
    async def test_get_task_callback_success(
        self, transport, mock_httpx_client
    ):
        """Test successful task callback retrieval."""
        task_id = str(uuid4())
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'jsonrpc': '2.0',
            'id': '1',
            'result': {
                'task_id': f'{task_id}',
                'id': 'config-1',
            },
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        request = GetTaskPushNotificationConfigRequest(
            task_id=f'{task_id}',
            id='config-1',
        )
        response = await transport.get_task_callback(request)

        assert isinstance(response, TaskPushNotificationConfig)
        call_args = mock_httpx_client.post.call_args
        payload = call_args[1]['json']
        assert payload['method'] == 'GetTaskPushNotificationConfig'

    @pytest.mark.asyncio
    async def test_list_task_callback_success(
        self, transport, mock_httpx_client
    ):
        """Test successful task multiple callbacks retrieval."""
        task_id = str(uuid4())
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'jsonrpc': '2.0',
            'id': '1',
            'result': {
                'configs': [
                    {
                        'task_id': f'{task_id}',
                        'id': 'config-1',
                        'push_notification_config': {
                            'id': 'config-1',
                            'url': 'https://example.com',
                        },
                    }
                ]
            },
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        request = ListTaskPushNotificationConfigRequest(
            task_id=f'{task_id}',
        )
        response = await transport.list_task_callback(request)

        assert len(response.configs) == 1
        assert response.configs[0].task_id == task_id
        call_args = mock_httpx_client.post.call_args
        payload = call_args[1]['json']
        assert payload['method'] == 'ListTaskPushNotificationConfig'

    @pytest.mark.asyncio
    async def test_delete_task_callback_success(
        self, transport, mock_httpx_client
    ):
        """Test successful task callback deletion."""
        task_id = str(uuid4())
        mock_response = MagicMock()
        mock_response.json.return_value = {
            'jsonrpc': '2.0',
            'id': '1',
            'result': {
                'task_id': f'{task_id}',
                'id': 'config-1',
            },
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        request = DeleteTaskPushNotificationConfigRequest(
            task_id=f'{task_id}',
            id='config-1',
        )
        response = await transport.delete_task_callback(request)

        mock_httpx_client.post.assert_called_once()  # Assuming mock_send_request was a typo for mock_httpx_client.post
        assert response is None
        call_args = mock_httpx_client.post.call_args
        payload = call_args[1]['json']
        assert payload['method'] == 'DeleteTaskPushNotificationConfig'


class TestClose:
    """Tests for the close method."""

    @pytest.mark.asyncio
    async def test_close(self, transport, mock_httpx_client):
        """Test that close properly closes the httpx client."""
        await transport.close()


class TestStreamingErrors:
    @pytest.mark.asyncio
    @patch('a2a.client.transports.jsonrpc.aconnect_sse')
    async def test_send_message_streaming_sse_error(
        self,
        mock_aconnect_sse: AsyncMock,
        transport: JsonRpcTransport,
    ):
        request = create_send_message_request()
        mock_event_source = AsyncMock()
        mock_event_source.response.raise_for_status = MagicMock()
        mock_event_source.aiter_sse = MagicMock(
            side_effect=SSEError('Simulated SSE error')
        )
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        with pytest.raises(A2AClientHTTPError):
            async for _ in transport.send_message_streaming(request):
                pass

    @pytest.mark.asyncio
    @patch('a2a.client.transports.jsonrpc.aconnect_sse')
    async def test_send_message_streaming_request_error(
        self,
        mock_aconnect_sse: AsyncMock,
        transport: JsonRpcTransport,
    ):
        request = create_send_message_request()
        mock_event_source = AsyncMock()
        mock_event_source.response.raise_for_status = MagicMock()
        mock_event_source.aiter_sse = MagicMock(
            side_effect=httpx.RequestError(
                'Simulated request error', request=MagicMock()
            )
        )
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        with pytest.raises(A2AClientHTTPError):
            async for _ in transport.send_message_streaming(request):
                pass

    @pytest.mark.asyncio
    @patch('a2a.client.transports.jsonrpc.aconnect_sse')
    async def test_send_message_streaming_timeout(
        self,
        mock_aconnect_sse: AsyncMock,
        transport: JsonRpcTransport,
    ):
        request = create_send_message_request()
        mock_event_source = AsyncMock()
        mock_event_source.response.raise_for_status = MagicMock()
        mock_event_source.aiter_sse = MagicMock(
            side_effect=httpx.TimeoutException('Timeout')
        )
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        with pytest.raises(A2AClientTimeoutError, match='timed out'):
            async for _ in transport.send_message_streaming(request):
                pass


class TestInterceptors:
    """Tests for interceptor functionality."""

    @pytest.mark.asyncio
    async def test_interceptor_called(self, mock_httpx_client, agent_card):
        """Test that interceptors are called during requests."""
        interceptor = AsyncMock()
        interceptor.intercept.return_value = (
            {'modified': 'payload'},
            {'headers': {'X-Custom': 'value'}},
        )

        transport = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            agent_card=agent_card,
            interceptors=[interceptor],
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'jsonrpc': '2.0',
            'id': '1',
            'result': {
                'task': {
                    'id': 'task-123',
                    'contextId': 'ctx-123',
                    'status': {'state': 'TASK_STATE_COMPLETED'},
                }
            },
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        request = create_send_message_request()

        await transport.send_message(request)

        interceptor.intercept.assert_called_once()
        call_args = interceptor.intercept.call_args
        assert call_args[0][0] == 'SendMessage'


class TestExtensions:
    """Tests for extension header functionality."""

    @pytest.mark.asyncio
    async def test_extensions_added_to_request(
        self, mock_httpx_client, agent_card
    ):
        """Test that extensions are added to request headers."""
        extensions = ['https://example.com/ext1']
        transport = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            agent_card=agent_card,
            extensions=extensions,
        )

        mock_response = MagicMock()
        mock_response.json.return_value = {
            'jsonrpc': '2.0',
            'id': '1',
            'result': {
                'task': {
                    'id': 'task-123',
                    'contextId': 'ctx-123',
                    'status': {'state': 'TASK_STATE_COMPLETED'},
                }
            },
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.post.return_value = mock_response

        request = create_send_message_request()

        await transport.send_message(request)

        # Verify request was made with extension headers
        mock_httpx_client.post.assert_called_once()
        call_args = mock_httpx_client.post.call_args
        # Extensions should be in the kwargs
        assert (
            call_args[1].get('headers', {}).get('X-A2A-Extensions')
            == 'https://example.com/ext1'
        )

    @pytest.mark.asyncio
    @patch('a2a.client.transports.jsonrpc.aconnect_sse')
    async def test_send_message_streaming_server_error_propagates(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        agent_card: AgentCard,
    ):
        """Test that send_message_streaming propagates server errors (e.g., 403, 500) directly."""
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            agent_card=agent_card,
        )
        request = create_send_message_request(text='Error stream')

        mock_event_source = AsyncMock(spec=EventSource)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            'Forbidden',
            request=httpx.Request('POST', 'http://test.url'),
            response=mock_response,
        )
        mock_event_source.response = mock_response

        async def empty_aiter():
            if False:
                yield

        mock_event_source.aiter_sse = MagicMock(return_value=empty_aiter())
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        with pytest.raises(A2AClientHTTPError) as exc_info:
            async for _ in client.send_message_streaming(request=request):
                pass

        assert exc_info.value.status_code == 403
        mock_aconnect_sse.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_card_no_card_provided_with_extensions(
        self, mock_httpx_client: AsyncMock, agent_card: AgentCard
    ):
        """Test get_extended_agent_card with extensions set in Client when no card is initially provided.
        Tests that the extensions are added to the HTTP GET request."""
        extensions = [
            'https://example.com/test-ext/v1',
            'https://example.com/test-ext/v2',
        ]
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            url='http://test-agent.example.com',
            extensions=extensions,
        )
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = json_format.MessageToDict(agent_card)
        mock_httpx_client.get.return_value = mock_response

        agent_card.capabilities.extended_agent_card = False

        await client.get_extended_agent_card()

        mock_httpx_client.get.assert_called_once()
        _, mock_kwargs = mock_httpx_client.get.call_args

        _assert_extensions_header(
            mock_kwargs,
            {
                'https://example.com/test-ext/v1',
                'https://example.com/test-ext/v2',
            },
        )

    @pytest.mark.asyncio
    async def test_get_card_with_extended_card_support_with_extensions(
        self, mock_httpx_client: AsyncMock, agent_card: AgentCard
    ):
        """Test get_extended_agent_card with extensions passed to call when extended card support is enabled.
        Tests that the extensions are added to the RPC request."""
        extensions = [
            'https://example.com/test-ext/v1',
            'https://example.com/test-ext/v2',
        ]
        agent_card.capabilities.extended_agent_card = True

        client = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            agent_card=agent_card,
            extensions=extensions,
        )

        extended_card = AgentCard()
        extended_card.CopyFrom(agent_card)
        extended_card.name = 'Extended'

        rpc_response = {
            'id': '123',
            'jsonrpc': '2.0',
            'result': json_format.MessageToDict(extended_card),
        }
        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_request:
            mock_send_request.return_value = rpc_response
            await client.get_extended_agent_card(extensions=extensions)

        mock_send_request.assert_called_once()
        _, mock_kwargs = mock_send_request.call_args[0]

        _assert_extensions_header(
            mock_kwargs,
            {
                'https://example.com/test-ext/v1',
                'https://example.com/test-ext/v2',
            },
        )
