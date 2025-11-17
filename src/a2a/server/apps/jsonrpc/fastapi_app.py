import logging

from collections.abc import Callable
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from fastapi import FastAPI

    _package_fastapi_installed = True
else:
    try:
        from fastapi import FastAPI

        _package_fastapi_installed = True
    except ImportError:
        FastAPI = Any

        _package_fastapi_installed = False

from a2a.server.apps.jsonrpc.jsonrpc_app import (
    CallContextBuilder,
    JSONRPCApplication,
)
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.jsonrpc_handler import RequestHandler
from a2a.types import A2ARequest, AgentCard
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    DEFAULT_RPC_URL,
    EXTENDED_AGENT_CARD_PATH,
    PREV_AGENT_CARD_WELL_KNOWN_PATH,
)


logger = logging.getLogger(__name__)


class A2AFastAPI(FastAPI):
    """A FastAPI application that adds A2A-specific OpenAPI components."""

    _a2a_components_added: bool = False

    def openapi(self) -> dict[str, Any]:
        """Generates the OpenAPI schema for the application."""
        openapi_schema = super().openapi()
        if not self._a2a_components_added:
            a2a_request_schema = A2ARequest.model_json_schema(
                ref_template='#/components/schemas/{model}'
            )
            defs = a2a_request_schema.pop('$defs', {})
            component_schemas = openapi_schema.setdefault(
                'components', {}
            ).setdefault('schemas', {})
            component_schemas.update(defs)
            component_schemas['A2ARequest'] = a2a_request_schema
            self._a2a_components_added = True
        return openapi_schema


class A2AFastAPIApplication(JSONRPCApplication):
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
        card_modifier: Callable[[AgentCard], AgentCard] | None = None,
        extended_card_modifier: Callable[
            [AgentCard, ServerCallContext], AgentCard
        ]
        | None = None,
        max_content_length: int | None = 10 * 1024 * 1024,  # 10MB
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
            card_modifier: An optional callback to dynamically modify the public
              agent card before it is served.
            extended_card_modifier: An optional callback to dynamically modify
              the extended agent card before it is served. It receives the
              call context.
            max_content_length: The maximum allowed content length for incoming
              requests. Defaults to 10MB. Set to None for unbounded maximum.
        """
        if not _package_fastapi_installed:
            raise ImportError(
                'The `fastapi` package is required to use the `A2AFastAPIApplication`.'
                ' It can be added as a part of `a2a-sdk` optional dependencies,'
                ' `a2a-sdk[http-server]`.'
            )
        super().__init__(
            agent_card=agent_card,
            http_handler=http_handler,
            extended_agent_card=extended_agent_card,
            context_builder=context_builder,
            card_modifier=card_modifier,
            extended_card_modifier=extended_card_modifier,
            max_content_length=max_content_length,
        )

    def add_routes_to_app(
        self,
        app: FastAPI,
        agent_card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
        rpc_url: str = DEFAULT_RPC_URL,
        extended_agent_card_url: str = EXTENDED_AGENT_CARD_PATH,
    ) -> None:
        """Adds the routes to the FastAPI application.

        Args:
            app: The FastAPI application to add the routes to.
            agent_card_url: The URL for the agent card endpoint.
            rpc_url: The URL for the A2A JSON-RPC endpoint.
            extended_agent_card_url: The URL for the authenticated extended agent card endpoint.
        """
        app.post(
            rpc_url,
            openapi_extra={
                'requestBody': {
                    'content': {
                        'application/json': {
                            'schema': {
                                '$ref': '#/components/schemas/A2ARequest'
                            }
                        }
                    },
                    'required': True,
                    'description': 'A2ARequest',
                }
            },
        )(self._handle_requests)
        app.get(agent_card_url)(self._handle_get_agent_card)

        if agent_card_url == AGENT_CARD_WELL_KNOWN_PATH:
            # For backward compatibility, serve the agent card at the deprecated path as well.
            # TODO: remove in a future release
            app.get(PREV_AGENT_CARD_WELL_KNOWN_PATH)(
                self._handle_get_agent_card
            )

        if self.agent_card.supports_authenticated_extended_card:
            app.get(extended_agent_card_url)(
                self._handle_get_authenticated_extended_agent_card
            )

    def build(
        self,
        agent_card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
        rpc_url: str = DEFAULT_RPC_URL,
        extended_agent_card_url: str = EXTENDED_AGENT_CARD_PATH,
        **kwargs: Any,
    ) -> FastAPI:
        """Builds and returns the FastAPI application instance.

        Args:
            agent_card_url: The URL for the agent card endpoint.
            rpc_url: The URL for the A2A JSON-RPC endpoint.
            extended_agent_card_url: The URL for the authenticated extended agent card endpoint.
            **kwargs: Additional keyword arguments to pass to the FastAPI constructor.

        Returns:
            A configured FastAPI application instance.
        """
        app = A2AFastAPI(**kwargs)

        self.add_routes_to_app(
            app, agent_card_url, rpc_url, extended_agent_card_url
        )

        return app
