import logging

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from a2a.compat.v0_3.rest_adapter import REST03Adapter
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers.request_handler import RequestHandler
from a2a.server.routes.common import ContextBuilder
from a2a.server.routes.rest_dispatcher import RestDispatcher
from a2a.types.a2a_pb2 import (
    AgentCard,
)


if TYPE_CHECKING:
    from sse_starlette.sse import EventSourceResponse
    from starlette.requests import Request
    from starlette.responses import JSONResponse, Response
    from starlette.routing import BaseRoute, Mount, Route

    _package_starlette_installed = True
else:
    try:
        from sse_starlette.sse import EventSourceResponse
        from starlette.requests import Request
        from starlette.responses import JSONResponse, Response
        from starlette.routing import BaseRoute, Mount, Route

        _package_starlette_installed = True
    except ImportError:
        EventSourceResponse = Any
        Request = Any
        JSONResponse = Any
        Response = Any
        Route = Any
        Mount = Any
        BaseRoute = Any

        _package_starlette_installed = False

logger = logging.getLogger(__name__)


def create_rest_routes(  # noqa: PLR0913
    agent_card: AgentCard,
    request_handler: RequestHandler,
    extended_agent_card: AgentCard | None = None,
   context_builder: ContextBuilder | None = None,
    card_modifier: Callable[[AgentCard], Awaitable[AgentCard] | AgentCard]
    | None = None,
    extended_card_modifier: Callable[
        [AgentCard, ServerCallContext], Awaitable[AgentCard] | AgentCard
    ]
    | None = None,
    enable_v0_3_compat: bool = False,
    path_prefix: str = '',
) -> list['BaseRoute']:
    """Creates the Starlette Routes for the A2A protocol REST endpoint.

    Args:
        agent_card: The AgentCard describing the agent's capabilities.
        request_handler: The handler instance responsible for processing A2A
          requests via http.
        extended_agent_card: An optional, distinct AgentCard to be served
          at the authenticated extended card endpoint.
       context_builder: Optional custom user builder to extract user from the
          request.
        card_modifier: An optional callback to dynamically modify the public
          agent card before it is served.
        extended_card_modifier: An optional callback to dynamically modify
          the extended agent card before it is served. It receives the
          call context.
        enable_v0_3_compat: If True, mounts backward-compatible v0.3 protocol
          endpoints using REST03Adapter.
        path_prefix: The URL prefix for the REST endpoints.
    """
    if not _package_starlette_installed:
        raise ImportError(
            'Packages `starlette` and `sse-starlette` are required to use'
            ' the `create_rest_routes`. They can be added as a part of `a2a-sdk` '
            'optional dependencies, `a2a-sdk[http-server]`.'
        )

    dispatcher = RestDispatcher(
        agent_card=agent_card,
        request_handler=request_handler,
        extended_agent_card=extended_agent_card,
       context_builder=user_builder,
        card_modifier=card_modifier,
        extended_card_modifier=extended_card_modifier,
    )

    routes: list[BaseRoute] = []
    if enable_v0_3_compat:
        v03_adapter = REST03Adapter(
            agent_card=agent_card,
            http_handler=request_handler,
            extended_agent_card=extended_agent_card,
           context_builder=user_builder,
            card_modifier=card_modifier,
            extended_card_modifier=extended_card_modifier,
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
