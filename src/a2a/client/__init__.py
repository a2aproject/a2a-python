"""Client-side components for interacting with an A2A agent."""

import logging

from a2a.client.auth import (
    AuthInterceptor,
    CredentialService,
    InMemoryContextCredentialStore,
)
from a2a.client.base_client import BaseClient
from a2a.client.card_resolver import A2ACardResolver
from a2a.client.client import Client, ClientConfig, ClientEvent, Consumer
from a2a.client.client_factory import ClientFactory, minimal_agent_card
from a2a.client.errors import (
    A2AClientError,
    A2AClientHTTPError,
    A2AClientJSONError,
    A2AClientTimeoutError,
)
from a2a.client.helpers import create_text_message_object
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor


logger = logging.getLogger(__name__)


__all__ = [
    'A2ACardResolver',
    'A2AClientError',
    'A2AClientHTTPError',
    'A2AClientJSONError',
    'A2AClientTimeoutError',
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
