import hashlib
import json

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


def _etag_for(card_dict: dict[str, Any]) -> str:
    """A weak ETag derived from the card being served.

    Hashed rather than taken from `version`, because `card_modifier` can
    return a different card per request without touching `version`.

    `signatures` is excluded and the tag is weak, which is what makes
    revalidation work for a card signed per request. The signature covers
    the card with `signatures` stripped, so it carries no information the
    rest of the card does not -- but with a randomized algorithm such as
    ES256 it differs on every signing, and a strong tag over it would change
    on every request and never once match. A weak tag claims only semantic
    equivalence, which two signings of identical content have.

    The cost is that a 304 leaves the client on the signature it already
    holds. That signature stays valid, since it covers content that has not
    changed.

    `sort_keys` rather than RFC 8785 canonicalization: an ETag is opaque and
    is only ever compared against one this server produced, so determinism
    is the whole requirement, and JCS would add a depth limit and a failure
    mode for no gain.
    """
    unsigned = {k: v for k, v in card_dict.items() if k != 'signatures'}
    body = json.dumps(unsigned, sort_keys=True, separators=(',', ':'))
    return f'W/"{hashlib.sha256(body.encode("utf-8")).hexdigest()}"'


def _if_none_match_hits(header: str, etag: str) -> bool:
    """Whether an If-None-Match header selects this entity (RFC 9110 8.2.2)."""
    candidates = [candidate.strip() for candidate in header.split(',')]
    if '*' in candidates:
        return True
    # If-None-Match uses the weak comparison function, so W/"x" and "x" are
    # the same entity on both sides of the comparison.
    return any(
        candidate.removeprefix('W/') == etag.removeprefix('W/')
        for candidate in candidates
    )


def create_agent_card_routes(
    agent_card: AgentCard,
    card_modifier: Callable[[AgentCard], Awaitable[AgentCard]] | None = None,
    card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
    cache_control: str | None = None,
) -> list['Route']:
    """Creates the Starlette Route for the A2A protocol agent card endpoint.

    Args:
        agent_card: The `AgentCard` to serve.
        card_modifier: Optional callback to adjust the card per request.
        card_url: Path the card is served from.
        cache_control: Value for the `Cache-Control` response header, e.g.
          ``'public, max-age=3600'``. A `max-age` has to suit the agent's
          expected update frequency, which only the deployment knows, so
          there is no default. The `ETag` is sent either way, and
          revalidating against it costs one 304.
    """
    if not _package_starlette_installed:
        raise ImportError(
            'The `starlette` package is required to use `create_agent_card_routes`. '
            'It can be installed as part of `a2a-sdk` optional dependencies, `a2a-sdk[http-server]`.'
        )

    async def _get_agent_card(request: Request) -> Response:
        """Returns the public AgentCard describing this agent's capabilities, supported transports, and skills."""
        card_to_serve = agent_card
        if card_modifier:
            card_to_serve = await card_modifier(card_to_serve)

        card_dict = agent_card_to_dict(card_to_serve)
        headers = {'ETag': _etag_for(card_dict)}
        if cache_control:
            headers['Cache-Control'] = cache_control

        if_none_match = request.headers.get('if-none-match')
        if if_none_match and _if_none_match_hits(
            if_none_match, headers['ETag']
        ):
            return Response(status_code=304, headers=headers)

        return JSONResponse(card_dict, headers=headers)

    return [
        Route(
            path=card_url,
            endpoint=_get_agent_card,
            methods=['GET'],
        )
    ]
