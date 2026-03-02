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


def main() -> None:
    # Read the serialized JSON payload from stdin
    input_json = sys.stdin.read().strip()
    if not input_json:
        print('Error: No input provided via stdin', file=sys.stderr)
        sys.exit(1)

    # Use the 0.3.24 SDK's model_validate_json to parse and validate
    try:
        # Validate that the legacy Pydantic model can parse the injected backward compatibility dict
        card = AgentCard.model_validate_json(input_json)

        expected_card = AgentCard(
            name='Complex Agent 0.3',
            description='A very complex agent from 0.3.0',
            version='1.5.2',
            protocolVersion='0.3.0',
            supportsAuthenticatedExtendedCard=True,
            capabilities=AgentCapabilities(
                streaming=True, pushNotifications=True
            ),
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

        assert card == expected_card, (
            'Deserialized card does not match expected legacy structure'
        )

        # Dump it back successfully so the main test knows it worked
        print(card.model_dump_json(exclude_none=True))
    except Exception as e:
        print(f'Failed to validate AgentCard with 0.3.24: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
