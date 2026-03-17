import logging

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI

    _package_fastapi_installed = True
else:
    try:
        from fastapi import APIRouter, FastAPI

        _package_fastapi_installed = True
    except ImportError:
        APIRouter = Any
        FastAPI = Any

        _package_fastapi_installed = False


from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.router.jsonrpc_dispatcher import (
    CallContextBuilder,
    JsonRpcDispatcher,
)
from a2a.types.a2a_pb2 import AgentCard
from a2a.utils.constants import (
    AGENT_CARD_WELL_KNOWN_PATH,
    DEFAULT_RPC_URL,
)


logger = logging.getLogger(__name__)


class JsonRpcRouter:
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
        card_modifier: Callable[[AgentCard], Awaitable[AgentCard] | AgentCard]
        | None = None,
        extended_card_modifier: Callable[
            [AgentCard, ServerCallContext], Awaitable[AgentCard] | AgentCard
        ]
        | None = None,
        max_content_length: int | None = 10 * 1024 * 1024,  # 10MB
        enable_v0_3_compat: bool = False,
        agent_card_url: str = AGENT_CARD_WELL_KNOWN_PATH,
        rpc_url: str = '',
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
            enable_v0_3_compat: Whether to enable v0.3 backward compatibility on the same endpoint.
        """
        if not _package_fastapi_installed:
            raise ImportError(
                'The `fastapi` package is required to use the `A2AFastAPIApplication`.'
                ' It can be added as a part of `a2a-sdk` optional dependencies,'
                ' `a2a-sdk[http-server]`.'
            )
        self.dispatcher = JsonRpcDispatcher(
            agent_card=agent_card,
            http_handler=http_handler,
            extended_agent_card=extended_agent_card,
            context_builder=context_builder,
            card_modifier=card_modifier,
            extended_card_modifier=extended_card_modifier,
            max_content_length=max_content_length,
            enable_v0_3_compat=enable_v0_3_compat,
        )
        self.router = APIRouter()
        self._setup_router(agent_card_url, rpc_url)

    def _setup_router(
        self,
        agent_card_url: str,
        rpc_url: str,
    ) -> None:
        """Configures the APIRouter with the A2A endpoints.

        Args:
            agent_card_url: The URL for the agent card endpoint.
            rpc_url: The URL for the A2A JSON-RPC endpoint.
        """
        self.router.post(
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
        )(self.dispatcher._handle_requests)
