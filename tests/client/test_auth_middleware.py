import json

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import respx

from a2a.client import (
    AuthInterceptor,
    Client,
    ClientCallContext,
    ClientCallInterceptor,
    ClientConfig,
    ClientFactory,
    InMemoryContextCredentialStore,
)
from a2a.types import (
    APIKeySecurityScheme,
    AgentCapabilities,
    AgentCard,
    AuthorizationCodeOAuthFlow,
    HTTPAuthSecurityScheme,
    In,
    Message,
    OAuth2SecurityScheme,
    OAuthFlows,
    OpenIdConnectSecurityScheme,
    Role,
    SecurityScheme,
    SendMessageSuccessResponse,
    TransportProtocol,
)


class HeaderInterceptor(ClientCallInterceptor):
    """A simple mock interceptor for testing basic middleware functionality."""

    def __init__(self, header_name: str, header_value: str):
        self.header_name = header_name
        self.header_value = header_value

    async def intercept(
        self,
        method_name: str,
        request_payload: dict[str, Any],
        http_kwargs: dict[str, Any],
        agent_card: AgentCard | None,
        context: ClientCallContext | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        headers = http_kwargs.get('headers', {})
        headers[self.header_name] = self.header_value
        http_kwargs['headers'] = headers
        return request_payload, http_kwargs


def build_success_response(request: httpx.Request) -> httpx.Response:
    """Creates a valid JSON-RPC success response based on the request."""
    request_payload = json.loads(request.content)
    response_payload = SendMessageSuccessResponse(
        id=request_payload['id'],
        jsonrpc='2.0',
        result=Message(
            kind='message',
            message_id='message-id',
            role=Role.agent,
            parts=[],
        ),
    ).model_dump(mode='json')
    return httpx.Response(200, json=response_payload)


def build_message() -> Message:
    """Builds a minimal Message."""
    return Message(
        message_id='msg1',
        role=Role.user,
        parts=[],
    )


async def send_message(
    client: Client,
    url: str,
    session_id: str | None = None,
) -> httpx.Request:
    """Mocks the response and sends a message using the client."""
    respx.post(url).mock(side_effect=build_success_response)
    context = ClientCallContext(
        state={'sessionId': session_id} if session_id else {}
    )
    async for _ in client.send_message(
        request=build_message(),
        context=context,
    ):
        pass
    return respx.calls.last.request


@pytest.fixture
def store():
    store = InMemoryContextCredentialStore()
    yield store


@pytest.mark.asyncio
async def test_auth_interceptor_skips_when_no_agent_card(
    store: InMemoryContextCredentialStore,
) -> None:
    """Tests that the AuthInterceptor does not modify the request when no AgentCard is provided."""
    request_payload = {'foo': 'bar'}
    http_kwargs = {'fizz': 'buzz'}
    auth_interceptor = AuthInterceptor(credential_service=store)

    new_payload, new_kwargs = await auth_interceptor.intercept(
        method_name='message/send',
        request_payload=request_payload,
        http_kwargs=http_kwargs,
        agent_card=None,
        context=ClientCallContext(state={}),
    )
    assert new_payload == request_payload
    assert new_kwargs == http_kwargs


@pytest.mark.asyncio
async def test_in_memory_context_credential_store(
    store: InMemoryContextCredentialStore,
) -> None:
    """Verifies that InMemoryContextCredentialStore correctly stores and retrieves
    credentials based on the session ID in the client context.
    """
    session_id = 'session-id'
    scheme_name = 'test-scheme'
    credential = 'test-token'
    await store.set_credentials(session_id, scheme_name, credential)

    # Assert: Successful retrieval
    context = ClientCallContext(state={'sessionId': session_id})
    retrieved_credential = await store.get_credentials(scheme_name, context)
    assert retrieved_credential == credential
    # Assert: Retrieval with wrong session ID returns None
    wrong_context = ClientCallContext(state={'sessionId': 'wrong-session'})
    retrieved_credential_wrong = await store.get_credentials(
        scheme_name, wrong_context
    )
    assert retrieved_credential_wrong is None
    # Assert: Retrieval with no context returns None
    retrieved_credential_none = await store.get_credentials(scheme_name, None)
    assert retrieved_credential_none is None
    # Assert: Retrieval with context but no sessionId returns None
    empty_context = ClientCallContext(state={})
    retrieved_credential_empty = await store.get_credentials(
        scheme_name, empty_context
    )
    assert retrieved_credential_empty is None
    # Assert: Overwrite the credential when session_id already exists
    new_credential = 'new-token'
    await store.set_credentials(session_id, scheme_name, new_credential)
    assert await store.get_credentials(scheme_name, context) == new_credential


@pytest.mark.asyncio
@respx.mock
async def test_client_with_simple_interceptor() -> None:
    """Ensures that a custom HeaderInterceptor correctly injects a static header into outbound HTTP requests from the A2AClient."""
    url = 'http://agent.com/rpc'
    interceptor = HeaderInterceptor('X-Test-Header', 'Test-Value-123')
    card = AgentCard(
        url=url,
        name='testbot',
        description='test bot',
        version='1.0',
        default_input_modes=[],
        default_output_modes=[],
        skills=[],
        capabilities=AgentCapabilities(),
        preferred_transport=TransportProtocol.jsonrpc,
    )

    async with httpx.AsyncClient() as http_client:
        config = ClientConfig(
            httpx_client=http_client,
            supported_transports=[TransportProtocol.jsonrpc],
        )
        factory = ClientFactory(config)
        client = factory.create(card, interceptors=[interceptor])

        request = await send_message(client, url)
        assert request.headers['x-test-header'] == 'Test-Value-123'


@dataclass
class AuthTestCase:
    """Represents a test scenario for verifying authentication behavior in AuthInterceptor."""

    url: str
    """The endpoint URL of the agent to which the request is sent."""
    session_id: str
    """The client session ID used to fetch credentials from the credential store."""
    scheme_name: str
    """The name of the security scheme defined in the agent card."""
    credential: str
    """The actual credential value (e.g., API key, access token) to be injected."""
    security_scheme: Any
    """The security scheme object (e.g., APIKeySecurityScheme, OAuth2SecurityScheme, etc.) to define behavior."""
    expected_header_key: str
    """The expected HTTP header name to be set by the interceptor."""
    expected_header_value_func: Callable[[str], str]
    """A function that maps the credential to its expected header value (e.g., lambda c: f"Bearer {c}")."""


api_key_test_case = AuthTestCase(
    url='http://agent.com/rpc',
    session_id='session-id',
    scheme_name='apikey',
    credential='secret-api-key',
    security_scheme=APIKeySecurityScheme(
        type='apiKey',
        name='X-API-Key',
        in_=In.header,
    ),
    expected_header_key='x-api-key',
    expected_header_value_func=lambda c: c,
)


oauth2_test_case = AuthTestCase(
    url='http://agent.com/rpc',
    session_id='session-id',
    scheme_name='oauth2',
    credential='secret-oauth-access-token',
    security_scheme=OAuth2SecurityScheme(
        type='oauth2',
        flows=OAuthFlows(
            authorization_code=AuthorizationCodeOAuthFlow(
                authorization_url='http://provider.com/auth',
                token_url='http://provider.com/token',
                scopes={'read': 'Read scope'},
            )
        ),
    ),
    expected_header_key='Authorization',
    expected_header_value_func=lambda c: f'Bearer {c}',
)


oidc_test_case = AuthTestCase(
    url='http://agent.com/rpc',
    session_id='session-id',
    scheme_name='oidc',
    credential='secret-oidc-id-token',
    security_scheme=OpenIdConnectSecurityScheme(
        type='openIdConnect',
        open_id_connect_url='http://provider.com/.well-known/openid-configuration',
    ),
    expected_header_key='Authorization',
    expected_header_value_func=lambda c: f'Bearer {c}',
)


bearer_test_case = AuthTestCase(
    url='http://agent.com/rpc',
    session_id='session-id',
    scheme_name='bearer',
    credential='bearer-token-123',
    security_scheme=HTTPAuthSecurityScheme(
        scheme='bearer',
    ),
    expected_header_key='Authorization',
    expected_header_value_func=lambda c: f'Bearer {c}',
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'test_case',
    [api_key_test_case, oauth2_test_case, oidc_test_case, bearer_test_case],
)
@respx.mock
async def test_auth_interceptor_variants(
    test_case: AuthTestCase, store: InMemoryContextCredentialStore
) -> None:
    """Parametrized test verifying that AuthInterceptor correctly attaches credentials based on the defined security scheme in the AgentCard."""
    await store.set_credentials(
        test_case.session_id, test_case.scheme_name, test_case.credential
    )
    auth_interceptor = AuthInterceptor(credential_service=store)
    agent_card = AgentCard(
        url=test_case.url,
        name=f'{test_case.scheme_name}bot',
        description=f'A bot that uses {test_case.scheme_name}',
        version='1.0',
        default_input_modes=[],
        default_output_modes=[],
        skills=[],
        capabilities=AgentCapabilities(),
        security=[{test_case.scheme_name: []}],
        security_schemes={
            test_case.scheme_name: SecurityScheme(
                root=test_case.security_scheme
            )
        },
        preferred_transport=TransportProtocol.jsonrpc,
    )

    async with httpx.AsyncClient() as http_client:
        config = ClientConfig(
            httpx_client=http_client,
            supported_transports=[TransportProtocol.jsonrpc],
        )
        factory = ClientFactory(config)
        client = factory.create(agent_card, interceptors=[auth_interceptor])

        request = await send_message(
            client, test_case.url, test_case.session_id
        )
        assert request.headers[
            test_case.expected_header_key
        ] == test_case.expected_header_value_func(test_case.credential)


@pytest.mark.asyncio
async def test_auth_interceptor_skips_when_scheme_not_in_security_schemes(
    store: InMemoryContextCredentialStore,
) -> None:
    """Tests that AuthInterceptor skips a scheme if it's listed in security requirements but not defined in security_schemes."""
    scheme_name = 'missing'
    session_id = 'session-id'
    credential = 'dummy-token'
    request_payload = {'foo': 'bar'}
    http_kwargs = {'fizz': 'buzz'}
    await store.set_credentials(session_id, scheme_name, credential)
    auth_interceptor = AuthInterceptor(credential_service=store)
    agent_card = AgentCard(
        url='http://agent.com/rpc',
        name='missingbot',
        description='A bot that uses missing scheme definition',
        version='1.0',
        default_input_modes=[],
        default_output_modes=[],
        skills=[],
        capabilities=AgentCapabilities(),
        security=[{scheme_name: []}],
        security_schemes={},
    )

    new_payload, new_kwargs = await auth_interceptor.intercept(
        method_name='message/send',
        request_payload=request_payload,
        http_kwargs=http_kwargs,
        agent_card=agent_card,
        context=ClientCallContext(state={'sessionId': session_id}),
    )
    assert new_payload == request_payload
    assert new_kwargs == http_kwargs
