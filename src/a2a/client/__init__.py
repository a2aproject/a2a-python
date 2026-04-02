"""Client-side components for interacting with an A2A agent."""

from a2a.client.auth import (
    AuthInterceptor,
    CredentialService,
    InMemoryContextCredentialStore,
)
from a2a.client.base_client import BaseClient
from a2a.client.card_resolver import A2ACardResolver
from a2a.client.client import (
    Client,
    ClientCallContext,
    ClientConfig,
    ClientEvent,
    Consumer,
)
from a2a.client.client_factory import (
    A2AClientFactory,
    ClientFactory,
    minimal_agent_card,
)
from a2a.client.errors import (
    A2AClientError,
    A2AClientTimeoutError,
    AgentCardResolutionError,
)
from a2a.client.helpers import create_text_message_object
from a2a.client.interceptors import ClientCallInterceptor


__all__ = [
    'A2ACardResolver',
    'A2AClientError',
    'A2AClientFactory',
    'A2AClientTimeoutError',
    'AgentCardResolutionError',
    'AuthInterceptor',
    'BaseClient',
    'Client',
    'ClientCallContext',
    'ClientCallInterceptor',
    'ClientConfig',
    'ClientEvent',
    'ClientFactory',
    'Consumer',
    'CredentialService',
    'InMemoryContextCredentialStore',
    'create_text_message_object',
    'minimal_agent_card',
]
