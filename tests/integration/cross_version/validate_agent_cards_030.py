"""This is a script used by test_cross_version_card_validation.py.

It is run in a subprocess with a SDK version 0.3.
Steps:
1. Read the serialized JSON payload from stdin.
2. Validate the AgentCards with 0.3.24.
3. Print re-serialized AgentCards to stdout.
"""

import sys
import json
from a2a.types import (
    AgentCard,
    AgentCapabilities,
    AgentInterface,
    AgentSkill,
    APIKeySecurityScheme,
    HTTPAuthSecurityScheme,
    MutualTLSSecurityScheme,
    OAuth2SecurityScheme,
    OAuthFlows,
    AuthorizationCodeOAuthFlow,
    OpenIdConnectSecurityScheme,
)


def validate_complex_card(card: AgentCard) -> None:
    expected_card = AgentCard(
        name='Complex Agent 0.3',
        description='A very complex agent from 0.3.0',
        version='1.5.2',
        protocolVersion='0.3.0',
        supportsAuthenticatedExtendedCard=True,
        capabilities=AgentCapabilities(streaming=True, pushNotifications=True),
        url='http://complex.agent.example.com/api',
        preferredTransport='HTTP+JSON',
        additionalInterfaces=[
            AgentInterface(
                url='http://complex.agent.example.com/grpc',
                transport='GRPC',
            ),
            AgentInterface(
                url='http://complex.agent.example.com/jsonrpc',
                transport='JSONRPC',
            ),
        ],
        defaultInputModes=['text/plain', 'application/json'],
        defaultOutputModes=['application/json', 'image/png'],
        security=[
            {'test_oauth': ['read', 'write'], 'test_api_key': []},
            {'test_http': []},
            {'test_oidc': ['openid', 'profile']},
            {'test_mtls': []},
        ],
        securitySchemes={
            'test_oauth': OAuth2SecurityScheme(
                type='oauth2',
                description='OAuth2 authentication',
                flows=OAuthFlows(
                    authorizationCode=AuthorizationCodeOAuthFlow(
                        authorizationUrl='http://auth.example.com',
                        tokenUrl='http://token.example.com',
                        scopes={
                            'read': 'Read access',
                            'write': 'Write access',
                        },
                    )
                ),
            ),
            'test_api_key': APIKeySecurityScheme(
                type='apiKey',
                description='API Key auth',
                in_='header',
                name='X-API-KEY',
            ),
            'test_http': HTTPAuthSecurityScheme(
                type='http',
                description='HTTP Basic auth',
                scheme='basic',
                bearerFormat='JWT',
            ),
            'test_oidc': OpenIdConnectSecurityScheme(
                type='openIdConnect',
                description='OIDC Auth',
                openIdConnectUrl='https://example.com/.well-known/openid-configuration',
            ),
            'test_mtls': MutualTLSSecurityScheme(
                type='mutualTLS', description='mTLS Auth'
            ),
        },
        skills=[
            AgentSkill(
                id='skill-1',
                name='Complex Skill 1',
                description='The first complex skill',
                tags=['example', 'complex'],
                inputModes=['application/json'],
                outputModes=['application/json'],
                security=[{'test_api_key': []}],
            ),
            AgentSkill(
                id='skill-2',
                name='Complex Skill 2',
                description='The second complex skill',
                tags=['example2'],
                security=[{'test_oidc': ['openid']}],
            ),
        ],
    )

    assert card == expected_card


def validate_minimal_card(card: AgentCard) -> None:
    expected_card = AgentCard(
        name='Minimal Agent',
        description='',
        version='',
        protocolVersion='0.3.0',
        capabilities=AgentCapabilities(),
        url='http://minimal.example.com',
        preferredTransport='JSONRPC',
        defaultInputModes=[],
        defaultOutputModes=[],
        skills=[],
    )

    assert card == expected_card


def main() -> None:
    # Read the serialized JSON payload from stdin
    input_text = sys.stdin.read().strip()
    if not input_text:
        sys.exit(1)

    try:
        input_dict = json.loads(input_text)

        complex_card = AgentCard.model_validate_json(input_dict['complex'])
        validate_complex_card(complex_card)

        minimal_card = AgentCard.model_validate_json(input_dict['minimal'])
        validate_minimal_card(minimal_card)

        payload = {
            'complex': complex_card.model_dump_json(),
            'minimal': minimal_card.model_dump_json(),
        }
        print(json.dumps(payload))

    except Exception as e:
        print(
            f'Failed to validate AgentCards with 0.3.24: {e}', file=sys.stderr
        )
        sys.exit(1)


if __name__ == '__main__':
    main()
