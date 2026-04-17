from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from google.protobuf import json_format
from google.protobuf.timestamp_pb2 import Timestamp
from httpx_sse import EventSource, ServerSentEvent

from a2a.helpers.proto_helpers import new_text_message
from a2a.client.client import ClientCallContext
from a2a.client.errors import A2AClientError
from a2a.client.transports.rest import RestTransport
from a2a.extensions.common import HTTP_EXTENSION_HEADER
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
    ListTasksRequest,
    SendMessageRequest,
    SubscribeToTaskRequest,
    TaskPushNotificationConfig,
    TaskState,
)
from a2a.utils.constants import TransportProtocol
from a2a.utils.errors import A2A_REST_ERROR_MAPPING


@pytest.fixture
def mock_httpx_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def mock_agent_card() -> MagicMock:
    mock = MagicMock(spec=AgentCard, url='http://agent.example.com/api')
    mock.supported_interfaces = [
        AgentInterface(
            protocol_binding=TransportProtocol.HTTP_JSON,
            url='http://agent.example.com/api',
        )
    ]
    mock.capabilities = MagicMock()
    mock.capabilities.extended_agent_card = False
    return mock


async def async_iterable_from_list(
    items: list[ServerSentEvent],
) -> AsyncGenerator[ServerSentEvent, None]:
    """Helper to create an async iterable from a list."""
    for item in items:
        yield item


def _assert_extensions_header(mock_kwargs: dict, expected_extensions: set[str]):
    headers = mock_kwargs.get('headers', {})
    assert HTTP_EXTENSION_HEADER in headers
    header_value = headers[HTTP_EXTENSION_HEADER]
    actual_extensions = {e.strip() for e in header_value.split(',')}
    assert actual_extensions == expected_extensions


class TestRestTransport:
    @pytest.mark.asyncio
    @patch('a2a.client.transports.http_helpers._SSEEventSource')
    async def test_send_message_streaming_timeout(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            url='http://agent.example.com/api',
        )
        params = SendMessageRequest(
            message=new_text_message(text='Hello stream')
        )
        mock_event_source = AsyncMock(spec=EventSource)
        mock_event_source.response = MagicMock(spec=httpx.Response)
        mock_event_source.response.headers = {
            'content-type': 'text/event-stream'
        }
        mock_event_source.response.raise_for_status.return_value = None
        mock_event_source.aiter_sse.side_effect = httpx.TimeoutException(
            'Read timed out'
        )
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        with pytest.raises(A2AClientError) as exc_info:
            _ = [
                item
                async for item in client.send_message_streaming(request=params)
            ]

        assert 'Client Request timed out' in str(exc_info.value)

    @pytest.mark.parametrize('error_cls', list(A2A_REST_ERROR_MAPPING.keys()))
    @pytest.mark.asyncio
    async def test_rest_mapped_errors(
        self,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
        error_cls,
    ):
        """Test handling of mapped REST HTTP error responses."""
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            url='http://agent.example.com/api',
        )
        params = SendMessageRequest(message=new_text_message(text='Hello'))

        mock_build_request = MagicMock(
            return_value=AsyncMock(spec=httpx.Request)
        )
        mock_httpx_client.build_request = mock_build_request

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 500

        reason = A2A_REST_ERROR_MAPPING[error_cls][2]

        mock_response.json.return_value = {
            'error': {
                'code': 500,
                'status': 'UNKNOWN',
                'message': 'Mapped Error',
                'details': [
                    {
                        '@type': 'type.googleapis.com/google.rpc.ErrorInfo',
                        'reason': reason,
                        'domain': 'a2a-protocol.org',
                        'metadata': {},
                    }
                ],
            }
        }

        error = httpx.HTTPStatusError(
            'Server Error',
            request=httpx.Request('POST', 'http://test.url'),
            response=mock_response,
        )

        mock_httpx_client.send.side_effect = error

        with pytest.raises(error_cls):
            await client.send_message(request=params)

    @pytest.mark.asyncio
    async def test_send_message_with_timeout_context(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        """Test that send_message passes context timeout to build_request."""

        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            url='http://agent.example.com/api',
        )
        params = SendMessageRequest(message=new_text_message(text='Hello'))
        context = ClientCallContext(timeout=10.0)

        mock_build_request = MagicMock(
            return_value=AsyncMock(spec=httpx.Request)
        )
        mock_httpx_client.build_request = mock_build_request

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_httpx_client.send.return_value = mock_response

        await client.send_message(request=params, context=context)

        mock_build_request.assert_called_once()
        _, kwargs = mock_build_request.call_args
        assert 'timeout' in kwargs
        assert kwargs['timeout'] == httpx.Timeout(10.0)

    @pytest.mark.asyncio
    async def test_url_serialization(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        """Test that query parameters are correctly serialized to the URL."""
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            url='http://agent.example.com/api',
        )

        timestamp = Timestamp()
        timestamp.FromJsonString('2024-03-09T16:00:00Z')

        request = ListTasksRequest(
            tenant='my-tenant',
            status=TaskState.TASK_STATE_WORKING,
            include_artifacts=True,
            status_timestamp_after=timestamp,
        )

        # Use real build_request to get actual URL serialization
        mock_httpx_client.build_request.side_effect = (
            httpx.AsyncClient().build_request
        )
        mock_httpx_client.send.return_value = AsyncMock(
            spec=httpx.Response, status_code=200, json=lambda: {'tasks': []}
        )

        await client.list_tasks(request=request)

        mock_httpx_client.send.assert_called_once()
        sent_request = mock_httpx_client.send.call_args[0][0]

        # Check decoded query parameters for spec compliance
        params = sent_request.url.params
        assert params['status'] == 'TASK_STATE_WORKING'
        assert params['includeArtifacts'] == 'true'
        assert params['statusTimestampAfter'] == '2024-03-09T16:00:00Z'
        assert 'tenant' not in params


class TestRestTransportExtensions:
    @pytest.mark.asyncio
    async def test_send_message_with_default_extensions(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        """Test that send_message adds extensions to headers."""
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            url='http://agent.example.com/api',
        )
        params = SendMessageRequest(message=new_text_message(text='Hello'))

        # Mock the build_request method to capture its inputs
        mock_build_request = MagicMock(
            return_value=AsyncMock(spec=httpx.Request)
        )
        mock_httpx_client.build_request = mock_build_request

        # Mock the send method
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_httpx_client.send.return_value = mock_response

        context = ClientCallContext(
            service_parameters={
                'A2A-Extensions': 'https://example.com/test-ext/v1,https://example.com/test-ext/v2'
            }
        )
        await client.send_message(request=params, context=context)

        mock_build_request.assert_called_once()
        _, kwargs = mock_build_request.call_args

        _assert_extensions_header(
            kwargs,
            {
                'https://example.com/test-ext/v1',
                'https://example.com/test-ext/v2',
            },
        )

    @pytest.mark.asyncio
    @patch('a2a.client.transports.http_helpers._SSEEventSource')
    async def test_send_message_streaming_with_new_extensions(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        """Test A2A-Extensions header in send_message_streaming."""
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            url='http://agent.example.com/api',
        )
        params = SendMessageRequest(
            message=new_text_message(text='Hello stream')
        )

        mock_event_source = AsyncMock(spec=EventSource)
        mock_event_source.response = MagicMock(spec=httpx.Response)
        mock_event_source.response.headers = {
            'content-type': 'text/event-stream'
        }
        mock_event_source.aiter_sse.return_value = async_iterable_from_list([])
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        context = ClientCallContext(
            service_parameters={
                'A2A-Extensions': 'https://example.com/test-ext/v2'
            }
        )

        async for _ in client.send_message_streaming(
            request=params, context=context
        ):
            pass

        mock_aconnect_sse.assert_called_once()
        _, kwargs = mock_aconnect_sse.call_args

        _assert_extensions_header(
            kwargs,
            {
                'https://example.com/test-ext/v2',
            },
        )

    @pytest.mark.asyncio
    @patch('a2a.client.transports.http_helpers._SSEEventSource')
    async def test_send_message_streaming_server_error_propagates(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        """Test that send_message_streaming propagates server errors (e.g., 403, 500) directly."""
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            url='http://agent.example.com/api',
        )
        request = SendMessageRequest(
            message=new_text_message(text='Error stream')
        )

        mock_event_source = AsyncMock(spec=EventSource)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            'Forbidden',
            request=httpx.Request('POST', 'http://test.url'),
            response=mock_response,
        )

        async def empty_aiter():
            if False:
                yield

        mock_event_source.response = mock_response
        mock_event_source.aiter_sse = MagicMock(return_value=empty_aiter())
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        with pytest.raises(A2AClientError) as exc_info:
            async for _ in client.send_message_streaming(request=request):
                pass

        assert 'HTTP Error 403' in str(exc_info.value)

        mock_aconnect_sse.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_card_with_extended_card_support_with_extensions(
        self, mock_httpx_client: AsyncMock
    ):
        """Test get_extended_agent_card with extensions passed to  call when extended card support is enabled.
        Tests that the extensions are added to the GET request."""
        extensions_str = (
            'https://example.com/test-ext/v1,https://example.com/test-ext/v2'
        )
        agent_card = AgentCard(
            name='Test Agent',
            description='Test Agent Description',
            version='1.0.0',
            capabilities=AgentCapabilities(extended_agent_card=True),
        )
        interface = agent_card.supported_interfaces.add()
        interface.protocol_binding = TransportProtocol.HTTP_JSON
        interface.url = 'http://agent.example.com/api'

        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=agent_card,
            url='http://agent.example.com/api',
        )

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = json_format.MessageToDict(
            agent_card
        )  # Extended card same for mock
        mock_httpx_client.send.return_value = mock_response

        request = GetExtendedAgentCardRequest()

        context = ClientCallContext(
            service_parameters={HTTP_EXTENSION_HEADER: extensions_str}
        )

        with patch.object(
            client, '_execute_request', new_callable=AsyncMock
        ) as mock_execute_request:
            mock_execute_request.return_value = json_format.MessageToDict(
                agent_card
            )
            await client.get_extended_agent_card(request, context=context)

        mock_execute_request.assert_called_once()
        call_args = mock_execute_request.call_args
        assert (
            call_args[1].get('context') == context or call_args[0][3] == context
        )

        _context = call_args[1].get('context') or call_args[0][3]
        assert _context.service_parameters == {
            HTTP_EXTENSION_HEADER: extensions_str
        }


class TestTaskCallback:
    """Tests for the task callback methods."""

    @pytest.mark.asyncio
    async def test_list_task_push_notification_configs_success(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        """Test successful task multiple callbacks retrieval."""
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            url='http://agent.example.com/api',
        )
        task_id = 'task-1'
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'configs': [
                {
                    'taskId': task_id,
                    'id': 'config-1',
                    'url': 'https://example.com',
                }
            ]
        }
        mock_httpx_client.send.return_value = mock_response

        # Mock the build_request method to capture its inputs
        mock_build_request = MagicMock(
            return_value=AsyncMock(spec=httpx.Request)
        )
        mock_httpx_client.build_request = mock_build_request

        request = ListTaskPushNotificationConfigsRequest(
            task_id=task_id,
        )
        response = await client.list_task_push_notification_configs(request)

        assert len(response.configs) == 1
        assert response.configs[0].task_id == task_id

        mock_build_request.assert_called_once()
        call_args = mock_build_request.call_args
        assert call_args[0][0] == 'GET'
        assert f'/tasks/{task_id}/pushNotificationConfigs' in call_args[0][1]

    @pytest.mark.asyncio
    async def test_delete_task_push_notification_config_success(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        """Test successful task callback deletion."""
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            url='http://agent.example.com/api',
        )
        task_id = 'task-1'
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_httpx_client.send.return_value = mock_response

        # Mock the build_request method to capture its inputs
        mock_build_request = MagicMock(
            return_value=AsyncMock(spec=httpx.Request)
        )
        mock_httpx_client.build_request = mock_build_request

        request = DeleteTaskPushNotificationConfigRequest(
            task_id=task_id,
            id='config-1',
        )
        await client.delete_task_push_notification_config(request)

        mock_build_request.assert_called_once()
        call_args = mock_build_request.call_args
        assert call_args[0][0] == 'DELETE'
        assert (
            f'/tasks/{task_id}/pushNotificationConfigs/config-1'
            in call_args[0][1]
        )


class TestRestTransportTenant:
    """Tests for tenant path prepending in RestTransport."""

    @pytest.mark.parametrize(
        'method_name, request_obj, expected_path',
        [
            (
                'send_message',
                SendMessageRequest(
                    tenant='my-tenant',
                    message=new_text_message(text='hi'),
                ),
                '/my-tenant/message:send',
            ),
            (
                'list_tasks',
                ListTasksRequest(tenant='my-tenant'),
                '/my-tenant/tasks',
            ),
            (
                'get_task',
                GetTaskRequest(tenant='my-tenant', id='task-123'),
                '/my-tenant/tasks/task-123',
            ),
            (
                'cancel_task',
                CancelTaskRequest(tenant='my-tenant', id='task-123'),
                '/my-tenant/tasks/task-123:cancel',
            ),
            (
                'create_task_push_notification_config',
                TaskPushNotificationConfig(
                    tenant='my-tenant', task_id='task-123'
                ),
                '/my-tenant/tasks/task-123/pushNotificationConfigs',
            ),
            (
                'get_task_push_notification_config',
                GetTaskPushNotificationConfigRequest(
                    tenant='my-tenant', task_id='task-123', id='cfg-1'
                ),
                '/my-tenant/tasks/task-123/pushNotificationConfigs/cfg-1',
            ),
            (
                'list_task_push_notification_configs',
                ListTaskPushNotificationConfigsRequest(
                    tenant='my-tenant', task_id='task-123'
                ),
                '/my-tenant/tasks/task-123/pushNotificationConfigs',
            ),
            (
                'delete_task_push_notification_config',
                DeleteTaskPushNotificationConfigRequest(
                    tenant='my-tenant', task_id='task-123', id='cfg-1'
                ),
                '/my-tenant/tasks/task-123/pushNotificationConfigs/cfg-1',
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_rest_methods_prepend_tenant(
        self,
        method_name,
        request_obj,
        expected_path,
        mock_httpx_client,
        mock_agent_card,
    ):
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            url='http://agent.example.com/api',
        )

        # 1. Get the method dynamically
        method = getattr(client, method_name)

        # 2. Setup mocks
        mock_httpx_client.build_request.return_value = MagicMock(
            spec=httpx.Request
        )
        mock_httpx_client.send.return_value = AsyncMock(
            spec=httpx.Response,
            status_code=200,
            json=MagicMock(return_value={}),
        )

        # 3. Call the method
        await method(request=request_obj)

        # 4. Verify the URL
        args, _ = mock_httpx_client.build_request.call_args
        assert args[1] == f'http://agent.example.com/api{expected_path}'

    @pytest.mark.asyncio
    async def test_rest_get_extended_agent_card_prepend_tenant(
        self,
        mock_httpx_client,
        mock_agent_card,
    ):
        mock_agent_card.capabilities.extended_agent_card = True
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            url='http://agent.example.com/api',
        )

        request = GetExtendedAgentCardRequest(tenant='my-tenant')

        # 1. Setup mocks
        mock_httpx_client.build_request.return_value = MagicMock(
            spec=httpx.Request
        )
        mock_httpx_client.send.return_value = AsyncMock(
            spec=httpx.Response,
            status_code=200,
            json=MagicMock(return_value={}),
        )

        # 2. Call the method
        await client.get_extended_agent_card(request=request)

        # 3. Verify the URL
        args, _ = mock_httpx_client.build_request.call_args
        assert (
            args[1]
            == 'http://agent.example.com/api/my-tenant/extendedAgentCard'
        )

    @pytest.mark.asyncio
    async def test_rest_get_task_prepend_empty_tenant(
        self,
        mock_httpx_client,
        mock_agent_card,
    ):
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            url='http://agent.example.com/api',
        )

        request = GetTaskRequest(tenant='', id='task-123')

        # 1. Setup mocks
        mock_httpx_client.build_request.return_value = MagicMock(
            spec=httpx.Request
        )
        mock_httpx_client.send.return_value = AsyncMock(
            spec=httpx.Response,
            status_code=200,
            json=MagicMock(return_value={}),
        )

        # 2. Call the method
        await client.get_task(request=request)

        # 3. Verify the URL
        args, _ = mock_httpx_client.build_request.call_args
        assert args[1] == 'http://agent.example.com/api/tasks/task-123'

    @pytest.mark.parametrize(
        'method_name, request_obj, expected_path',
        [
            (
                'subscribe',
                SubscribeToTaskRequest(tenant='my-tenant', id='task-123'),
                '/my-tenant/tasks/task-123:subscribe',
            ),
            (
                'send_message_streaming',
                SendMessageRequest(
                    tenant='my-tenant',
                    message=new_text_message(text='hi'),
                ),
                '/my-tenant/message:stream',
            ),
        ],
    )
    @pytest.mark.asyncio
    @patch('a2a.client.transports.http_helpers._SSEEventSource')
    async def test_rest_streaming_methods_prepend_tenant(  # noqa: PLR0913
        self,
        mock_aconnect_sse,
        method_name,
        request_obj,
        expected_path,
        mock_httpx_client,
        mock_agent_card,
    ):
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            url='http://agent.example.com/api',
        )

        # 1. Get the method dynamically
        method = getattr(client, method_name)

        # 2. Setup mocks
        mock_event_source = AsyncMock(spec=EventSource)
        mock_event_source.response = MagicMock(spec=httpx.Response)
        mock_event_source.response.headers = {
            'content-type': 'text/event-stream'
        }
        mock_event_source.response.raise_for_status.return_value = None

        async def empty_aiter():
            if False:
                yield

        mock_event_source.aiter_sse.return_value = empty_aiter()
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        # 3. Call the method
        async for _ in method(request=request_obj):
            pass

        # 4. Verify the URL and method
        mock_aconnect_sse.assert_called_once()
        args, kwargs = mock_aconnect_sse.call_args
        # method is 2nd positional argument
        assert args[1] == 'POST'
        if method_name == 'subscribe':
            assert kwargs.get('json') is None
        else:
            assert kwargs.get('json') == json_format.MessageToDict(request_obj)

        # url is 3rd positional argument in aconnect_sse(client, method, url, ...)
        assert args[2] == f'http://agent.example.com/api{expected_path}'
