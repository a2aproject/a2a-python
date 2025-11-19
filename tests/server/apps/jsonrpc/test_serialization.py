from unittest import mock

import pytest

from fastapi import FastAPI
from pydantic import ValidationError
from starlette.testclient import TestClient

from a2a.server.apps import A2AFastAPIApplication, A2AStarletteApplication
from a2a.types import (
    APIKeySecurityScheme,
    AgentCapabilities,
    AgentCard,
    In,
    InvalidRequestError,
    JSONParseError,
    Message,
    Part,
    Role,
    SecurityScheme,
    TextPart,
)


@pytest.fixture
def agent_card_with_api_key():
    """Provides an AgentCard with an APIKeySecurityScheme for testing serialization."""
    # This data uses the alias 'in', which is correct for creating the model.
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


def test_starlette_agent_card_with_api_key_scheme_alias(
    agent_card_with_api_key: AgentCard,
):
    """
    Tests that the A2AStarletteApplication endpoint correctly serializes aliased fields.

    This verifies the fix for `APIKeySecurityScheme.in_` being serialized as `in_` instead of `in`.
    """
    handler = mock.AsyncMock()
    app_instance = A2AStarletteApplication(agent_card_with_api_key, handler)
    client = TestClient(app_instance.build())

    response = client.get('/.well-known/agent-card.json')
    assert response.status_code == 200
    response_data = response.json()

    security_scheme_json = response_data['securitySchemes']['api_key_auth']
    assert 'in' in security_scheme_json
    assert security_scheme_json['in'] == 'header'
    assert 'in_' not in security_scheme_json

    try:
        parsed_card = AgentCard.model_validate(response_data)
        parsed_scheme_wrapper = parsed_card.security_schemes['api_key_auth']
        assert isinstance(parsed_scheme_wrapper.root, APIKeySecurityScheme)
        assert parsed_scheme_wrapper.root.in_ == In.header
    except ValidationError as e:
        pytest.fail(
            f"AgentCard.model_validate failed on the server's response: {e}"
        )


def test_fastapi_agent_card_with_api_key_scheme_alias(
    agent_card_with_api_key: AgentCard,
):
    """
    Tests that the A2AFastAPIApplication endpoint correctly serializes aliased fields.

    This verifies the fix for `APIKeySecurityScheme.in_` being serialized as `in_` instead of `in`.
    """
    handler = mock.AsyncMock()
    app_instance = A2AFastAPIApplication(agent_card_with_api_key, handler)
    client = TestClient(app_instance.build())

    response = client.get('/.well-known/agent-card.json')
    assert response.status_code == 200
    response_data = response.json()

    security_scheme_json = response_data['securitySchemes']['api_key_auth']
    assert 'in' in security_scheme_json
    assert 'in_' not in security_scheme_json
    assert security_scheme_json['in'] == 'header'


def test_handle_invalid_json(agent_card_with_api_key: AgentCard):
    """Test handling of malformed JSON."""
    handler = mock.AsyncMock()
    app_instance = A2AStarletteApplication(agent_card_with_api_key, handler)
    client = TestClient(app_instance.build())

    response = client.post(
        '/',
        content='{ "jsonrpc": "2.0", "method": "test", "id": 1, "params": { "key": "value" }',
    )
    assert response.status_code == 200
    data = response.json()
    assert data['error']['code'] == JSONParseError().code


def test_handle_oversized_payload(agent_card_with_api_key: AgentCard):
    """Test handling of oversized JSON payloads."""
    handler = mock.AsyncMock()
    app_instance = A2AStarletteApplication(agent_card_with_api_key, handler)
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
    assert data['error']['code'] == InvalidRequestError().code


@pytest.mark.parametrize(
    'max_content_length',
    [
        None,
        11 * 1024 * 1024,
        30 * 1024 * 1024,
    ],
)
def test_handle_oversized_payload_with_max_content_length(
    agent_card_with_api_key: AgentCard,
    max_content_length: int | None,
):
    """Test handling of JSON payloads with sizes within custom max_content_length."""
    handler = mock.AsyncMock()
    app_instance = A2AStarletteApplication(
        agent_card_with_api_key, handler, max_content_length=max_content_length
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
    assert data['error']['code'] != InvalidRequestError().code


def test_handle_unicode_characters(agent_card_with_api_key: AgentCard):
    """Test handling of unicode characters in JSON payload."""
    handler = mock.AsyncMock()
    app_instance = A2AStarletteApplication(agent_card_with_api_key, handler)
    client = TestClient(app_instance.build())

    unicode_text = 'こんにちは世界'  # "Hello world" in Japanese
    unicode_payload = {
        'jsonrpc': '2.0',
        'method': 'message/send',
        'id': 'unicode_test',
        'params': {
            'message': {
                'role': 'user',
                'parts': [{'kind': 'text', 'text': unicode_text}],
                'message_id': 'msg-unicode',
            }
        },
    }

    # Mock a handler for this method
    handler.on_message_send.return_value = Message(
        role=Role.agent,
        parts=[Part(root=TextPart(text=f'Received: {unicode_text}'))],
        message_id='response-unicode',
    )

    response = client.post('/', json=unicode_payload)

    # We are not testing the handler logic here, just that the server can correctly
    # deserialize the unicode payload without errors. A 200 response with any valid
    # JSON-RPC response indicates success.
    assert response.status_code == 200
    data = response.json()
    assert 'error' not in data or data['error'] is None
    assert data['result']['parts'][0]['text'] == f'Received: {unicode_text}'


def test_fastapi_sub_application(agent_card_with_api_key: AgentCard):
    """
    Tests that the A2AFastAPIApplication endpoint correctly passes the url in sub-application.
    """
    handler = mock.AsyncMock()
    sub_app_instance = A2AFastAPIApplication(agent_card_with_api_key, handler)
    app_instance = FastAPI()
    app_instance.mount('/a2a', sub_app_instance.build())
    client = TestClient(app_instance)

    response = client.get('/a2a/openapi.json')
    assert response.status_code == 200
    response_data = response.json()

    assert 'servers' in response_data
    assert response_data['servers'] == [{'url': '/a2a'}]
