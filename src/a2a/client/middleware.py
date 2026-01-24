from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import MutableMapping  # noqa: TC003
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field


if TYPE_CHECKING:
    from a2a.types.a2a_pb2 import AgentCard


class ClientCallContext(BaseModel):
    """A context passed with each client call, allowing for call-specific.

    configuration and data passing. Such as authentication details or
    request deadlines.
    """

    state: MutableMapping[str, Any] = Field(default_factory=dict)


class ClientCallInterceptor(ABC):
    """An abstract base class for client-side call interceptors.

    Interceptors can inspect and modify requests before they are sent,
    which is ideal for concerns like authentication, logging, or tracing.
    """

    @abstractmethod
    async def intercept(
        self,
        method_name: str,
        request_payload: dict[str, Any],
        http_kwargs: dict[str, Any],
        agent_card: AgentCard | None,
        context: ClientCallContext | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """
        Intercepts a client call before the request is sent.

        Args:
            method_name: The name of the RPC method (e.g., 'message/send').
            request_payload: The JSON RPC request payload dictionary.
            http_kwargs: The keyword arguments for the httpx request.
            agent_card: The AgentCard associated with the client.
            context: The ClientCallContext for this specific call.

        Returns:
            A tuple containing the (potentially modified) request_payload
            and http_kwargs.
        """
