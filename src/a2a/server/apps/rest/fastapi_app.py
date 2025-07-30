import logging

from typing import Any

from fastapi import APIRouter, FastAPI, Request, Response

from a2a.server.apps.jsonrpc.jsonrpc_app import CallContextBuilder
from a2a.server.apps.rest.rest_adapter import RESTAdapter
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types import AgentCard
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH


logger = logging.getLogger(__name__)


class A2ARESTFastAPIApplication:
    """A FastAPI application implementing the A2A protocol server REST endpoints.

    Handles incoming REST requests, routes them to the appropriate
    handler methods, and manages response generation including Server-Sent Events
    (SSE).
    """

    def __init__(
        self,
        agent_card: AgentCard,
        http_handler: RequestHandler,
        context_builder: CallContextBuilder | None = None,
    ):
        """Initializes the A2ARESTFastAPIApplication.

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
        self._adapter = RESTAdapter(
            agent_card=agent_card,
            http_handler=http_handler,
            context_builder=context_builder,
        )

    def build(
        self,
        agent_card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
        rpc_url: str = '',
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
        app = FastAPI(**kwargs)
        router = APIRouter()
        for route, callback in self._adapter.routes().items():
            router.add_api_route(
                f'{rpc_url}{route[0]}', callback, methods=[route[1]]
            )

        @router.get(f'{rpc_url}{agent_card_url}')
        async def get_agent_card(request: Request) -> Response:
            return await self._adapter.handle_get_agent_card(request)

        app.include_router(router)
        return app
