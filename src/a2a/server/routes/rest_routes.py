import logging

from typing import TYPE_CHECKING, Any

from a2a.compat.v0_3.rest_adapter import REST03Adapter
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes.common import ServerCallContextBuilder
from a2a.server.routes.rest_dispatcher import RestDispatcher


if TYPE_CHECKING:
    from starlette.routing import BaseRoute, Mount, Route

    _package_starlette_installed = True
else:
    try:
        from starlette.routing import BaseRoute, Mount, Route

        _package_starlette_installed = True
    except ImportError:
        Route = Any
        Mount = Any
        BaseRoute = Any

        _package_starlette_installed = False

logger = logging.getLogger(__name__)


def create_rest_routes(
    request_handler: RequestHandler,
    context_builder: ServerCallContextBuilder | None = None,
    enable_v0_3_compat: bool = False,
    path_prefix: str = '',
    shutdown_grace_period: float = 0,
) -> list['BaseRoute']:
    """Creates the Starlette Routes for the A2A protocol REST endpoint.

    Args:
        request_handler: The handler instance responsible for processing A2A
          requests via http.
        context_builder: The ServerCallContextBuilder used to construct the
          ServerCallContext passed to the request_handler. If None the
          DefaultServerCallContextBuilder is used.
        enable_v0_3_compat: If True, mounts backward-compatible v0.3 protocol
          endpoints using REST03Adapter.
        path_prefix: The URL prefix for the REST endpoints.
        shutdown_grace_period: Seconds to allow active SSE streams to finish
          before force-cancellation during shutdown. This value should be less
          than the ASGI server's graceful shutdown timeout. Defaults to 0,
          matching the ``sse-starlette`` default behavior.
    """
    if not _package_starlette_installed:
        raise ImportError(
            'Packages `starlette` and `sse-starlette` are required to use'
            ' the `create_rest_routes`. They can be added as a part of `a2a-sdk` '
            'optional dependencies, `a2a-sdk[http-server]`.'
        )

    dispatcher = RestDispatcher(
        request_handler=request_handler,
        context_builder=context_builder,
        shutdown_grace_period=shutdown_grace_period,
    )

    routes: list[BaseRoute] = []
    if enable_v0_3_compat:
        v03_adapter = REST03Adapter(
            http_handler=request_handler,
            context_builder=context_builder,
        )
        v03_routes = v03_adapter.routes()
        for (path, method), endpoint in v03_routes.items():
            routes.append(
                Route(
                    path=f'{path_prefix}{path}',
                    endpoint=endpoint,
                    methods=[method],
                )
            )

    base_routes = {
        ('/message:send', 'POST'): dispatcher.on_message_send,
        ('/message:stream', 'POST'): dispatcher.on_message_send_stream,
        ('/tasks/{id}:cancel', 'POST'): dispatcher.on_cancel_task,
        ('/tasks/{id}:subscribe', 'GET'): dispatcher.on_subscribe_to_task,
        ('/tasks/{id}:subscribe', 'POST'): dispatcher.on_subscribe_to_task,
        ('/tasks/{id}', 'GET'): dispatcher.on_get_task,
        (
            '/tasks/{id}/pushNotificationConfigs/{push_id}',
            'GET',
        ): dispatcher.get_push_notification,
        (
            '/tasks/{id}/pushNotificationConfigs/{push_id}',
            'DELETE',
        ): dispatcher.delete_push_notification,
        (
            '/tasks/{id}/pushNotificationConfigs',
            'POST',
        ): dispatcher.set_push_notification,
        (
            '/tasks/{id}/pushNotificationConfigs',
            'GET',
        ): dispatcher.list_push_notifications,
        ('/tasks', 'GET'): dispatcher.list_tasks,
        (
            '/extendedAgentCard',
            'GET',
        ): dispatcher.handle_authenticated_agent_card,
    }

    base_route_objects = []
    for (path, method), endpoint in base_routes.items():
        base_route_objects.append(
            Route(
                path=f'{path_prefix}{path}',
                endpoint=endpoint,
                methods=[method],
            )
        )
    routes.extend(base_route_objects)
    routes.append(Mount(path='/{tenant}', routes=base_route_objects))

    return routes
