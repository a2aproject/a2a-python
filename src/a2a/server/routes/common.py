from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from starlette.authentication import BaseUser
    from starlette.requests import Request
else:
    try:
        from starlette.authentication import BaseUser
        from starlette.requests import Request
    except ImportError:
        Request = Any
        BaseUser = Any

from a2a.auth.user import UnauthenticatedUser, User
from a2a.extensions.common import (
    HTTP_EXTENSION_HEADER,
    get_requested_extensions,
)
from a2a.server.context import ServerCallContext


class StarletteUser(User):
    """Adapts a Starlette BaseUser to the A2A User interface."""

    def __init__(self, user: BaseUser):
        self._user = user

    @property
    def is_authenticated(self) -> bool:
        """Returns whether the current user is authenticated."""
        return self._user.is_authenticated

    @property
    def user_name(self) -> str:
        """Returns the user name of the current user."""
        return self._user.display_name


class ServerCallContextBuilder(ABC):
    """A class for building ServerCallContexts using the Starlette Request."""

    @abstractmethod
    def build(self, request: Request) -> ServerCallContext:
        """Builds a ServerCallContext from a Starlette Request."""


class DefaultServerCallContextBuilder(ServerCallContextBuilder):
    """A default implementation of ServerCallContextBuilder."""

    def build(self, request: Request) -> ServerCallContext:
        """Builds a ServerCallContext from a Starlette Request.

        Args:
            request: The incoming Starlette Request object.

        Returns:
            A ServerCallContext instance populated with user and state
            information from the request.
        """
        state = {}
        if 'auth' in request.scope:
            state['auth'] = request.auth
        state['headers'] = dict(request.headers)
        return ServerCallContext(
            user=self.build_user(request),
            state=state,
            requested_extensions=get_requested_extensions(
                request.headers.getlist(HTTP_EXTENSION_HEADER)
            ),
        )

    def build_user(self, request: Request) -> User:
        """Builds a User from a Starlette Request.

        Args:
            request: The incoming Starlette Request object.

        Returns:
            A User instance populated with user information from the request.
        """
        if 'user' in request.scope:
            return StarletteUser(request.user)
        return UnauthenticatedUser()
