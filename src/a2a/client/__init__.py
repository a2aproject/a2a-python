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
from a2a.client.legacy import A2AClient
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor


logger = logging.getLogger(__name__)

try:
    from a2a.client.legacy_grpc import A2AGrpcClient  # type: ignore
except ImportError as e:
    _original_error = e
    logger.debug(
        'A2AGrpcClient not loaded. This is expected if gRPC dependencies are not installed. Error: %s',
        _original_error,
    )

    class A2AGrpcClient:  # type: ignore
        """Placeholder for A2AGrpcClient when dependencies are not installed."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                'To use A2AGrpcClient, its dependencies must be installed. '
                'You can install them with \'pip install "a2a-sdk[grpc]"\''
            ) from _original_error


__all__ = [
    'A2ACardResolver',
    'A2AClient',
    'A2AClientError',
    'A2AClientHTTPError',
    'A2AClientJSONError',
    'A2AClientTimeoutError',
    'A2AGrpcClient',
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
