from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import MutableMapping  # noqa: TC003
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from a2a.client.service_parameters import ServiceParameters  # noqa: TC001


if TYPE_CHECKING:
    from a2a.types.a2a_pb2 import AgentCard


class ClientCallContext(BaseModel):
    """A context passed with each client call, allowing for call-specific.

    configuration and data passing. Such as authentication details or
    request deadlines.
    """

    state: MutableMapping[str, Any] = Field(default_factory=dict)
    timeout: float | None = None
    service_parameters: ServiceParameters | None = None


class ClientCallInterceptor(ABC):
    """An abstract base class for client-side call interceptors.

    Interceptors can inspect and modify requests before they are sent,
    which is ideal for concerns like authentication, logging, or tracing.
    """

    @abstractmethod
    async def before(self, args: BeforeArgs) -> None:
        """Invoked before transport method."""

    @abstractmethod
    async def after(self, args: AfterArgs) -> None:
        """Invoked after transport method."""


@dataclass
class MethodInput:
    """Represents the method and its associated input arguments payload."""

    method: str
    value: Any


@dataclass
class MethodResult:
    """Represents the method and its associated result payload."""

    method: str
    value: Any


@dataclass
class BeforeArgs:
    """Arguments passed to the interceptor before a method call."""

    input: MethodInput
    agent_card: AgentCard
    context: ClientCallContext | None = None
    early_return: MethodResult | None = None


@dataclass
class AfterArgs:
    """Arguments passed to the interceptor after a method call completes."""

    result: MethodResult
    agent_card: AgentCard
    context: ClientCallContext | None = None
    early_return: bool = False
