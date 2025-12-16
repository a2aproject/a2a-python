"""Tests for the A2ACardResolver."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from a2a.client.card_resolver import A2ACardResolver
from a2a.client.errors import A2AClientHTTPError, A2AClientJSONError

from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH


@pytest.fixture
def mock_httpx_client() -> AsyncMock:
    """Provides a mock httpx.AsyncClient."""
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def base_agent_card_data() -> dict:
    """Provides base valid agent card data."""
    return {
        'name': 'Test Agent',
        'description': 'An agent for testing.',
        'url': 'http://example.com',
        'version': '1.0.0',
        'capabilities': {},
        'skills': [],
        'default_input_modes': [],
        'default_output_modes': [],
        'preferred_transport': 'jsonrpc',
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'relative_path, expected_path_segment',
    [
        (None, AGENT_CARD_WELL_KNOWN_PATH),
        ('/custom/card', '/custom/card'),
        ('', AGENT_CARD_WELL_KNOWN_PATH),
    ],
)
async def test_get_agent_card_success(
    mock_httpx_client: AsyncMock,
    base_agent_card_data: dict,
    relative_path: str | None,
    expected_path_segment: str,
):
    """Test successful agent card retrieval using default and relative paths."""
    base_url = 'http://example.com'
    resolver = A2ACardResolver(mock_httpx_client, base_url)

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = base_agent_card_data
    mock_httpx_client.get.return_value = mock_response

    agent_card = await resolver.get_agent_card(relative_card_path=relative_path)

    expected_url = f'{base_url}{expected_path_segment}'
    mock_httpx_client.get.assert_awaited_once_with(expected_url)
    mock_response.raise_for_status.assert_called_once()
    assert agent_card.name == base_agent_card_data['name']
    assert agent_card.url == base_agent_card_data['url']


@pytest.mark.asyncio
async def test_get_agent_card_http_error(mock_httpx_client: AsyncMock):
    """Test handling of HTTP errors during agent card retrieval."""
    base_url = 'http://example.com'
    resolver = A2ACardResolver(mock_httpx_client, base_url)

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        'Not Found', request=MagicMock(), response=mock_response
    )
    mock_response.status_code = 404
    mock_httpx_client.get.return_value = mock_response

    with pytest.raises(A2AClientHTTPError) as excinfo:
        await resolver.get_agent_card()
    assert excinfo.value.status_code == 404


@pytest.mark.asyncio
async def test_get_agent_card_json_decode_error(mock_httpx_client: AsyncMock):
    """Test handling of JSON decoding errors."""
    base_url = 'http://example.com'
    resolver = A2ACardResolver(mock_httpx_client, base_url)

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.side_effect = json.JSONDecodeError('msg', 'doc', 0)
    mock_httpx_client.get.return_value = mock_response

    with pytest.raises(A2AClientJSONError, match='Failed to parse JSON'):
        await resolver.get_agent_card()


@pytest.mark.asyncio
async def test_get_agent_card_network_error(mock_httpx_client: AsyncMock):
    """Test handling of network communication errors."""
    base_url = 'http://example.com'
    resolver = A2ACardResolver(mock_httpx_client, base_url)

    mock_httpx_client.get.side_effect = httpx.RequestError('Network error')

    with pytest.raises(A2AClientHTTPError, match='Network communication error'):
        await resolver.get_agent_card()


@pytest.mark.asyncio
async def test_get_agent_card_validation_error(
    mock_httpx_client: AsyncMock, base_agent_card_data: dict
):
    """Test handling of Pydantic validation errors."""
    base_url = 'http://example.com'
    resolver = A2ACardResolver(mock_httpx_client, base_url)

    invalid_card_data = base_agent_card_data.copy()
    del invalid_card_data['name']  # Make it invalid

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = invalid_card_data
    mock_httpx_client.get.return_value = mock_response

    with pytest.raises(
        A2AClientJSONError, match='Failed to validate agent card structure'
    ):
        await resolver.get_agent_card()


@pytest.mark.asyncio
async def test_get_agent_card_with_http_kwargs(
    mock_httpx_client: AsyncMock, base_agent_card_data: dict
):
    """Test that http_kwargs are passed to the httpx client."""
    base_url = 'http://example.com'
    resolver = A2ACardResolver(mock_httpx_client, base_url)
    http_kwargs = {'headers': {'X-Test': 'true'}}

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = base_agent_card_data
    mock_httpx_client.get.return_value = mock_response

    await resolver.get_agent_card(http_kwargs=http_kwargs)

    expected_url = f'{base_url}{AGENT_CARD_WELL_KNOWN_PATH}'
    mock_httpx_client.get.assert_awaited_once_with(
        expected_url, headers={'X-Test': 'true'}
    )


@pytest.mark.asyncio
async def test_get_agent_card_with_signature_verifier(
    mock_httpx_client: AsyncMock, base_agent_card_data: dict
):
    """Test that the signature verifier is called if provided."""
    base_url = 'http://example.com'
    resolver = A2ACardResolver(mock_httpx_client, base_url)
    mock_verifier = MagicMock()

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.json.return_value = base_agent_card_data
    mock_httpx_client.get.return_value = mock_response

    agent_card = await resolver.get_agent_card(signature_verifier=mock_verifier)

    mock_verifier.assert_called_once_with(agent_card)
