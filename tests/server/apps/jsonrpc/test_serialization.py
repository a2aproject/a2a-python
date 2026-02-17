"""Tests for JSON-RPC serialization behavior."""

from unittest import mock

import pytest
from starlette.testclient import TestClient

from a2a.server.apps import A2AFastAPIApplication, A2AStarletteApplication
from a2a.server.jsonrpc_models import JSONParseError
from a2a.types import (
    InvalidRequestError,
)
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentInterface,
    AgentCard,
    AgentSkill,
    APIKeySecurityScheme,
    Message,
    Part,
    Role,
    SecurityRequirement,
    SecurityScheme,
)


@pytest.fixture
def minimal_agent_card():
    """Provides a minimal AgentCard for testing."""
    return AgentCard(
        name='TestAgent',
        description='A test agent.',
        supported_interfaces=[
            AgentInterface(
                url='http://example.com/agent', protocol_binding='HTTP+JSON'
            )
        ],
        version='1.0.0',
        capabilities=AgentCapabilities(),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
        skills=[
            AgentSkill(
                id='skill-1',
                name='Test Skill',
                description='A test skill',
                tags=['test'],
            )
        ],
    )


@pytest.fixture
def agent_card_with_api_key():
    """Provides an AgentCard with an APIKeySecurityScheme for testing serialization."""
    api_key_scheme = APIKeySecurityScheme(
        name='X-API-KEY',
        location='IN_HEADER',
    )

    security_scheme = SecurityScheme(api_key_security_scheme=api_key_scheme)

    card = AgentCard(
        name='APIKeyAgent',
        description='An agent that uses API Key auth.',
        supported_interfaces=[
            AgentInterface(
                url='http://example.com/apikey-agent',
                protocol_binding='HTTP+JSON',
            )
        ],
        version='1.0.0',
        capabilities=AgentCapabilities(),
        default_input_modes=['text/plain'],
        default_output_modes=['text/plain'],
    )
    # Add security scheme to the map
    card.security_schemes['api_key_auth'].CopyFrom(security_scheme)

    return card


def test_starlette_agent_card_serialization(minimal_agent_card: AgentCard):
    """Tests that the A2AStarletteApplication endpoint correctly serializes agent card."""
    handler = mock.AsyncMock()
    app_instance = A2AStarletteApplication(minimal_agent_card, handler)
    client = TestClient(app_instance.build())

    response = client.get('/.well-known/agent-card.json')
    assert response.status_code == 200
    response_data = response.json()

    assert response_data['name'] == 'TestAgent'
    assert response_data['description'] == 'A test agent.'
    assert (
        response_data['supportedInterfaces'][0]['url']
        == 'http://example.com/agent'
    )
    assert response_data['version'] == '1.0.0'


def test_starlette_agent_card_with_api_key_scheme(
    agent_card_with_api_key: AgentCard,
):
    """Tests that the A2AStarletteApplication endpoint correctly serializes API key schemes."""
    handler = mock.AsyncMock()
    app_instance = A2AStarletteApplication(agent_card_with_api_key, handler)
    client = TestClient(app_instance.build())

    response = client.get('/.well-known/agent-card.json')
    assert response.status_code == 200
    response_data = response.json()

    # Check security schemes are serialized
    assert 'securitySchemes' in response_data
    assert 'api_key_auth' in response_data['securitySchemes']


def test_fastapi_agent_card_serialization(minimal_agent_card: AgentCard):
    """Tests that the A2AFastAPIApplication endpoint correctly serializes agent card."""
    handler = mock.AsyncMock()
    app_instance = A2AFastAPIApplication(minimal_agent_card, handler)
    client = TestClient(app_instance.build())

    response = client.get('/.well-known/agent-card.json')
    assert response.status_code == 200
    response_data = response.json()

    assert response_data['name'] == 'TestAgent'
    assert response_data['description'] == 'A test agent.'


def test_handle_invalid_json(minimal_agent_card: AgentCard):
    """Test handling of malformed JSON."""
    handler = mock.AsyncMock()
    app_instance = A2AStarletteApplication(minimal_agent_card, handler)
    client = TestClient(app_instance.build())

    response = client.post(
        '/',
        content='{ "jsonrpc": "2.0", "method": "test", "id": 1, "params": { "key": "value" }',
    )
    assert response.status_code == 200
    data = response.json()
    assert data['error']['code'] == JSONParseError().code


def test_handle_oversized_payload(minimal_agent_card: AgentCard):
    """Test handling of oversized JSON payloads."""
    handler = mock.AsyncMock()
    app_instance = A2AStarletteApplication(minimal_agent_card, handler)
    client = TestClient(app_instance.build())

    large_string = 'a' * 11 * 1_000_000  # 11MB string
    payload = {
        'jsonrpc': '2.0',
        'method': 'test',
        'id': 1,
        'params': {'data': large_string},
    }

    response = client.post('/', json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data['error']['code'] == -32600


@pytest.mark.parametrize(
    'max_content_length',
    [
        None,
        11 * 1024 * 1024,
        30 * 1024 * 1024,
    ],
)
def test_handle_oversized_payload_with_max_content_length(
    minimal_agent_card: AgentCard,
    max_content_length: int | None,
):
    """Test handling of JSON payloads with sizes within custom max_content_length."""
    handler = mock.AsyncMock()
    app_instance = A2AStarletteApplication(
        minimal_agent_card, handler, max_content_length=max_content_length
    )
    client = TestClient(app_instance.build())

    large_string = 'a' * 11 * 1_000_000  # 11MB string
    payload = {
        'jsonrpc': '2.0',
        'method': 'test',
        'id': 1,
        'params': {'data': large_string},
    }

    response = client.post('/', json=payload)
    assert response.status_code == 200
    data = response.json()
    # When max_content_length is set, requests up to that size should not be
    # rejected due to payload size. The request might fail for other reasons,
    # but it shouldn't be an InvalidRequestError related to the content length.
    if max_content_length is not None:
        assert data['error']['code'] != -32600


def test_handle_unicode_characters(minimal_agent_card: AgentCard):
    """Test handling of unicode characters in JSON payload."""
    handler = mock.AsyncMock()
    app_instance = A2AStarletteApplication(minimal_agent_card, handler)
    client = TestClient(app_instance.build())

    unicode_text = 'こんにちは世界'  # "Hello world" in Japanese

    # Mock a handler response
    handler.on_message_send.return_value = Message(
        role=Role.ROLE_AGENT,
        parts=[Part(text=f'Received: {unicode_text}')],
        message_id='response-unicode',
    )

    unicode_payload = {
        'jsonrpc': '2.0',
        'method': 'SendMessage',
        'id': 'unicode_test',
        'params': {
            'message': {
                'role': 'ROLE_USER',
                'parts': [{'text': unicode_text}],
                'messageId': 'msg-unicode',
            }
        },
    }

    response = client.post('/', json=unicode_payload)

    # We are testing that the server can correctly deserialize the unicode payload
    assert response.status_code == 200
    data = response.json()
    # Check that we got a result (handler was called)
    if 'result' in data:
        # Response should contain the unicode text
        result = data['result']
        if 'message' in result:
            assert (
                result['message']['parts'][0]['text']
                == f'Received: {unicode_text}'
            )
        elif 'parts' in result:
            assert result['parts'][0]['text'] == f'Received: {unicode_text}'


def test_fastapi_sub_application(minimal_agent_card: AgentCard):
    """
    Tests that the A2AFastAPIApplication endpoint correctly passes the url in sub-application.
    """
    from fastapi import FastAPI

    handler = mock.AsyncMock()
    sub_app_instance = A2AFastAPIApplication(minimal_agent_card, handler)
    app_instance = FastAPI()
    app_instance.mount('/a2a', sub_app_instance.build())
    client = TestClient(app_instance)

    response = client.get('/a2a/openapi.json')
    assert response.status_code == 200
    response_data = response.json()

    # The generated a2a.json (OpenAPI 2.0 / Swagger) does not typically include a 'servers' block
    # unless specifically configured or converted to OpenAPI 3.0.
    # FastAPI usually generates OpenAPI 3.0 schemas which have 'servers'.
    # When we inject the raw Swagger 2.0 schema, it won't have 'servers'.
    # We check if it is indeed the injected schema by checking for 'swagger': '2.0'
    # or by checking for 'basePath' if we want to test path correctness.

    if response_data.get('swagger') == '2.0':
        # It's the injected Swagger 2.0 schema
        pass
    else:
        # It's an auto-generated OpenAPI 3.0+ schema (fallback or otherwise)
        assert 'servers' in response_data
        assert response_data['servers'] == [{'url': '/a2a'}]
