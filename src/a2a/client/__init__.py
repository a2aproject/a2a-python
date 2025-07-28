"""Client-side components for interacting with an A2A agent."""

import logging

from a2a.client.auth import (
    AuthInterceptor,
    CredentialService,
    InMemoryContextCredentialStore,
)
from a2a.client.card_resolver import A2ACardResolver
from a2a.client.client import (
    Client,
    ClientConfig,
    ClientEvent,
    Consumer,
)
from a2a.client.client_factory import (
    ClientFactory,
    ClientProducer,
    minimal_agent_card,
)
from a2a.client.errors import (
    A2AClientError,
    A2AClientHTTPError,
    A2AClientJSONError,
    A2AClientTimeoutError,
)
from a2a.client.helpers import create_text_message_object
from a2a.client.jsonrpc_client import (
    A2AClient,
    JsonRpcClient,
    JsonRpcTransportClient,
    NewJsonRpcClient,
)
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.client.rest_client import (
    NewRestfulClient,
    RestClient,
    RestTransportClient,
)


logger = logging.getLogger(__name__)

try:
    from a2a.client.grpc_client import (
        GrpcClient,
        GrpcTransportClient,  # type: ignore
        NewGrpcClient,
    )
except ImportError as e:
    _original_error = e
    logger.debug(
        'A2AGrpcClient not loaded. This is expected if gRPC dependencies are not installed. Error: %s',
        _original_error,
    )

    class GrpcTransportClient:  # type: ignore
        """Placeholder for A2AGrpcClient when dependencies are not installed."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                'To use A2AGrpcClient, its dependencies must be installed. '
                'You can install them with \'pip install "a2a-sdk[grpc]"\''
            ) from _original_error
finally:
    # For backward compatability define this alias. This will be deprecated in
    # a future release.
    A2AGrpcClient = GrpcTransportClient  # type: ignore


__all__ = [
    'A2ACardResolver',
    'A2AClient',  # for backward compatability
    'A2AClientError',
    'A2AClientHTTPError',
    'A2AClientJSONError',
    'A2AClientTimeoutError',
    'A2AGrpcClient',  # for backward compatability
    'AuthInterceptor',
    'Client',
    'ClientCallContext',
    'ClientCallInterceptor',
    'ClientConfig',
    'ClientEvent',
    'ClientFactory',
    'ClientProducer',
    'Consumer',
    'CredentialService',
    'GrpcClient',
    'GrpcTransportClient',
    'InMemoryContextCredentialStore',
    'JsonRpcClient',
    'JsonRpcTransportClient',
    'NewGrpcClient',
    'NewJsonRpcClient',
    'NewRestfulClient',
    'RestClient',
    'RestTransportClient',
    'create_text_message_object',
    'minimal_agent_card',
]
