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


def test_cross_version_complex_agent_card_deserialization() -> None:
    # 1. Grab the card (1.0.0 Protobuf object) from the complex test
    card = AgentCard(
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

    # 2. Serialize using the current 1.0.0 codebase
    # preserving_proto_field_name=False means we use camelCase like 0.3.0 expects
    serialized_data = agent_card_to_dict(
        card, preserving_proto_field_name=False
    )
    serialized_json = json.dumps(serialized_data)

    # 3. Feed it to the 0.3.24 SDK subprocess to validate strict Pydantic model parsing
    result = subprocess.run(
        [  # noqa: S607
            'uv',
            'run',
            '--with',
            'a2a-sdk==0.3.24',
            '--no-project',
            'python',
            'tests/integration/cross_version/deserialize_complex_card_030.py',
        ],
        input=serialized_json,
        capture_output=True,
        text=True,
        check=True,
    )

    # If it didn't throw an error, it parsed perfectly.
    parsed_json = result.stdout.strip()
    assert len(parsed_json) > 0
