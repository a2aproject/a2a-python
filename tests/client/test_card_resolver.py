import copy
import difflib
import json
import logging
from unittest.mock import AsyncMock, MagicMock, Mock

from google.protobuf.json_format import MessageToDict
import httpx
import pytest

from a2a.client import A2ACardResolver, AgentCardResolutionError
from a2a.client.card_resolver import parse_agent_card
from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.types import AgentCard
from a2a.types.a2a_pb2 import (
    APIKeySecurityScheme,
    AgentCapabilities,
    AgentCardSignature,
    AgentInterface,
    AgentProvider,
    AgentSkill,
    AuthorizationCodeOAuthFlow,
    HTTPAuthSecurityScheme,
    MutualTlsSecurityScheme,
    OAuth2SecurityScheme,
    OAuthFlows,
    OpenIdConnectSecurityScheme,
    Role,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
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
        'supported_interfaces': [
            {
                'url': 'https://example.com/a2a',
                'protocol_binding': 'HTTP+JSON',
            }
        ],
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

        result = await resolver.get_agent_card()
        mock_httpx_client.get.assert_called_once_with(
            f'{base_url}/{AGENT_CARD_WELL_KNOWN_PATH[1:]}',
        )
        mock_response.raise_for_status.assert_called_once()
        mock_response.json.assert_called_once()
        assert result is not None
        assert isinstance(result, AgentCard)

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
        await resolver.get_agent_card(relative_card_path='/')
        mock_httpx_client.get.assert_called_once_with(f'{base_url}')

    @pytest.mark.asyncio
    async def test_get_agent_card_with_empty_resolver_agent_card_path(
        self,
        base_url,
        resolver,
        mock_httpx_client,
        mock_response,
        valid_agent_card_data,
    ):
        """Test fetching agent card when the resolver's agent_card_path is empty."""
        resolver.agent_card_path = ''
        mock_response.json.return_value = valid_agent_card_data
        mock_httpx_client.get.return_value = mock_response
        await resolver.get_agent_card()
        mock_httpx_client.get.assert_called_once_with(f'{base_url}')

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

        with pytest.raises(AgentCardResolutionError) as exc_info:
            await resolver.get_agent_card()

        assert exc_info.value.status_code == status_code
        assert f'HTTP {status_code}' in str(exc_info.value)
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
        with pytest.raises(AgentCardResolutionError) as exc_info:
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
        with pytest.raises(AgentCardResolutionError) as exc_info:
            await resolver.get_agent_card()
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
        return_json = {'name': {'invalid': 'type'}}
        mock_response.json.return_value = return_json
        mock_httpx_client.get.return_value = mock_response
        with pytest.raises(AgentCardResolutionError) as exc_info:
            await resolver.get_agent_card()
        assert (
            f'Failed to validate agent card structure from {base_url}/{AGENT_CARD_WELL_KNOWN_PATH[1:]}'
            in str(exc_info.value)
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
        with caplog.at_level(logging.INFO):
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
        with pytest.raises(AgentCardResolutionError) as exc_info:
            await resolver.get_agent_card()
        assert f'HTTP {status_code}' in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_agent_card_returns_agent_card_instance(
        self, resolver, mock_httpx_client, mock_response, valid_agent_card_data
    ):
        """Test that get_agent_card returns an AgentCard instance."""
        mock_response.json.return_value = valid_agent_card_data
        mock_httpx_client.get.return_value = mock_response
        result = await resolver.get_agent_card()
        assert isinstance(result, AgentCard)
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


class TestParseAgentCard:
    """Tests for parse_agent_card function."""

    @staticmethod
    def _assert_agent_card_diff(
        original_data: dict, serialized_data: dict
    ) -> None:
        """Helper to assert that the re-serialized 1.0.0 JSON payload contains all original 0.3.0 data (no dropped fields)."""
        original_json_str = json.dumps(original_data, indent=2, sort_keys=True)
        serialized_json_str = json.dumps(
            serialized_data, indent=2, sort_keys=True
        )

        diff_lines = list(
            difflib.unified_diff(
                original_json_str.splitlines(),
                serialized_json_str.splitlines(),
                lineterm='',
            )
        )

        removed_lines = []
        for line in diff_lines:
            if line.startswith('-') and not line.startswith('---'):
                removed_lines.append(line)

        if removed_lines:
            error_msg = (
                'Re-serialization dropped fields from the original payload:\n'
                + '\n'.join(removed_lines)
            )
            raise AssertionError(error_msg)

    def test_parse_agent_card_legacy_support(self) -> None:
        data = {
            'name': 'Legacy Agent',
            'description': 'Legacy Description',
            'version': '1.0',
            'supportsAuthenticatedExtendedCard': True,
        }
        card = parse_agent_card(data)
        assert card.name == 'Legacy Agent'
        assert card.capabilities.extended_agent_card is True
        # Ensure it's popped from the dict
        assert 'supportsAuthenticatedExtendedCard' not in data

    def test_parse_agent_card_new_support(self) -> None:
        data = {
            'name': 'New Agent',
            'description': 'New Description',
            'version': '1.0',
            'capabilities': {'extendedAgentCard': True},
        }
        card = parse_agent_card(data)
        assert card.name == 'New Agent'
        assert card.capabilities.extended_agent_card is True

    def test_parse_agent_card_no_support(self) -> None:
        data = {
            'name': 'No Support Agent',
            'description': 'No Support Description',
            'version': '1.0',
            'capabilities': {'extendedAgentCard': False},
        }
        card = parse_agent_card(data)
        assert card.name == 'No Support Agent'
        assert card.capabilities.extended_agent_card is False

    def test_parse_agent_card_both_legacy_and_new(self) -> None:
        data = {
            'name': 'Mixed Agent',
            'description': 'Mixed Description',
            'version': '1.0',
            'supportsAuthenticatedExtendedCard': True,
            'capabilities': {'streaming': True},
        }
        card = parse_agent_card(data)
        assert card.name == 'Mixed Agent'
        assert card.capabilities.streaming is True
        assert card.capabilities.extended_agent_card is True

    def test_parse_typical_030_agent_card(self) -> None:
        data = {
            'additionalInterfaces': [
                {
                    'transport': 'GRPC',
                    'url': 'http://agent.example.com/api/grpc',
                }
            ],
            'capabilities': {'streaming': True},
            'defaultInputModes': ['text/plain'],
            'defaultOutputModes': ['application/json'],
            'description': 'A typical agent from 0.3.0',
            'name': 'Typical Agent 0.3',
            'preferredTransport': 'JSONRPC',
            'protocolVersion': '0.3.0',
            'security': [{'test_oauth': ['read', 'write']}],
            'securitySchemes': {
                'test_oauth': {
                    'description': 'OAuth2 authentication',
                    'flows': {
                        'authorizationCode': {
                            'authorizationUrl': 'http://auth.example.com',
                            'scopes': {
                                'read': 'Read access',
                                'write': 'Write access',
                            },
                            'tokenUrl': 'http://token.example.com',
                        }
                    },
                    'type': 'oauth2',
                }
            },
            'skills': [
                {
                    'description': 'The first skill',
                    'id': 'skill-1',
                    'name': 'Skill 1',
                    'security': [{'test_oauth': ['read']}],
                    'tags': ['example'],
                }
            ],
            'supportsAuthenticatedExtendedCard': True,
            'url': 'http://agent.example.com/api',
            'version': '1.0',
        }
        original_data = copy.deepcopy(data)
        card = parse_agent_card(data)

        expected_card = AgentCard(
            name='Typical Agent 0.3',
            description='A typical agent from 0.3.0',
            version='1.0',
            capabilities=AgentCapabilities(
                extended_agent_card=True, streaming=True
            ),
            default_input_modes=['text/plain'],
            default_output_modes=['application/json'],
            supported_interfaces=[
                AgentInterface(
                    url='http://agent.example.com/api',
                    protocol_binding='JSONRPC',
                    protocol_version='0.3.0',
                ),
                AgentInterface(
                    url='http://agent.example.com/api/grpc',
                    protocol_binding='GRPC',
                    protocol_version='0.3.0',
                ),
            ],
            security_requirements=[
                SecurityRequirement(
                    schemes={'test_oauth': StringList(list=['read', 'write'])}
                )
            ],
            security_schemes={
                'test_oauth': SecurityScheme(
                    oauth2_security_scheme=OAuth2SecurityScheme(
                        description='OAuth2 authentication',
                        flows=OAuthFlows(
                            authorization_code=AuthorizationCodeOAuthFlow(
                                authorization_url='http://auth.example.com',
                                token_url='http://token.example.com',
                                scopes={
                                    'read': 'Read access',
                                    'write': 'Write access',
                                },
                            )
                        ),
                    )
                )
            },
            skills=[
                AgentSkill(
                    id='skill-1',
                    name='Skill 1',
                    description='The first skill',
                    tags=['example'],
                    security_requirements=[
                        SecurityRequirement(
                            schemes={'test_oauth': StringList(list=['read'])}
                        )
                    ],
                )
            ],
        )

        assert card == expected_card

        # Serialize back to JSON and compare
        serialized_data = agent_card_to_dict(card)

        self._assert_agent_card_diff(original_data, serialized_data)
        assert 'preferredTransport' in serialized_data

        # Re-parse from the serialized payload and verify identical to original parsing
        re_parsed_card = parse_agent_card(copy.deepcopy(serialized_data))
        assert re_parsed_card == card

    def test_parse_agent_card_security_scheme_without_in(self) -> None:
        data = {
            'name': 'API Key Agent',
            'description': 'API Key without in param',
            'version': '1.0',
            'securitySchemes': {
                'test_api_key': {'type': 'apiKey', 'name': 'X-API-KEY'}
            },
        }
        card = parse_agent_card(data)
        assert 'test_api_key' in card.security_schemes
        assert (
            card.security_schemes['test_api_key'].api_key_security_scheme.name
            == 'X-API-KEY'
        )
        assert (
            card.security_schemes[
                'test_api_key'
            ].api_key_security_scheme.location
            == ''
        )

    def test_parse_agent_card_security_scheme_unknown_type(self) -> None:
        data = {
            'name': 'Unknown Scheme Agent',
            'description': 'Has unknown scheme type',
            'version': '1.0',
            'securitySchemes': {
                'test_unknown': {
                    'type': 'someFutureType',
                    'future_prop': 'value',
                },
                'test_missing_type': {'prop': 'value'},
            },
        }
        card = parse_agent_card(data)
        assert 'test_unknown' in card.security_schemes
        assert not card.security_schemes['test_unknown'].WhichOneof('scheme')

        assert 'test_missing_type' in card.security_schemes
        assert not card.security_schemes['test_missing_type'].WhichOneof(
            'scheme'
        )

    def test_parse_030_agent_card_route_planner(self) -> None:
        data = {
            'protocolVersion': '0.3',
            'name': 'GeoSpatial Route Planner Agent',
            'description': 'Provides advanced route planning.',
            'url': 'https://georoute-agent.example.com/a2a/v1',
            'preferredTransport': 'JSONRPC',
            'additionalInterfaces': [
                {
                    'url': 'https://georoute-agent.example.com/a2a/v1',
                    'transport': 'JSONRPC',
                },
                {
                    'url': 'https://georoute-agent.example.com/a2a/grpc',
                    'transport': 'GRPC',
                },
                {
                    'url': 'https://georoute-agent.example.com/a2a/json',
                    'transport': 'HTTP+JSON',
                },
            ],
            'provider': {
                'organization': 'Example Geo Services Inc.',
                'url': 'https://www.examplegeoservices.com',
            },
            'iconUrl': 'https://georoute-agent.example.com/icon.png',
            'version': '1.2.0',
            'documentationUrl': 'https://docs.examplegeoservices.com/georoute-agent/api',
            'supportsAuthenticatedExtendedCard': True,
            'capabilities': {
                'streaming': True,
                'pushNotifications': True,
                'stateTransitionHistory': False,
            },
            'securitySchemes': {
                'google': {
                    'type': 'openIdConnect',
                    'openIdConnectUrl': 'https://accounts.google.com/.well-known/openid-configuration',
                }
            },
            'security': [{'google': ['openid', 'profile', 'email']}],
            'defaultInputModes': ['application/json', 'text/plain'],
            'defaultOutputModes': ['application/json', 'image/png'],
            'skills': [
                {
                    'id': 'route-optimizer-traffic',
                    'name': 'Traffic-Aware Route Optimizer',
                    'description': 'Calculates the optimal driving route between two or more locations, taking into account real-time traffic conditions, road closures, and user preferences (e.g., avoid tolls, prefer highways).',
                    'tags': [
                        'maps',
                        'routing',
                        'navigation',
                        'directions',
                        'traffic',
                    ],
                    'examples': [
                        "Plan a route from '1600 Amphitheatre Parkway, Mountain View, CA' to 'San Francisco International Airport' avoiding tolls.",
                        '{"origin": {"lat": 37.422, "lng": -122.084}, "destination": {"lat": 37.7749, "lng": -122.4194}, "preferences": ["avoid_ferries"]}',
                    ],
                    'inputModes': ['application/json', 'text/plain'],
                    'outputModes': [
                        'application/json',
                        'application/vnd.geo+json',
                        'text/html',
                    ],
                    'security': [
                        {'example': []},
                        {'google': ['openid', 'profile', 'email']},
                    ],
                },
                {
                    'id': 'custom-map-generator',
                    'name': 'Personalized Map Generator',
                    'description': 'Creates custom map images or interactive map views based on user-defined points of interest, routes, and style preferences. Can overlay data layers.',
                    'tags': [
                        'maps',
                        'customization',
                        'visualization',
                        'cartography',
                    ],
                    'examples': [
                        'Generate a map of my upcoming road trip with all planned stops highlighted.',
                        'Show me a map visualizing all coffee shops within a 1-mile radius of my current location.',
                    ],
                    'inputModes': ['application/json'],
                    'outputModes': [
                        'image/png',
                        'image/jpeg',
                        'application/json',
                        'text/html',
                    ],
                },
            ],
            'signatures': [
                {
                    'protected': 'eyJhbGciOiJFUzI1NiIsInR5cCI6IkpPU0UiLCJraWQiOiJrZXktMSIsImprdSI6Imh0dHBzOi8vZXhhbXBsZS5jb20vYWdlbnQvandrcy5qc29uIn0',
                    'signature': 'QFdkNLNszlGj3z3u0YQGt_T9LixY3qtdQpZmsTdDHDe3fXV9y9-B3m2-XgCpzuhiLt8E0tV6HXoZKHv4GtHgKQ',
                }
            ],
        }

        original_data = copy.deepcopy(data)
        card = parse_agent_card(data)

        expected_card = AgentCard(
            name='GeoSpatial Route Planner Agent',
            description='Provides advanced route planning.',
            version='1.2.0',
            documentation_url='https://docs.examplegeoservices.com/georoute-agent/api',
            icon_url='https://georoute-agent.example.com/icon.png',
            provider=AgentProvider(
                organization='Example Geo Services Inc.',
                url='https://www.examplegeoservices.com',
            ),
            capabilities=AgentCapabilities(
                extended_agent_card=True,
                streaming=True,
                push_notifications=True,
            ),
            default_input_modes=['application/json', 'text/plain'],
            default_output_modes=['application/json', 'image/png'],
            supported_interfaces=[
                AgentInterface(
                    url='https://georoute-agent.example.com/a2a/v1',
                    protocol_binding='JSONRPC',
                    protocol_version='0.3',
                ),
                AgentInterface(
                    url='https://georoute-agent.example.com/a2a/v1',
                    protocol_binding='JSONRPC',
                    protocol_version='0.3',
                ),
                AgentInterface(
                    url='https://georoute-agent.example.com/a2a/grpc',
                    protocol_binding='GRPC',
                    protocol_version='0.3',
                ),
                AgentInterface(
                    url='https://georoute-agent.example.com/a2a/json',
                    protocol_binding='HTTP+JSON',
                    protocol_version='0.3',
                ),
            ],
            security_requirements=[
                SecurityRequirement(
                    schemes={
                        'google': StringList(
                            list=['openid', 'profile', 'email']
                        )
                    }
                )
            ],
            security_schemes={
                'google': SecurityScheme(
                    open_id_connect_security_scheme=OpenIdConnectSecurityScheme(
                        open_id_connect_url='https://accounts.google.com/.well-known/openid-configuration'
                    )
                )
            },
            skills=[
                AgentSkill(
                    id='route-optimizer-traffic',
                    name='Traffic-Aware Route Optimizer',
                    description='Calculates the optimal driving route between two or more locations, taking into account real-time traffic conditions, road closures, and user preferences (e.g., avoid tolls, prefer highways).',
                    tags=[
                        'maps',
                        'routing',
                        'navigation',
                        'directions',
                        'traffic',
                    ],
                    examples=[
                        "Plan a route from '1600 Amphitheatre Parkway, Mountain View, CA' to 'San Francisco International Airport' avoiding tolls.",
                        '{"origin": {"lat": 37.422, "lng": -122.084}, "destination": {"lat": 37.7749, "lng": -122.4194}, "preferences": ["avoid_ferries"]}',
                    ],
                    input_modes=['application/json', 'text/plain'],
                    output_modes=[
                        'application/json',
                        'application/vnd.geo+json',
                        'text/html',
                    ],
                    security_requirements=[
                        SecurityRequirement(schemes={'example': StringList()}),
                        SecurityRequirement(
                            schemes={
                                'google': StringList(
                                    list=['openid', 'profile', 'email']
                                )
                            }
                        ),
                    ],
                ),
                AgentSkill(
                    id='custom-map-generator',
                    name='Personalized Map Generator',
                    description='Creates custom map images or interactive map views based on user-defined points of interest, routes, and style preferences. Can overlay data layers.',
                    tags=[
                        'maps',
                        'customization',
                        'visualization',
                        'cartography',
                    ],
                    examples=[
                        'Generate a map of my upcoming road trip with all planned stops highlighted.',
                        'Show me a map visualizing all coffee shops within a 1-mile radius of my current location.',
                    ],
                    input_modes=['application/json'],
                    output_modes=[
                        'image/png',
                        'image/jpeg',
                        'application/json',
                        'text/html',
                    ],
                ),
            ],
            signatures=[
                AgentCardSignature(
                    protected='eyJhbGciOiJFUzI1NiIsInR5cCI6IkpPU0UiLCJraWQiOiJrZXktMSIsImprdSI6Imh0dHBzOi8vZXhhbXBsZS5jb20vYWdlbnQvandrcy5qc29uIn0',
                    signature='QFdkNLNszlGj3z3u0YQGt_T9LixY3qtdQpZmsTdDHDe3fXV9y9-B3m2-XgCpzuhiLt8E0tV6HXoZKHv4GtHgKQ',
                )
            ],
        )

        assert card == expected_card
        serialized_data = agent_card_to_dict(card)
        del original_data['capabilities']['stateTransitionHistory']
        self._assert_agent_card_diff(original_data, serialized_data)
        re_parsed_card = parse_agent_card(copy.deepcopy(serialized_data))
        assert re_parsed_card == card

    def test_parse_complex_030_agent_card(self) -> None:
        data = {
            'additionalInterfaces': [
                {
                    'transport': 'GRPC',
                    'url': 'http://complex.agent.example.com/grpc',
                },
                {
                    'transport': 'JSONRPC',
                    'url': 'http://complex.agent.example.com/jsonrpc',
                },
            ],
            'capabilities': {'pushNotifications': True, 'streaming': True},
            'defaultInputModes': ['text/plain', 'application/json'],
            'defaultOutputModes': ['application/json', 'image/png'],
            'description': 'A very complex agent from 0.3.0',
            'name': 'Complex Agent 0.3',
            'preferredTransport': 'HTTP+JSON',
            'protocolVersion': '0.3.0',
            'security': [
                {'test_oauth': ['read', 'write'], 'test_api_key': []},
                {'test_http': []},
                {'test_oidc': ['openid', 'profile']},
                {'test_mtls': []},
            ],
            'securitySchemes': {
                'test_oauth': {
                    'description': 'OAuth2 authentication',
                    'flows': {
                        'authorizationCode': {
                            'authorizationUrl': 'http://auth.example.com',
                            'scopes': {
                                'read': 'Read access',
                                'write': 'Write access',
                            },
                            'tokenUrl': 'http://token.example.com',
                        }
                    },
                    'type': 'oauth2',
                },
                'test_api_key': {
                    'description': 'API Key auth',
                    'in': 'header',
                    'name': 'X-API-KEY',
                    'type': 'apiKey',
                },
                'test_http': {
                    'bearerFormat': 'JWT',
                    'description': 'HTTP Basic auth',
                    'scheme': 'basic',
                    'type': 'http',
                },
                'test_oidc': {
                    'description': 'OIDC Auth',
                    'openIdConnectUrl': 'https://example.com/.well-known/openid-configuration',
                    'type': 'openIdConnect',
                },
                'test_mtls': {'description': 'mTLS Auth', 'type': 'mutualTLS'},
            },
            'skills': [
                {
                    'description': 'The first complex skill',
                    'id': 'skill-1',
                    'inputModes': ['application/json'],
                    'name': 'Complex Skill 1',
                    'outputModes': ['application/json'],
                    'security': [{'test_api_key': []}],
                    'tags': ['example', 'complex'],
                },
                {
                    'description': 'The second complex skill',
                    'id': 'skill-2',
                    'name': 'Complex Skill 2',
                    'security': [{'test_oidc': ['openid']}],
                    'tags': ['example2'],
                },
            ],
            'supportsAuthenticatedExtendedCard': True,
            'url': 'http://complex.agent.example.com/api',
            'version': '1.5.2',
        }
        original_data = copy.deepcopy(data)
        card = parse_agent_card(data)

        expected_card = AgentCard(
            name='Complex Agent 0.3',
            description='A very complex agent from 0.3.0',
            version='1.5.2',
            capabilities=AgentCapabilities(
                extended_agent_card=True,
                streaming=True,
                push_notifications=True,
            ),
            default_input_modes=['text/plain', 'application/json'],
            default_output_modes=['application/json', 'image/png'],
            supported_interfaces=[
                AgentInterface(
                    url='http://complex.agent.example.com/api',
                    protocol_binding='HTTP+JSON',
                    protocol_version='0.3.0',
                ),
                AgentInterface(
                    url='http://complex.agent.example.com/grpc',
                    protocol_binding='GRPC',
                    protocol_version='0.3.0',
                ),
                AgentInterface(
                    url='http://complex.agent.example.com/jsonrpc',
                    protocol_binding='JSONRPC',
                    protocol_version='0.3.0',
                ),
            ],
            security_requirements=[
                SecurityRequirement(
                    schemes={
                        'test_oauth': StringList(list=['read', 'write']),
                        'test_api_key': StringList(),
                    }
                ),
                SecurityRequirement(schemes={'test_http': StringList()}),
                SecurityRequirement(
                    schemes={
                        'test_oidc': StringList(list=['openid', 'profile'])
                    }
                ),
                SecurityRequirement(schemes={'test_mtls': StringList()}),
            ],
            security_schemes={
                'test_oauth': SecurityScheme(
                    oauth2_security_scheme=OAuth2SecurityScheme(
                        description='OAuth2 authentication',
                        flows=OAuthFlows(
                            authorization_code=AuthorizationCodeOAuthFlow(
                                authorization_url='http://auth.example.com',
                                token_url='http://token.example.com',
                                scopes={
                                    'read': 'Read access',
                                    'write': 'Write access',
                                },
                            )
                        ),
                    )
                ),
                'test_api_key': SecurityScheme(
                    api_key_security_scheme=APIKeySecurityScheme(
                        description='API Key auth',
                        location='header',
                        name='X-API-KEY',
                    )
                ),
                'test_http': SecurityScheme(
                    http_auth_security_scheme=HTTPAuthSecurityScheme(
                        description='HTTP Basic auth',
                        scheme='basic',
                        bearer_format='JWT',
                    )
                ),
                'test_oidc': SecurityScheme(
                    open_id_connect_security_scheme=OpenIdConnectSecurityScheme(
                        description='OIDC Auth',
                        open_id_connect_url='https://example.com/.well-known/openid-configuration',
                    )
                ),
                'test_mtls': SecurityScheme(
                    mtls_security_scheme=MutualTlsSecurityScheme(
                        description='mTLS Auth'
                    )
                ),
            },
            skills=[
                AgentSkill(
                    id='skill-1',
                    name='Complex Skill 1',
                    description='The first complex skill',
                    tags=['example', 'complex'],
                    input_modes=['application/json'],
                    output_modes=['application/json'],
                    security_requirements=[
                        SecurityRequirement(
                            schemes={'test_api_key': StringList()}
                        )
                    ],
                ),
                AgentSkill(
                    id='skill-2',
                    name='Complex Skill 2',
                    description='The second complex skill',
                    tags=['example2'],
                    security_requirements=[
                        SecurityRequirement(
                            schemes={'test_oidc': StringList(list=['openid'])}
                        )
                    ],
                ),
            ],
        )

        assert card == expected_card
        serialized_data = agent_card_to_dict(card)
        self._assert_agent_card_diff(original_data, serialized_data)
        re_parsed_card = parse_agent_card(copy.deepcopy(serialized_data))
        assert re_parsed_card == card
