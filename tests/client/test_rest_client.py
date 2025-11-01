from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from httpx_sse import EventSource, ServerSentEvent

from a2a.client import create_text_message_object
from a2a.client.transports.rest import RestTransport
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.types import AgentCard, MessageSendParams, Role


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


class TestRestTransportExtensions:
    def test_update_extension_header_no_initial_headers(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        extensions = ['test_extension_1', 'test_extension_2']
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            extensions=extensions,
        )
        http_kwargs = {}
        result_kwargs = client._update_extension_header(http_kwargs)
        header_value = result_kwargs['headers'][HTTP_EXTENSION_HEADER]
        actual_extensions_list = [e.strip() for e in header_value.split(',')]
        actual_extensions = set(actual_extensions_list)

        expected_extensions = {
            'test_extension_1',
            'test_extension_2',
        }
        assert len(actual_extensions_list) == 2
        assert actual_extensions == expected_extensions

    @pytest.mark.parametrize(
        'existing_header, expected_count',
        [
            ('test_extension_1, test_extension_2', 3),
            ('test_extension_1,test_extension_2', 3),
            ('test_extension_1', 3),
        ],
    )
    def test_update_extension_header_merge_with_existing_extensions(
        self,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
        existing_header: str,
        expected_count: int,
    ):
        extensions = ['test_extension_2', 'test_extension_3']
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            extensions=extensions,
        )
        http_kwargs = {'headers': {HTTP_EXTENSION_HEADER: existing_header}}
        result_kwargs = client._update_extension_header(http_kwargs)

        header_value = result_kwargs['headers'][HTTP_EXTENSION_HEADER]
        actual_extensions_list = [e.strip() for e in header_value.split(',')]
        actual_extensions = set(actual_extensions_list)

        expected_extensions = {
            'test_extension_1',
            'test_extension_2',
            'test_extension_3',
        }
        assert len(actual_extensions_list) == expected_count
        assert actual_extensions == expected_extensions

    def test_update_extension_header_with_other_headers(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        extensions = ['test_extension_1']
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=mock_agent_card,
            extensions=extensions,
        )
        http_kwargs = {'headers': {'X_Other': 'Test'}}
        result_kwargs = client._update_extension_header(http_kwargs)
        headers = result_kwargs.get('headers', {})
        assert HTTP_EXTENSION_HEADER in headers
        assert headers[HTTP_EXTENSION_HEADER] == 'test_extension_1'
        assert headers['X_Other'] == 'Test'

    @pytest.mark.asyncio
    async def test_send_message_with_extensions(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        """Test that send_message adds extensions to headers."""
        extensions = ['test_extension_1', 'test_extension_2']
        client = RestTransport(
            httpx_client=mock_httpx_client,
            extensions=extensions,
            agent_card=mock_agent_card,
        )
        params = MessageSendParams(
            message=create_text_message_object(content='Hello')
        )

        # Mock the build_request method to capture its inputs
        mock_build_request = MagicMock(
            return_value=AsyncMock(spec=httpx.Request)
        )
        mock_httpx_client.build_request = mock_build_request

        # Mock the send method
        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_httpx_client.send.return_value = mock_response

        await client.send_message(request=params)

        mock_build_request.assert_called_once()
        _, kwargs = mock_build_request.call_args

        headers = kwargs.get('headers', {})
        assert HTTP_EXTENSION_HEADER in headers
        header_value = kwargs['headers'][HTTP_EXTENSION_HEADER]
        actual_extensions_list = [e.strip() for e in header_value.split(',')]
        actual_extensions = set(actual_extensions_list)

        expected_extensions = {
            'test_extension_1',
            'test_extension_2',
        }
        assert len(actual_extensions_list) == 2
        assert actual_extensions == expected_extensions

    @pytest.mark.asyncio
    @patch('a2a.client.transports.rest.aconnect_sse')
    async def test_send_message_streaming_with_extensions(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        """Test X-A2A-Extensions header in send_message_streaming."""
        extensions = ['test_extension']
        client = RestTransport(
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

        async for _ in client.send_message_streaming(request=params):
            pass

        mock_aconnect_sse.assert_called_once()
        _, kwargs = mock_aconnect_sse.call_args

        headers = kwargs.get('headers', {})
        assert HTTP_EXTENSION_HEADER in headers
        assert headers[HTTP_EXTENSION_HEADER] == 'test_extension'
