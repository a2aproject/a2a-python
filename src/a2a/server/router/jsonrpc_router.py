import logging

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from starlette.routing import Router

    _package_starlette_installed = True
else:
    try:
        from starlette.routing import Router

        _package_starlette_installed = True
    except ImportError:
        Router = Any

        _package_starlette_installed = False


from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.router.jsonrpc_dispatcher import (
    CallContextBuilder,
    JsonRpcDispatcher,
)
from a2a.types.a2a_pb2 import AgentCard


logger = logging.getLogger(__name__)


from starlette.middleware import Middleware
from starlette.routing import Route, Router


class JsonRpcRouter:
    """Provides the Starlette Route for the A2A protocol JSON-RPC endpoint.

    Handles incoming JSON-RPC requests, routes them to the appropriate
    handler methods, and manages response generation including Server-Sent Events
    (SSE).
    """

    def __init__(  # noqa: PLR0913
        self,
        agent_card: AgentCard,
        request_handler: RequestHandler,
        extended_agent_card: AgentCard | None = None,
        context_builder: CallContextBuilder | None = None,
        card_modifier: Callable[[AgentCard], Awaitable[AgentCard] | AgentCard]
        | None = None,
        extended_card_modifier: Callable[
            [AgentCard, ServerCallContext], Awaitable[AgentCard] | AgentCard
        ]
        | None = None,
        enable_v0_3_compat: bool = False,
        rpc_url: str = '/',
        middleware: Sequence[Middleware] | None = None,
    ) -> None:
        """Initializes the JsonRpcRouter.
        
        ... (docstrings remain the same) ...
        """
        if not _package_starlette_installed:
            raise ImportError(
                'The `starlette` package is required to use the `JsonRpcRouter`.'
                ' It can be added as a part of `a2a-sdk` optional dependencies,'
                ' `a2a-sdk[http-server]`.'
            )

        self.dispatcher = JsonRpcDispatcher(
            agent_card=agent_card,
            http_handler=request_handler,
            extended_agent_card=extended_agent_card,
            context_builder=context_builder,
            card_modifier=card_modifier,
            extended_card_modifier=extended_card_modifier,
            enable_v0_3_compat=enable_v0_3_compat,
        )

        self.route = Route(
            path=rpc_url,
            endpoint=self.dispatcher._handle_requests,
            methods=['POST'],
            middleware=middleware,
        )
