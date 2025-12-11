from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from httpx_sse import EventSource, ServerSentEvent

from a2a.client import create_text_message_object
from a2a.client.errors import A2AClientHTTPError
from a2a.client.transports.rest import RestTransport
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    MessageSendParams,
)


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


class TestRestTransportExtensions:
    @pytest.mark.asyncio
    async def test_send_message_with_default_extensions(
        self, mock_httpx_client: AsyncMock, mock_agent_card: MagicMock
    ):
        """Test that send_message adds extensions to headers."""
        extensions = [
            'https://example.com/test-ext/v1',
            'https://example.com/test-ext/v2',
        ]
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

        _assert_extensions_header(
            kwargs,
            {
                'https://example.com/test-ext/v1',
                'https://example.com/test-ext/v2',
            },
        )

    @pytest.mark.asyncio
    @patch('a2a.client.transports.rest.aconnect_sse')
    async def test_send_message_streaming_with_new_extensions(
        self,
        mock_aconnect_sse: AsyncMock,
        mock_httpx_client: AsyncMock,
        mock_agent_card: MagicMock,
    ):
        """Test X-A2A-Extensions header in send_message_streaming."""
        new_extensions = ['https://example.com/test-ext/v2']
        extensions = ['https://example.com/test-ext/v1']
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
    @patch('a2a.client.transports.rest.aconnect_sse')
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
        client = RestTransport(
            httpx_client=mock_httpx_client,
            url='http://agent.example.com/api',
            extensions=extensions,
        )

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'name': 'Test Agent',
            'description': 'Test Agent Description',
            'url': 'http://agent.example.com/api',
            'version': '1.0.0',
            'default_input_modes': ['text'],
            'default_output_modes': ['text'],
            'capabilities': AgentCapabilities().model_dump(),
            'skills': [],
        }
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
        Tests that the extensions are added to the GET request."""
        extensions = [
            'https://example.com/test-ext/v1',
            'https://example.com/test-ext/v2',
        ]
        agent_card = AgentCard(
            name='Test Agent',
            description='Test Agent Description',
            url='http://agent.example.com/api',
            version='1.0.0',
            default_input_modes=['text'],
            default_output_modes=['text'],
            capabilities=AgentCapabilities(),
            skills=[],
            supports_authenticated_extended_card=True,
        )
        client = RestTransport(
            httpx_client=mock_httpx_client,
            agent_card=agent_card,
        )

        mock_response = AsyncMock(spec=httpx.Response)
        mock_response.status_code = 200
        mock_response.json.return_value = agent_card.model_dump(mode='json')
        mock_httpx_client.send.return_value = mock_response

        with patch.object(
            client, '_send_get_request', new_callable=AsyncMock
        ) as mock_send_get_request:
            mock_send_get_request.return_value = agent_card.model_dump(
                mode='json'
            )
            await client.get_card(extensions=extensions)

        mock_send_get_request.assert_called_once()
        _, _, mock_kwargs = mock_send_get_request.call_args[0]

        _assert_extensions_header(
            mock_kwargs,
            {
                'https://example.com/test-ext/v1',
                'https://example.com/test-ext/v2',
            },
        )
