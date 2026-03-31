from abc import ABC, abstractmethod

from starlette.authentication import BaseUser
from starlette.requests import Request

from a2a.auth.user import UnauthenticatedUser
from a2a.auth.user import User as A2AUser
from a2a.extensions.common import (
    HTTP_EXTENSION_HEADER,
    get_requested_extensions,
)
from a2a.server.context import ServerCallContext


class StarletteUserProxy(A2AUser):
    """Adapts the Starlette User class to the A2A user representation."""

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


class CallContextBuilder(ABC):
    """A class for building ServerCallContexts using the Starlette Request."""

    @abstractmethod
    def build(self, request: Request) -> ServerCallContext:
        """Builds a ServerCallContext from a Starlette Request."""


class DefaultCallContextBuilder(CallContextBuilder):
    """A default implementation of CallContextBuilder."""

    def build(self, request: Request) -> ServerCallContext:
        """Builds a ServerCallContext from a Starlette Request.

        Args:
            request: The incoming Starlette Request object.

        Returns:
            A ServerCallContext instance populated with user and state
            information from the request.
        """
        user: A2AUser = UnauthenticatedUser()
        state = {}
        if 'user' in request.scope:
            user = StarletteUserProxy(request.user)
            state['auth'] = request.auth
        state['headers'] = dict(request.headers)
        return ServerCallContext(
            user=user,
            state=state,
            requested_extensions=get_requested_extensions(
                request.headers.getlist(HTTP_EXTENSION_HEADER)
            ),
        )
