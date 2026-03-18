import logging

from collections.abc import Awaitable, Callable
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


class JsonRpcRouter:
    """A FastAPI application implementing the A2A protocol server endpoints.

    Handles incoming JSON-RPC requests, routes them to the appropriate
    handler methods, and manages response generation including Server-Sent Events
    (SSE).
    """

    def __init__(  # noqa: PLR0913
        self,
        agent_card: AgentCard,
        http_handler: RequestHandler,
        extended_agent_card: AgentCard | None = None,
        context_builder: CallContextBuilder | None = None,
        card_modifier: Callable[[AgentCard], Awaitable[AgentCard] | AgentCard]
        | None = None,
        extended_card_modifier: Callable[
            [AgentCard, ServerCallContext], Awaitable[AgentCard] | AgentCard
        ]
        | None = None,
        enable_v0_3_compat: bool = False,
        rpc_url: str = '',
    ) -> None:
        """Initializes the A2AFastAPIApplication.

        Args:
            agent_card: The AgentCard describing the agent's capabilities.
            http_handler: The handler instance responsible for processing A2A
              requests via http.
            extended_agent_card: An optional, distinct AgentCard to be served
              at the authenticated extended card endpoint.
            context_builder: The CallContextBuilder used to construct the
              ServerCallContext passed to the http_handler. If None, no
              ServerCallContext is passed.
            extended_card_modifier: An optional callback to dynamically modify
              the extended agent card before it is served. It receives the
              call context.
            enable_v0_3_compat: Whether to enable v0.3 backward compatibility on the same endpoint.
        """
        if not _package_starlette_installed:
            raise ImportError(
                'The `starlette` package is required to use the `JsonRpcRouter`.'
                ' It can be added as a part of `a2a-sdk` optional dependencies,'
                ' `a2a-sdk[http-server]`.'
            )
        self.dispatcher = JsonRpcDispatcher(
            agent_card=agent_card,
            http_handler=http_handler,
            extended_agent_card=extended_agent_card,
            context_builder=context_builder,
            card_modifier=card_modifier,
            extended_card_modifier=extended_card_modifier,
            enable_v0_3_compat=enable_v0_3_compat,
        )
        self.router = Router()
        self._setup_router(rpc_url)

    def _setup_router(
        self,
        rpc_url: str,
    ) -> None:
        """Configures the APIRouter with the A2A endpoints.

        Args:
            agent_card_url: The URL for the agent card endpoint.
            rpc_url: The URL for the A2A JSON-RPC endpoint.
        """
        self.router.add_route(
            rpc_url,
            self.dispatcher._handle_requests,
            methods=['POST']
        )
