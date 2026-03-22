from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    _package_starlette_installed = True
else:
    try:
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        from starlette.routing import Route

        _package_starlette_installed = True
    except ImportError:
        Route = Any
        Request = Any
        Response = Any
        JSONResponse = Any

        _package_starlette_installed = False

from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.types.a2a_pb2 import AgentCard
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH
from a2a.utils.helpers import maybe_await


def create_agent_card_routes(
    agent_card: AgentCard,
    card_modifier: Callable[[AgentCard], Awaitable[AgentCard] | AgentCard]
    | None = None,
    card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
) -> list['Route']:
    """Creates the Starlette Route for the A2A protocol agent card endpoint."""
    if not _package_starlette_installed:
        raise ImportError(
            'The `starlette` package is required to use `create_agent_card_routes`. '
            'It can be installed as part of `a2a-sdk` optional dependencies, `a2a-sdk[http-server]`.'
        )

    async def _get_agent_card(request: Request) -> Response:
        card_to_serve = agent_card
        if card_modifier:
            card_to_serve = await maybe_await(card_modifier(card_to_serve))
        return JSONResponse(agent_card_to_dict(card_to_serve))

    return [
        Route(
            path=card_url,
            endpoint=_get_agent_card,
            methods=['GET'],
        )
    ]
