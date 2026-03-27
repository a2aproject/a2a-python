from collections.abc import Awaitable, Callable
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


from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes.jsonrpc_dispatcher import (
    CallContextBuilder,
    JsonRpcDispatcher,
)
from a2a.types.a2a_pb2 import AgentCard


def create_jsonrpc_routes(  # noqa: PLR0913
    agent_card: AgentCard,
    request_handler: RequestHandler,
    rpc_url: str,
    extended_agent_card: AgentCard | None = None,
    context_builder: CallContextBuilder | None = None,
    card_modifier: Callable[[AgentCard], Awaitable[AgentCard] | AgentCard]
    | None = None,
    extended_card_modifier: Callable[
        [AgentCard, ServerCallContext], Awaitable[AgentCard] | AgentCard
    ]
    | None = None,
    enable_v0_3_compat: bool = False,
) -> list['Route']:
    """Creates the Starlette Route for the A2A protocol JSON-RPC endpoint.

    Handles incoming JSON-RPC requests, routes them to the appropriate
    handler methods, and manages response generation including Server-Sent Events
    (SSE).

    Args:
        agent_card: The AgentCard describing the agent's capabilities.
        request_handler: The handler instance responsible for processing A2A
          requests via http.
        rpc_url: The URL prefix for the RPC endpoints.
        extended_agent_card: An optional, distinct AgentCard to be served
          at the authenticated extended card endpoint.
        context_builder: The CallContextBuilder used to construct the
          ServerCallContext passed to the request_handler. If None the
          DefaultCallContextBuilder is used.
        card_modifier: An optional callback to dynamically modify the public
          agent card before it is served.
        extended_card_modifier: An optional callback to dynamically modify
          the extended agent card before it is served. It receives the
          call context.
        enable_v0_3_compat: Whether to enable v0.3 backward compatibility on the same endpoint.
    """
    if not _package_starlette_installed:
        raise ImportError(
            'The `starlette` package is required to use `create_jsonrpc_routes`.'
            ' It can be added as a part of `a2a-sdk` optional dependencies,'
            ' `a2a-sdk[http-server]`.'
        )

    dispatcher = JsonRpcDispatcher(
        agent_card=agent_card,
        request_handler=request_handler,
        extended_agent_card=extended_agent_card,
        context_builder=context_builder,
        card_modifier=card_modifier,
        extended_card_modifier=extended_card_modifier,
        enable_v0_3_compat=enable_v0_3_compat,
    )

    return [
        Route(
            path=rpc_url,
            endpoint=dispatcher.handle_requests,
            methods=['POST'],
        )
    ]
