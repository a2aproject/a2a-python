import dataclasses
import logging

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable, MutableMapping
from types import TracebackType
from typing import Any

import httpx

from pydantic import BaseModel, Field
from typing_extensions import Self

from a2a.client.interceptors import ClientCallInterceptor
from a2a.client.optionals import Channel
from a2a.client.service_parameters import ServiceParameters
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    SendMessageRequest,
    StreamResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)


logger = logging.getLogger(__name__)


@dataclasses.dataclass
class ClientConfig:
    """Configuration class for the A2AClient Factory."""

    streaming: bool = True
    """Whether client supports streaming"""

    polling: bool = False
    """Whether client prefers to poll for updates from message:send. It is
    the callers job to check if the response is completed and if not run a
    polling loop."""

    httpx_client: httpx.AsyncClient | None = None
    """Http client to use to connect to agent."""

    grpc_channel_factory: Callable[[str], Channel] | None = None
    """Generates a grpc connection channel for a given url."""

    supported_protocol_bindings: list[str] = dataclasses.field(
        default_factory=list
    )
    """Ordered list of transports for connecting to agent
       (in order of preference). Empty implies JSONRPC only.

       This is a string type to allow custom
       transports to exist in closed ecosystems.
    """

    use_client_preference: bool = False
    """Whether to use client transport preferences over server preferences.
       Recommended to use server preferences in most situations."""

    accepted_output_modes: list[str] = dataclasses.field(default_factory=list)
    """The set of accepted output modes for the client."""

    push_notification_config: TaskPushNotificationConfig | None = None
    """Push notification configuration to use for every request."""


class ClientCallContext(BaseModel):
    """A context passed with each client call, allowing for call-specific.

    configuration and data passing. Such as authentication details or
    request deadlines.
    """

    state: MutableMapping[str, Any] = Field(default_factory=dict)
    timeout: float | None = None
    service_parameters: ServiceParameters | None = None


class Client(ABC):
    """Abstract base class defining the interface for an A2A client.

    This class provides a standard set of methods for interacting with an A2A
    agent, regardless of the underlying transport protocol (e.g., gRPC, JSON-RPC).
    It supports sending messages, managing tasks, and handling event streams.
    """

    def __init__(
        self,
        interceptors: list[ClientCallInterceptor] | None = None,
    ):
        """Initializes the client with interceptors.

        Args:
            interceptors: A list of interceptors to process requests and responses.
        """
        self._interceptors = interceptors or []

    async def __aenter__(self) -> Self:
        """Enters the async context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exits the async context manager and closes the client."""
        await self.close()

    @abstractmethod
    async def send_message(
        self,
        request: SendMessageRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncIterator[StreamResponse]:
        """Sends a message to the server.

        This will automatically use the streaming or non-streaming approach
        as supported by the server and the client config. Client will
        aggregate update events and return an iterator of `StreamResponse`.
        """
        return
        yield

    @abstractmethod
    async def get_task(
        self,
        request: GetTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Retrieves the current state and history of a specific task."""

    @abstractmethod
    async def list_tasks(
        self,
        request: ListTasksRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTasksResponse:
        """Retrieves tasks for an agent."""

    @abstractmethod
    async def cancel_task(
        self,
        request: CancelTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> Task:
        """Requests the agent to cancel a specific task."""

    @abstractmethod
    async def create_task_push_notification_config(
        self,
        request: TaskPushNotificationConfig,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Sets or updates the push notification configuration for a specific task."""

    @abstractmethod
    async def get_task_push_notification_config(
        self,
        request: GetTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> TaskPushNotificationConfig:
        """Retrieves the push notification configuration for a specific task."""

    @abstractmethod
    async def list_task_push_notification_configs(
        self,
        request: ListTaskPushNotificationConfigsRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> ListTaskPushNotificationConfigsResponse:
        """Lists push notification configurations for a specific task."""

    @abstractmethod
    async def delete_task_push_notification_config(
        self,
        request: DeleteTaskPushNotificationConfigRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> None:
        """Deletes the push notification configuration for a specific task."""

    @abstractmethod
    async def subscribe(
        self,
        request: SubscribeToTaskRequest,
        *,
        context: ClientCallContext | None = None,
    ) -> AsyncIterator[StreamResponse]:
        """Resubscribes to a task's event stream."""
        return
        yield

    @abstractmethod
    async def get_extended_agent_card(
        self,
        request: GetExtendedAgentCardRequest,
        *,
        context: ClientCallContext | None = None,
        signature_verifier: Callable[[AgentCard], None] | None = None,
    ) -> AgentCard:
        """Retrieves the agent's card."""

    async def add_interceptor(self, interceptor: ClientCallInterceptor) -> None:
        """Attaches additional interceptors to the `Client`."""
        self._interceptors.append(interceptor)

    @abstractmethod
    async def close(self) -> None:
        """Closes the client and releases any underlying resources."""
