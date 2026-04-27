from typing import TYPE_CHECKING, Any

from a2a.server.routes.helpers._proto_schema import (
    REST_BODY_TYPES,
    message_schema,
)
from a2a.server.routes.helpers.jsonrpc import (
    DESCRIPTION as _JSONRPC_DESCRIPTION,
)
from a2a.server.routes.helpers.jsonrpc import (
    envelope_schema as _jsonrpc_envelope_schema,
)
from a2a.utils.constants import PROTOCOL_VERSION_1_0, VERSION_HEADER


if TYPE_CHECKING:
    from fastapi import FastAPI
    from fastapi.routing import APIRoute as _A2ARoute
    from starlette.routing import BaseRoute, Route

    _package_fastapi_installed = True
else:
    try:
        from fastapi.routing import APIRoute
        from starlette.routing import Route, request_response

        class _A2ARoute(APIRoute):
            """APIRoute that uses Starlette's request_response to bypass FastAPI middleware scope requirements."""

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self.app = request_response(self.endpoint)

        _package_fastapi_installed = True
    except ImportError:
        Route = Any
        _A2ARoute = Any

        _package_fastapi_installed = False


_AGENT_CARD_TAG = 'A2A: Agent Card'
_JSONRPC_TAG = 'A2A: JSON-RPC'
_REST_TAG = 'A2A: REST'

_A2A_VERSION_HEADER = {
    'in': 'header',
    'name': VERSION_HEADER,
    'required': True,
    'schema': {'type': 'string', 'enum': [PROTOCOL_VERSION_1_0]},
    'example': PROTOCOL_VERSION_1_0,
}


def _request_body_extra(
    ref: dict[str, Any], description: str
) -> dict[str, Any]:
    return {
        'requestBody': {
            'description': description,
            'required': True,
            'content': {'application/json': {'schema': ref}},
        },
    }


def _rest_body_extra(
    route: 'Route', rest_bodies: dict[tuple[str, str], dict[str, Any]]
) -> dict[str, Any] | None:
    methods = route.methods or set()
    for (suffix, method), extra in rest_bodies.items():
        if method in methods and route.path.endswith(suffix):
            return extra
    return None


def _attach_route(
    app: 'FastAPI',
    route: 'BaseRoute',
    tag: str,
    openapi_extra: dict[str, Any] | None,
    require_version_header: bool = False,
) -> None:
    if not (isinstance(route, Route) and route.methods):
        app.routes.append(route)
        return
    # Drop HEAD: Starlette adds it alongside GET, but FastAPI registers duplicate operation IDs.
    methods = sorted(m for m in route.methods if m != 'HEAD')
    if require_version_header:
        extra = dict(openapi_extra or {})
        extra.setdefault('parameters', [_A2A_VERSION_HEADER])
        openapi_extra = extra
    app.routes.append(
        _A2ARoute(
            path=route.path,
            endpoint=route.endpoint,
            methods=methods,
            tags=[tag],
            openapi_extra=openapi_extra,
        )
    )


def _install_components(app: 'FastAPI', schemas: dict[str, Any]) -> None:
    original_openapi = app.openapi

    def _openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = original_openapi()
        component_schemas = schema.setdefault('components', {}).setdefault(
            'schemas', {}
        )
        for name, sub_schema in schemas.items():
            component_schemas.setdefault(name, sub_schema)
        return schema

    app.openapi = _openapi  # type: ignore[method-assign]


def add_a2a_routes_to_fastapi(
    app: 'FastAPI',
    *,
    agent_card_routes: 'list[BaseRoute] | None' = None,
    jsonrpc_routes: 'list[BaseRoute] | None' = None,
    rest_routes: 'list[BaseRoute] | None' = None,
) -> None:
    """Mounts A2A routes on a FastAPI app and enriches them for ``/docs``.

    Re-registers Starlette routes as ``APIRoute`` instances so they appear in
    the auto-generated OpenAPI schema, tagged and annotated with proto-derived
    request-body schemas.

    Usage::

        app = FastAPI()
        add_a2a_routes_to_fastapi(
            app,
            agent_card_routes=create_agent_card_routes(agent_card),
            jsonrpc_routes=create_jsonrpc_routes(request_handler, rpc_url='/'),
            rest_routes=create_rest_routes(request_handler),
        )

    Args:
        app: The FastAPI application to mount the routes on.
        agent_card_routes: Routes returned by ``create_agent_card_routes``.
        jsonrpc_routes: Routes returned by ``create_jsonrpc_routes``.
        rest_routes: Routes returned by ``create_rest_routes``.
    """
    if not _package_fastapi_installed:
        raise ImportError(
            'The `fastapi` package is required to use '
            '`add_a2a_routes_to_fastapi`. Install it alongside '
            '`a2a-sdk[http-server]`.'
        )

    components: dict[str, Any] = {}
    jsonrpc_extra = {
        'summary': 'A2A JSON-RPC endpoint',
        'description': _JSONRPC_DESCRIPTION,
        **_request_body_extra(
            _jsonrpc_envelope_schema(components), 'A2A JSON-RPC 2.0 request'
        ),
    }
    rest_extras = {
        key: _request_body_extra(
            message_schema(cls.DESCRIPTOR, components),
            f'A2A {cls.__name__}',
        )
        for key, cls in REST_BODY_TYPES.items()
    }

    for route in agent_card_routes or ():
        _attach_route(app, route, _AGENT_CARD_TAG, openapi_extra=None)

    for route in jsonrpc_routes or ():
        extra = jsonrpc_extra if isinstance(route, Route) else None
        _attach_route(
            app,
            route,
            _JSONRPC_TAG,
            openapi_extra=extra,
            require_version_header=True,
        )

    for route in rest_routes or ():
        extra = (
            _rest_body_extra(route, rest_extras)
            if isinstance(route, Route)
            else None
        )
        _attach_route(
            app,
            route,
            _REST_TAG,
            openapi_extra=extra,
            require_version_header=True,
        )

    _install_components(app, components)
