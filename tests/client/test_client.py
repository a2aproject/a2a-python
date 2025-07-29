import json

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import httpx
import pytest

from httpx_sse import EventSource, SSEError, ServerSentEvent

from a2a.client import (
    A2ACardResolver,
    A2AClient,
    A2AClientHTTPError,
    A2AClientJSONError,
    A2AClientTimeoutError,
    create_text_message_object,
)
from a2a.types import (
    A2ARequest,
    AgentCapabilities,
    AgentCard,
    AgentSkill,
    CancelTaskRequest,
    CancelTaskResponse,
    CancelTaskSuccessResponse,
    GetTaskPushNotificationConfigRequest,
    GetTaskPushNotificationConfigResponse,
    GetTaskPushNotificationConfigSuccessResponse,
    GetTaskRequest,
    GetTaskResponse,
    InvalidParamsError,
    JSONRPCErrorResponse,
    MessageSendParams,
    PushNotificationConfig,
    Role,
    SendMessageRequest,
    SendMessageResponse,
    SendMessageSuccessResponse,
    SendStreamingMessageRequest,
    SendStreamingMessageResponse,
    SetTaskPushNotificationConfigRequest,
    SetTaskPushNotificationConfigResponse,
    SetTaskPushNotificationConfigSuccessResponse,
    TaskIdParams,
    TaskNotCancelableError,
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
    return MagicMock(spec=AgentCard, url='http://agent.example.com/api')


async def async_iterable_from_list(
    items: list[ServerSentEvent],
) -> AsyncGenerator[ServerSentEvent]:
    """Helper to create an async iterable from a list."""
    for item in items:
        yield item


class TestA2ACardResolver:
    BASE_URL = 'http://example.com'
    AGENT_CARD_PATH = AGENT_CARD_WELL_KNOWN_PATH
    FULL_AGENT_CARD_URL = f'{BASE_URL}{AGENT_CARD_PATH}'
    EXTENDED_AGENT_CARD_PATH = (
        '/agent/authenticatedExtendedCard'  # Default path
    )

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

        # Test default agent_card_path
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
            base_url='http://example.com/',  # With trailing slash
            agent_card_path='/.well-known/agent.json/',  # With leading/trailing slash
        )
        assert (
            resolver.base_url == 'http://example.com'
        )  # Trailing slash stripped
        # constructor lstrips agent_card_path, but keeps trailing if provided
        assert resolver.agent_card_path == '.well-known/agent.json/'

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
        # Ensure only one call was made (for the public card)
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

        # Mock the single call for the extended card
        mock_httpx_client.get.return_value = extended_card_response

        resolver = A2ACardResolver(
            httpx_client=mock_httpx_client,
            base_url=self.BASE_URL,
            agent_card_path=self.AGENT_CARD_PATH,
        )

        # Fetch the extended card by providing its relative path and example auth
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
        assert (
            agent_card_result == AGENT_CARD_EXTENDED
        )  # Should return the extended card

    @pytest.mark.asyncio
    async def test_get_agent_card_validation_error(
        self, mock_httpx_client: AsyncMock
    ):
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        # Data that will cause a Pydantic ValidationError
        mock_response.json.return_value = {
            'invalid_field': 'value',
            'name': 'Test Agent',
        }
        mock_httpx_client.get.return_value = mock_response

        resolver = A2ACardResolver(
            httpx_client=mock_httpx_client, base_url=self.BASE_URL
        )
        # The call that is expected to raise an error should be within pytest.raises
        with pytest.raises(A2AClientJSONError) as exc_info:
            await resolver.get_agent_card()  # Fetches from default path

        assert (
            f'Failed to validate agent card structure from {self.FULL_AGENT_CARD_URL}'
            in str(exc_info.value)
        )
        assert 'invalid_field' in str(
            exc_info.value
        )  # Check if Pydantic error details are present
        assert (
            mock_httpx_client.get.call_count == 1
        )  # Should only be called once

    @pytest.mark.asyncio
    async def test_get_agent_card_http_status_error(
        self, mock_httpx_client: AsyncMock
    ):
        mock_response = MagicMock(
            spec=httpx.Response
        )  # Use MagicMock for response attribute
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
        # Define json_error before using it
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

        # Assertions using exc_info must be after the with block
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


class TestA2AClient:
    AGENT_URL = 'http://agent.example.com/api'

    def test_init_with_agent_card(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        assert client.url == mock_agent_card.url
        assert client.httpx_client == mock_httpx_client

    def test_init_with_url(self, mock_httpx_client: AsyncMock):
        client = A2AClient(httpx_client=mock_httpx_client, url=self.AGENT_URL)
        assert client.url == self.AGENT_URL
        assert client.httpx_client == mock_httpx_client

    def test_init_with_agent_card_and_url_prioritizes_agent_card(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            url='http://otherurl.com',
        )
        assert (
            client.url == mock_agent_card.url
        )  # Agent card URL should be used

    def test_init_raises_value_error_if_no_card_or_url(
        self, mock_httpx_client: AsyncMock
    ):
        with pytest.raises(ValueError) as exc_info:
            A2AClient(httpx_client=mock_httpx_client)
        assert 'Must provide either agent_card or url' in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_client_from_agent_card_url_success(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        base_url = 'http://example.com'
        agent_card_path = '/.well-known/custom-agent.json'
        resolver_kwargs = {'timeout': 30}

        mock_resolver_instance = AsyncMock(spec=A2ACardResolver)
        mock_resolver_instance.get_agent_card.return_value = mock_agent_card

        with patch(
            'a2a.client.client.A2ACardResolver',
            return_value=mock_resolver_instance,
        ) as mock_resolver_class:
            client = await A2AClient.get_client_from_agent_card_url(
                httpx_client=mock_httpx_client,
                base_url=base_url,
                agent_card_path=agent_card_path,
                http_kwargs=resolver_kwargs,
            )

            mock_resolver_class.assert_called_once_with(
                mock_httpx_client,
                base_url=base_url,
                agent_card_path=agent_card_path,
            )
            mock_resolver_instance.get_agent_card.assert_called_once_with(
                http_kwargs=resolver_kwargs,
                # relative_card_path=None is implied by not passing it
            )
            assert isinstance(client, A2AClient)
            assert client.url == mock_agent_card.url
            assert client.httpx_client == mock_httpx_client

    @pytest.mark.asyncio
    async def test_get_client_from_agent_card_url_resolver_error(
        self, mock_httpx_client: AsyncMock
    ):
        error_to_raise = A2AClientHTTPError(404, 'Agent card not found')
        with patch(
            'a2a.client.client.A2ACardResolver.get_agent_card',
            new_callable=AsyncMock,
            side_effect=error_to_raise,
        ):
            with pytest.raises(A2AClientHTTPError) as exc_info:
                await A2AClient.get_client_from_agent_card_url(
                    httpx_client=mock_httpx_client,
                    base_url='http://example.com',
                )
            assert exc_info.value == error_to_raise

    @pytest.mark.asyncio
    async def test_send_message_success_use_request(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )

        params = MessageSendParams(
            message=create_text_message_object(content='Hello')
        )

        request = SendMessageRequest(id=123, params=params)

        success_response = create_text_message_object(
            role=Role.agent, content='Hi there!'
        ).model_dump(exclude_none=True)

        rpc_response: dict[str, Any] = {
            'id': 123,
            'jsonrpc': '2.0',
            'result': success_response,
        }

        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_req:
            mock_send_req.return_value = rpc_response
            response = await client.send_message(
                request=request, http_kwargs={'timeout': 10}
            )

            assert mock_send_req.call_count == 1
            called_args, called_kwargs = mock_send_req.call_args
            assert not called_kwargs  # no kwargs to _send_request
            assert len(called_args) == 2
            json_rpc_request: dict[str, Any] = called_args[0]
            assert isinstance(json_rpc_request['id'], int)
            http_kwargs: dict[str, Any] = called_args[1]
            assert http_kwargs['timeout'] == 10

            a2a_request_arg = A2ARequest.model_validate(json_rpc_request)
            assert isinstance(a2a_request_arg.root, SendMessageRequest)
            assert isinstance(a2a_request_arg.root.params, MessageSendParams)

            assert a2a_request_arg.root.params.model_dump(
                exclude_none=True
            ) == params.model_dump(exclude_none=True)

            assert isinstance(response, SendMessageResponse)
            assert isinstance(response.root, SendMessageSuccessResponse)
            assert (
                response.root.result.model_dump(exclude_none=True)
                == success_response
            )

    @pytest.mark.asyncio
    async def test_send_message_error_response(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )

        params = MessageSendParams(
            message=create_text_message_object(content='Hello')
        )

        request = SendMessageRequest(id=123, params=params)

        error_response = InvalidParamsError()

        rpc_response: dict[str, Any] = {
            'id': 123,
            'jsonrpc': '2.0',
            'error': error_response.model_dump(exclude_none=True),
        }

        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_req:
            mock_send_req.return_value = rpc_response
            response = await client.send_message(request=request)

            assert isinstance(response, SendMessageResponse)
            assert isinstance(response.root, JSONRPCErrorResponse)
            assert response.root.error.model_dump(
                exclude_none=True
            ) == InvalidParamsError().model_dump(exclude_none=True)

    @pytest.mark.asyncio
    @patch('a2a.client.client.aconnect_sse')
    async def test_send_message_streaming_success_request(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params = MessageSendParams(
            message=create_text_message_object(content='Hello stream')
        )

        request = SendStreamingMessageRequest(id=123, params=params)

        mock_stream_response_1_dict: dict[str, Any] = {
            'id': 'stream_id_123',
            'jsonrpc': '2.0',
            'result': create_text_message_object(
                content='First part ', role=Role.agent
            ).model_dump(mode='json', exclude_none=True),
        }
        mock_stream_response_2_dict: dict[str, Any] = {
            'id': 'stream_id_123',
            'jsonrpc': '2.0',
            'result': create_text_message_object(
                content='second part ', role=Role.agent
            ).model_dump(mode='json', exclude_none=True),
        }

        sse_event_1 = ServerSentEvent(
            data=json.dumps(mock_stream_response_1_dict)
        )
        sse_event_2 = ServerSentEvent(
            data=json.dumps(mock_stream_response_2_dict)
        )

        mock_event_source = AsyncMock(spec=EventSource)
        with patch.object(mock_event_source, 'aiter_sse') as mock_aiter_sse:
            mock_aiter_sse.return_value = async_iterable_from_list(
                [sse_event_1, sse_event_2]
            )
            mock_aconnect_sse.return_value.__aenter__.return_value = (
                mock_event_source
            )

            results: list[Any] = []
            async for response in client.send_message_streaming(
                request=request
            ):
                results.append(response)

            assert len(results) == 2
            assert isinstance(results[0], SendStreamingMessageResponse)
            # Assuming SendStreamingMessageResponse is a RootModel like SendMessageResponse
            assert results[0].root.id == 'stream_id_123'
            assert (
                results[0].root.result.model_dump(  # type: ignore
                    mode='json', exclude_none=True
                )
                == mock_stream_response_1_dict['result']
            )

            assert isinstance(results[1], SendStreamingMessageResponse)
            assert results[1].root.id == 'stream_id_123'
            assert (
                results[1].root.result.model_dump(  # type: ignore
                    mode='json', exclude_none=True
                )
                == mock_stream_response_2_dict['result']
            )

            mock_aconnect_sse.assert_called_once()
            call_args, call_kwargs = mock_aconnect_sse.call_args
            assert call_args[0] == mock_httpx_client
            assert call_args[1] == 'POST'
            assert call_args[2] == mock_agent_card.url

            sent_json_payload = call_kwargs['json']
            assert sent_json_payload['method'] == 'message/stream'
            assert sent_json_payload['params'] == params.model_dump(
                mode='json', exclude_none=True
            )
            assert (
                call_kwargs['timeout'] is None
            )  # Default timeout for streaming

    @pytest.mark.asyncio
    @patch('a2a.client.client.aconnect_sse')
    async def test_send_message_streaming_http_kwargs_passed(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params = MessageSendParams(
            message=create_text_message_object(content='Stream with kwargs')
        )
        request = SendStreamingMessageRequest(id='kwarg_req', params=params)
        custom_kwargs = {
            'headers': {'X-Custom-Header': 'TestValue'},
            'timeout': 60,
        }

        # Setup mock_aconnect_sse to behave minimally
        mock_event_source = AsyncMock(spec=EventSource)
        mock_event_source.aiter_sse.return_value = async_iterable_from_list(
            []
        )  # No events needed for this test
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        async for _ in client.send_message_streaming(
            request=request, http_kwargs=custom_kwargs
        ):
            pass  # We just want to check the call to aconnect_sse

        mock_aconnect_sse.assert_called_once()
        _, called_kwargs = mock_aconnect_sse.call_args
        assert called_kwargs['headers'] == custom_kwargs['headers']
        assert (
            called_kwargs['timeout'] == custom_kwargs['timeout']
        )  # Ensure custom timeout is used

    @pytest.mark.asyncio
    @patch('a2a.client.client.aconnect_sse')
    async def test_send_message_streaming_sse_error_handling(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        request = SendStreamingMessageRequest(
            id='sse_err_req',
            params=MessageSendParams(
                message=create_text_message_object(content='SSE error test')
            ),
        )

        # Configure the mock aconnect_sse to raise SSEError when aiter_sse is called
        mock_event_source = AsyncMock(spec=EventSource)
        mock_event_source.aiter_sse.side_effect = SSEError(
            'Simulated SSE protocol error'
        )
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        with pytest.raises(A2AClientHTTPError) as exc_info:
            async for _ in client.send_message_streaming(request=request):
                pass

        assert exc_info.value.status_code == 400  # As per client implementation
        assert 'Invalid SSE response or protocol error' in str(exc_info.value)
        assert 'Simulated SSE protocol error' in str(exc_info.value)

    @pytest.mark.asyncio
    @patch('a2a.client.client.aconnect_sse')
    async def test_send_message_streaming_json_decode_error_handling(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        request = SendStreamingMessageRequest(
            id='json_err_req',
            params=MessageSendParams(
                message=create_text_message_object(content='JSON error test')
            ),
        )

        # Malformed JSON event
        malformed_sse_event = ServerSentEvent(data='not valid json')

        mock_event_source = AsyncMock(spec=EventSource)
        # json.loads will be called on "not valid json" and raise JSONDecodeError
        mock_event_source.aiter_sse.return_value = async_iterable_from_list(
            [malformed_sse_event]
        )
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        with pytest.raises(A2AClientJSONError) as exc_info:
            async for _ in client.send_message_streaming(request=request):
                pass

        assert 'Expecting value: line 1 column 1 (char 0)' in str(
            exc_info.value
        )  # Example of JSONDecodeError message

    @pytest.mark.asyncio
    @patch('a2a.client.client.aconnect_sse')
    async def test_send_message_streaming_httpx_request_error_handling(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        request = SendStreamingMessageRequest(
            id='httpx_err_req',
            params=MessageSendParams(
                message=create_text_message_object(content='httpx error test')
            ),
        )

        # Configure aconnect_sse itself to raise httpx.RequestError (e.g., during connection)
        # This needs to be raised when aconnect_sse is entered or iterated.
        # One way is to make the context manager's __aenter__ raise it, or aiter_sse.
        # For simplicity, let's make aiter_sse raise it, as if the error occurs after connection.
        mock_event_source = AsyncMock(spec=EventSource)
        mock_event_source.aiter_sse.side_effect = httpx.RequestError(
            'Simulated network error', request=MagicMock()
        )
        mock_aconnect_sse.return_value.__aenter__.return_value = (
            mock_event_source
        )

        with pytest.raises(A2AClientHTTPError) as exc_info:
            async for _ in client.send_message_streaming(request=request):
                pass

        assert exc_info.value.status_code == 503  # As per client implementation
        assert 'Network communication error' in str(exc_info.value)
        assert 'Simulated network error' in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_send_request_http_status_error(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = A2AClient(
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
        client = A2AClient(
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
        client = A2AClient(
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
    async def test_set_task_callback_success(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        task_id_val = 'task_set_cb_001'
        # Correctly create the PushNotificationConfig (inner model)
        push_config_payload = PushNotificationConfig(
            url='https://callback.example.com/taskupdate'
        )
        # Correctly create the TaskPushNotificationConfig (outer model)
        params_model = TaskPushNotificationConfig(
            task_id=task_id_val, push_notification_config=push_config_payload
        )

        # request.id will be generated by the client method if not provided
        request = SetTaskPushNotificationConfigRequest(
            id='', params=params_model
        )  # Test ID auto-generation

        # The result for a successful set operation is the same config
        rpc_response_payload: dict[str, Any] = {
            'id': ANY,  # Will be checked against generated ID
            'jsonrpc': '2.0',
            'result': params_model.model_dump(mode='json', exclude_none=True),
        }

        with (
            patch.object(
                client, '_send_request', new_callable=AsyncMock
            ) as mock_send_req,
            patch(
                'a2a.client.client.uuid4',
                return_value=MagicMock(hex='testuuid'),
            ) as mock_uuid,
        ):
            # Capture the generated ID for assertion
            generated_id = str(mock_uuid.return_value)
            rpc_response_payload['id'] = (
                generated_id  # Ensure mock response uses the generated ID
            )
            mock_send_req.return_value = rpc_response_payload

            response = await client.set_task_callback(request=request)

            mock_send_req.assert_called_once()
            called_args, _ = mock_send_req.call_args
            sent_json_payload = called_args[0]

            assert sent_json_payload['id'] == generated_id
            assert (
                sent_json_payload['method']
                == 'tasks/pushNotificationConfig/set'
            )
            assert sent_json_payload['params'] == params_model.model_dump(
                mode='json', exclude_none=True
            )

            assert isinstance(response, SetTaskPushNotificationConfigResponse)
            assert isinstance(
                response.root, SetTaskPushNotificationConfigSuccessResponse
            )
            assert response.root.id == generated_id
            assert response.root.result.model_dump(
                mode='json', exclude_none=True
            ) == params_model.model_dump(mode='json', exclude_none=True)

    @pytest.mark.asyncio
    async def test_set_task_callback_error_response(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        req_id = 'set_cb_err_req'
        push_config_payload = PushNotificationConfig(url='https://errors.com')
        params_model = TaskPushNotificationConfig(
            task_id='task_err_cb', push_notification_config=push_config_payload
        )
        request = SetTaskPushNotificationConfigRequest(
            id=req_id, params=params_model
        )
        error_details = InvalidParamsError(message='Invalid callback URL')

        rpc_response_payload: dict[str, Any] = {
            'id': req_id,
            'jsonrpc': '2.0',
            'error': error_details.model_dump(mode='json', exclude_none=True),
        }

        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_req:
            mock_send_req.return_value = rpc_response_payload
            response = await client.set_task_callback(request=request)

            assert isinstance(response, SetTaskPushNotificationConfigResponse)
            assert isinstance(response.root, JSONRPCErrorResponse)
            assert response.root.error.model_dump(
                mode='json', exclude_none=True
            ) == error_details.model_dump(mode='json', exclude_none=True)
            assert response.root.id == req_id

    @pytest.mark.asyncio
    async def test_set_task_callback_http_kwargs_passed(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        push_config_payload = PushNotificationConfig(url='https://kwargs.com')
        params_model = TaskPushNotificationConfig(
            task_id='task_cb_kwargs',
            push_notification_config=push_config_payload,
        )
        request = SetTaskPushNotificationConfigRequest(
            id='cb_kwargs_req', params=params_model
        )
        custom_kwargs = {'headers': {'X-Callback-Token': 'secret'}}

        # Minimal successful response
        rpc_response_payload: dict[str, Any] = {
            'id': 'cb_kwargs_req',
            'jsonrpc': '2.0',
            'result': params_model.model_dump(mode='json'),
        }

        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_req:
            mock_send_req.return_value = rpc_response_payload
            await client.set_task_callback(
                request=request, http_kwargs=custom_kwargs
            )

            mock_send_req.assert_called_once()
            called_args, _ = mock_send_req.call_args  # Correctly unpack args
            assert (
                called_args[1] == custom_kwargs
            )  # http_kwargs is the second positional arg

    @pytest.mark.asyncio
    async def test_get_task_callback_success(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        task_id_val = 'task_get_cb_001'
        params_model = TaskIdParams(
            id=task_id_val
        )  # Params for get is just TaskIdParams

        request = GetTaskPushNotificationConfigRequest(
            id='', params=params_model
        )  # ID is empty string for auto-generation test

        # Expected result for a successful get operation
        push_config_payload = PushNotificationConfig(
            url='https://callback.example.com/taskupdate'
        )
        expected_callback_config = TaskPushNotificationConfig(
            task_id=task_id_val, push_notification_config=push_config_payload
        )
        rpc_response_payload: dict[str, Any] = {
            'id': ANY,
            'jsonrpc': '2.0',
            'result': expected_callback_config.model_dump(
                mode='json', exclude_none=True
            ),
        }

        with (
            patch.object(
                client, '_send_request', new_callable=AsyncMock
            ) as mock_send_req,
            patch(
                'a2a.client.client.uuid4',
                return_value=MagicMock(hex='testgetuuid'),
            ) as mock_uuid,
        ):
            generated_id = str(mock_uuid.return_value)
            rpc_response_payload['id'] = generated_id
            mock_send_req.return_value = rpc_response_payload

            response = await client.get_task_callback(request=request)

            mock_send_req.assert_called_once()
            called_args, _ = mock_send_req.call_args
            sent_json_payload = called_args[0]

            assert sent_json_payload['id'] == generated_id
            assert (
                sent_json_payload['method']
                == 'tasks/pushNotificationConfig/get'
            )
            assert sent_json_payload['params'] == params_model.model_dump(
                mode='json', exclude_none=True
            )

            assert isinstance(response, GetTaskPushNotificationConfigResponse)
            assert isinstance(
                response.root, GetTaskPushNotificationConfigSuccessResponse
            )
            assert response.root.id == generated_id
            assert response.root.result.model_dump(
                mode='json', exclude_none=True
            ) == expected_callback_config.model_dump(
                mode='json', exclude_none=True
            )

    @pytest.mark.asyncio
    async def test_get_task_callback_error_response(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        req_id = 'get_cb_err_req'
        params_model = TaskIdParams(id='task_get_err_cb')
        request = GetTaskPushNotificationConfigRequest(
            id=req_id, params=params_model
        )
        error_details = TaskNotCancelableError(
            message='Cannot get callback for uncancelable task'
        )  # Example error

        rpc_response_payload: dict[str, Any] = {
            'id': req_id,
            'jsonrpc': '2.0',
            'error': error_details.model_dump(mode='json', exclude_none=True),
        }

        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_req:
            mock_send_req.return_value = rpc_response_payload
            response = await client.get_task_callback(request=request)

            assert isinstance(response, GetTaskPushNotificationConfigResponse)
            assert isinstance(response.root, JSONRPCErrorResponse)
            assert response.root.error.model_dump(
                mode='json', exclude_none=True
            ) == error_details.model_dump(mode='json', exclude_none=True)
            assert response.root.id == req_id

    @pytest.mark.asyncio
    async def test_get_task_callback_http_kwargs_passed(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params_model = TaskIdParams(id='task_get_cb_kwargs')
        request = GetTaskPushNotificationConfigRequest(
            id='get_cb_kwargs_req', params=params_model
        )
        custom_kwargs = {'headers': {'X-Tenant-ID': 'tenant-x'}}

        # Correctly create the nested PushNotificationConfig
        push_config_payload_for_expected = PushNotificationConfig(
            url='https://getkwargs.com'
        )
        expected_callback_config = TaskPushNotificationConfig(
            task_id='task_get_cb_kwargs',
            push_notification_config=push_config_payload_for_expected,
        )
        rpc_response_payload: dict[str, Any] = {
            'id': 'get_cb_kwargs_req',
            'jsonrpc': '2.0',
            'result': expected_callback_config.model_dump(mode='json'),
        }

        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_req:
            mock_send_req.return_value = rpc_response_payload
            await client.get_task_callback(
                request=request, http_kwargs=custom_kwargs
            )

            mock_send_req.assert_called_once()
            called_args, _ = mock_send_req.call_args  # Correctly unpack args
            assert (
                called_args[1] == custom_kwargs
            )  # http_kwargs is the second positional arg

    @pytest.mark.asyncio
    async def test_get_task_success_use_request(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        task_id_val = 'task_for_req_obj'
        params_model = TaskQueryParams(id=task_id_val)
        request_obj_id = 789
        request = GetTaskRequest(id=request_obj_id, params=params_model)

        rpc_response_payload: dict[str, Any] = {
            'id': request_obj_id,
            'jsonrpc': '2.0',
            'result': MINIMAL_TASK,
        }

        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_req:
            mock_send_req.return_value = rpc_response_payload
            response = await client.get_task(
                request=request, http_kwargs={'timeout': 20}
            )

            assert mock_send_req.call_count == 1
            called_args, called_kwargs = mock_send_req.call_args
            assert len(called_args) == 2
            json_rpc_request_sent: dict[str, Any] = called_args[0]
            assert not called_kwargs  # no extra kwargs to _send_request
            http_kwargs: dict[str, Any] = called_args[1]
            assert http_kwargs['timeout'] == 20

            assert json_rpc_request_sent['method'] == 'tasks/get'
            assert json_rpc_request_sent['id'] == request_obj_id
            assert json_rpc_request_sent['params'] == params_model.model_dump(
                mode='json', exclude_none=True
            )

            assert isinstance(response, GetTaskResponse)
            assert hasattr(response.root, 'result')
            assert (
                response.root.result.model_dump(mode='json', exclude_none=True)  # type: ignore
                == MINIMAL_TASK
            )
            assert response.root.id == request_obj_id

    @pytest.mark.asyncio
    async def test_get_task_error_response(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params_model = TaskQueryParams(id='task_error_case')
        request = GetTaskRequest(id='err_req_id', params=params_model)
        error_details = InvalidParamsError()

        rpc_response_payload: dict[str, Any] = {
            'id': 'err_req_id',
            'jsonrpc': '2.0',
            'error': error_details.model_dump(mode='json', exclude_none=True),
        }

        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_req:
            mock_send_req.return_value = rpc_response_payload
            response = await client.get_task(request=request)

            assert isinstance(response, GetTaskResponse)
            assert isinstance(response.root, JSONRPCErrorResponse)
            assert response.root.error.model_dump(
                mode='json', exclude_none=True
            ) == error_details.model_dump(exclude_none=True)
            assert response.root.id == 'err_req_id'

    @pytest.mark.asyncio
    async def test_cancel_task_success_use_request(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        task_id_val = MINIMAL_CANCELLED_TASK['id']
        params_model = TaskIdParams(id=task_id_val)
        request_obj_id = 'cancel_req_obj_id_001'
        request = CancelTaskRequest(id=request_obj_id, params=params_model)

        rpc_response_payload: dict[str, Any] = {
            'id': request_obj_id,
            'jsonrpc': '2.0',
            'result': MINIMAL_CANCELLED_TASK,
        }

        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_req:
            mock_send_req.return_value = rpc_response_payload
            response = await client.cancel_task(
                request=request, http_kwargs={'timeout': 15}
            )

            assert mock_send_req.call_count == 1
            called_args, called_kwargs = mock_send_req.call_args
            assert not called_kwargs  # no extra kwargs to _send_request
            assert len(called_args) == 2
            json_rpc_request_sent: dict[str, Any] = called_args[0]
            http_kwargs: dict[str, Any] = called_args[1]
            assert http_kwargs['timeout'] == 15

            assert json_rpc_request_sent['method'] == 'tasks/cancel'
            assert json_rpc_request_sent['id'] == request_obj_id
            assert json_rpc_request_sent['params'] == params_model.model_dump(
                mode='json', exclude_none=True
            )

            assert isinstance(response, CancelTaskResponse)
            assert isinstance(response.root, CancelTaskSuccessResponse)
            assert (
                response.root.result.model_dump(mode='json', exclude_none=True)  # type: ignore
                == MINIMAL_CANCELLED_TASK
            )
            assert response.root.id == request_obj_id

    @pytest.mark.asyncio
    async def test_cancel_task_error_response(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )
        params_model = TaskIdParams(id='task_cancel_error_case')
        request = CancelTaskRequest(id='err_cancel_req', params=params_model)
        error_details = TaskNotCancelableError()

        rpc_response_payload: dict[str, Any] = {
            'id': 'err_cancel_req',
            'jsonrpc': '2.0',
            'error': error_details.model_dump(mode='json', exclude_none=True),
        }

        with patch.object(
            client, '_send_request', new_callable=AsyncMock
        ) as mock_send_req:
            mock_send_req.return_value = rpc_response_payload
            response = await client.cancel_task(request=request)

            assert isinstance(response, CancelTaskResponse)
            assert isinstance(response.root, JSONRPCErrorResponse)
            assert response.root.error.model_dump(
                mode='json', exclude_none=True
            ) == error_details.model_dump(exclude_none=True)
            assert response.root.id == 'err_cancel_req'

    @pytest.mark.asyncio
    async def test_send_message_client_timeout(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        mock_httpx_client.post.side_effect = httpx.ReadTimeout(
            'Request timed out'
        )
        client = A2AClient(
            httpx_client=mock_httpx_client, agent_card=mock_agent_card
        )

        params = MessageSendParams(
            message=create_text_message_object(content='Hello')
        )

        request = SendMessageRequest(id=123, params=params)

        with pytest.raises(A2AClientTimeoutError) as exc_info:
            await client.send_message(request=request)

        assert 'Request timed out' in str(exc_info.value)
