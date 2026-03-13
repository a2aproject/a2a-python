from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from a2a.client.client import ClientCallContext

from a2a.types.a2a_pb2 import (  # noqa: TC001
    AgentCard,
)


@dataclass
class BeforeArgs:
    """Arguments passed to the interceptor before a method call."""

    input: Any
    method: str
    agent_card: AgentCard
    context: ClientCallContext | None = None
    early_return: Any | None = None


@dataclass
class AfterArgs:
    """Arguments passed to the interceptor after a method call completes."""

    result: Any
    method: str
    agent_card: AgentCard
    context: ClientCallContext | None = None
    early_return: bool = False


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
