import logging

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from starlette.middleware import Middleware
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route
else:
    try:
        from starlette.middleware import Middleware
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Route

    except ImportError:
        Middleware = Any
        Route = Any
        Request = Any
        Response = Any
        JSONResponse = Any

from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.types.a2a_pb2 import AgentCard
from a2a.utils.helpers import maybe_await


logger = logging.getLogger(__name__)


class AgentCardRoute:
    """Provides the Starlette Route for the A2A protocol agent card endpoint."""

    def __init__(
        self,
        agent_card: AgentCard,
        card_modifier: Callable[[AgentCard], Awaitable[AgentCard] | AgentCard]
        | None = None,
        card_url: str = '/',
        middleware: Sequence['Middleware'] | None = None,
    ) -> None:
        """Initializes the AgentCardRoute.

        Args:
            agent_card: The AgentCard describing the agent's capabilities.
            card_modifier: An optional callback to dynamically modify the public
              agent card before it is served.
            card_url: The URL for the agent card endpoint.
        """
        self.agent_card = agent_card
        self.card_modifier = card_modifier

        async def get_agent_card(request: Request) -> Response:
            card_to_serve = self.agent_card
            if self.card_modifier:
                card_to_serve = await maybe_await(
                    self.card_modifier(card_to_serve)
                )
            return JSONResponse(agent_card_to_dict(card_to_serve))

        self.route = Route(
            path=card_url,
            endpoint=get_agent_card,
            methods=['GET'],
            middleware=middleware,
        )
