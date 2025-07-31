import logging

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from a2a.server.apps.jsonrpc.jsonrpc_app import (
    JSONRPCApplication,
)
from a2a.types import A2ARequest
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    DEFAULT_RPC_URL,
    EXTENDED_AGENT_CARD_PATH,
    PREV_AGENT_CARD_WELL_KNOWN_PATH,
)


logger = logging.getLogger(__name__)


class A2AFastAPIApplication(JSONRPCApplication):
    """A FastAPI application implementing the A2A protocol server endpoints.

    Handles incoming JSON-RPC requests, routes them to the appropriate
    handler methods, and manages response generation including Server-Sent Events
    (SSE).
    """

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

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            a2a_request_schema = A2ARequest.model_json_schema(
                ref_template='#/components/schemas/{model}'
            )
            defs = a2a_request_schema.pop('$defs', {})
            openapi_schema = app.openapi()
            component_schemas = openapi_schema.setdefault(
                'components', {}
            ).setdefault('schemas', {})
            component_schemas.update(defs)
            component_schemas['A2ARequest'] = a2a_request_schema

            yield

        app = FastAPI(lifespan=lifespan, **kwargs)

        self.add_routes_to_app(
            app, agent_card_url, rpc_url, extended_agent_card_url
        )

        return app
