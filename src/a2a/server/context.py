"""Defines the ServerCallContext class."""

import collections.abc
import typing

from pydantic import BaseModel, ConfigDict, Field

from a2a.auth.user import UnauthenticatedUser, User


State = collections.abc.MutableMapping[str, typing.Any]


class ServerCallContext(BaseModel):
    """A context passed when calling a server method.

    This class allows storing arbitrary user data in the state attribute.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    state: State = Field(default={})
    user: User = Field(default=UnauthenticatedUser())
    requested_extensions: set[str] = Field(default_factory=set)
    activated_extensions: set[str] = Field(default_factory=set)
