from typing import TYPE_CHECKING, Any

from a2a.types import AgentCard


if TYPE_CHECKING:
    from starlette.datastructures import URL
    from starlette.requests import Request

    _package_starlette_installed = True
else:
    try:
        from starlette.datastructures import URL
        from starlette.requests import Request

        _package_starlette_installed = True
    except ImportError:
        _package_starlette_installed = False
        URL = Any
        Request = Any


def update_card_rpc_url_from_request(
    agent_card: AgentCard, request: Request
) -> None:
    """Modifies Agent's RPC URL based on the AgentCard request.

    Args:
        agent_card (AgentCard): Original AgentCard
        request (Request): AgentCard request
    """
    rpc_url = URL(agent_card.url)
    rpc_path = rpc_url.path
    port = None
    if 'X-Forwarded-Host' in request.headers:
        host = request.headers['X-Forwarded-Host']
    else:
        host = request.url.hostname or rpc_url.hostname or 'localhost'
        port = request.url.port

    if 'X-Forwarded-Proto' in request.headers:
        scheme = request.headers['X-Forwarded-Proto']
        port = None
    else:
        scheme = request.url.scheme
    if not scheme:
        scheme = 'http'
    if ':' in host:  # type: ignore
        comps = host.rsplit(':', 1)  # type: ignore
        host = comps[0]
        port = int(comps[1]) if comps[1] else port

    # Handle URL maps,
    # e.g. "agents/my-agent/.well-known/agent-card.json"
    if 'X-Forwarded-Path' in request.headers:
        forwarded_path = request.headers['X-Forwarded-Path'].strip()
        if (
            forwarded_path
            and request.url.path != forwarded_path
            and forwarded_path.endswith(request.url.path)
        ):
            # "agents/my-agent" for "agents/my-agent/.well-known/agent-card.json"
            extra_path = forwarded_path[: -len(request.url.path)]
            new_path = extra_path + rpc_path
            # If original path was just "/",
            # we remove trailing "/" in the extended one
            if len(new_path) > 1 and rpc_path == '/':
                new_path = new_path.rstrip('/')
            rpc_path = new_path

    agent_card.url = str(
        rpc_url.replace(hostname=host, port=port, scheme=scheme, path=rpc_path)
    )
