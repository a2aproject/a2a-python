import json
import logging

from unittest.mock import AsyncMock, MagicMock, Mock, patch

import httpx
import pytest

from a2a.client import A2ACardResolver, A2AClientHTTPError, A2AClientJSONError
from a2a.types import AgentCard
from a2a.utils import AGENT_CARD_WELL_KNOWN_PATH


@pytest.fixture
def mock_httpx_client():
    """Fixture providing a mocked async httpx client."""
    return AsyncMock(spec=httpx.AsyncClient)


@pytest.fixture
def base_url():
    """Fixture providing a test base URL."""
    return 'https://example.com'


@pytest.fixture
def resolver(mock_httpx_client, base_url):
    """Fixture providing an A2ACardResolver instance."""
    return A2ACardResolver(
        httpx_client=mock_httpx_client,
        base_url=base_url,
    )


@pytest.fixture
def mock_response():
    """Fixture providing a mock httpx Response."""
    response = Mock(spec=httpx.Response)
    response.raise_for_status = Mock()
    return response


@pytest.fixture
def valid_agent_card_data():
    """Fixture providing valid agent card data."""
    return {
        'name': 'TestAgent',
        'description': 'A test agent',
        'version': '1.0.0',
        'url': 'https://example.com/a2a',
        'capabilities': {},
        'default_input_modes': ['text/plain'],
        'default_output_modes': ['text/plain'],
        'skills': [
            {
                'id': 'test-skill',
                'name': 'Test Skill',
                'description': 'A skill for testing',
                'tags': ['test'],
            }
        ],
    }


class TestA2ACardResolverInit:
    """Tests for A2ACardResolver initialization."""

    def test_init_with_defaults(self, mock_httpx_client, base_url):
        """Test initialization with default agent_card_path."""
        resolver = A2ACardResolver(
            httpx_client=mock_httpx_client,
            base_url=base_url,
        )
        assert resolver.base_url == base_url
        assert resolver.agent_card_path == AGENT_CARD_WELL_KNOWN_PATH[1:]
        assert resolver.httpx_client == mock_httpx_client

    def test_init_with_custom_path(self, mock_httpx_client, base_url):
        """Test initialization with custom agent_card_path."""
        custom_path = '/custom/agent/card'
        resolver = A2ACardResolver(
            httpx_client=mock_httpx_client,
            base_url=base_url,
            agent_card_path=custom_path,
        )
        assert resolver.base_url == base_url
        assert resolver.agent_card_path == custom_path[1:]

    def test_init_strips_leading_slash_from_agent_card_path(
        self, mock_httpx_client, base_url
    ):
        """Test that leading slash is stripped from agent_card_path."""
        agent_card_path = '/well-known/agent'
        resolver = A2ACardResolver(
            httpx_client=mock_httpx_client,
            base_url=base_url,
            agent_card_path=agent_card_path,
        )
        assert resolver.agent_card_path == agent_card_path[1:]


class TestGetAgentCard:
    """Tests for get_agent_card methods."""

    @pytest.mark.asyncio
    async def test_get_agent_card_success_default_path(
        self,
        base_url,
        resolver,
        mock_httpx_client,
        mock_response,
        valid_agent_card_data,
    ):
        """Test successful agent card fetch using default path."""
        mock_response.json.return_value = valid_agent_card_data
        mock_httpx_client.get.return_value = mock_response

        with patch.object(
            AgentCard, 'model_validate', return_value=Mock(spec=AgentCard)
        ) as mock_validate:
            result = await resolver.get_agent_card()
            mock_httpx_client.get.assert_called_once_with(
                f'{base_url}/{AGENT_CARD_WELL_KNOWN_PATH[1:]}',
            )
            mock_response.raise_for_status.assert_called_once()
            mock_response.json.assert_called_once()
            mock_validate.assert_called_once_with(valid_agent_card_data)
            assert result is not None

    @pytest.mark.asyncio
    async def test_get_agent_card_success_custom_path(
        self,
        base_url,
        resolver,
        mock_httpx_client,
        mock_response,
        valid_agent_card_data,
    ):
        """Test successful agent card fetch using custom relative path."""
        custom_path = 'custom/path/card'
        mock_response.json.return_value = valid_agent_card_data
        mock_httpx_client.get.return_value = mock_response
        with patch.object(
            AgentCard, 'model_validate', return_value=Mock(spec=AgentCard)
        ):
            await resolver.get_agent_card(relative_card_path=custom_path)

            mock_httpx_client.get.assert_called_once_with(
                f'{base_url}/{custom_path}',
            )

    @pytest.mark.asyncio
    async def test_get_agent_card_strips_leading_slash_from_relative_path(
        self,
        base_url,
        resolver,
        mock_httpx_client,
        mock_response,
        valid_agent_card_data,
    ):
        """Test successful agent card fetch using custom path with leading slash."""
        custom_path = '/custom/path/card'
        mock_response.json.return_value = valid_agent_card_data
        mock_httpx_client.get.return_value = mock_response
        with patch.object(
            AgentCard, 'model_validate', return_value=Mock(spec=AgentCard)
        ):
            await resolver.get_agent_card(relative_card_path=custom_path)

            mock_httpx_client.get.assert_called_once_with(
                f'{base_url}/{custom_path[1:]}',
            )

    @pytest.mark.asyncio
    async def test_get_agent_card_with_http_kwargs(
        self,
        base_url,
        resolver,
        mock_httpx_client,
        mock_response,
        valid_agent_card_data,
    ):
        """Test that http_kwargs are passed to httpx.get."""
        mock_response.json.return_value = valid_agent_card_data
        mock_httpx_client.get.return_value = mock_response
        http_kwargs = {
            'timeout': 30,
            'headers': {'Authorization': 'Bearer token'},
        }
        with patch.object(
            AgentCard, 'model_validate', return_value=Mock(spec=AgentCard)
        ):
            await resolver.get_agent_card(http_kwargs=http_kwargs)
            mock_httpx_client.get.assert_called_once_with(
                f'{base_url}/{AGENT_CARD_WELL_KNOWN_PATH[1:]}',
                timeout=30,
                headers={'Authorization': 'Bearer token'},
            )

    @pytest.mark.asyncio
    async def test_get_agent_card_root_path(
        self,
        base_url,
        resolver,
        mock_httpx_client,
        mock_response,
        valid_agent_card_data,
    ):
        """Test fetching agent card from root path."""
        mock_response.json.return_value = valid_agent_card_data
        mock_httpx_client.get.return_value = mock_response
        with patch.object(
            AgentCard, 'model_validate', return_value=Mock(spec=AgentCard)
        ):
            await resolver.get_agent_card(relative_card_path='/')
            mock_httpx_client.get.assert_called_once_with(f'{base_url}/')

    @pytest.mark.asyncio
    async def test_get_agent_card_http_status_error(
        self, resolver, mock_httpx_client
    ):
        """Test A2AClientHTTPError raised on HTTP status error."""
        status_code = 404
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = status_code
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            'Not Found', request=Mock(), response=mock_response
        )
        mock_httpx_client.get.return_value = mock_response

        with pytest.raises(A2AClientHTTPError) as exc_info:
            await resolver.get_agent_card()

        assert exc_info.value.status_code == status_code
        assert 'Failed to fetch agent card' in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_agent_card_json_decode_error(
        self, resolver, mock_httpx_client, mock_response
    ):
        """Test A2AClientJSONError raised on JSON decode error."""
        mock_response.json.side_effect = json.JSONDecodeError(
            'Invalid JSON', '', 0
        )
        mock_httpx_client.get.return_value = mock_response
        with pytest.raises(A2AClientJSONError) as exc_info:
            await resolver.get_agent_card()
        assert 'Failed to parse JSON' in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_agent_card_request_error(
        self, resolver, mock_httpx_client
    ):
        """Test A2AClientHTTPError raised on network request error."""
        mock_httpx_client.get.side_effect = httpx.RequestError(
            'Connection timeout', request=Mock()
        )
        with pytest.raises(A2AClientHTTPError) as exc_info:
            await resolver.get_agent_card()
        assert exc_info.value.status_code == 503
        assert 'Network communication error' in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_agent_card_validation_error(
        self,
        base_url,
        resolver,
        mock_httpx_client,
        mock_response,
        valid_agent_card_data,
    ):
        """Test A2AClientJSONError is raised on agent card validation error."""
        return_json = {'invalid': 'data'}
        mock_response.json.return_value = return_json
        mock_httpx_client.get.return_value = mock_response
        with pytest.raises(A2AClientJSONError) as exc_info:
            await resolver.get_agent_card()
        assert (
            f'Failed to validate agent card structure from {base_url}/{AGENT_CARD_WELL_KNOWN_PATH[1:]}'
            in exc_info.value.message
        )
        mock_httpx_client.get.assert_called_once_with(
            f'{base_url}/{AGENT_CARD_WELL_KNOWN_PATH[1:]}',
        )

    @pytest.mark.asyncio
    async def test_get_agent_card_logs_success(  # noqa: PLR0913
        self,
        base_url,
        resolver,
        mock_httpx_client,
        mock_response,
        valid_agent_card_data,
        caplog,
    ):
        mock_response.json.return_value = valid_agent_card_data
        mock_httpx_client.get.return_value = mock_response
        with (
            patch.object(
                AgentCard, 'model_validate', return_value=Mock(spec=AgentCard)
            ),
            caplog.at_level(logging.INFO),
        ):
            await resolver.get_agent_card()
        assert (
            f'Successfully fetched agent card data from {base_url}/{AGENT_CARD_WELL_KNOWN_PATH[1:]}'
            in caplog.text
        )

    @pytest.mark.asyncio
    async def test_get_agent_card_none_relative_path(
        self,
        base_url,
        resolver,
        mock_httpx_client,
        mock_response,
        valid_agent_card_data,
    ):
        """Test that None relative_card_path uses default path."""
        mock_response.json.return_value = valid_agent_card_data
        mock_httpx_client.get.return_value = mock_response

        with patch.object(
            AgentCard, 'model_validate', return_value=Mock(spec=AgentCard)
        ):
            await resolver.get_agent_card(relative_card_path=None)
            mock_httpx_client.get.assert_called_once_with(
                f'{base_url}/{AGENT_CARD_WELL_KNOWN_PATH[1:]}',
            )

    @pytest.mark.asyncio
    async def test_get_agent_card_empty_string_relative_path(
        self,
        base_url,
        resolver,
        mock_httpx_client,
        mock_response,
        valid_agent_card_data,
    ):
        """Test that empty string relative_card_path uses default path."""
        mock_response.json.return_value = valid_agent_card_data
        mock_httpx_client.get.return_value = mock_response

        with patch.object(
            AgentCard, 'model_validate', return_value=Mock(spec=AgentCard)
        ):
            await resolver.get_agent_card(relative_card_path='')

            mock_httpx_client.get.assert_called_once_with(
                f'{base_url}/{AGENT_CARD_WELL_KNOWN_PATH[1:]}',
            )

    @pytest.mark.parametrize('status_code', [400, 401, 403, 500, 502])
    @pytest.mark.asyncio
    async def test_get_agent_card_different_status_codes(
        self, resolver, mock_httpx_client, status_code
    ):
        """Test different HTTP status codes raise appropriate errors."""
        mock_response = Mock(spec=httpx.Response)
        mock_response.status_code = status_code
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            f'Status {status_code}', request=Mock(), response=mock_response
        )
        mock_httpx_client.get.return_value = mock_response
        with pytest.raises(A2AClientHTTPError) as exc_info:
            await resolver.get_agent_card()
        assert exc_info.value.status_code == status_code

    @pytest.mark.asyncio
    async def test_get_agent_card_returns_agent_card_instance(
        self, resolver, mock_httpx_client, mock_response, valid_agent_card_data
    ):
        """Test that get_agent_card returns an AgentCard instance."""
        mock_response.json.return_value = valid_agent_card_data
        mock_httpx_client.get.return_value = mock_response
        mock_agent_card = Mock(spec=AgentCard)

        with patch.object(
            AgentCard, 'model_validate', return_value=mock_agent_card
        ):
            result = await resolver.get_agent_card()
            assert result == mock_agent_card
            mock_response.raise_for_status.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_agent_card_with_signature_verifier(
        self, resolver, mock_httpx_client, valid_agent_card_data
    ):
        """Test that the signature verifier is called if provided."""
        mock_verifier = MagicMock()

        mock_response = MagicMock(spec=httpx.Response)
        mock_response.json.return_value = valid_agent_card_data
        mock_httpx_client.get.return_value = mock_response

        agent_card = await resolver.get_agent_card(
            signature_verifier=mock_verifier
        )

        mock_verifier.assert_called_once_with(agent_card)
