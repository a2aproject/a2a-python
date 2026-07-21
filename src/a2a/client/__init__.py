"""Client-side components for interacting with an A2A agent."""

from a2a.client.auth import (
    AuthInterceptor,
    CredentialService,
    InMemoryContextCredentialStore,
)
from a2a.client.base_client import BaseClient
from a2a.client.card_resolver import A2ACardResolver
from a2a.client.card_validators import (
    CardValidator,
    InvalidAgentCardError,
    reject_non_https_urls,
    reject_private_urls,
    require_supported_interfaces,
    validate_card,
)
from a2a.client.client import (
    Client,
    ClientCallContext,
    ClientConfig,
)
from a2a.client.client_factory import (
    ClientFactory,
    create_client,
    minimal_agent_card,
)
from a2a.client.errors import (
    A2AClientError,
    A2AClientTimeoutError,
    AgentCardResolutionError,
)
from a2a.client.interceptors import ClientCallInterceptor


__all__ = [
    'A2ACardResolver',
    'A2AClientError',
    'A2AClientTimeoutError',
    'AgentCardResolutionError',
    'AuthInterceptor',
    'BaseClient',
    'CardValidator',
    'Client',
    'ClientCallContext',
    'ClientCallInterceptor',
    'ClientConfig',
    'ClientFactory',
    'CredentialService',
    'InMemoryContextCredentialStore',
    'InvalidAgentCardError',
    'create_client',
    'minimal_agent_card',
    'reject_non_https_urls',
    'reject_private_urls',
    'require_supported_interfaces',
    'validate_card',
]
