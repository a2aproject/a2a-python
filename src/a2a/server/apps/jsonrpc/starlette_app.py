import logging
import posixpath

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse, urlunparse

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from typing_extensions import Self

from a2a.server.apps.jsonrpc.jsonrpc_app import (
    CallContextBuilder,
    JSONRPCApplication,
)
from a2a.server.request_handlers.jsonrpc_handler import RequestHandler
from a2a.types import AgentCard, AgentCatalog, AgentLinkContext, AgentLinkTarget
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    DEFAULT_RPC_URL,
    EXTENDED_AGENT_CARD_PATH,
)


logger = logging.getLogger(__name__)


class A2AStarletteApplication(JSONRPCApplication):
    """A Starlette application implementing the A2A protocol server endpoints.

    Handles incoming JSON-RPC requests, routes them to the appropriate
    handler methods, and manages response generation including Server-Sent Events
    (SSE).
    """

    def __init__(
        self,
        agent_card: AgentCard,
        http_handler: RequestHandler,
        extended_agent_card: AgentCard | None = None,
        context_builder: CallContextBuilder | None = None,
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
        """
        super().__init__(
            agent_card=agent_card,
            http_handler=http_handler,
            extended_agent_card=extended_agent_card,
            context_builder=context_builder,
        )

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


@dataclass
class StarletteRouteConfig:
    """Configuration for route paths used in the Starlette-based JSON-RPC application.

    Attributes:
        agent_card_path: The URL path for the agent card endpoint.
        extended_agent_card_path: The URL path for the authenticated extended agent card endpoint.
        rpc_path: The URL path for the A2A JSON-RPC endpoint (POST requests).
    """

    agent_card_path: str = '/agent.json'
    extended_agent_card_path: str = '/agent/authenticatedExtendedCard'
    rpc_path: str = '/'


class StarletteRouteBuilder(JSONRPCApplication):
    """Configurable builder for Starlette routes that serve A2A protocol endpoints.

    This builder constructs the necessary HTTP routes for an A2A-compliant agent.
    It defines the routing logic, associates endpoints with handler methods,
    and prepares responses—including support for Server-Sent Events (SSE).
    """

    def __init__(
        self,
        agent_card: AgentCard,
        http_handler: RequestHandler,
        extended_agent_card: AgentCard | None = None,
        context_builder: CallContextBuilder | None = None,
        config: StarletteRouteConfig | None = None,
    ):
        """Initializes the A2AStarletteRouter.

        Args:
            agent_card: The AgentCard describing the agent's capabilities.
            http_handler: The handler instance responsible for processing A2A
              requests via http.
            extended_agent_card: An optional, distinct AgentCard to be served
              at the authenticated extended card endpoint.
            context_builder: The CallContextBuilder used to construct the
              ServerCallContext passed to the http_handler. If None, no
              ServerCallContext is passed.
            config: Optional route configuration including the paths for the agent card,
              extended agent card, and A2A JSON-RPC endpoint. If None, defaults are used.
        """
        super().__init__(
            agent_card=agent_card,
            http_handler=http_handler,
            extended_agent_card=extended_agent_card,
            context_builder=context_builder,
        )
        self.config = config or StarletteRouteConfig()

    def build(self) -> list[Route]:
        """Returns the Starlette Routes for handling A2A requests.

        Returns:
            A list of Starlette Route objects.
        """
        routes = [
            Route(
                self.config.rpc_path,
                self._handle_requests,
                methods=['POST'],
                name='a2a_handler',
            ),
            Route(
                self.config.agent_card_path,
                self._handle_get_agent_card,
                methods=['GET'],
                name='agent_card',
            ),
        ]
        if self.agent_card.supports_authenticated_extended_card:
            routes.append(
                Route(
                    self.config.extended_agent_card_path,
                    self._handle_get_authenticated_extended_agent_card,
                    methods=['GET'],
                    name='authenticated_extended_agent_card',
                )
            )
        return routes


def _join_url(base: str, *paths: str) -> str:
    """Joins a base URL with one or more URL path fragments into a normalized absolute URL.

    This method ensures that redundant slashes are removed between path segments,
    and that the resulting URL is correctly formatted.

    Args:
        base: The base URL.
        *paths: One or more URL fragments to append to the base path.

    Returns:
        A well-formed absolute URL with the joined path components.
    """
    parsed = urlparse(base)
    clean_paths = [p.strip('/') for p in paths]
    base_path = parsed.path if parsed.path != '/' else ''
    base_path = base_path.strip('/')
    joined_path = posixpath.join(base_path, *clean_paths)
    final_path = '/' + joined_path if joined_path else '/'
    return urlunparse(parsed._replace(path=final_path))


def _get_path_from_url(url: str) -> str:
    """Extracts and returns the path component from a full URL.

    Args:
        url: The full URL string.

    Returns:
        The path component of the URL.
    """
    path = urlparse(url).path
    return path if path else '/'


class StarletteBuilder:
    """Builder class for assembling a Starlette application with A2A protocol routes.

    This class enables mounting multiple A2AStarletteRouteBuilder instances under
    specific paths and generates a complete Starlette application that serves them.
    It also collects AgentLinkContext entries and exposes them as an AgentCatalog
    document at the standard path /.well-known/api-catalog.
    """

    def __init__(self) -> None:
        """Initializes an empty A2AStarletteBuilder instance.

        This sets up the internal structure to hold multiple mounted A2A route groups
        and the corresponding AgentLinkContext entries for inclusion in the Agent Catalog.

        Attributes:
            _mounts: A list of Starlette Mount objects representing route groups
              mounted at specific paths.
            _catalog_links: A list of AgentLinkContext instances used to generate
              the Agent Catalog served at /.well-known/api-catalog.
        """
        self._mounts: list[Mount] = []
        self._catalog_links: list[AgentLinkContext] = []

    async def _handle_get_api_catalog(self, request: Request) -> JSONResponse:
        """Handles GET requests for the AgentCatalog endpoint.

        Args:
            request: The incoming Starlette Request object.

        Returns:
            A JSONResponse containing the AgentCatalog data.
        """
        catalog = AgentCatalog(linkset=self._catalog_links)
        return JSONResponse(
            catalog.model_dump(mode='json', exclude_none=True),
            headers={
                'Content-Type': (
                    'application/linkset+json; '
                    'profile="https://www.rfc-editor.org/info/rfc9727"'
                )
            },
        )

    def mount(
        self,
        route_builder: StarletteRouteBuilder,
    ) -> Self:
        """Mounts routes generated by the given builder and adds metadata to the agent catalog.

        Raises:
            ValueError: If a mount for the given path already exists.
        """
        path = _get_path_from_url(route_builder.agent_card.url)
        if any(
            posixpath.normpath(mount.path) == posixpath.normpath(path)
            for mount in self._mounts
        ):
            raise ValueError(f'A mount for path "{path}" already exists.')
        if (
            route_builder.extended_agent_card is not None
            and route_builder.agent_card.url
            != route_builder.extended_agent_card.url
        ):
            raise ValueError(
                'agent_card.url and extended_agent_card.url must be the same '
                'if extended_agent_card is provided'
            )
        routes = route_builder.build()
        self._mounts.append(Mount(path, routes=routes))
        anchor = _join_url(
            route_builder.agent_card.url, route_builder.config.rpc_path
        )
        describedby = [
            AgentLinkTarget(
                href=_join_url(
                    route_builder.agent_card.url,
                    route_builder.config.agent_card_path,
                )
            )
        ]
        if (
            route_builder.extended_agent_card is not None
            and route_builder.agent_card.supports_authenticated_extended_card
        ):
            describedby.append(
                AgentLinkTarget(
                    href=_join_url(
                        route_builder.extended_agent_card.url,
                        route_builder.config.extended_agent_card_path,
                    )
                )
            )
        self._catalog_links.append(
            AgentLinkContext(
                anchor=anchor,
                describedby=describedby,
            )
        )
        return self

    def build(self, **kwargs: Any) -> Starlette:
        """Builds and returns a Starlette application."""
        catalog_route = Route(
            '/.well-known/api-catalog',
            endpoint=self._handle_get_api_catalog,
            methods=['GET'],
            name='api_catalog',
        )
        routes = [*self._mounts, catalog_route]
        if 'routes' in kwargs:
            kwargs['routes'].extend(routes)
        else:
            kwargs['routes'] = routes
        return Starlette(**kwargs)
