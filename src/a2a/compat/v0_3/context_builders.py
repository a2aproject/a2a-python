"""Context builders that add v0.3 backwards-compatibility for extensions.

The current spec uses ``A2A-Extensions`` (RFC 6648, no ``X-`` prefix). v0.3
clients still send the old ``X-A2A-Extensions`` name, so the v0.3 compat
adapters wrap the default builders with these classes to recognize both names.
"""

from typing import TYPE_CHECKING

from a2a.compat.v0_3.extension_headers import LEGACY_HTTP_EXTENSION_HEADER
from a2a.extensions.common import get_requested_extensions
from a2a.server.context import ServerCallContext


if TYPE_CHECKING:
    import grpc

    from starlette.requests import Request

    from a2a.server.request_handlers.grpc_handler import (
        GrpcServerCallContextBuilder,
    )
    from a2a.server.routes.common import ServerCallContextBuilder


def _get_legacy_grpc_extensions(
    context: 'grpc.aio.ServicerContext',
) -> list[str]:
    md = context.invocation_metadata()
    if md is None:
        return []
    lower_key = LEGACY_HTTP_EXTENSION_HEADER.lower()
    return [
        e if isinstance(e, str) else e.decode('utf-8')
        for k, e in md
        if k.lower() == lower_key
    ]


class V03ServerCallContextBuilder:
    """Wraps a ServerCallContextBuilder to also accept the legacy header.

    Recognizes the v0.3 ``X-A2A-Extensions`` HTTP header in addition to the
    spec ``A2A-Extensions``.
    """

    def __init__(self, inner: 'ServerCallContextBuilder') -> None:
        self._inner = inner

    def build(self, request: 'Request') -> ServerCallContext:
        """Builds a ServerCallContext, merging legacy extension headers."""
        context = self._inner.build(request)
        context.requested_extensions |= get_requested_extensions(
            request.headers.getlist(LEGACY_HTTP_EXTENSION_HEADER)
        )
        return context


class V03GrpcServerCallContextBuilder:
    """Wraps a GrpcServerCallContextBuilder to also accept the legacy metadata.

    Recognizes the v0.3 ``X-A2A-Extensions`` gRPC metadata key in addition to
    the spec ``A2A-Extensions``.
    """

    def __init__(self, inner: 'GrpcServerCallContextBuilder') -> None:
        self._inner = inner

    def build(self, context: 'grpc.aio.ServicerContext') -> ServerCallContext:
        """Builds a ServerCallContext, merging legacy extension metadata."""
        server_context = self._inner.build(context)
        server_context.requested_extensions |= get_requested_extensions(
            _get_legacy_grpc_extensions(context)
        )
        return server_context
