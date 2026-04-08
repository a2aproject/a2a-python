import logging

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from starlette.routing import Route

    _package_starlette_installed = True
else:
    try:
        from starlette.routing import Route

        _package_starlette_installed = True
    except ImportError:
        Route = Any

        _package_starlette_installed = False

from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes.common import ServerCallContextBuilder
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher


logger = logging.getLogger(__name__)


def create_jsonrpc_routes(
    request_handler: RequestHandler,
    rpc_url: str,
    context_builder: ServerCallContextBuilder | None = None,
    enable_v0_3_compat: bool = False,
) -> list['Route']:
    """Creates the Starlette Route for the A2A protocol JSON-RPC endpoint.

    Handles incoming JSON-RPC requests, routes them to the appropriate
    handler methods, and manages response generation including Server-Sent Events
    (SSE).

    Args:
        request_handler: The handler instance responsible for processing A2A
          requests via http.
        rpc_url: The URL prefix for the RPC endpoints. Should start with a leading slash '/'.
        context_builder: The ServerCallContextBuilder used to construct the
          ServerCallContext passed to the request_handler. If None the
          DefaultServerCallContextBuilder is used.
        enable_v0_3_compat: Whether to enable v0.3 backward compatibility on the same endpoint.
    """
    if not _package_starlette_installed:
        raise ImportError(
            'The `starlette` package is required to use `create_jsonrpc_routes`.'
            ' It can be added as a part of `a2a-sdk` optional dependencies,'
            ' `a2a-sdk[http-server]`.'
        )

    dispatcher = JsonRpcDispatcher(
        request_handler=request_handler,
        context_builder=context_builder,
        enable_v0_3_compat=enable_v0_3_compat,
    )

    return [
        Route(
            path=rpc_url,
            endpoint=dispatcher.handle_requests,
            methods=['POST'],
        )
    ]
