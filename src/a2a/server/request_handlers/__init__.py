"""Request handler components for the A2A server."""

import logging

from a2a.server.request_handlers.default_request_handler import (
    LegacyRequestHandler,
)
from a2a.server.request_handlers.default_request_handler_v2 import (
    DefaultRequestHandlerV2,
)
from a2a.server.request_handlers.request_handler import (
    RequestHandler,
    validate_request_params,
)
from a2a.server.request_handlers.response_helpers import (
    build_error_response,
    prepare_response_object,
)


logger = logging.getLogger(__name__)

try:
    from a2a.server.request_handlers.grpc_handler import (
        DefaultGrpcServerCallContextBuilder,
        GrpcHandler,  # type: ignore
        GrpcServerCallContextBuilder,
    )
except ImportError as e:
    _original_error = e
    logger.debug(
        'GrpcHandler not loaded. This is expected if gRPC dependencies are not installed. Error: %s',
        _original_error,
    )

    class GrpcHandler:  # type: ignore
        """Placeholder for GrpcHandler when dependencies are not installed."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                'To use GrpcHandler, its dependencies must be installed. '
                'You can install them with \'pip install "a2a-sdk[grpc]"\''
            ) from _original_error


DefaultRequestHandler = DefaultRequestHandlerV2

__all__ = [
    'DefaultGrpcServerCallContextBuilder',
    'DefaultRequestHandler',
    'DefaultRequestHandlerV2',
    'GrpcHandler',
    'GrpcServerCallContextBuilder',
    'LegacyRequestHandler',
    'RequestHandler',
    'build_error_response',
    'prepare_response_object',
    'validate_request_params',
]
