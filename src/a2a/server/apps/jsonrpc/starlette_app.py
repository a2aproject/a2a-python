import logging

from typing import Any

from starlette.applications import Starlette
from starlette.routing import Route

from a2a.server.apps.jsonrpc.jsonrpc_app import (
    JSONRPCApplication,
)
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    DEFAULT_RPC_URL,
    EXTENDED_AGENT_CARD_PATH,
    PREV_AGENT_CARD_WELL_KNOWN_PATH,
)


logger = logging.getLogger(__name__)


class A2AStarletteApplication(JSONRPCApplication):
    """A Starlette application implementing the A2A protocol server endpoints.

    Handles incoming JSON-RPC requests, routes them to the appropriate
    handler methods, and manages response generation including Server-Sent Events
    (SSE).
    """

    def routes(
        self,
        agent_card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
        rpc_url: str = DEFAULT_RPC_URL,
        extended_agent_card_url: str = EXTENDED_AGENT_CARD_PATH,
    ) -> list[Route]:
        """Returns the Starlette Routes for handling A2A requests.

        Args:
            agent_card_url: The URL path for the agent card endpoint.
            rpc_url: The URL path for the A2A JSON-RPC endpoint (POST requests).
            extended_agent_card_url: The URL for the authenticated extended agent card endpoint.

        Returns:
            A list of Starlette Route objects.
        """
        app_routes = [
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

        if agent_card_url == AGENT_CARD_WELL_KNOWN_PATH:
            # For backward compatibility, serve the agent card at the deprecated path as well.
            # TODO: remove in a future release
            app_routes.append(
                Route(
                    PREV_AGENT_CARD_WELL_KNOWN_PATH,
                    self._handle_get_agent_card,
                    methods=['GET'],
                    name='deprecated_agent_card',
                )
            )

        # TODO: deprecated endpoint to be removed in a future release
        if self.agent_card.supports_authenticated_extended_card:
            app_routes.append(
                Route(
                    extended_agent_card_url,
                    self._handle_get_authenticated_extended_agent_card,
                    methods=['GET'],
                    name='authenticated_extended_agent_card',
                )
            )
        return app_routes

    def add_routes_to_app(
        self,
        app: Starlette,
        agent_card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
        rpc_url: str = DEFAULT_RPC_URL,
        extended_agent_card_url: str = EXTENDED_AGENT_CARD_PATH,
    ) -> None:
        """Adds the routes to the Starlette application.

        Args:
            app: The Starlette application to add the routes to.
            agent_card_url: The URL path for the agent card endpoint.
            rpc_url: The URL path for the A2A JSON-RPC endpoint (POST requests).
            extended_agent_card_url: The URL for the authenticated extended agent card endpoint.
        """
        routes = self.routes(
            agent_card_url=agent_card_url,
            rpc_url=rpc_url,
            extended_agent_card_url=extended_agent_card_url,
        )
        app.routes.extend(routes)

    def build(
        self,
        agent_card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
        rpc_url: str = DEFAULT_RPC_URL,
        extended_agent_card_url: str = EXTENDED_AGENT_CARD_PATH,
        **kwargs: Any,
    ) -> Starlette:
        """Builds and returns the Starlette application instance.

        Args:
            agent_card_url: The URL path for the agent card endpoint.
            rpc_url: The URL path for the A2A JSON-RPC endpoint (POST requests).
            extended_agent_card_url: The URL for the authenticated extended agent card endpoint.
            **kwargs: Additional keyword arguments to pass to the Starlette constructor.

        Returns:
            A configured Starlette application instance.
        """
        app = Starlette(**kwargs)

        self.add_routes_to_app(
            app, agent_card_url, rpc_url, extended_agent_card_url
        )

        return app
