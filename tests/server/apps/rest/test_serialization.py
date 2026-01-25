from unittest import mock

import pytest

from httpx import ASGITransport, AsyncClient

from a2a.server.apps.rest.fastapi_app import A2ARESTFastAPIApplication
from a2a.types import (
    APIKeySecurityScheme,
    AgentCapabilities,
    AgentCard,
    In,
    SecurityScheme,
)


@pytest.fixture
def agent_card_with_api_key() -> AgentCard:
    api_key_scheme_data = {
        'type': 'apiKey',
        'name': 'X-API-KEY',
        'in': 'header',
    }
    api_key_scheme = APIKeySecurityScheme.model_validate(api_key_scheme_data)

    return AgentCard(
        name='APIKeyAgent',
        description='An agent that uses API Key auth.',
        url='http://example.com/apikey-agent',
        version='1.0.0',
        capabilities=AgentCapabilities(),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        skills=[],
        security_schemes={'api_key_auth': SecurityScheme(root=api_key_scheme)},
        security=[{'api_key_auth': []}],
    )


@pytest.mark.anyio
async def test_rest_agent_card_with_api_key_scheme_alias(
    agent_card_with_api_key: AgentCard,
):
    """Ensures REST agent card serialization uses the 'in' alias."""
    handler = mock.AsyncMock()
    app_instance = A2ARESTFastAPIApplication(agent_card_with_api_key, handler)
    app = app_instance.build(
        agent_card_url='/.well-known/agent.json', rpc_url=''
    )

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url='http://test'
    ) as client:
        response = await client.get('/.well-known/agent.json')

    assert response.status_code == 200
    response_data = response.json()

    security_scheme_json = response_data['securitySchemes']['api_key_auth']
    assert 'in' in security_scheme_json
    assert security_scheme_json['in'] == 'header'
    assert 'in_' not in security_scheme_json

    parsed_card = AgentCard.model_validate(response_data)
    parsed_scheme_wrapper = parsed_card.security_schemes['api_key_auth']
    assert parsed_scheme_wrapper.root.in_ == In.header
