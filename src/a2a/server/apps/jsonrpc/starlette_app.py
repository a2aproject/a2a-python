import logging

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from starlette.applications import Starlette
    from starlette.routing import Route

    _package_starlette_installed = True

else:
    try:
        from starlette.applications import Starlette
        from starlette.routing import Route

        _package_starlette_installed = True
    except ImportError:
        Starlette = Any
        Route = Any

        _package_starlette_installed = False

from a2a.server.apps.jsonrpc.jsonrpc_app import (
    CallContextBuilder,
    JSONRPCApplication,
)
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import AgentCard
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    DEFAULT_RPC_URL,
)


logger = logging.getLogger(__name__)


class A2AStarletteApplication(JSONRPCApplication):
    """A Starlette application implementing the A2A protocol server endpoints.

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
        max_content_length: int | None = 10 * 1024 * 1024,  # 10MB
        enable_v0_3_compat: bool = False,
    ) -> None:
        """Initializes the A2AStarletteApplication.

        Args:
            agent_card: The AgentCard describing the agent's capabilities.
            http_handler: The handler instance responsible for processing A2A
              requests via http.
            extended_agent_card: An optional, distinct AgentCard to be served
              at the authenticated extended card endpoint.
            context_builder: The CallContextBuilder used to construct the
              ServerCallContext passed to the http_handler. If None, no
              ServerCallContext is passed.
            card_modifier: An optional callback to dynamically modify the public
              agent card before it is served.
            extended_card_modifier: An optional callback to dynamically modify
              the extended agent card before it is served. It receives the
              call context.
            max_content_length: The maximum allowed content length for incoming
              requests. Defaults to 10MB. Set to None for unbounded maximum.
            enable_v0_3_compat: Whether to enable v0.3 backward compatibility on the same endpoint.
        """
        if not _package_starlette_installed:
            raise ImportError(
                'Packages `starlette` and `sse-starlette` are required to use the'
                ' `A2AStarletteApplication`. It can be added as a part of `a2a-sdk`'
                ' optional dependencies, `a2a-sdk[http-server]`.'
            )
        super().__init__(
            agent_card=agent_card,
            http_handler=http_handler,
            extended_agent_card=extended_agent_card,
            context_builder=context_builder,
            card_modifier=card_modifier,
            extended_card_modifier=extended_card_modifier,
            max_content_length=max_content_length,
            enable_v0_3_compat=enable_v0_3_compat,
        )

    def routes(
        self,
        agent_card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
        rpc_url: str = DEFAULT_RPC_URL,
    ) -> list[Route]:
        """Returns the Starlette Routes for handling A2A requests.

        Args:
            agent_card_url: The URL path for the agent card endpoint.
            rpc_url: The URL path for the A2A JSON-RPC endpoint (POST requests).

        Returns:
            A list of Starlette Route objects.
        """
        return [
            Route(
                rpc_url,
                self._handle_requests,
                methods=['POST'],
                name='a2a_handler',
            ),
            Route(
                agent_card_url,
                self._handle_get_agent_card,
                methods=['GET'],
                name='agent_card',
            ),
        ]

    def add_routes_to_app(
        self,
        app: Starlette,
        agent_card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
        rpc_url: str = DEFAULT_RPC_URL,
    ) -> None:
        """Adds the routes to the Starlette application.

        Args:
            app: The Starlette application to add the routes to.
            agent_card_url: The URL path for the agent card endpoint.
            rpc_url: The URL path for the A2A JSON-RPC endpoint (POST requests).
        """
        routes = self.routes(
            agent_card_url=agent_card_url,
            rpc_url=rpc_url,
        )
        app.routes.extend(routes)

    def build(
        self,
        agent_card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
        rpc_url: str = DEFAULT_RPC_URL,
        **kwargs: Any,
    ) -> Starlette:
        """Builds and returns the Starlette application instance.

        Args:
            agent_card_url: The URL path for the agent card endpoint.
            rpc_url: The URL path for the A2A JSON-RPC endpoint (POST requests).
            **kwargs: Additional keyword arguments to pass to the Starlette constructor.

        Returns:
            A configured Starlette application instance.
        """
        app = Starlette(**kwargs)

        self.add_routes_to_app(app, agent_card_url, rpc_url)

        return app
