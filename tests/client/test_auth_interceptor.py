# ruff: noqa: INP001, S106
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
    ClientConfig,
    ClientFactory,
    InMemoryContextCredentialStore,
)
from a2a.client.interceptors import BeforeArgs
from a2a.types.a2a_pb2 import (
    APIKeySecurityScheme,
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AuthorizationCodeOAuthFlow,
    HTTPAuthSecurityScheme,
    Message,
    OAuth2SecurityScheme,
    OAuthFlows,
    OpenIdConnectSecurityScheme,
    Role,
    SecurityRequirement,
    SecurityScheme,
    SendMessageRequest,
    SendMessageResponse,
    StringList,
)
from a2a.utils.constants import TransportProtocol
from google.protobuf import json_format


def build_success_response(request: httpx.Request) -> httpx.Response:
    """Creates a valid JSON-RPC success response based on the request."""

    request_payload = json.loads(request.content)
    message = Message(
        message_id='message-id',
        role=Role.ROLE_AGENT,
        parts=[],
    )
    response = SendMessageResponse(message=message)
    response_payload = {
        'id': request_payload['id'],
        'jsonrpc': '2.0',
        'result': json_format.MessageToDict(response),
    }
    return httpx.Response(200, json=response_payload)


def build_message() -> Message:
    """Builds a minimal Message."""
    return Message(
        message_id='msg1',
        role=Role.ROLE_USER,
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
    request = SendMessageRequest(message=build_message())
    async for _ in client.send_message(
        request=request,
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
    auth_interceptor = AuthInterceptor(credential_service=store)
    request = SendMessageRequest(message=Message())
    context = ClientCallContext(state={})
    args = BeforeArgs(
        input=request,
        method='send_message',
        agent_card=AgentCard(),
        context=context,
    )

    await auth_interceptor.before(args)
    assert context.service_parameters is None


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


def wrap_security_scheme(scheme: Any) -> SecurityScheme:
    """Wraps a security scheme in the correct SecurityScheme proto field."""
    if isinstance(scheme, APIKeySecurityScheme):
        return SecurityScheme(api_key_security_scheme=scheme)
    if isinstance(scheme, HTTPAuthSecurityScheme):
        return SecurityScheme(http_auth_security_scheme=scheme)
    if isinstance(scheme, OAuth2SecurityScheme):
        return SecurityScheme(oauth2_security_scheme=scheme)
    if isinstance(scheme, OpenIdConnectSecurityScheme):
        return SecurityScheme(open_id_connect_security_scheme=scheme)
    raise ValueError(f'Unknown security scheme type: {type(scheme)}')


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
        name='X-API-Key',
        location='header',
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
        flows=OAuthFlows(
            authorization_code=AuthorizationCodeOAuthFlow(
                authorization_url='http://provider.com/auth',
                token_url='http://provider.com/token',
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
        supported_interfaces=[
            AgentInterface(
                url=test_case.url, protocol_binding=TransportProtocol.JSONRPC
            )
        ],
        name=f'{test_case.scheme_name}bot',
        description=f'A bot that uses {test_case.scheme_name}',
        version='1.0',
        default_input_modes=[],
        default_output_modes=[],
        skills=[],
        capabilities=AgentCapabilities(),
        security_requirements=[
            SecurityRequirement(schemes={test_case.scheme_name: StringList()})
        ],
        security_schemes={
            test_case.scheme_name: wrap_security_scheme(
                test_case.security_scheme
            )
        },
    )

    async with httpx.AsyncClient() as http_client:
        config = ClientConfig(
            httpx_client=http_client,
            supported_protocol_bindings=[TransportProtocol.JSONRPC],
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
    credential = 'test-token'
    await store.set_credentials(session_id, scheme_name, credential)
    auth_interceptor = AuthInterceptor(credential_service=store)
    agent_card = AgentCard(
        supported_interfaces=[
            AgentInterface(
                url='http://agent.com/rpc',
                protocol_binding=TransportProtocol.JSONRPC,
            )
        ],
        name='missingbot',
        description='A bot that uses missing scheme definition',
        version='1.0',
        default_input_modes=[],
        default_output_modes=[],
        skills=[],
        capabilities=AgentCapabilities(),
        security_requirements=[
            SecurityRequirement(schemes={scheme_name: StringList()})
        ],
        security_schemes={},
    )
    request = SendMessageRequest(message=Message())
    context = ClientCallContext(state={'sessionId': session_id})
    args = BeforeArgs(
        input=request,
        method='send_message',
        agent_card=agent_card,
        context=context,
    )

    await auth_interceptor.before(args)
    assert context.service_parameters is None


# ---------------------------------------------------------------------------
# Tests for multi-scheme AND / multi-requirement OR semantics (#1138)
# ---------------------------------------------------------------------------


class DictCredentialService:
    """A simple credential service backed by a plain dict."""

    def __init__(self, creds: dict[str, str]) -> None:
        self._creds = creds

    async def get_credentials(
        self,
        security_scheme_name: str,
        context: ClientCallContext | None,
    ) -> str | None:
        return self._creds.get(security_scheme_name)


def _make_agent_card(
    *,
    security_schemes: dict[str, SecurityScheme],
    security_requirements: list[SecurityRequirement],
) -> AgentCard:
    """Helper to build a minimal AgentCard with security configuration."""
    return AgentCard(
        name='testbot',
        description='test',
        version='1.0',
        default_input_modes=[],
        default_output_modes=[],
        skills=[],
        capabilities=AgentCapabilities(),
        security_schemes=security_schemes,
        security_requirements=security_requirements,
    )


@pytest.mark.asyncio
async def test_multi_scheme_and_semantics() -> None:
    """A single requirement with two API-key-in-header schemes must apply both headers.

    This is the exact reproduction from issue #1138.
    """
    creds = {'AUTH_KEY': 'gw-secret-key', 'USER_ID': 'user-123'}
    card = _make_agent_card(
        security_schemes={
            'AUTH_KEY': SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(
                    location='header', name='AUTH_KEY'
                )
            ),
            'USER_ID': SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(
                    location='header', name='USER_ID'
                )
            ),
        },
        security_requirements=[
            SecurityRequirement(
                schemes={'AUTH_KEY': StringList(), 'USER_ID': StringList()}
            )
        ],
    )

    args = BeforeArgs(input=None, method='message/send', agent_card=card)
    await AuthInterceptor(DictCredentialService(creds)).before(args)

    assert args.context is not None
    assert args.context.service_parameters is not None
    assert args.context.service_parameters['AUTH_KEY'] == 'gw-secret-key'
    assert args.context.service_parameters['USER_ID'] == 'user-123'


@pytest.mark.asyncio
async def test_multi_requirement_or_fallback() -> None:
    """When the first requirement is unsatisfiable, the second (OR) should be used."""
    creds = {'BACKUP_KEY': 'backup-secret'}
    card = _make_agent_card(
        security_schemes={
            'PRIMARY_KEY': SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(
                    location='header', name='X-Primary'
                )
            ),
            'BACKUP_KEY': SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(
                    location='header', name='X-Backup'
                )
            ),
        },
        security_requirements=[
            # First requirement: credential not available
            SecurityRequirement(schemes={'PRIMARY_KEY': StringList()}),
            # Second requirement: credential available
            SecurityRequirement(schemes={'BACKUP_KEY': StringList()}),
        ],
    )

    args = BeforeArgs(input=None, method='message/send', agent_card=card)
    await AuthInterceptor(DictCredentialService(creds)).before(args)

    assert args.context is not None
    assert args.context.service_parameters is not None
    assert args.context.service_parameters['X-Backup'] == 'backup-secret'
    # The primary key should NOT be present
    assert 'X-Primary' not in args.context.service_parameters


@pytest.mark.asyncio
async def test_partial_requirement_not_applied() -> None:
    """A requirement with two schemes where one credential is missing must not be partially applied."""
    creds = {'AUTH_KEY': 'gw-secret-key'}  # USER_ID is missing
    card = _make_agent_card(
        security_schemes={
            'AUTH_KEY': SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(
                    location='header', name='AUTH_KEY'
                )
            ),
            'USER_ID': SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(
                    location='header', name='USER_ID'
                )
            ),
        },
        security_requirements=[
            SecurityRequirement(
                schemes={'AUTH_KEY': StringList(), 'USER_ID': StringList()}
            )
        ],
    )

    args = BeforeArgs(input=None, method='message/send', agent_card=card)
    await AuthInterceptor(DictCredentialService(creds)).before(args)

    # Nothing should be applied — partial application is not allowed
    assert args.context is None or args.context.service_parameters is None


@pytest.mark.asyncio
async def test_mixed_bearer_and_apikey_in_single_requirement() -> None:
    """A single requirement with both Bearer and API-key-in-header schemes must apply both."""
    creds = {'bearer_scheme': 'my-token', 'apikey_scheme': 'my-api-key'}
    card = _make_agent_card(
        security_schemes={
            'bearer_scheme': SecurityScheme(
                http_auth_security_scheme=HTTPAuthSecurityScheme(
                    scheme='bearer'
                )
            ),
            'apikey_scheme': SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(
                    location='header', name='X-API-Key'
                )
            ),
        },
        security_requirements=[
            SecurityRequirement(
                schemes={
                    'bearer_scheme': StringList(),
                    'apikey_scheme': StringList(),
                }
            )
        ],
    )

    args = BeforeArgs(input=None, method='message/send', agent_card=card)
    await AuthInterceptor(DictCredentialService(creds)).before(args)

    assert args.context is not None
    assert args.context.service_parameters is not None
    assert args.context.service_parameters['Authorization'] == 'Bearer my-token'
    assert args.context.service_parameters['X-API-Key'] == 'my-api-key'


@pytest.mark.asyncio
async def test_unsupported_scheme_type_makes_requirement_unsatisfiable() -> (
    None
):
    """An unsupported scheme type (API key in query) makes its requirement unsatisfiable.

    A fallback requirement should be used instead.
    """
    creds = {
        'query_key': 'query-secret',
        'header_key': 'header-secret',
    }
    card = _make_agent_card(
        security_schemes={
            'query_key': SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(
                    location='query', name='api_key'
                )
            ),
            'header_key': SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(
                    location='header', name='X-Header-Key'
                )
            ),
        },
        security_requirements=[
            # First requirement: uses unsupported query location
            SecurityRequirement(schemes={'query_key': StringList()}),
            # Second requirement: uses supported header location
            SecurityRequirement(schemes={'header_key': StringList()}),
        ],
    )

    args = BeforeArgs(input=None, method='message/send', agent_card=card)
    await AuthInterceptor(DictCredentialService(creds)).before(args)

    assert args.context is not None
    assert args.context.service_parameters is not None
    assert args.context.service_parameters['X-Header-Key'] == 'header-secret'
    assert 'api_key' not in args.context.service_parameters
