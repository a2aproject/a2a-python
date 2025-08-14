import logging

from collections.abc import Callable
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI, Request, Response
    from fastapi.responses import JSONResponse

    _package_fastapi_installed = True
else:
    try:
        from fastapi import APIRouter, FastAPI, Request, Response
        from fastapi.responses import JSONResponse

        _package_fastapi_installed = True
    except ImportError:
        APIRouter = Any
        FastAPI = Any
        Request = Any
        Response = Any

        _package_fastapi_installed = False


from a2a.server.apps.jsonrpc.jsonrpc_app import CallContextBuilder
from a2a.server.apps.rest.rest_adapter import RESTAdapter
from a2a.server.context import ServerCallContext
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
            card_modifier: An optional callback to dynamically modify the public
              agent card before it is served.
            extended_card_modifier: An optional callback to dynamically modify
              the extended agent card before it is served. It receives the
              call context.
        """
        if not _package_fastapi_installed:
            raise ImportError(
                'The `fastapi` package is required to use the'
                ' `A2ARESTFastAPIApplication`. It can be added as a part of'
                ' `a2a-sdk` optional dependencies, `a2a-sdk[http-server]`.'
            )
        self._adapter = RESTAdapter(
            agent_card=agent_card,
            http_handler=http_handler,
            extended_agent_card=extended_agent_card,
            context_builder=context_builder,
            card_modifier=card_modifier,
            extended_card_modifier=extended_card_modifier,
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
            card = await self._adapter.handle_get_agent_card(request)
            return JSONResponse(card)

        app.include_router(router)
        return app
