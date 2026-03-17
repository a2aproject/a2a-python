import importlib.resources
import json
import logging

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from fastapi import APIRouter, FastAPI, Request, Response
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    _package_fastapi_installed = True
else:
    try:
        from fastapi import APIRouter, FastAPI, Request, Response
        from fastapi.responses import JSONResponse
        from starlette.exceptions import HTTPException as StarletteHTTPException

        _package_fastapi_installed = True
    except ImportError:
        APIRouter = Any
        FastAPI = Any
        Request = Any
        Response = Any
        StarletteHTTPException = Any

        _package_fastapi_installed = False


from a2a.compat.v0_3.rest_adapter import REST03Adapter
from a2a.server.apps.jsonrpc.jsonrpc_app import CallContextBuilder
from a2a.server.apps.rest.rest_adapter import RESTAdapter
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.types.a2a_pb2 import AgentCard
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH


logger = logging.getLogger(__name__)


_HTTP_TO_GRPC_STATUS_MAP = {
    400: 'INVALID_ARGUMENT',
    401: 'UNAUTHENTICATED',
    403: 'PERMISSION_DENIED',
    404: 'NOT_FOUND',
    405: 'UNIMPLEMENTED',
    409: 'ALREADY_EXISTS',
    415: 'INVALID_ARGUMENT',
    422: 'INVALID_ARGUMENT',
    500: 'INTERNAL',
    501: 'UNIMPLEMENTED',
    502: 'INTERNAL',
    503: 'UNAVAILABLE',
    504: 'DEADLINE_EXCEEDED',
}


class A2AFastAPI(FastAPI):
    """A FastAPI application that adds A2A-specific OpenAPI components."""

    _a2a_components_added: bool = False
    rpc_url: str = ''

    def openapi(self) -> dict[str, Any]:
        """Generates the OpenAPI schema for the application."""
        if self.openapi_schema:
            return self.openapi_schema

        # Try to use the a2a.json schema generated from the proto file
        # if available, instead of generating one from the python types.
        try:
            from a2a import types  # noqa: PLC0415

            schema_file = importlib.resources.files(types).joinpath('a2a.json')
            if schema_file.is_file():
                self.openapi_schema = json.loads(
                    schema_file.read_text(encoding='utf-8')
                )
                if self.rpc_url and self.openapi_schema:
                    paths = self.openapi_schema.get('paths', {})
                    self.openapi_schema['paths'] = {
                        f'{self.rpc_url}{k}': v for k, v in paths.items()
                    }
                if self.openapi_schema:
                    return self.openapi_schema
        except Exception:  # noqa: BLE001
            logger.warning(
                "Could not load 'a2a.json' from 'a2a.types'. Falling back to auto-generation."
            )

        openapi_schema = super().openapi()
        if not self._a2a_components_added:
            self._a2a_components_added = True
        return openapi_schema


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
        card_modifier: Callable[[AgentCard], Awaitable[AgentCard] | AgentCard]
        | None = None,
        extended_card_modifier: Callable[
            [AgentCard, ServerCallContext], Awaitable[AgentCard] | AgentCard
        ]
        | None = None,
        enable_v0_3_compat: bool = False,
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
            enable_v0_3_compat: If True, mounts backward-compatible v0.3 protocol
              endpoints under the '/v0.3' path prefix using REST03Adapter.
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
        self.enable_v0_3_compat = enable_v0_3_compat
        self._v03_adapter = None

        if self.enable_v0_3_compat:
            self._v03_adapter = REST03Adapter(
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
            rpc_url: The URL for the A2A REST endpoint base path.
            **kwargs: Additional keyword arguments to pass to the FastAPI constructor.

        Returns:
            A configured FastAPI application instance.
        """
        app = A2AFastAPI(**kwargs)
        app.rpc_url = rpc_url

        @app.exception_handler(StarletteHTTPException)
        async def http_exception_handler(
            request: Request, exc: StarletteHTTPException
        ) -> Response:
            """Catches framework-level HTTP exceptions.

            For example, 404 Not Found for bad routes, 422 Unprocessable Entity
            for schema validation, and formats them into the A2A standard
            google.rpc.Status JSON format (AIP-193).
            """
            grpc_status = _HTTP_TO_GRPC_STATUS_MAP.get(
                exc.status_code, 'UNKNOWN'
            )
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    'error': {
                        'code': exc.status_code,
                        'status': grpc_status,
                        'message': str(exc.detail)
                        if hasattr(exc, 'detail')
                        else 'HTTP Exception',
                    }
                },
                media_type='application/json',
            )

        if self.enable_v0_3_compat and self._v03_adapter:
            v03_adapter = self._v03_adapter
            v03_router = APIRouter()
            for route, callback in v03_adapter.routes().items():
                v03_router.add_api_route(
                    f'{rpc_url}{route[0]}', callback, methods=[route[1]]
                )
            app.include_router(v03_router)

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
