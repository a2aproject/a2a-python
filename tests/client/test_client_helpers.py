import copy
import difflib
import json
from google.protobuf.json_format import MessageToDict

from a2a.client.helpers import create_text_message_object, parse_agent_card
from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.types.a2a_pb2 import (
    APIKeySecurityScheme,
    AgentCapabilities,
    AgentCard,
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


def test_parse_agent_card_legacy_support() -> None:
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


def test_parse_agent_card_new_support() -> None:
    data = {
        'name': 'New Agent',
        'description': 'New Description',
        'version': '1.0',
        'capabilities': {'extendedAgentCard': True},
    }
    card = parse_agent_card(data)
    assert card.name == 'New Agent'
    assert card.capabilities.extended_agent_card is True


def test_parse_agent_card_no_support() -> None:
    data = {
        'name': 'No Support Agent',
        'description': 'No Support Description',
        'version': '1.0',
        'capabilities': {'extendedAgentCard': False},
    }
    card = parse_agent_card(data)
    assert card.name == 'No Support Agent'
    assert card.capabilities.extended_agent_card is False


def test_parse_agent_card_both_legacy_and_new() -> None:
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


def _assert_agent_card_diff(original_data: dict, serialized_data: dict) -> None:
    """Helper to assert that the re-serialized 1.0.0 JSON payload contains all original 0.3.0 data (no dropped fields)."""
    original_json_str = json.dumps(original_data, indent=2, sort_keys=True)
    serialized_json_str = json.dumps(serialized_data, indent=2, sort_keys=True)

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


def test_parse_typical_030_agent_card() -> None:
    data = {
        'additionalInterfaces': [
            {'transport': 'GRPC', 'url': 'http://agent.example.com/api/grpc'}
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
    serialized_data = agent_card_to_dict(
        card, preserving_proto_field_name=False
    )

    _assert_agent_card_diff(original_data, serialized_data)
    assert 'preferredTransport' in serialized_data

    # Re-parse from the serialized payload and verify identical to original parsing
    re_parsed_card = parse_agent_card(copy.deepcopy(serialized_data))
    assert re_parsed_card == card


def test_parse_agent_card_security_scheme_without_in() -> None:
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
        card.security_schemes['test_api_key'].api_key_security_scheme.location
        == ''
    )


def test_parse_agent_card_security_scheme_unknown_type() -> None:
    data = {
        'name': 'Unknown Scheme Agent',
        'description': 'Has unknown scheme type',
        'version': '1.0',
        'securitySchemes': {
            'test_unknown': {'type': 'someFutureType', 'future_prop': 'value'},
            'test_missing_type': {'prop': 'value'},
        },
    }
    card = parse_agent_card(data)
    # the ParseDict ignore_unknown_fields=True handles the unknown fields.
    # Because there is no mapping logic for 'someFutureType', the Protobuf
    # creates an empty SecurityScheme message under those keys.
    assert 'test_unknown' in card.security_schemes
    assert not card.security_schemes['test_unknown'].WhichOneof('scheme')

    assert 'test_missing_type' in card.security_schemes
    assert not card.security_schemes['test_missing_type'].WhichOneof('scheme')


def test_create_text_message_object() -> None:
    msg = create_text_message_object(role=Role.ROLE_AGENT, content='Hello')
    assert msg.role == Role.ROLE_AGENT
    assert len(msg.parts) == 1
    assert msg.parts[0].text == 'Hello'
    assert msg.message_id != ''


def test_parse_030_agent_card_route_planner() -> None:
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
            extended_agent_card=True, streaming=True, push_notifications=True
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
                    'google': StringList(list=['openid', 'profile', 'email'])
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
                tags=['maps', 'routing', 'navigation', 'directions', 'traffic'],
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
                tags=['maps', 'customization', 'visualization', 'cartography'],
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

    # Serialize back to JSON and compare
    serialized_data = agent_card_to_dict(
        card, preserving_proto_field_name=False
    )

    # Remove deprecated stateTransitionHistory before diffing
    del original_data['capabilities']['stateTransitionHistory']

    _assert_agent_card_diff(original_data, serialized_data)

    # Re-parse from the serialized payload and verify identical to original parsing
    re_parsed_card = parse_agent_card(copy.deepcopy(serialized_data))
    assert re_parsed_card == card


def test_parse_complex_030_agent_card() -> None:
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
            extended_agent_card=True, streaming=True, push_notifications=True
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
                schemes={'test_oidc': StringList(list=['openid', 'profile'])}
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
                    SecurityRequirement(schemes={'test_api_key': StringList()})
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

    # Serialize back to JSON and compare
    serialized_data = agent_card_to_dict(
        card, preserving_proto_field_name=False
    )
    _assert_agent_card_diff(original_data, serialized_data)

    # Re-parse from the serialized payload and verify identical to original parsing
    re_parsed_card = parse_agent_card(copy.deepcopy(serialized_data))
    assert re_parsed_card == card
