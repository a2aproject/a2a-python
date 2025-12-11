import json

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from httpx_sse import EventSource, SSEError, ServerSentEvent

from a2a.client import (
    A2ACardResolver,
    A2AClientHTTPError,
    A2AClientJSONError,
    A2AClientTimeoutError,
    create_text_message_object,
)
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    InvalidParamsError,
    Message,
    MessageSendParams,
    PushNotificationConfig,
    Role,
    SendMessageSuccessResponse,
    Task,
    TaskIdParams,
    TaskPushNotificationConfig,
    TaskQueryParams,
)
from a2a.utils import AGENT_CARD_WELL_KNOWN_PATH


AGENT_CARD = AgentCard(
    name='Hello World Agent',
    description='Just a hello world agent',
    url='http://localhost:9999/',
    version='1.0.0',
    default_input_modes=['text'],
    default_output_modes=['text'],
    capabilities=AgentCapabilities(),
    skills=[
        AgentSkill(
            id='hello_world',
            name='Returns hello world',
            description='just returns hello world',
            tags=['hello world'],
            examples=['hi', 'hello world'],
        )
    ],
)

AGENT_CARD_EXTENDED = AGENT_CARD.model_copy(
    update={
        'name': 'Hello World Agent - Extended Edition',
        'skills': [
            *AGENT_CARD.skills,
            AgentSkill(
                id='extended_skill',
                name='Super Greet',
                description='A more enthusiastic greeting.',
                tags=['extended'],
                examples=['super hi'],
            ),
        ],
        'version': '1.0.1',
    }
)

AGENT_CARD_SUPPORTS_EXTENDED = AGENT_CARD.model_copy(
    update={'supports_authenticated_extended_card': True}
)
AGENT_CARD_NO_URL_SUPPORTS_EXTENDED = AGENT_CARD_SUPPORTS_EXTENDED.model_copy(
    update={'url': ''}
)

MINIMAL_TASK: dict[str, Any] = {
    'id': 'task-abc',
    'contextId': 'session-xyz',
    'status': {'state': 'working'},
    'kind': 'task',
}

MINIMAL_CANCELLED_TASK: dict[str, Any] = {
    'id': 'task-abc',
    'contextId': 'session-xyz',
    'status': {'state': 'canceled'},
    'kind': 'task',
}


@pytest.fixture
def mock_httpx_client() -> AsyncMock:
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def mock_agent_card() -> MagicMock:
    mock = MagicMock(spec=AgentCard, url='http://agent.example.com/api')
    mock.supports_authenticated_extended_card = False
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


class TestA2ACardResolver:
    BASE_URL = 'http://example.com'
    AGENT_CARD_PATH = AGENT_CARD_WELL_KNOWN_PATH
    FULL_AGENT_CARD_URL = f'{BASE_URL}{AGENT_CARD_PATH}'
    EXTENDED_AGENT_CARD_PATH = '/agent/authenticatedExtendedCard'

    @pytest.mark.asyncio
    async def test_init_parameters_stored_correctly(
        self, mock_httpx_client: AsyncMock
    ):
        base_url = 'http://example.com'
        custom_path = '/custom/agent-card.json'
        resolver = A2ACardResolver(
            httpx_client=mock_httpx_client,
            base_url=base_url,
            agent_card_path=custom_path,
        )
        assert resolver.base_url == base_url
        assert resolver.agent_card_path == custom_path.lstrip('/')
        assert resolver.httpx_client == mock_httpx_client

        resolver_default_path = A2ACardResolver(
            httpx_client=mock_httpx_client,
            base_url=base_url,
        )
        assert (
            '/' + resolver_default_path.agent_card_path
            == AGENT_CARD_WELL_KNOWN_PATH
        )

    @pytest.mark.asyncio
    async def test_init_strips_slashes(self, mock_httpx_client: AsyncMock):
        resolver = A2ACardResolver(
            httpx_client=mock_httpx_client,
            base_url='http://example.com/',
            agent_card_path='/.well-known/agent-card.json/',
        )
        assert resolver.base_url == 'http://example.com'
        assert resolver.agent_card_path == '.well-known/agent-card.json/'

    @pytest.mark.asyncio
    async def test_get_agent_card_success_public_only(
        self, mock_httpx_client: AsyncMock
    ):
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = AGENT_CARD.model_dump(mode='json')
        mock_httpx_client.get.return_value = mock_response

        resolver = A2ACardResolver(
            httpx_client=mock_httpx_client,
            base_url=self.BASE_URL,
            agent_card_path=self.AGENT_CARD_PATH,
        )
        agent_card = await resolver.get_agent_card(http_kwargs={'timeout': 10})

        mock_httpx_client.get.assert_called_once_with(
            self.FULL_AGENT_CARD_URL, timeout=10
        )
        mock_response.raise_for_status.assert_called_once()
        assert isinstance(agent_card, AgentCard)
        assert agent_card == AGENT_CARD
        assert mock_httpx_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_get_agent_card_success_with_specified_path_for_extended_card(
        self, mock_httpx_client: AsyncMock
    ):
        extended_card_response = AsyncMock(spec=httpx.Response)
        extended_card_response.status_code = 200
        extended_card_response.json.return_value = (
            AGENT_CARD_EXTENDED.model_dump(mode='json')
        )
        mock_httpx_client.get.return_value = extended_card_response

        resolver = A2ACardResolver(
            httpx_client=mock_httpx_client,
            base_url=self.BASE_URL,
            agent_card_path=self.AGENT_CARD_PATH,
        )

        auth_kwargs = {'headers': {'Authorization': 'Bearer test token'}}
        agent_card_result = await resolver.get_agent_card(
            relative_card_path=self.EXTENDED_AGENT_CARD_PATH,
            http_kwargs=auth_kwargs,
        )

        expected_extended_url = (
            f'{self.BASE_URL}/{self.EXTENDED_AGENT_CARD_PATH.lstrip("/")}'
        )
        mock_httpx_client.get.assert_called_once_with(
            expected_extended_url, **auth_kwargs
        )
        extended_card_response.raise_for_status.assert_called_once()
        assert isinstance(agent_card_result, AgentCard)
        assert agent_card_result == AGENT_CARD_EXTENDED

    @pytest.mark.asyncio
    async def test_get_agent_card_validation_error(
        self, mock_httpx_client: AsyncMock
    ):
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'invalid_field': 'value',
            'name': 'Test Agent',
        }
        mock_httpx_client.get.return_value = mock_response

        resolver = A2ACardResolver(
            httpx_client=mock_httpx_client, base_url=self.BASE_URL
        )
        with pytest.raises(A2AClientJSONError) as exc_info:
            await resolver.get_agent_card()

        assert (
            f'Failed to validate agent card structure from {self.FULL_AGENT_CARD_URL}'
            in str(exc_info.value)
        )
        assert 'invalid_field' in str(exc_info.value)
        assert mock_httpx_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_get_agent_card_http_status_error(
        self, mock_httpx_client: AsyncMock
    ):
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 404
        mock_response.text = 'Not Found'
        http_status_error = httpx.HTTPStatusError(
            'Not Found', request=MagicMock(), response=mock_response
        )
        mock_httpx_client.get.side_effect = http_status_error

        resolver = A2ACardResolver(
            httpx_client=mock_httpx_client,
            base_url=self.BASE_URL,
            agent_card_path=self.AGENT_CARD_PATH,
        )

        with pytest.raises(A2AClientHTTPError) as exc_info:
            await resolver.get_agent_card()

        assert exc_info.value.status_code == 404
        assert (
            f'Failed to fetch agent card from {self.FULL_AGENT_CARD_URL}'
            in str(exc_info.value)
        )
        assert 'Not Found' in str(exc_info.value)
        mock_httpx_client.get.assert_called_once_with(self.FULL_AGENT_CARD_URL)

    @pytest.mark.asyncio
    async def test_get_agent_card_json_decode_error(
        self, mock_httpx_client: AsyncMock
    ):
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        json_error = json.JSONDecodeError('Expecting value', 'doc', 0)
        mock_response.json.side_effect = json_error
        mock_httpx_client.get.return_value = mock_response

        resolver = A2ACardResolver(
            httpx_client=mock_httpx_client,
            base_url=self.BASE_URL,
            agent_card_path=self.AGENT_CARD_PATH,
        )

        with pytest.raises(A2AClientJSONError) as exc_info:
            await resolver.get_agent_card()

        assert (
            f'Failed to parse JSON for agent card from {self.FULL_AGENT_CARD_URL}'
            in str(exc_info.value)
        )
        assert 'Expecting value' in str(exc_info.value)
        mock_httpx_client.get.assert_called_once_with(self.FULL_AGENT_CARD_URL)

    @pytest.mark.asyncio
    async def test_get_agent_card_request_error(
        self, mock_httpx_client: AsyncMock
    ):
        request_error = httpx.RequestError('Network issue', request=MagicMock())
        mock_httpx_client.get.side_effect = request_error

        resolver = A2ACardResolver(
            httpx_client=mock_httpx_client,
            base_url=self.BASE_URL,
            agent_card_path=self.AGENT_CARD_PATH,
        )

        with pytest.raises(A2AClientHTTPError) as exc_info:
            await resolver.get_agent_card()

        assert exc_info.value.status_code == 503
        assert (
            f'Network communication error fetching agent card from {self.FULL_AGENT_CARD_URL}'
            in str(exc_info.value)
        )
        assert 'Network issue' in str(exc_info.value)
        mock_httpx_client.get.assert_called_once_with(self.FULL_AGENT_CARD_URL)


class TestJsonRpcTransport:
    AGENT_URL = 'http://agent.example.com/api'

    def test_init_with_agent_card(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        assert client.url == mock_agent_card.url
        assert client.httpx_client == mock_httpx_client

    def test_init_with_url(self, mock_httpx_client: AsyncMock):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, url=self.AGENT_URL
        )
        assert client.url == self.AGENT_URL
        assert client.httpx_client == mock_httpx_client

    def test_init_with_agent_card_and_url_prioritizes_url(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            url='http://otherurl.com',
        )
        assert client.url == 'http://otherurl.com'

    def test_init_raises_value_error_if_no_card_or_url(
        self, mock_httpx_client: AsyncMock
    ):
        with pytest.raises(ValueError) as exc_info:
            JsonRpcTransport(httpx_client=mock_httpx_client)
        assert 'Must provide either agent_card or url' in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_send_message_success(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params = MessageSendParams(
            message=create_text_message_object(content='Hello')
        )
        success_response = create_text_message_object(
            role=Role.agent, content='Hi there!'
        )
        rpc_response = SendMessageSuccessResponse(
            id='123', jsonrpc='2.0', result=success_response
        )
        response = httpx.Response(
            200, json=rpc_response.model_dump(mode='json')
        )
        response.request = httpx.Request('POST', 'http://agent.example.com/api')
        mock_httpx_client.post.return_value = response

        response = await client.send_message(request=params)

        assert isinstance(response, Message)
        assert response.model_dump() == success_response.model_dump()

    @pytest.mark.asyncio
    async def test_send_message_error_response(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params = MessageSendParams(
            message=create_text_message_object(content='Hello')
        )
        error_response = InvalidParamsError()
        rpc_response = {
            'id': '123',
            'jsonrpc': '2.0',
            'error': error_response.model_dump(exclude_none=True),
        }
        mock_httpx_client.post.return_value.json.return_value = rpc_response

        with pytest.raises(Exception):
            await client.send_message(request=params)

    @pytest.mark.asyncio
    @patch('a2a.client.transports.jsonrpc.aconnect_sse')
    async def test_send_message_streaming_success(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params = MessageSendParams(
            message=create_text_message_object(content='Hello stream')
        )
        mock_stream_response_1 = SendMessageSuccessResponse(
            id='stream_id_123',
            jsonrpc='2.0',
            result=create_text_message_object(
                content='First part ', role=Role.agent
            ),
        )
        mock_stream_response_2 = SendMessageSuccessResponse(
            id='stream_id_123',
            jsonrpc='2.0',
            result=create_text_message_object(
                content='second part ', role=Role.agent
            ),
        )
        sse_event_1 = ServerSentEvent(
            data=mock_stream_response_1.model_dump_json()
        )
        sse_event_2 = ServerSentEvent(
            data=mock_stream_response_2.model_dump_json()
        )
        mock_event_source = AsyncMock(spec=EventSource)
        mock_event_source.aiter_sse.return_value = async_iterable_from_list(
            [sse_event_1, sse_event_2]
        )
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        results = [
            item async for item in client.send_message_streaming(request=params)
        ]

        assert len(results) == 2
        assert isinstance(results[0], Message)
        assert (
            results[0].model_dump()
            == mock_stream_response_1.result.model_dump()
        )
        assert isinstance(results[1], Message)
        assert (
            results[1].model_dump()
            == mock_stream_response_2.result.model_dump()
        )

    @pytest.mark.asyncio
    async def test_send_request_http_status_error(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 404
        mock_response.text = 'Not Found'
        http_error = httpx.HTTPStatusError(
            'Not Found', request=MagicMock(), response=mock_response
        )
        mock_httpx_client.post.side_effect = http_error

        with pytest.raises(A2AClientHTTPError) as exc_info:
            await client._send_request({}, {})

        assert exc_info.value.status_code == 404
        assert 'Not Found' in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_send_request_json_decode_error(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        json_error = json.JSONDecodeError('Expecting value', 'doc', 0)
        mock_response.json.side_effect = json_error
        mock_httpx_client.post.return_value = mock_response

        with pytest.raises(A2AClientJSONError) as exc_info:
            await client._send_request({}, {})

        assert 'Expecting value' in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_send_request_httpx_request_error(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        request_error = httpx.RequestError('Network issue', request=MagicMock())
        mock_httpx_client.post.side_effect = request_error

        with pytest.raises(A2AClientHTTPError) as exc_info:
            await client._send_request({}, {})

        assert exc_info.value.status_code == 503
        assert 'Network communication error' in str(exc_info.value)
        assert 'Network issue' in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_send_message_client_timeout(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        mock_httpx_client.post.side_effect = httpx.ReadTimeout(
            'Request timed out'
        )
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params = MessageSendParams(
            message=create_text_message_object(content='Hello')
        )

        with pytest.raises(A2AClientTimeoutError) as exc_info:
            await client.send_message(request=params)

        assert 'Client Request timed out' in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_task_success(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params = TaskQueryParams(id='task-abc')
        rpc_response = {
            'id': '123',
            'jsonrpc': '2.0',
            'result': MINIMAL_TASK,
        }
        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_request:
            mock_send_request.return_value = rpc_response
            response = await client.get_task(request=params)

        assert isinstance(response, Task)
        assert (
            response.model_dump()
            == Task.model_validate(MINIMAL_TASK).model_dump()
        )
        mock_send_request.assert_called_once()
        sent_payload = mock_send_request.call_args.args[0]
        assert sent_payload['method'] == 'tasks/get'

    @pytest.mark.asyncio
    async def test_cancel_task_success(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params = TaskIdParams(id='task-abc')
        rpc_response = {
            'id': '123',
            'jsonrpc': '2.0',
            'result': MINIMAL_CANCELLED_TASK,
        }
        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_request:
            mock_send_request.return_value = rpc_response
            response = await client.cancel_task(request=params)

        assert isinstance(response, Task)
        assert (
            response.model_dump()
            == Task.model_validate(MINIMAL_CANCELLED_TASK).model_dump()
        )
        mock_send_request.assert_called_once()
        sent_payload = mock_send_request.call_args.args[0]
        assert sent_payload['method'] == 'tasks/cancel'

    @pytest.mark.asyncio
    async def test_set_task_callback_success(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params = TaskPushNotificationConfig(
            task_id='task-abc',
            push_notification_config=PushNotificationConfig(
                url='http://callback.com'
            ),
        )
        rpc_response = {
            'id': '123',
            'jsonrpc': '2.0',
            'result': params.model_dump(mode='json'),
        }
        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_request:
            mock_send_request.return_value = rpc_response
            response = await client.set_task_callback(request=params)

        assert isinstance(response, TaskPushNotificationConfig)
        assert response.model_dump() == params.model_dump()
        mock_send_request.assert_called_once()
        sent_payload = mock_send_request.call_args.args[0]
        assert sent_payload['method'] == 'tasks/pushNotificationConfig/set'

    @pytest.mark.asyncio
    async def test_get_task_callback_success(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params = TaskIdParams(id='task-abc')
        expected_response = TaskPushNotificationConfig(
            task_id='task-abc',
            push_notification_config=PushNotificationConfig(
                url='http://callback.com'
            ),
        )
        rpc_response = {
            'id': '123',
            'jsonrpc': '2.0',
            'result': expected_response.model_dump(mode='json'),
        }
        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_request:
            mock_send_request.return_value = rpc_response
            response = await client.get_task_callback(request=params)

        assert isinstance(response, TaskPushNotificationConfig)
        assert response.model_dump() == expected_response.model_dump()
        mock_send_request.assert_called_once()
        sent_payload = mock_send_request.call_args.args[0]
        assert sent_payload['method'] == 'tasks/pushNotificationConfig/get'

    @pytest.mark.asyncio
    @patch('a2a.client.transports.jsonrpc.aconnect_sse')
    async def test_send_message_streaming_sse_error(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params = MessageSendParams(
            message=create_text_message_object(content='Hello stream')
        )
        mock_event_source = AsyncMock(spec=EventSource)
        mock_event_source.aiter_sse.side_effect = SSEError(
            'Simulated SSE error'
        )
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        with pytest.raises(A2AClientHTTPError):
            _ = [
                item
                async for item in client.send_message_streaming(request=params)
            ]

    @pytest.mark.asyncio
    @patch('a2a.client.transports.jsonrpc.aconnect_sse')
    async def test_send_message_streaming_json_error(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params = MessageSendParams(
            message=create_text_message_object(content='Hello stream')
        )
        sse_event = ServerSentEvent(data='{invalid json')
        mock_event_source = AsyncMock(spec=EventSource)
        mock_event_source.aiter_sse.return_value = async_iterable_from_list(
            [sse_event]
        )
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        with pytest.raises(A2AClientJSONError):
            _ = [
                item
                async for item in client.send_message_streaming(request=params)
            ]

    @pytest.mark.asyncio
    @patch('a2a.client.transports.jsonrpc.aconnect_sse')
    async def test_send_message_streaming_request_error(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params = MessageSendParams(
            message=create_text_message_object(content='Hello stream')
        )
        mock_event_source = AsyncMock(spec=EventSource)
        mock_event_source.aiter_sse.side_effect = httpx.RequestError(
            'Simulated request error', request=MagicMock()
        )
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        with pytest.raises(A2AClientHTTPError):
            _ = [
                item
                async for item in client.send_message_streaming(request=params)
            ]

    @pytest.mark.asyncio
    async def test_get_card_no_card_provided(
        self, mock_httpx_client: AsyncMock
    ):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, url=self.AGENT_URL
        )
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = AGENT_CARD.model_dump(mode='json')
        mock_httpx_client.get.return_value = mock_response

        card = await client.get_card()

        assert card == AGENT_CARD
        mock_httpx_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_card_with_extended_card_support(
        self, mock_httpx_client: AsyncMock
    ):
        agent_card = AGENT_CARD.model_copy(
            update={'supports_authenticated_extended_card': True}
        )
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, agent_card=agent_card
        )

        rpc_response = {
            'id': '123',
            'jsonrpc': '2.0',
            'result': AGENT_CARD_EXTENDED.model_dump(mode='json'),
        }
        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_request:
            mock_send_request.return_value = rpc_response
            card = await client.get_card()

        assert card == AGENT_CARD_EXTENDED
        mock_send_request.assert_called_once()
        sent_payload = mock_send_request.call_args.args[0]
        assert sent_payload['method'] == 'agent/getAuthenticatedExtendedCard'

    @pytest.mark.asyncio
    async def test_close(self, mock_httpx_client: AsyncMock):
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client, url=self.AGENT_URL
        )
        await client.close()
        mock_httpx_client.aclose.assert_called_once()


class TestJsonRpcTransportExtensions:
    @pytest.mark.asyncio
    async def test_send_message_with_default_extensions(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        """Test that send_message adds extension headers when extensions are provided."""
        extensions = [
            'https://example.com/test-ext/v1',
            'https://example.com/test-ext/v2',
        ]
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            extensions=extensions,
        )
        params = MessageSendParams(
            message=create_text_message_object(content='Hello')
        )
        success_response = create_text_message_object(
            role=Role.agent, content='Hi there!'
        )
        rpc_response = SendMessageSuccessResponse(
            id='123', jsonrpc='2.0', result=success_response
        )
        # Mock the response from httpx_client.post
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = rpc_response.model_dump(mode='json')
        mock_httpx_client.post.return_value = mock_response

        await client.send_message(request=params)

        mock_httpx_client.post.assert_called_once()
        _, mock_kwargs = mock_httpx_client.post.call_args

        _assert_extensions_header(
            mock_kwargs,
            {
                'https://example.com/test-ext/v1',
                'https://example.com/test-ext/v2',
            },
        )

    @pytest.mark.asyncio
    @patch('a2a.client.transports.jsonrpc.aconnect_sse')
    async def test_send_message_streaming_with_new_extensions(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        """Test X-A2A-Extensions header in send_message_streaming."""
        new_extensions = ['https://example.com/test-ext/v2']
        extensions = ['https://example.com/test-ext/v1']
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            extensions=extensions,
        )
        params = MessageSendParams(
            message=create_text_message_object(content='Hello stream')
        )

        mock_event_source = AsyncMock(spec=EventSource)
        mock_event_source.aiter_sse.return_value = async_iterable_from_list([])
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        async for _ in client.send_message_streaming(
            request=params, extensions=new_extensions
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
    @patch('a2a.client.transports.jsonrpc.aconnect_sse')
    async def test_send_message_streaming_server_error_propagates(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        """Test that send_message_streaming propagates server errors (e.g., 403, 500) directly."""
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
        )
        params = MessageSendParams(
            message=create_text_message_object(content='Error stream')
        )

        mock_event_source = AsyncMock(spec=EventSource)
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.status_code = 403
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            'Forbidden',
            request=httpx.Request('POST', 'http://test.url'),
            response=mock_response,
        )
        mock_event_source.response = mock_response
        mock_event_source.aiter_sse.return_value = async_iterable_from_list([])
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        with pytest.raises(A2AClientHTTPError) as exc_info:
            async for _ in client.send_message_streaming(request=params):
                pass

        assert exc_info.value.status_code == 403
        mock_aconnect_sse.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_card_no_card_provided_with_extensions(
        self, mock_httpx_client: AsyncMock
    ):
        """Test get_card with extensions set in Client when no card is initially provided.
        Tests that the extensions are added to the HTTP GET request."""
        extensions = [
            'https://example.com/test-ext/v1',
            'https://example.com/test-ext/v2',
        ]
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            url=TestJsonRpcTransport.AGENT_URL,
            extensions=extensions,
        )
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = AGENT_CARD.model_dump(mode='json')
        mock_httpx_client.get.return_value = mock_response

        await client.get_card()

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
        self, mock_httpx_client: AsyncMock
    ):
        """Test get_card with extensions passed to get_card call when extended card support is enabled.
        Tests that the extensions are added to the RPC request."""
        extensions = [
            'https://example.com/test-ext/v1',
            'https://example.com/test-ext/v2',
        ]
        agent_card = AGENT_CARD.model_copy(
            update={'supports_authenticated_extended_card': True}
        )
        client = JsonRpcTransport(
            httpx_client=mock_httpx_client,
            agent_card=agent_card,
            extensions=extensions,
        )

        rpc_response = {
            'id': '123',
            'jsonrpc': '2.0',
            'result': AGENT_CARD_EXTENDED.model_dump(mode='json'),
        }
        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_request:
            mock_send_request.return_value = rpc_response
            await client.get_card(extensions=extensions)

        mock_send_request.assert_called_once()
        _, mock_kwargs = mock_send_request.call_args[0]

        _assert_extensions_header(
            mock_kwargs,
            {
                'https://example.com/test-ext/v1',
                'https://example.com/test-ext/v2',
            },
        )
