from collections.abc import Callable

from starlette.requests import Request

from a2a.auth.user import UnauthenticatedUser, User
from a2a.extensions.common import (
    HTTP_EXTENSION_HEADER,
    get_requested_extensions,
)
from a2a.server.context import ServerCallContext


UserBuilder = Callable[[Request], User]


def default_user_builder(request: Request) -> User:
    """Default strategy for creating an A2AUser from a Starlette Request."""
    if 'user' in request.scope:

        class BaseUser(User):
            @property
            def is_authenticated(self) -> bool:
                return request.user.is_authenticated

            @property
            def user_name(self) -> str:
                return request.user.display_name

        return BaseUser()
    return UnauthenticatedUser()


def build_server_call_context(
    request: Request, user_builder: UserBuilder
) -> ServerCallContext:
    """Builds a ServerCallContext from a Starlette Request.

    Args:
        request: The incoming Starlette Request object.
        user_builder: Optional custom user builder.

    Returns:
        A ServerCallContext instance populated with user and state.
    """
    user = user_builder(request)

    state = {}
    if 'auth' in request.scope:
        state['auth'] = request.auth
    state['headers'] = dict(request.headers)

    return ServerCallContext(
        user=user,
        state=state,
        requested_extensions=get_requested_extensions(
            request.headers.getlist(HTTP_EXTENSION_HEADER)
        ),
    )
