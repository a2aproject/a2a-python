import json
import subprocess

from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.types.a2a_pb2 import (
    APIKeySecurityScheme,
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    AuthorizationCodeOAuthFlow,
    HTTPAuthSecurityScheme,
    MutualTlsSecurityScheme,
    OAuth2SecurityScheme,
    OAuthFlows,
    OpenIdConnectSecurityScheme,
    SecurityRequirement,
    SecurityScheme,
    StringList,
)
from a2a.client.card_resolver import parse_agent_card
from google.protobuf.json_format import MessageToDict, ParseDict


def test_cross_version_agent_card_deserialization() -> None:
    # 1. Complex card
    complex_card = AgentCard(
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

    # 2. Minimal card
    minimal_card = AgentCard(
        name='Minimal Agent',
        supported_interfaces=[
            AgentInterface(
                url='http://minimal.example.com',
                protocol_binding='JSONRPC',
                protocol_version='0.3.0',
            )
        ],
    )

    # 3. Serialize both
    payload = {
        'complex': json.dumps(agent_card_to_dict(complex_card)),
        'minimal': json.dumps(agent_card_to_dict(minimal_card)),
    }
    payload_json = json.dumps(payload)

    # 4. Feed it to the 0.3.24 SDK subprocess
    result = subprocess.run(
        [  # noqa: S607
            'uv',
            'run',
            '--with',
            'a2a-sdk==0.3.24',
            '--no-project',
            'python',
            'tests/integration/cross_version/validate_agent_cards_030.py',
        ],
        input=payload_json,
        capture_output=True,
        text=True,
        check=True,
    )

    # 5. Parse the response
    payload_v030 = json.loads(result.stdout)
    print(payload_v030['complex'])
    cards_v030 = {
        key: parse_agent_card(json.loads(card_json))
        for key, card_json in payload_v030.items()
    }

    # 6. Validate the parsed cards from 0.3
    def _remove_empty_capabilities(card):
        if card['capabilities'] == {}:
            card.pop('capabilities')
        return card

    assert _remove_empty_capabilities(
        MessageToDict(cards_v030['minimal'])
    ) == MessageToDict(minimal_card)
    assert MessageToDict(cards_v030['complex']) == MessageToDict(complex_card)

    # 7. Validate parsing of 1.0 cards with ParseDict
    cards_v100 = {
        key: ParseDict(
            json.loads(card_json), AgentCard(), ignore_unknown_fields=True
        )
        for key, card_json in payload.items()
    }
    assert _remove_empty_capabilities(
        MessageToDict(cards_v100['minimal'])
    ) == MessageToDict(minimal_card)
    assert MessageToDict(cards_v100['complex']) == MessageToDict(complex_card)
